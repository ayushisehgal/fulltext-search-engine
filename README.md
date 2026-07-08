# Full-Text Search Engine

A Python full-text search engine built on **SQLite FTS5**, supporting
multi-word queries, partial words, spelling-mistake tolerance,
order-independent matching, and a flag-based score boost.

## Why SQLite FTS5

FTS5 is compiled into Python's standard `sqlite3` module on most
platforms (verified in this environment) - no server, no extra services,
no extra pip installs for the core engine. It gives a battle-tested
inverted index and BM25 relevance ranking out of the box, which matters
under a tight time budget and is a legitimate production choice for
single-node / embedded search at small-to-medium scale.

## Requirements

- Python 3.9+ (the core engine uses only the standard library)
- `pytest` for running the test suite
- `flask` only if you want the web UI (`src/app.py`) — the CLI and core
  engine never require it

No database server to install or run.

## Web UI

A small Flask API (`src/app.py`) + a single static page (`static/index.html`)
sit on top of the same `SearchEngine` used by the CLI — no logic is
duplicated. The UI shows, live as you type: query tokenization, any
spelling corrections applied, per-result relevance scores split into a
**base BM25 segment and a flag-boost segment** (so the flag-boost
requirement is visible, not just a number in a log line), and query
latency.

```bash
pip install flask
python3 -m src.cli ingest sample_data/docs.json   # if not already done
python3 -m src.app
# open http://localhost:5000
```

## Setup & Run

```bash
# 1. clone / unzip the repo, then cd into it
cd fulltext_search_engine

# 2. (optional) create a venv
python3 -m venv venv && source venv/bin/activate

# 3. install test dependency
pip install pytest

# 4. ingest the sample dataset
python3 -m src.cli ingest sample_data/docs.json

# 5. search
python3 -m src.cli search "pyhton dictionry"
python3 -m src.cli search "search engin"
python3 -m src.cli search "data" --limit 5
python3 -m src.cli search "python" --flag-boost 3.0
```

Each search prints: the matched results with scores, whether a result is
flagged, any spelling corrections that were applied, and the query time
in milliseconds.

## Running tests

```bash
python3 -m pytest tests/ -v
```

The suite (8 tests) directly covers the assignment's required nuances:
multi-word AND semantics, word-order independence, partial-word prefix
matching, spelling-mistake correction, flagged-document score boosting,
deterministic result ordering, presence of score+timing in every
response, and that ingestion does not block a concurrent search.

## Running the performance benchmark

```bash
python3 scripts/benchmark.py --docs 20000 --queries 300
```

Reports ingestion throughput, p50/p95/p99 search latency, and peak RSS
memory (stdlib `resource` module only - no extra install needed). On this
machine, 20,000 documents ingest in well under a second and searches stay
in the low tens of milliseconds at ~40MB peak memory - comfortable
headroom on a 4GB RAM machine.

## Design decisions (see video for full walkthrough)

| Requirement | Approach |
|---|---|
| Multi-word queries | FTS5 `MATCH` with tokens ANDed together |
| Words in any order | FTS5 AND semantics are order-independent (no phrase query used) |
| Partial words | Each token queried as a prefix (`token*`) |
| Spelling mistakes | Vocabulary extracted via FTS5's built-in `fts5vocab` table; unknown query tokens are corrected to the closest vocabulary term by Levenshtein edit distance (capped at distance 2), then queried as a prefix alongside the original |
| Score per result | FTS5 `bm25()` ranking function, negated so higher = more relevant |
| Flag-based score boost | Final score multiplied by a configurable boost factor when `flagged = 1` |
| Deterministic results | `ORDER BY score DESC, id ASC` - explicit tie-break |
| Logged search time | `time.perf_counter()` wrapped around each query, returned in every response |
| Fast ingestion, non-blocking | SQLite WAL journal mode (readers don't block on writer commits) + a background queue/thread that batches inserts |
| Low CPU/RAM usage | Bounded page cache (`PRAGMA cache_size`), `mmap_size` for zero-copy reads, batched transactions to minimize fsync calls |

### Trade-offs considered and rejected

- **`spellfix1` SQLite extension** for fuzzy matching instead of the
  hand-rolled vocabulary + edit-distance approach: rejected because it
  requires loading a separate compiled extension, adding a setup step
  that isn't guaranteed to work identically across the reviewer's
  machine/OS - the pure-Python approach is slower per-lookup but has zero
  extra setup risk, and it's only run on the few tokens in a query, not
  the whole corpus.
- **A BK-tree or trie for the vocabulary** instead of a linear scan with a
  length pre-filter: would give better asymptotic lookup time on a very
  large vocabulary (100k+ unique terms), but adds real implementation
  complexity for a benefit that doesn't show up until the corpus is much
  bigger than what this assignment's time budget could realistically be
  tested against. Documented here as the next optimization if the
  vocabulary grows large.
- **MongoDB / a client-server DB** as instructed as an option: rejected
  in favor of embedded SQLite specifically because of the "runs on low
  spec infra" bonus requirement - no separate DB process means no extra
  RAM/CPU overhead from a server, and no network hop for every query.

## AI usage disclosure

Claude (Anthropic) was used as a supportive tool during this assignment:
generating boilerplate (schema/trigger SQL, CLI argument parsing,
Levenshtein DP implementation), explaining SQLite FTS5 mechanics (BM25,
external-content tables, WAL mode) I was not previously familiar with,
and debugging test failures. All architectural decisions, trade-off
reasoning, and final code review were done by me, and I can walk through
and modify any part of this codebase live.
