"""
cli.py
------
Minimal command-line interface. No web framework required -- keeps the
dependency footprint at zero (stdlib only), which matters for the
"runs on constrained infra, beginner-friendly setup" requirement.

Usage:
    python -m src.cli ingest sample_data/docs.json
    python -m src.cli search "pyhton dictionary" --limit 5
    python -m src.cli search "quick brown fox" --flag-boost 3.0
"""

import argparse
import json
import sys

from .db import get_connection
from .ingest import bulk_load_sync
from .search import SearchEngine


def cmd_ingest(args):
    conn = get_connection(args.db)
    with open(args.file) as f:
        docs = json.load(f)
    n = bulk_load_sync(conn, docs)
    print(f"Ingested {n} documents into {args.db}")


def cmd_search(args):
    conn = get_connection(args.db)
    engine = SearchEngine(conn, flag_boost=args.flag_boost)
    resp = engine.search(args.query, limit=args.limit)

    print(f'Query: "{resp.query}"  |  {len(resp.results)} results  |  {resp.elapsed_ms} ms')
    if resp.corrected_terms:
        print(f"Spelling corrections applied: {resp.corrected_terms}")
    print("-" * 60)
    for r in resp.results:
        flag = " [FLAGGED]" if r.flagged else ""
        snippet = r.body[:100].replace("\n", " ")
        print(f"[{r.id}] score={r.score:<10}{flag} {r.title}")
        print(f"      {snippet}...")


def main():
    parser = argparse.ArgumentParser(description="Full-text search engine CLI")
    parser.add_argument("--db", default="search.db", help="SQLite DB file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Load documents from a JSON file")
    p_ingest.add_argument("file", help="Path to JSON file: list of {title, body, flagged}")
    p_ingest.set_defaults(func=cmd_ingest)

    p_search = sub.add_parser("search", help="Search the index")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--flag-boost", type=float, default=2.0)
    p_search.set_defaults(func=cmd_search)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
