"""
search.py
---------
The public API of the engine: SearchEngine.search(query).

Query handling pipeline:
  1. Tokenize the raw query (simple whitespace/punctuation split).
  2. Spell-correct each token against the known vocabulary (spellcheck.py).
  3. Build an FTS5 MATCH expression where each token becomes a prefix
     match (`token*`) OR'd with its corrected form, all tokens ANDed
     together. Prefix matching gives "partial words" for free; OR-ing the
     corrected form gives "spelling mistakes" without discarding the
     original in case the correction was wrong.
  4. Rank with FTS5's built-in bm25() function (a well-tested TF-IDF-style
     relevance score - no need to reinvent it).
  5. Multiply the score by a boost factor for flagged documents.
  6. Sort by (final_score DESC, id ASC). The id tie-break is what makes
     results deterministic even when two documents score identically.
  7. Time the whole thing and return it alongside the results.

Why AND (not OR) of tokens by default: "real world" search engines treat
multi-word queries as "find docs containing all these words" unless told
otherwise - this is how Google/Elasticsearch's default `match` behave too.
Order doesn't matter because FTS5 MATCH with AND is order-independent -
only phrase queries ("...") are order sensitive, and we're not using those.
"""

import re
import time
from dataclasses import dataclass, field

from .spellcheck import SpellCorrector

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# How much extra weight a flagged document's score gets. Exposed as a
# constant (not hardcoded inline) so it's trivial to tune or expose via
# an API parameter later without touching query logic.
DEFAULT_FLAG_BOOST = 2.0


@dataclass
class SearchResult:
    id: int
    title: str
    body: str
    flagged: bool
    score: float
    base_score: float = 0.0   # raw bm25 relevance, before any flag boost
    boost_score: float = 0.0  # score contributed purely by the flag boost


@dataclass
class SearchResponse:
    query: str
    results: list = field(default_factory=list)
    elapsed_ms: float = 0.0
    corrected_terms: dict = field(default_factory=dict)


class SearchEngine:
    def __init__(self, conn, flag_boost: float = DEFAULT_FLAG_BOOST):
        self.conn = conn
        self.flag_boost = flag_boost
        self.corrector = SpellCorrector(conn)

    def _tokenize(self, query: str) -> list:
        return [t.lower() for t in TOKEN_RE.findall(query)]

    def _build_match_expr(self, tokens: list) -> tuple:
        """
        Returns (match_expression, corrections_dict).
        Each token -> `token*` (prefix match, handles partial words)
        OR'd with `corrected*` if spell-correction found a different term.
        Tokens are ANDed (FTS5's default when terms are just space
        separated would actually be implicit AND already, but we're
        explicit here for clarity and because we're combining with OR
        groups per-token).
        """
        corrections = {}
        clauses = []
        for tok in tokens:
            corrected = self.corrector.correct(tok)
            variants = {tok}
            if corrected != tok:
                corrections[tok] = corrected
                variants.add(corrected)
            clause = " OR ".join(f'{v}*' for v in variants)
            clauses.append(f"({clause})")
        return " AND ".join(clauses), corrections

    def search(self, query: str, limit: int = 20) -> SearchResponse:
        start = time.perf_counter()
        tokens = self._tokenize(query)

        if not tokens:
            return SearchResponse(query=query, results=[], elapsed_ms=0.0)

        match_expr, corrections = self._build_match_expr(tokens)

        # bm25() returns a NEGATIVE number where values closer to zero are
        # more relevant (this is an FTS5-ism). We negate it so "higher is
        # better" like a normal score, then apply the flag boost.
        sql = """
            SELECT
                d.id, d.title, d.body, d.flagged,
                (-bm25(documents_fts)) AS base_score,
                (-bm25(documents_fts)) *
                    CASE WHEN d.flagged = 1 THEN ? ELSE 1.0 END AS score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY score DESC, d.id ASC
            LIMIT ?
        """
        rows = self.conn.execute(sql, (self.flag_boost, match_expr, limit)).fetchall()

        results = [
            SearchResult(
                id=r["id"],
                title=r["title"],
                body=r["body"],
                flagged=bool(r["flagged"]),
                score=round(r["score"], 4),
                base_score=round(r["base_score"], 4),
                boost_score=round(r["score"] - r["base_score"], 4),
            )
            for r in rows
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return SearchResponse(
            query=query,
            results=results,
            elapsed_ms=round(elapsed_ms, 3),
            corrected_terms=corrections,
        )
