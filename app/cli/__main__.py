"""`wpp-context` CLI: thin argparse wrapper. Just parses args and calls into
existing config/adapters — services/* (ingestion.py, search.py) don't exist
yet (T13-T14), so ingest/search/inspect/source print stub summaries until
then.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from app.config import Settings


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
        n_sources = len(Settings().sources())
        print(
            f"ingest: {n_sources} allowlisted source(s), range "
            f"{args.start}..{args.end} (services.ingestion not implemented yet)"
        )
    elif args.command == "search":
        print(f"search: query={args.query!r} (services.search not implemented yet)")
    elif args.command == "inspect":
        print(f"inspect: id={args.id!r} (services.inspect not implemented yet)")
    elif args.command == "source":
        print(f"source: id={args.id!r} (services.source not implemented yet)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
