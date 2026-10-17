#!/usr/bin/env python3
"""CLI: read-only API queries, collection and export. Python 3.10+."""
import argparse
import json
from pathlib import Path
import sys

from mygolbs.api import Client, READ_COMMANDS
from mygolbs.collector import run
from mygolbs.storage import Store, write_json

DEFAULTS = dict(city_name="贵阳市", city_code="520100", company_prefixes=["贵阳公交", "贵阳黔爽"],
                output="data", workers=1, batch_pause=3, timeout=25, retries=3,
                rounds=1, pause=300, save_raw=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Optional JSON config; specify before the subcommand")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("query", help="One read-only API request")
    query.add_argument("--cmd", required=True, choices=sorted(READ_COMMANDS))
    query.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    query.add_argument("--out", type=Path)
    collection = sub.add_parser("collect", help="Collect a daily observed roster")
    collection.add_argument("--resume", type=Path, help="Existing data/jobs/JOB folder; uses its frozen settings")
    selection = collection.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="All catalog companies")
    selection.add_argument("--line", action="append", help="Exact line name; repeat for multiple lines")
    selection.add_argument("--company-prefix", action="append", help="Company prefix; repeat for multiple prefixes")
    collection.add_argument("--min-number", type=int)
    collection.add_argument("--max-number", type=int)
    for option in ("workers", "retries", "rounds"):
        collection.add_argument("--" + option, type=int)
    for option in ("batch-pause", "timeout", "pause"):
        collection.add_argument("--" + option, type=float)
    collection.add_argument("--output", type=Path)
    collection.add_argument("--save-raw", action="store_true", default=None)
    export = sub.add_parser("export", help="Export all accumulated dates from SQLite")
    export.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    settings = DEFAULTS.copy()
    if args.config:
        settings.update(json.loads(args.config.read_text(encoding="utf-8")))
    if args.command == "query":
        params = {}
        for value in args.param:
            if "=" not in value:
                parser.error("--param requires KEY=VALUE")
            key, val = value.split("=", 1)
            if key != key.upper() or key == "CMD":
                parser.error("Use uppercase API parameter names; CMD is supplied by --cmd")
            params[key] = val
        obj = Client(settings["city_name"], settings["city_code"], settings["timeout"]).call(args.cmd, **params)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, obj)
        else:
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 0
    if args.command == "export":
        root = Path(args.output or settings["output"]).resolve()
        if not (root / "attendance.sqlite3").exists():
            parser.error("No attendance.sqlite3 found in output folder")
        store = Store(root / "attendance.sqlite3")
        try:
            store.export_daily(root)
        finally:
            store.close()
        return 0
    if args.resume:
        return run(settings, args.resume)
    for key in ("workers", "retries", "rounds", "batch_pause", "timeout", "pause", "output", "save_raw"):
        value = getattr(args, key)
        if value is not None:
            settings[key] = str(value) if key == "output" else value
    if not 1 <= settings["workers"] <= 12:
        parser.error("workers must be between 1 and 12")
    if (settings["retries"] < 0 or settings["rounds"] < 0 or settings["pause"] < 0
            or settings["batch_pause"] < 3 or settings["timeout"] <= 0):
        parser.error("retries/rounds/pause must be nonnegative; batch-pause >=3; timeout >0")
    if args.min_number is not None and args.max_number is not None and args.min_number > args.max_number:
        parser.error("min-number must not exceed max-number")
    settings.update(lines=args.line or [], min_number=args.min_number, max_number=args.max_number)
    if args.all or args.line:
        settings["company_prefixes"] = []
    elif args.company_prefix:
        settings["company_prefixes"] = args.company_prefix
    return run(settings)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
