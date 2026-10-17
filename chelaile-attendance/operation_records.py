"""Append-only operation observations; never infer metres from coordinates."""
import csv
import hashlib
import json
from pathlib import Path

SCHEMA = '''CREATE TABLE IF NOT EXISTS operation_records(
 event_key TEXT PRIMARY KEY,day TEXT,city_id TEXT,line_id TEXT,bus_id TEXT,
 vehicle_label TEXT,line_name TEXT,destination TEXT,next_station TEXT,
 next_distance_m REAL,observed_at TEXT,signal_time TEXT,position_status TEXT,
 raw_bus_json TEXT)'''
COLUMNS = ['车号','运营线路','运营方向','下一站名称','距离下站距离（米）','采集时间']


def records(data, city, stamp):
    line = data.get('line', {})
    lid = str(line.get('lineId', ''))
    stations = {str(s.get('order')): s for s in data.get('stations', []) if isinstance(s, dict)}
    if not lid or not isinstance(data.get('buses'), list):
        raise ValueError('Operation records need lineId and buses')
    for bus in data['buses']:
        if str(bus.get('rType', '')) == '1':
            continue
        bid = bus.get('busId')
        if isinstance(bid, bool) or not isinstance(bid, (str, int)) or not str(bid).strip():
            continue
        station = stations.get(str(bus.get('order')))
        next_name, distance, status = '', None, 'unknown_position'
        state = str(bus.get('state', ''))
        if station and state == '0':
            next_name = str(station.get('sn') or '')
            raw = bus.get('distanceToSc')
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
                distance, status = raw, 'approaching_reported_next_stop_distance'
            else:
                status = 'approaching_distance_unavailable'
        elif station and state == '1':
            # Output convention: while arrived, show the occupied stop as the
            # displayed next stop, including a terminal, with zero metres.
            next_name = str(station.get('sn') or '')
            distance, status = 0, 'at_stop_current_station_zero_distance'
        payload = json.dumps(bus, ensure_ascii=False, sort_keys=True, separators=(',',':'))
        event = json.dumps([city,lid,str(bid),stamp,payload],ensure_ascii=False)
        yield (hashlib.sha256(event.encode()).hexdigest(),stamp[:10],city,lid,str(bid),
               str(bus.get('licence') or bid),str(line.get('name') or ''),
               str(line.get('endSn') or line.get('destinationName') or ''),next_name,
               distance,stamp,str(bus.get('timeStr') or ''),status,payload)


def insert(db, data, city, stamp):
    db.execute(SCHEMA)
    rows = list(records(data, city, stamp))
    db.executemany('INSERT INTO operation_records VALUES('+','.join(['?']*14)+') '
                   'ON CONFLICT(event_key) DO UPDATE SET next_station=excluded.next_station,'
                   'next_distance_m=excluded.next_distance_m,position_status=excluded.position_status',rows)
    return len(rows)


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=','+','-','@')):
        return "'"+value
    return value


def export(db, folder, day):
    db.execute(SCHEMA)
    rows = db.execute('SELECT vehicle_label,line_name,destination,next_station,next_distance_m,'
                      'observed_at,city_id,line_id,bus_id,signal_time,position_status '
                      'FROM operation_records WHERE day=? ORDER BY observed_at,line_id,bus_id', (day,)).fetchall()
    folder=Path(folder)/day;folder.mkdir(parents=True,exist_ok=True)
    for name, headings, content in [
        ('operation_records.csv', COLUMNS, [r[:6] for r in rows]),
        ('operation_records_audit.csv', COLUMNS+['城市ID','方向ID','平台busId','车辆信号时间','位置解析状态'],rows)]:
        with (folder/name).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(headings)
            for row in content:
                writer.writerow([safe_cell(v) for v in row])
    return len(rows)
