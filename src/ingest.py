"""
ingest.py
---------
"Any changes in data should not hold the search process."

Two things make that true here:
  1. WAL journal mode (set in db.py) - a writer committing does not block
     concurrent readers, they see the last committed snapshot.
  2. A background writer thread with a queue - callers calling
     `add_document()` just enqueue and return immediately; a single
     dedicated thread drains the queue and batches inserts into
     transactions. This keeps write-lock hold time short and predictable
     even under bursty ingestion, and means the calling thread (e.g. an
     API request handler) is never blocked on disk I/O.

Batching also matters for low-resource machines: committing one
transaction per 200 documents does far fewer fsyncs than one per document,
which is the dominant cost on a slow/inefficient SSD.

NOTE: Both write paths accept an optional `corrector`. After every commit
that changes the documents table, we invalidate its cached vocabulary
(see spellcheck.py) so newly-ingested words are correctable immediately,
instead of only after the process restarts.
"""

import queue
import threading
import time

from .db import get_connection


class BackgroundIngestor:
    def __init__(self, db_path: str, batch_size: int = 200, flush_interval: float = 0.5, corrector=None):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.corrector = corrector
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def add_document(self, title: str, body: str, flagged: bool = False) -> None:
        """Non-blocking: just enqueues. Returns immediately."""
        self._queue.put((title, body, int(flagged)))

    def add_many(self, docs) -> None:
        for d in docs:
            self.add_document(d["title"], d["body"], d.get("flagged", False))

    def _run(self) -> None:
        conn = get_connection(self.db_path)
        buffer = []
        last_flush = time.monotonic()
        while not self._stop.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
                buffer.append(item)
            except queue.Empty:
                pass

            due = (time.monotonic() - last_flush) >= self.flush_interval
            if buffer and (len(buffer) >= self.batch_size or due):
                self._flush(conn, buffer)
                buffer = []
                last_flush = time.monotonic()

        if buffer:
            self._flush(conn, buffer)
        conn.close()

    def _flush(self, conn, buffer) -> None:
        conn.executemany(
            "INSERT INTO documents (title, body, flagged) VALUES (?, ?, ?)",
            buffer,
        )
        conn.commit()
        if self.corrector is not None:
            self.corrector.invalidate()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait:
            self._thread.join()


def bulk_load_sync(conn, docs, corrector=None) -> int:
    """
    Synchronous bulk loader for the common "load a big initial dataset
    once at startup" case - simpler than the background queue when you
    don't need overlapping search+ingest, and still batches into one
    transaction for speed.
    """
    conn.executemany(
        "INSERT INTO documents (title, body, flagged) VALUES (?, ?, ?)",
        [(d["title"], d["body"], int(d.get("flagged", False))) for d in docs],
    )
    conn.commit()
    if corrector is not None:
        corrector.invalidate()
    return len(docs)
