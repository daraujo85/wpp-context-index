"""`wpp-context` CLI: thin argparse wrapper. Parses args and delegates to
services/* (T15) — ingest calls services.ingestion.run(), search calls
services.search.search(); the CLI never duplicates their logic, same rule
as the API layer (app/api/search.py, app/api/ingest.py).

inspect/source stay stubs: no services.inspect/source exist yet (M3 scope,
out of this round).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from app.services import ingestion
from app.services import search as search_service


def _iso_date(value: str) -> str:
    date.fromisoformat(value)  # raises ValueError on bad format
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wpp-context")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest messages for a date range")
    ingest.add_argument("--start", type=_iso_date, required=True)
    ingest.add_argument("--end", type=_iso_date, required=True)

    search = sub.add_parser("search", help="search indexed messages")
    search.add_argument("query")

    inspect = sub.add_parser("inspect", help="inspect a message by id")
    inspect.add_argument("id")

    source = sub.add_parser("source", help="show source/provenance for an id")
    source.add_argument("id")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "ingest":
        summary = ingestion.run(f"{args.start}T00:00:00", f"{args.end}T00:00:00")
        print(
            f"ingest: range {args.start}..{args.end} run_id={summary.run_id} "
            f"messages_read={summary.messages_read} "
            f"contexts_indexed={summary.contexts_indexed} errors={summary.errors}"
        )
    elif args.command == "search":
        results = search_service.search(args.query)
        print(f"search: query={args.query!r} results={len(results)}")
        for r in results:
            print(f"  - {r.context_id} score={r.score:.3f} title={r.title!r}")
    elif args.command == "inspect":
        print(f"inspect: id={args.id!r} (services.inspect not implemented yet)")
    elif args.command == "source":
        print(f"source: id={args.id!r} (services.source not implemented yet)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
