"""Selection, bounded concurrent collection, retry queues and durable resume."""
import concurrent.futures as cf
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .api import AccessDenied, ApiError, Client, classify_realtime, is_transient
from .storage import Store, now, write_json


def select_targets(catalog, settings):
    rows = catalog.get("buslines")
    if not isinstance(rows, list) or not rows:
        raise ApiError("Missing or empty catalog; refusing to treat it as a complete city")
    names = set(settings.get("lines", []))
    found = set()
    targets = {}
    for row in rows:
        line, direction = row.get("lineName"), str(row.get("upperOrDown"))
        if not line or direction not in ("1", "2"):
            continue
        if names and line not in names:
            continue
        company = str(row.get("company") or "").strip()
        prefixes = settings.get("company_prefixes", [])
        if prefixes and not any(company.startswith(p) for p in prefixes):
            continue
        lo, hi = settings.get("min_number"), settings.get("max_number")
        if lo is not None or hi is not None:
            match = re.fullmatch(r"(\d+)路", line)
            if not match or (lo is not None and int(match[1]) < lo) or (hi is not None and int(match[1]) > hi):
                continue
        found.add(line)
        targets.setdefault((line, direction), set()).add(company)
    if names - found:
        raise ValueError("Requested lines absent from selected catalog: " + ", ".join(sorted(names - found)))
    if not targets:
        raise ValueError("No lines matched the filters")
    return [dict(line=line, direction=direction, companies=sorted(companies))
            for (line, direction), companies in sorted(targets.items())]


def catalog_with_retry(client, retries, pause):
    for attempt in range(retries + 1):
        try:
            return client.call("119", KEY="")
        except Exception as error:
            if not is_transient(error) or attempt == retries:
                raise
            time.sleep(max(pause, min(30, 5 * (attempt + 1))))


