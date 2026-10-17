"""SQLite is the durable source of truth for resume and daily deduplication."""
import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path, obj):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS attempts(
          job TEXT, round INTEGER, line TEXT, direction TEXT, attempt INTEGER,
          city TEXT, observed_at TEXT, company TEXT, outcome TEXT,
          identifiers TEXT, error TEXT, retryable INTEGER, seconds REAL, raw_file TEXT,
          PRIMARY KEY(job,round,line,direction,attempt));
        CREATE TABLE IF NOT EXISTS sightings(
          day TEXT, city TEXT, line TEXT, direction TEXT, vehicle TEXT,
          first_seen TEXT, last_seen TEXT, observations INTEGER,
          PRIMARY KEY(day,city,line,direction,vehicle));
        """)

    def state(self, job, round_number):
        cursor = self.db.execute("""SELECT line,direction,attempt,outcome,retryable
            FROM attempts WHERE job=? AND round=? ORDER BY attempt""", (job, round_number))
        return {(line, direction): dict(attempt=attempt, outcome=outcome, retryable=bool(retry))
                for line, direction, attempt, outcome, retry in cursor}

    def record(self, job, round_number, city, target, attempt, result):
        line, direction = target["line"], target["direction"]
        stamp = result["observed_at"]
        ids = result.get("identifiers", [])
        with self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO attempts VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job, round_number, line, direction, attempt, city, stamp,
                 " / ".join(target["companies"]), result["outcome"], json.dumps(ids),
                 result.get("error"), int(result.get("retryable", False)),
                 result["seconds"], result.get("raw_file")))
            if not cursor.rowcount:
                return  # Same attempt replayed after an interrupted checkpoint.
            if result["outcome"] == "success":
                for vehicle in ids:
                    self.db.execute("""INSERT INTO sightings VALUES(?,?,?,?,?,?,?,1)
                        ON CONFLICT(day,city,line,direction,vehicle) DO UPDATE SET
                        first_seen=MIN(first_seen,excluded.first_seen),
                        last_seen=MAX(last_seen,excluded.last_seen),
                        observations=observations+1""",
                        (stamp[:10], city, line, direction, vehicle, stamp, stamp))

    @staticmethod
    def csv(path, header, rows):
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        temp.replace(path)

    def export_daily(self, root):
        for name, sql in [
            ("daily_by_direction.csv", "SELECT * FROM sightings ORDER BY day,city,line,direction,vehicle"),
            ("daily_by_line.csv", """SELECT day,city,line,vehicle,MIN(first_seen) first_seen,
                MAX(last_seen) last_seen FROM sightings GROUP BY day,city,line,vehicle
                ORDER BY day,city,line,vehicle""")]:
            cursor = self.db.execute(sql)
            self.csv(root / name, [c[0] for c in cursor.description], cursor)

    def export_job(self, root, job):
        cursor = self.db.execute("""SELECT round,line,direction,attempt,observed_at,company,
            outcome,identifiers,error,retryable,seconds,raw_file FROM attempts
            WHERE job=? ORDER BY round,line,direction,attempt""", (job,))
        entries = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor]
        for entry in entries:
            entry["identifiers"] = json.loads(entry["identifiers"])
        latest = {}
        for entry in entries:
            latest[(entry["round"], entry["line"], entry["direction"])] = entry
        self.csv(root / "attempts.csv",
                 ["round", "line", "direction", "attempt", "observed_at", "outcome", "error", "seconds"],
                 ([e[k] for k in ("round", "line", "direction", "attempt", "observed_at", "outcome", "error", "seconds")]
                  for e in entries))
        self.csv(root / "vehicles.csv",
                 ["round", "day", "line", "direction", "company", "vehicle", "observed_at"],
                 ([e["round"], e["observed_at"][:10], e["line"], e["direction"], e["company"], v, e["observed_at"]]
                  for e in latest.values() if e["outcome"] == "success" for v in e["identifiers"]))
        lines = sorted({e["line"] for e in latest.values()})
        self.csv(root / "line_summary.csv",
                 ["line", "company", "unique_vehicles", "identifiers"],
                 ([line, " / ".join(sorted({e["company"] for e in latest.values() if e["line"] == line})),
                   len({v for e in latest.values() if e["line"] == line for v in e["identifiers"]}),
                   "、".join(sorted({v for e in latest.values() if e["line"] == line for v in e["identifiers"]}))]
                  for line in lines))
        return list(latest.values())

    def close(self):
        self.db.close()
