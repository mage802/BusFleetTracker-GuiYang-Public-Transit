import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from mygolbs.api import AccessDenied, ApiError, Client, classify_realtime
from mygolbs.collector import run, select_targets
from mygolbs.storage import Store


def settings(path):
    return dict(city_name="贵阳市", city_code="520100", company_prefixes=["贵阳公交", "贵阳黔爽"],
                lines=[], min_number=None, max_number=None, output=str(path), workers=1,
                batch_pause=3, timeout=25, retries=2, rounds=1, pause=0, save_raw=True)


CATALOG = {"status": 1, "buslines": [
    {"lineName": "3路", "upperOrDown": "1", "company": "贵阳公交中部分公司"},
    {"lineName": "3路", "upperOrDown": "2", "company": "贵阳公交中部分公司"},
    {"lineName": "B1路", "upperOrDown": "1", "company": "贵阳公交北部分公司"},
    {"lineName": "100路", "upperOrDown": "1", "company": "其他公司"}]}


class ApiTests(unittest.TestCase):
    def test_form_headers_and_name_only(self):
        with patch("mygolbs.api.urllib.request.urlopen", return_value=io.BytesIO(b'{"status":"1","list":[]}')) as opened:
            obj = Client(city_code="").call(104, LINENAME="3路", DIRECTION="1")
            req = opened.call_args.args[0]
            form = urllib.parse.parse_qs(req.data.decode())
            self.assertEqual(req.get_header("Origin"), "https://h5.mygolbs.com")
            self.assertEqual(form["LINENAME"], ["3路"])
            self.assertNotIn("CITYCODE", form)
            self.assertEqual(classify_realtime(obj), ("success", []))

    def test_non_json_denial_and_missing_ids(self):
        with patch("mygolbs.api.urllib.request.urlopen", return_value=io.BytesIO(b'not json')):
            with self.assertRaises(ApiError):
                Client().call(104)
        with patch("mygolbs.api.urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 429, "limited", {}, None)):
            with self.assertRaises(AccessDenied):
                Client().call(104)
        self.assertEqual(classify_realtime({"hasReal": 0}), ("unavailable", []))
        with self.assertRaises(ApiError):
            classify_realtime({})
        with self.assertRaises(ApiError):
            classify_realtime({"list": [{"busNumber": "007001"}, {}]})

    def test_selection(self):
        cfg = settings("unused")
        self.assertEqual(len(select_targets(CATALOG, cfg)), 3)
        cfg.update(min_number=1, max_number=100)
        self.assertEqual(len(select_targets(CATALOG, cfg)), 2)
        cfg.update(company_prefixes=[], lines=["100路"])
        self.assertEqual(select_targets(CATALOG, cfg)[0]["line"], "100路")
        cfg["lines"] = ["不存在"]
        with self.assertRaises(ValueError):
            select_targets(CATALOG, cfg)


class PersistenceTests(unittest.TestCase):
    def test_idempotency_day_and_city_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.sqlite3")
            target = dict(line="3路", direction="1", companies=["公司"])
            result = dict(outcome="success", observed_at="2026-10-17T12:00:00+08:00", identifiers=["007001"], seconds=1)
            store.record("job", 1, "贵阳", target, 1, result)
            store.record("job", 1, "贵阳", target, 1, result)
            self.assertEqual(store.db.execute("SELECT vehicle,observations FROM sightings").fetchall(), [("007001", 1)])
            result["observed_at"] = "2026-10-18T00:00:00+08:00"
            store.record("job", 2, "贵阳", target, 1, result)
            store.record("other", 1, "其他城市", target, 1, result)
            self.assertEqual(store.db.execute("SELECT count(*) FROM sightings").fetchone()[0], 3)
            store.close()

    def test_retry_unavailable_and_completed_resume(self):
        counts = {}
        class FakeClient:
            def __init__(self, *args): pass
            def call(self, cmd, **params):
                if str(cmd) == "119": return CATALOG
                key = (params["LINENAME"], params["DIRECTION"])
                counts[key] = counts.get(key, 0) + 1
                if key == ("3路", "1") and counts[key] == 1:
                    raise TimeoutError("sample timeout")
                if key == ("3路", "2"):
                    return {"status": 1, "hasReal": 0}
                return {"status": 1, "list": [{"busNumber": "007001"}]}
        with tempfile.TemporaryDirectory() as tmp, patch("mygolbs.collector.Client", FakeClient), patch("mygolbs.collector.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            cfg = settings(tmp)
            self.assertEqual(run(cfg), 0)
            folder = next((Path(tmp) / "jobs").iterdir())
            summary = json.loads((folder / "summary.json").read_text(encoding="utf8"))
            self.assertEqual(summary["outcomes"], {"success": 2, "unavailable": 1, "pending": 0})
            self.assertEqual(counts[("3路", "1")], 2)
            self.assertEqual(counts[("3路", "2")], 1)
            before = dict(counts)
            self.assertEqual(run(cfg, folder), 0)
            self.assertEqual(counts, before)
            self.assertTrue((Path(tmp) / "daily_by_line.csv").exists())

    def test_interrupt_and_resume(self):
        interrupted = False
        class FakeClient:
            def __init__(self, *args): pass
            def call(self, cmd, **params):
                nonlocal interrupted
                if str(cmd) == "119": return CATALOG
                if params["DIRECTION"] == "2" and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt()
                return {"status": 1, "list": [{"busNumber": "000001"}]}
        with tempfile.TemporaryDirectory() as tmp, patch("mygolbs.collector.Client", FakeClient), patch("mygolbs.collector.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            cfg = settings(tmp)
            self.assertEqual(run(cfg), 2)
            folder = next((Path(tmp) / "jobs").iterdir())
            self.assertEqual(run(cfg, folder), 0)
            store = Store(Path(tmp) / "attendance.sqlite3")
            self.assertTrue(all(n == 1 for (n,) in store.db.execute("SELECT observations FROM sightings")))
            self.assertEqual(store.db.execute("SELECT count(*) FROM sightings").fetchone()[0], 3)
            store.close()

    def test_access_denial_stops_submissions(self):
        calls = []
        class FakeClient:
            def __init__(self, *args): pass
            def call(self, cmd, **params):
                calls.append(str(cmd))
                if str(cmd) == "119": return CATALOG
                raise AccessDenied("HTTP 429")
        with tempfile.TemporaryDirectory() as tmp, patch("mygolbs.collector.Client", FakeClient), patch("mygolbs.collector.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(settings(tmp)), 2)
            self.assertEqual(calls, ["119", "104"])
            folder = next((Path(tmp) / "jobs").iterdir())
            self.assertEqual(json.loads((folder / "summary.json").read_text(encoding="utf8"))["outcomes"]["pending"], 2)

    def test_retry_limit_and_nonzero_exit(self):
        calls = 0
        class FakeClient:
            def __init__(self, *args): pass
            def call(self, cmd, **params):
                nonlocal calls
                if str(cmd) == "119": return CATALOG
                calls += 1
                raise TimeoutError("sample timeout")
        with tempfile.TemporaryDirectory() as tmp, patch("mygolbs.collector.Client", FakeClient), patch("mygolbs.collector.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            cfg = settings(tmp)
            cfg.update(lines=["B1路"])
            self.assertEqual(run(cfg), 3)
            self.assertEqual(calls, 3)

    def test_resume_after_round_completed_before_pause(self):
        class FakeClient:
            def __init__(self, *args): pass
            def call(self, cmd, **params):
                if str(cmd) == "119": return CATALOG
                return {"status": 1, "list": [{"busNumber": "000001"}]}
        def interrupted_pause(duration):
            if duration == 30:
                raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as tmp, patch("mygolbs.collector.Client", FakeClient), contextlib.redirect_stdout(io.StringIO()):
            cfg = settings(tmp)
            cfg.update(rounds=2, pause=30)
            with patch("mygolbs.collector.time.sleep", side_effect=interrupted_pause):
                self.assertEqual(run(cfg), 2)
            folder = next((Path(tmp) / "jobs").iterdir())
            with patch("mygolbs.collector.time.sleep"):
                self.assertEqual(run(cfg, folder), 0)
            store = Store(Path(tmp) / "attendance.sqlite3")
            self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 6)
            self.assertTrue(all(n == 2 for (n,) in store.db.execute("SELECT observations FROM sightings")))
            store.close()


if __name__ == "__main__":
    unittest.main()
