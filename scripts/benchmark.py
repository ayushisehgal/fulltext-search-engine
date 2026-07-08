"""
benchmark.py
------------
Generates a synthetic corpus, ingests it, and reports:
  - ingestion throughput (docs/sec)
  - search latency (p50 / p95 / p99) across a mixed query set
  - peak RSS memory during the run (via resource module, stdlib only,
    so this works with no extra installs -- relevant on constrained infra
    where you might not even want to pip install psutil)

Usage:
    python scripts/benchmark.py --docs 20000 --queries 500
"""

import argparse
import os
import random
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection
from src.ingest import bulk_load_sync
from src.search import SearchEngine

WORDS = [
    "python", "database", "search", "engine", "index", "query", "score",
    "document", "algorithm", "network", "server", "client", "cache",
    "thread", "memory", "storage", "vector", "language", "model", "data",
]


def make_corpus(n):
    docs = []
    for i in range(n):
        title_words = random.sample(WORDS, 3)
        body_words = random.choices(WORDS, k=25)
        docs.append({
            "title": " ".join(title_words).title(),
            "body": " ".join(body_words),
            "flagged": random.random() < 0.1,
        })
    return docs


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = int(len(sorted_vals) * p) 
    k = min(k, len(sorted_vals) - 1)
    return sorted_vals[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=20000)
    ap.add_argument("--queries", type=int, default=500)
    ap.add_argument("--db", default="bench.db")
    args = ap.parse_args()

    if os.path.exists(args.db):
        os.remove(args.db)
    for ext in ("-wal", "-shm"):
        if os.path.exists(args.db + ext):
            os.remove(args.db + ext)

    conn = get_connection(args.db)

    print(f"Generating {args.docs} synthetic documents...")
    docs = make_corpus(args.docs)

    t0 = time.perf_counter()
    bulk_load_sync(conn, docs)
    ingest_elapsed = time.perf_counter() - t0
    print(f"Ingestion: {args.docs} docs in {ingest_elapsed:.2f}s "
          f"({args.docs / ingest_elapsed:.0f} docs/sec)")

    engine = SearchEngine(conn)
    latencies = []
    for _ in range(args.queries):
        q = " ".join(random.sample(WORDS, random.choice([1, 2, 3])))
        resp = engine.search(q)
        latencies.append(resp.elapsed_ms)

    latencies.sort()
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB->MB on Linux

    print(f"\nSearch latency over {args.queries} queries (ms):")
    print(f"  p50: {percentile(latencies, 0.50):.3f}")
    print(f"  p95: {percentile(latencies, 0.95):.3f}")
    print(f"  p99: {percentile(latencies, 0.99):.3f}")
    print(f"  max: {latencies[-1]:.3f}")
    print(f"\nPeak RSS memory: {peak_rss_mb:.1f} MB")


if __name__ == "__main__":
    main()
