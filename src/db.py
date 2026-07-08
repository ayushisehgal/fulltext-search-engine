"""
db.py
-----
Owns the SQLite schema and connection setup.

Design choice: FTS5 "external content" table.
  - `documents` holds the real row data (title, body, flagged, timestamps).
  - `documents_fts` is a virtual FTS5 index that stores ONLY the inverted
    index, not a second copy of the text (content='documents' tells FTS5
    to pull text from the real table when needed). This roughly halves
    storage vs a naive FTS5 table and keeps RAM/disk pressure low, which
    matters for the "runs on 4GB RAM / slow SSD" requirement.
  - Triggers keep the FTS index in sync automatically on INSERT/UPDATE/DELETE,
    so callers never have to remember to update the index by hand.

Why WAL (Write-Ahead Logging) journal mode:
  - Default SQLite journal mode blocks readers while a writer is
    committing. WAL lets one writer commit while other connections keep
    reading the last-committed snapshot. That's the mechanism behind the
    "ingestion should not hold up search" requirement.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    flagged     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    body,
    content='documents',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

-- keep FTS index in sync with the real table
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO documents_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE INDEX IF NOT EXISTS idx_documents_flagged ON documents(flagged);
"""


def get_connection(db_path: str = "search.db") -> sqlite3.Connection:
    """
    One connection per thread is the safe pattern for SQLite. We tune a
    handful of PRAGMAs for low-resource machines:
      - WAL: concurrent read during write (see module docstring)
      - synchronous=NORMAL: safe with WAL, much less fsync overhead than FULL
      - cache_size: cap page cache (negative = KiB) so RAM stays bounded
      - mmap_size: memory-map the DB file for faster reads without
        loading everything into the process heap
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-8000;")     # ~8MB page cache cap
    conn.execute("PRAGMA mmap_size=134217728;")  # 128MB mmap
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def init_db(db_path: str = "search.db") -> None:
    Path(db_path).touch(exist_ok=True)
    get_connection(db_path).close()
