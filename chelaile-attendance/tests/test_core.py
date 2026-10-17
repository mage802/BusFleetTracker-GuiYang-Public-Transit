import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chelaile as c
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CoreTests(unittest.TestCase):
    def test_protocol_envelope_and_real_crypto_roundtrip(self):
        payload = {'buses':[{'busId':'007274', 'licence':'测试中文'}]}
        for profile in c.KEYS:
            pad = padding.PKCS7(128).padder()
            raw = json.dumps(payload, ensure_ascii=False).encode()
            raw = pad.update(raw) + pad.finalize()
            enc = Cipher(algorithms.AES(c.KEYS[profile].encode()), modes.ECB()).encryptor()
            ciphertext = base64.b64encode(enc.update(raw)+enc.finalize()).decode()
            envelope = {'jsonr':{'status':'00','data':{'encryptResult':ciphertext}}}
            framed = ('**YGKJ'+json.dumps(envelope)+'YGKJ##').encode()
            self.assertEqual(c.decode_data(c.parse_envelope(framed),profile), payload)

    def test_unknown_cipher_not_silently_empty(self):
        with self.assertRaises(ValueError):
            c.decode_data({'encryptResult':base64.b64encode(b'abc').decode()}, 'mini')
        with self.assertRaises(c.BusinessError):
            c.parse_envelope(b'{"jsonr":{"status":"0002","data":{}}}')

    def test_signature_preserves_empty_strings_and_order(self):
        params = {'cityId':'083', 'lat':''}
        expected = hashlib.md5(b'"cityId"="083"&"lat"=""qwihrnbtmj').hexdigest()
        self.assertEqual(c.signature(params,'mini'),expected)
        self.assertNotEqual(c.signature(dict(reversed(list(params.items()))),'mini'),expected)
        self.assertNotEqual(c.signature(params,'h5'),expected)

    def test_vehicle_identity_and_history(self):
        data = {'buses':[{'busId':'007274'}, {'busId':'old','rType':1}, {'licence':'masked'}, {'busId':False}]}
        entries, raw, missing = c.vehicles(data)
        self.assertEqual(entries, [{'bus_id':'007274','licence':''}])
        self.assertEqual((raw,missing),(4,2))
        with self.assertRaises(ValueError):
            c.vehicles({})

    def test_daily_union_across_directions_and_restart(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);c.initialize(folder)
            for lid, stamp in [('forward','2026-10-17T10:00:00+08:00'),('reverse','2026-10-17T11:00:00+08:00')]:
                c.store(folder,'083',lid,stamp,{'buses':[{'busId':'007274'}]})
            c.initialize(folder)
            c.store(folder,'083','forward','2026-10-17T12:00:00+08:00',{'buses':[{'busId':'007274','licence':'label'}]})
            c.export(folder,'2026-10-17')
            rows=json.loads((folder/'2026-10-17/vehicle_list.json').read_text(encoding='utf-8'))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['bus_id'],'007274')
            self.assertEqual(set(rows[0]['line_ids']),{'forward','reverse'})
            self.assertIn('10:00:00',rows[0]['first_seen'])
            self.assertIn('12:00:00',rows[0]['last_seen'])

    def test_scan_records_partial_failures_and_unions_all_directions_offline(self):
        with tempfile.TemporaryDirectory() as name:
            class OfflineClient:
                def __init__(self,args):
                    self.folder=Path(args.out);c.initialize(self.folder)
                    self.dbpath=self.folder/'state.sqlite3';self.stop=threading.Event()
                def catalogue(self,city,refresh):
                    return [{'line_id':lid} for lid in ['a','b','broken']]
                def detail(self,city,line):
                    if line['line_id']=='broken':
                        raise ValueError('Fixture missing buses')
                    return {'buses':[{'busId':'001'}]}
            with patch.object(c,'Client',OfflineClient),patch.object(sys,'argv',
                    ['chelaile.py','scan','--out',name,'--workers','2']):
                c.main()
            with c.connect(Path(name)/'state.sqlite3') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],2)
                self.assertEqual(db.execute("SELECT count(*) FROM coverage WHERE status='error'").fetchone()[0],1)
            output=json.loads((Path(name)/c.now()[:10]/'vehicle_list.json').read_text(encoding='utf-8'))
            self.assertEqual(len(output),1)
            self.assertEqual(set(output[0]['line_ids']),{'a','b'})


if __name__ == '__main__':
    unittest.main()
