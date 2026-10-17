"""Import archived decoded snapshots and their explicit request timestamps offline."""
import argparse
import json
from pathlib import Path
import chelaile as c


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Archived city scan folder')
    p.add_argument('--out',type=Path,default=Path('data'))
    args=p.parse_args();c.initialize(args.out)
    pairs=[]
    for decoded in (args.source/'逐方向响应').glob('*_decoded.json'):
        pairs.append((decoded,decoded.with_name(decoded.name.replace('_decoded.json','_request.json'))))
    for decoded in (args.source/'异常方向复核').glob('*/decoded.json'):
        pairs.append((decoded,decoded.with_name('validation.json')))
    if not pairs:
        raise ValueError('No supported decoded snapshots found')
    days=set();failed=[];imported=0
    for decoded,metadata in pairs:
        try:
            d=json.loads(decoded.read_text(encoding='utf-8'))
            meta=json.loads(metadata.read_text(encoding='utf-8'))
            stamp=meta['time']
            if len(stamp)<19 or stamp[10]!='T':
                raise ValueError('Explicit ISO collection timestamp required')
            line=d['line'];lid=str(line['lineId'])
            city=str(line.get('realCityId') or meta.get('parameters',{}).get('cityId') or '')
            if not city:
                raise ValueError('Missing city ID')
            c.vehicles(d)
            c.store(args.out,city,lid,stamp,d)
            days.add(stamp[:10]);imported+=1
        except Exception as exc:
            failed.append({'file':str(decoded),'error':type(exc).__name__+': '+str(exc)})
    for day in sorted(days):
        c.export(args.out,day)
    c.write_json(args.out/'import_summary.json',dict(snapshots_found=len(pairs),imported=imported,failed=failed))
    print(json.dumps(dict(snapshots_found=len(pairs),imported=imported,failures=len(failed))))
    if failed:
        raise SystemExit(1)


if __name__=='__main__':
    main()