def run(settings, resume=None):
    if resume:
        folder = Path(resume).resolve()
        meta = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        settings = meta["settings"]
        root = folder.parent.parent
        if folder.parent.name != "jobs" or not (root / "attendance.sqlite3").exists():
            raise ValueError("Resume requires the entire data folder, including attendance.sqlite3")
    else:
        root = Path(settings["output"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        client = Client(settings["city_name"], settings["city_code"], settings["timeout"])
        catalog = catalog_with_retry(client, settings["retries"], settings["batch_pause"])
        targets = select_targets(catalog, settings)
        job_id = now()[:19].replace(":", "").replace("-", "") + "_" + uuid.uuid4().hex[:8]
        folder = root / "jobs" / job_id
        folder.mkdir(parents=True)
        write_json(folder / "catalog.json", catalog)
        meta = dict(job=job_id, started=now(), settings=settings, targets=targets,
                    completed_rounds=0, active_round=1, stopped=None)
        write_json(folder / "job.json", meta)
    client = Client(settings["city_name"], settings["city_code"], settings["timeout"])
    store = Store(root / "attendance.sqlite3")
    # Include both city selectors in the key; name-only profiles remain distinct.
    city = settings["city_name"] + "|" + str(settings.get("city_code") or "")
    targets = meta["targets"]
    workers = settings["workers"]
    all_rounds = settings["rounds"]
    job_id = meta["job"]
    if meta["completed_rounds"] >= meta["active_round"] and (all_rounds == 0 or meta["completed_rounds"] < all_rounds):
        meta["active_round"] = meta["completed_rounds"] + 1
    stopped = None

    def fetch(target):
        begin = time.monotonic()
        result = {}
        try:
            obj = client.call("104", LINENAME=target["line"], DIRECTION=target["direction"])
            outcome, ids = classify_realtime(obj)
            result.update(outcome=outcome, identifiers=ids, response=obj)
        except Exception as error:
            result.update(outcome="failed", error=f"{type(error).__name__}: {error}",
                          retryable=is_transient(error), blocked=isinstance(error, AccessDenied))
        result.update(observed_at=now(), seconds=round(time.monotonic() - begin, 3))
        return result

    def checkpoint():
        latest = store.export_job(folder, job_id)
        active = [e for e in latest if e["round"] == meta["active_round"]]
        outcomes = Counter(e["outcome"] for e in active)
        outcomes["pending"] = len(targets) - len(active)
        summary = dict(job=job_id, started=meta["started"], updated_at=now(),
                       target_lines=len({t["line"] for t in targets}), target_directions=len(targets),
                       active_round=meta["active_round"], completed_rounds=meta["completed_rounds"],
                       outcomes=dict(outcomes),
                       unique_vehicles=len({v for e in latest for v in e["identifiers"]}),
                       failed_direction_rounds=sum(e["outcome"] == "failed" for e in latest),
                       stopped=stopped)
        write_json(folder / "summary.json", summary)
        write_json(folder / "job.json", meta)
        return summary

    print(json.dumps(dict(job=str(folder), lines=len({t["line"] for t in targets}),
                          directions=len(targets), workers=workers), ensure_ascii=False), flush=True)
    try:
        meta["stopped"] = None
        while all_rounds == 0 or meta["completed_rounds"] < all_rounds:
            round_number = meta["active_round"]
            state = store.state(job_id, round_number)
            pending = [t for t in targets if (t["line"], t["direction"]) not in state or
                       (state[(t["line"], t["direction"])] ["outcome"] == "failed" and
                        state[(t["line"], t["direction"])] ["retryable"] and
                        state[(t["line"], t["direction"])] ["attempt"] < settings["retries"] + 1)]
            retry_pass = 0
            recent = []
            while pending and not stopped:
                requeued = []
                offset = 0
                # Reduce concurrency on retries; worker count never exceeds the user setting.
                if retry_pass:
                    workers = max(1, workers // 2)
                    time.sleep(max(settings["batch_pause"], min(30, 5 * retry_pass)))
                while offset < len(pending) and not stopped:
                    batch = pending[offset:offset + workers]
                    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
                        returned = list(pool.map(fetch, batch))
                    failures = 0
                    for target, result in zip(batch, returned):
                        key = (target["line"], target["direction"])
                        attempt = state.get(key, {}).get("attempt", 0) + 1
                        obj = result.pop("response", None)
                        if settings["save_raw"] and obj is not None:
                            digest = hashlib.sha256((target["line"] + "|" + target["direction"]).encode()).hexdigest()[:16]
                            relative = f"raw/r{round_number}_{digest}_a{attempt}.json"
                            (folder / "raw").mkdir(exist_ok=True)
                            write_json(folder / relative, obj)
                            result["raw_file"] = relative
                        store.record(job_id, round_number, city, target, attempt, result)
                        state[key] = dict(attempt=attempt, outcome=result["outcome"], retryable=result.get("retryable", False))
                        if result["outcome"] == "failed":
                            failures += 1
                            if result.get("blocked"):
                                stopped = "Access rejected or rate limited; automatic retries stopped"
                            elif result.get("retryable") and attempt <= settings["retries"]:
                                requeued.append(target)
                    offset += len(batch)
                    recent.append((len(batch), failures))
                    recent = recent[-4:]
                    summary = checkpoint()
                    print(json.dumps(dict(round=round_number, retry_pass=retry_pass, workers=workers,
                                          outcomes=summary["outcomes"], requeued=len(requeued)), ensure_ascii=False), flush=True)
                    if len(recent) == 4 and sum(f for _, f in recent) / sum(n for n, _ in recent) >= 0.25:
                        workers = max(1, workers // 2)
                        recent.clear()
                    if not stopped:
                        time.sleep(settings["batch_pause"])
                pending = requeued
                retry_pass += 1
            if stopped:
                break
            meta["completed_rounds"] += 1
            checkpoint()
            store.export_daily(root)
            if all_rounds and meta["completed_rounds"] >= all_rounds:
                break
            time.sleep(settings["pause"])
            meta["active_round"] += 1
            checkpoint()
    except KeyboardInterrupt:
        stopped = "Interrupted; resume the same job to continue"
    finally:
        meta["stopped"] = stopped
        final_summary = checkpoint()
        store.export_daily(root)
        store.close()
    return 2 if stopped else (3 if final_summary["failed_direction_rounds"] else 0)
