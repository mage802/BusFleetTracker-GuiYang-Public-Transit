"""Chelaile protocol client and daily observed-vehicle collector. Python 3.10+."""
import argparse
import base64
import csv
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import operation_records
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ORIGIN = 'https://web.chelaile.net.cn'
CST = timezone(timedelta(hours=8))
SALT = 'qwihrnbtmj'
KEYS = {'mini': 'FF32AE65FBFD19414EAAFF6291A54B42',
        'h5': '422556651C7F7B2B5C266EED06068230'}
MINI_PARAMS = dict(s='h5', wxs='wx_app', sign='1', h5RealData='1',
                   v='3.11.28', src='weixinapp_cx', ctm_mp='mp_wx', vc='2',
                   favoriteGray='1', gpstype='wgs', geo_type='wgs', scene='1256')
MINI_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 '
                  'MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI '
                  'MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) '
                  'UnifiedPCWindowsWechat(0xf254160a) XWEB/18055',
    'Content-Type': 'text', 'Accept': '*/*',
    'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty',
    'Referer': 'https://servicewechat.com/wx71d589ea01ce3321/814/page-frame.html',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}


def now():
    return datetime.now(CST).isoformat(timespec='seconds')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class BusinessError(RuntimeError):
    pass


class StopCollection(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_envelope(raw):
    obj = raw.decode('utf-8-sig')
    for _ in range(3):
        if not isinstance(obj, str):
            break
        obj = obj.strip()
        for start, end in [('**YGKJ', 'YGKJ##'), ('YGKJ##', '**YGKJ')]:
            if obj.startswith(start) and obj.endswith(end):
                obj = obj[len(start):-len(end)]
                break
        obj = json.loads(obj)
    if not isinstance(obj, dict):
        raise ValueError('Response must contain a JSON object')
    root = obj.get('jsonr', obj)
    if not isinstance(root, dict):
        raise ValueError('Invalid envelope')
    status = str(root.get('status', ''))
    if status not in ('00', 'OK'):
        if status in ('401', '403', '429'):
            raise StopCollection('Upstream access/rate response: ' + status)
        raise BusinessError('Upstream business status: ' + status)
    data = root.get('data')
    if not isinstance(data, dict):
        raise ValueError('Missing data object')
    return data


def decode_data(data, profile):
    if 'encryptResult' not in data:
        return data
    raw = base64.b64decode(data['encryptResult'], validate=True)
    if not raw or len(raw) % 16:
        raise ValueError('Unsupported ciphertext framing: AES block length mismatch')
    dec = Cipher(algorithms.AES(KEYS[profile].encode('ascii')), modes.ECB()).decryptor()
    plain = dec.update(raw) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    plain = unpad.update(plain) + unpad.finalize()
    obj = json.loads(plain.decode('utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('Decrypted response must be a JSON object')
    return obj


def signature(params, profile):
    if profile == 'mini':
        text = '&'.join(f'"{k}"="{v}"' for k, v in params.items())
    else:
        text = '&'.join(f'{k}={v}' for k, v in params.items())
    return hashlib.md5((text + SALT).encode('utf-8')).hexdigest()


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    db = sqlite3.connect(path, timeout=30, factory=ManagedConnection)
    db.execute('PRAGMA busy_timeout=30000')
    return db


def initialize(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with connect(folder / 'state.sqlite3') as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS pace(id INTEGER PRIMARY KEY, last_start REAL, day TEXT, used INTEGER);
        INSERT OR IGNORE INTO pace VALUES(1,0,'',0);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS observations(
          day TEXT,city_id TEXT,line_id TEXT,bus_id TEXT,licence TEXT,
          first_seen TEXT,last_seen TEXT,PRIMARY KEY(day,city_id,line_id,bus_id));
        CREATE TABLE IF NOT EXISTS coverage(
          observed_at TEXT,city_id TEXT,line_id TEXT,status TEXT,
          raw_count INTEGER,live_count INTEGER,missing_ids INTEGER,error TEXT);
        ''')
        db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',
                   ('guest', 'browser_' + str(int(time.time()*1000)) + '_' + secrets.token_hex(4)))
        db.execute(operation_records.SCHEMA)


class Client:
    def __init__(self, args):
        self.folder = Path(args.out)
        initialize(self.folder)
        self.dbpath = self.folder / 'state.sqlite3'
        self.profile, self.src = args.profile, args.src
        self.interval, self.budget = args.interval, args.daily_budget
        self.stop = threading.Event()
        with connect(self.dbpath) as db:
            self.guest = db.execute("SELECT value FROM meta WHERE key='guest'").fetchone()[0]

    def reserve(self):
        # SQLite transaction shares one start-rate and daily budget across workers/processes.
        while not self.stop.is_set():
            db = connect(self.dbpath)
            try:
                db.execute('BEGIN IMMEDIATE')
                last, day, used = db.execute('SELECT last_start,day,used FROM pace WHERE id=1').fetchone()
                today = now()[:10]
                used = used if day == today else 0
                if used >= self.budget:
                    raise StopCollection('Daily request budget exhausted')
                delay = max(0, last + self.interval - time.time())
                if delay <= 0:
                    db.execute('UPDATE pace SET last_start=?,day=?,used=? WHERE id=1',
                               (time.time(), today, used + 1))
                    db.commit()
                    return
                db.rollback()
            finally:
                db.close()
            self.stop.wait(min(delay, 1))
        raise StopCollection('Collection stopped')

    def request(self, path, params, profile, headers):
        self.reserve()
        url = ORIGIN + path + '?' + urlencode(params)
        try:
            with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=25) as res:
                raw = res.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('Response exceeded 4 MiB')
            return decode_data(parse_envelope(raw), profile)
        except HTTPError as e:
            if e.code in (401, 403, 429):
                self.stop.set()
                raise StopCollection('HTTP ' + str(e.code) + '; stopped without channel switching') from e
            raise
        except StopCollection:
            self.stop.set()
            raise

    def h5_public(self, city, business):
        params = dict(business)
        params.update(cityId=city, s='h5', v='3.3.21', src=self.src or 'webapp_default',
                      userId=self.guest, h5Id=self.guest, sign='1', vc='1')
        return params

    def catalogue(self, city, refresh=False):
        cache = self.folder / ('catalogue_' + city + '.json')
        if cache.exists() and not refresh and 0 <= time.time() - cache.stat().st_mtime < 86400:
            return json.loads(cache.read_text(encoding='utf-8'))
        data = self.request('/api/bus/cityLineList', self.h5_public(city, {}), 'h5',
                            {'Referer': ORIGIN + '/ch5/index.html', 'Accept': '*/*'})
        items = data.get('allLines', {}).get('all')
        if not isinstance(items, list):
            raise ValueError('Missing allLines.all; directory completeness unknown')
        lines = {}
        for row in items:
            lid = row.get('lineId')
            if not isinstance(lid, str) or not lid:
                raise ValueError('Invalid directory lineId')
            lines[lid] = dict(city_id=city, line_id=lid, line_name=row.get('lineName', ''),
                              start=row.get('startStopName', ''), end=row.get('endStopName', ''))
        result = list(lines.values())
        write_json(cache, result)
        return result

    def detail(self, city, line, business=None):
        lid = line['line_id']
        if self.profile == 'mini':
            params = dict(MINI_PARAMS)
            if self.src:
                params['src'] = self.src
            params.update(cityId=city, localCityId=city, lineId=lid,
                          lat='', lng='', geo_lat='', geo_lng='')
            if business:
                params.update(business)
            params['cryptoSign'] = signature(params, 'mini')
            headers = MINI_HEADERS
        else:
            if not business or str(business.get('lineId')) != lid:
                raise ValueError('H5 requires matching --business JSON with station/direction fields')
            signed = dict(business)
            signed['cryptoSign'] = signature(business, 'h5')
            params = self.h5_public(city, signed)
            params['src'] = self.src or 'webapp_alipay_fuwu'
            headers = {'Referer': ORIGIN + '/ch5/index.html', 'Accept': '*/*',
                       'Content-Type': 'text/plain;charset=UTF-8'}
        data = self.request('/api/bus/line!encryptedLineDetail.action', params, self.profile, headers)
        if str(data.get('line', {}).get('lineId')) != lid:
            raise ValueError('Returned lineId differs from requested direction')
        return data


def vehicles(data):
    buses = data.get('buses')
    if not isinstance(buses, list):
        raise ValueError('Missing buses array; not an empty fleet')
    result, missing = [], 0
    for bus in buses:
        if not isinstance(bus, dict):
            raise ValueError('Invalid vehicle object')
        if str(bus.get('rType', '')) == '1':
            continue
        bid = bus.get('busId')
        if isinstance(bid, bool) or not isinstance(bid, (str, int)) or not str(bid).strip():
            missing += 1
            continue
        result.append(dict(bus_id=str(bid), licence=str(bus.get('licence') or '')))
    return result, len(buses), missing


def export(folder, day):
    with connect(folder / 'state.sqlite3') as db:
        operation_records.export(db, folder, day)
        rows = db.execute('SELECT city_id,line_id,bus_id,licence,first_seen,last_seen '
                          'FROM observations WHERE day=? ORDER BY city_id,bus_id,line_id', (day,)).fetchall()
        cov = db.execute('SELECT observed_at,city_id,line_id,status,raw_count,live_count,missing_ids,error '
                         'FROM coverage WHERE substr(observed_at,1,10)=?', (day,)).fetchall()
    records = {}
    for city, line, bid, plate, first, last in rows:
        r = records.setdefault((city, bid), dict(day=day, city_id=city, bus_id=bid, licence=plate,
                                               line_ids=[], first_seen=first, last_seen=last))
        r['line_ids'].append(line)
        r['first_seen'], r['last_seen'] = min(r['first_seen'], first), max(r['last_seen'], last)
        if plate:
            r['licence'] = plate
    dest = folder / day
    write_json(dest / 'vehicle_list.json', list(records.values()))
    write_json(dest / 'coverage.json', [dict(zip(
        ['observed_at','city_id','line_id','status','raw_count','live_count','missing_ids','error'], r)) for r in cov])
    with (dest / 'vehicle_list.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['day','city_id','bus_id','licence','line_ids','first_seen','last_seen'])
        w.writeheader()
        for r in records.values():
            r = dict(r, line_ids=';'.join(r['line_ids']))
            w.writerow({k: "'"+v if isinstance(v, str) and v.lstrip().startswith(('=','+','-','@')) else v
                        for k, v in r.items()})
    print(json.dumps({'day': day, 'unique_vehicles': len(records), 'output': str(dest)}, ensure_ascii=False))


def store(folder, city, lid, stamp, data=None, error=''):
    entries, raw, missing = vehicles(data) if data is not None else ([], None, None)
    with connect(folder / 'state.sqlite3') as db:
        if data is not None:
                operation_records.insert(db, data, city, stamp)
        for row in entries:
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?) '
                       'ON CONFLICT(day,city_id,line_id,bus_id) DO UPDATE SET '
                       'last_seen=excluded.last_seen,licence=CASE WHEN excluded.licence<>\'\' '
                       'THEN excluded.licence ELSE observations.licence END',
                       (stamp[:10], city, lid, row['bus_id'], row['licence'], stamp, stamp))
        db.execute('INSERT INTO coverage VALUES(?,?,?,?,?,?,?,?)',
                   (stamp, city, lid, 'decoded' if data is not None else 'error', raw, len(entries), missing, error))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['cities','catalogue','line','scan','export','decode'])
    ap.add_argument('--city', default='083', help='City ID as a string, including leading zeros')
    ap.add_argument('--line-id', default='851259873106')
    ap.add_argument('--profile', choices=['mini','h5'], default='mini')
    ap.add_argument('--src', help='Explicit compatible src; no automatic channel switching')
    ap.add_argument('--business', type=Path, help='JSON business parameters; required for H5 line mode')
    ap.add_argument('--out', default='data')
    ap.add_argument('--interval', type=float, default=1.0, help='Global minimum seconds between request starts')
    ap.add_argument('--daily-budget', type=int, default=1200)
    ap.add_argument('--workers', type=int, default=1, choices=range(1,17), metavar='1..16')
    ap.add_argument('--rounds', type=int, default=1, help='Number of scan passes; 0 runs until interrupted')
    ap.add_argument('--period', type=float, default=300, help='Minimum seconds between scan starts')
    ap.add_argument('--refresh', action='store_true', help='Refresh the 24-hour directory cache')
    ap.add_argument('--save-responses', action='store_true', help='Save decrypted line JSON locally')
    ap.add_argument('--day', default=now()[:10])
    ap.add_argument('--input', type=Path, help='Response text for offline decode')
    args = ap.parse_args()
    if args.interval <= 0 or args.daily_budget < 1 or args.rounds < 0 or args.period < 0:
        ap.error('Invalid pacing, budget or rounds')
    folder = Path(args.out)
    if args.command == 'decode':
        if not args.input:
            ap.error('decode needs --input')
        write_json(folder / 'decoded.json', decode_data(parse_envelope(args.input.read_bytes()), args.profile))
        return
    if args.command == 'export':
        initialize(folder)
        export(folder, args.day)
        return
    if args.profile == 'h5' and args.command == 'scan':
        ap.error('City scan uses mini profile; H5 business parameters are direction/station-specific')
    business = json.loads(args.business.read_text(encoding='utf-8')) if args.business else None
    client = Client(args)
    if args.command == 'cities':
        data = client.request('/cdatasource/citylist', {}, 'h5', {'Accept':'*/*'})
        write_json(folder / 'cities.json', data)
    elif args.command == 'catalogue':
        print(json.dumps({'direction_count':len(client.catalogue(args.city, args.refresh))}))
    elif args.command == 'line':
        data = client.detail(args.city, {'line_id':args.line_id}, business)
        stamp = now()
        store(folder, args.city, args.line_id, stamp, data)
        write_json(folder / 'line_decoded.json', data)
        live, count, missing = vehicles(data)
        print(json.dumps(dict(raw_count=count, vehicles=live, missing_ids=missing), ensure_ascii=False))
        export(folder, stamp[:10])
    elif args.command == 'scan':
        lines = client.catalogue(args.city, args.refresh)
        def query(line):
            try:
                data = client.detail(args.city, line)
                vehicles(data)
                return line, now(), data, ''
            except StopCollection:
                client.stop.set()
                raise
            except Exception as exc:
                return line, now(), None, type(exc).__name__ + ': ' + str(exc)
        iteration = 0
        try:
            while not client.stop.is_set() and (args.rounds == 0 or iteration < args.rounds):
                started = time.monotonic()
                failures = done = 0
                dates = set()
                pending = set()
                iterator = iter(lines)
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    for _ in range(args.workers):
                        line = next(iterator, None)
                        if line is not None:
                            pending.add(pool.submit(query, line))
                    try:
                        while pending:
                            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                            for task in completed:
                                line, stamp, data, error = task.result()
                                store(folder, args.city, line['line_id'], stamp, data, error)
                                dates.add(stamp[:10])
                                done += 1
                                failures += bool(error)
                                if data is not None and args.save_responses:
                                    name = hashlib.sha256(line['line_id'].encode()).hexdigest()[:20]
                                    write_json(folder / 'snapshots' / (name+'.json'), data)
                                if done % 20 == 0:
                                    print(json.dumps(dict(completed=done, total=len(lines), failed=failures)), flush=True)
                                line = next(iterator, None)
                                if line is not None:
                                    pending.add(pool.submit(query, line))
                    except BaseException:
                        client.stop.set()
                        for task in pending:
                            task.cancel()
                        raise
                iteration += 1
                for day in dates:
                    export(folder, day)
                print(json.dumps(dict(round=iteration, directions=done, failed=failures)), flush=True)
                if args.rounds and iteration >= args.rounds:
                    break
                client.stop.wait(max(0, args.period - (time.monotonic()-started)))
        finally:
            client.stop.set()
            # Export all observed dates even if a pass was interrupted at midnight.
            with connect(client.dbpath) as db:
                days = [r[0] for r in db.execute('SELECT DISTINCT day FROM observations')]
            for day in days:
                export(folder, day)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped; previously committed observations are retained.')
        raise SystemExit(130)
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc))
        raise SystemExit(1)
