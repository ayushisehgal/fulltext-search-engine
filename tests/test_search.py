"""
Tests map directly to the assignment's stated requirements:
  - multiple words              -> test_multi_word_and_semantics
  - words in any order          -> test_word_order_independence
  - partial words                -> test_partial_word_prefix_match
  - spelling mistakes            -> test_spelling_mistake_correction
  - flag-based score boost       -> test_flagged_document_boosted
  - deterministic results        -> test_deterministic_results
  - search returns score+timing  -> test_response_has_score_and_timing
  - ingestion doesn't block search -> test_ingest_does_not_block_search
"""

import os
import sys
import time
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection
from src.ingest import bulk_load_sync, BackgroundIngestor
from src.search import SearchEngine

SAMPLE_DOCS = [
    {"title": "Python Basics", "body": "Python is a popular programming language.", "flagged": False},
    {"title": "Python Dictionaries", "body": "A dictionary stores key value pairs efficiently.", "flagged": True},
    {"title": "JavaScript Intro", "body": "JavaScript runs in the browser.", "flagged": False},
    {"title": "Database Systems", "body": "Databases store and query structured data.", "flagged": False},
    {"title": "Search Engines", "body": "A search engine indexes documents and ranks results.", "flagged": True},
]


@pytest.fixture()
def engine(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    bulk_load_sync(conn, SAMPLE_DOCS)
    return SearchEngine(conn)


def test_multi_word_and_semantics(engine):
    resp = engine.search("python dictionary")
    titles = [r.title for r in resp.results]
    assert "Python Dictionaries" in titles
    # "javascript" alone should NOT satisfy an AND query for two unrelated terms
    resp2 = engine.search("python javascript")
    assert len(resp2.results) == 0


def test_word_order_independence(engine):
    r1 = [r.id for r in engine.search("dictionary python").results]
    r2 = [r.id for r in engine.search("python dictionary").results]
    assert r1 == r2


def test_partial_word_prefix_match(engine):
    resp = engine.search("data")  # should match "database(s)" via prefix
    titles = [r.title for r in resp.results]
    assert "Database Systems" in titles


def test_spelling_mistake_correction(engine):
    resp = engine.search("pyhton")  # transposed letters
    assert "pyhton" in resp.corrected_terms
    titles = [r.title for r in resp.results]
    assert any("Python" in t for t in titles)


def test_flagged_document_boosted(engine):
    # "search" matches both "Search Engines" (flagged) and appears in body
    # text elsewhere; flagged doc should rank at/near the top when relevance
    # is otherwise comparable.
    resp = engine.search("search")
    assert len(resp.results) >= 1
    assert resp.results[0].flagged is True


def test_deterministic_results(engine):
    r1 = engine.search("python").results
    r2 = engine.search("python").results
    assert [r.id for r in r1] == [r.id for r in r2]
    assert [r.score for r in r1] == [r.score for r in r2]


def test_response_has_score_and_timing(engine):
    resp = engine.search("python")
    assert resp.elapsed_ms >= 0
    for r in resp.results:
        assert isinstance(r.score, float)


def test_ingest_does_not_block_search(tmp_path):
    db_path = str(tmp_path / "ingest_test.db")
    conn = get_connection(db_path)
    bulk_load_sync(conn, SAMPLE_DOCS)
    engine = SearchEngine(get_connection(db_path))

    ingestor = BackgroundIngestor(db_path)
    ingestor.add_many([
        {"title": f"Doc {i}", "body": "filler content about various topics", "flagged": False}
        for i in range(500)
    ])

    # search immediately while ingestion is (likely) still in flight;
    # this must not raise, hang, or time out.
    start = time.perf_counter()
    resp = engine.search("python")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert len(resp.results) >= 1

    ingestor.stop(wait=True)
