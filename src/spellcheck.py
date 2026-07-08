"""
spellcheck.py
-------------
FTS5 has no built-in fuzzy matching. Two common real options are:
  1. The `spellfix1` SQLite extension (needs a C extension compiled/loaded)
  2. A vocabulary table + edit distance at query time (pure Python)

I picked (2) on purpose: no compiled extension to install (keeps setup
trivial for a low-resource / beginner environment), fully deterministic,
and fast enough because we only run it on the handful of tokens in a
user's query, never on the whole corpus.

How it works:
  - FTS5 ships a built-in `fts5vocab` virtual table type that exposes the
    indexed vocabulary (term -> document frequency) for free, with no
    extra bookkeeping on our side.
  - At query time, for any query token that doesn't exist in the
    vocabulary, we find the closest known term by edit (Levenshtein)
    distance and substitute it.
  - Distance is capped (default 2) so we don't "correct" a word into
    something unrelated.
"""

from functools import lru_cache


def create_vocab_table(conn) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS documents_vocab "
        "USING fts5vocab('documents_fts', 'row')"
    )
    conn.commit()


def levenshtein(a: str, b: str) -> int:
    """Classic O(len(a)*len(b)) edit distance, iterative DP, no deps."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,      # insertion
                prev[j] + 1,          # deletion
                prev[j - 1] + cost,   # substitution
            )
        prev = curr
    return prev[-1]


class SpellCorrector:
    def __init__(self, conn, max_distance: int = 2, min_doc_freq: int = 1):
        self.conn = conn
        self.max_distance = max_distance
        create_vocab_table(conn)

    @lru_cache(maxsize=1)
    def _vocab(self) -> tuple:
        """
        Cached snapshot of the current vocabulary. Cache is small (a tuple
        of strings) and cheap to rebuild; call invalidate() after large
        ingestion batches if you want corrections to see brand-new terms
        immediately. For a search-heavy / write-light workload this cache
        avoids re-scanning the vocab table on every single query.
        """
        rows = self.conn.execute(
            "SELECT term FROM documents_vocab WHERE doc IS NOT NULL"
        ).fetchall()
        # fts5vocab 'row' mode already returns distinct terms
        seen = set()
        terms = []
        for r in rows:
            t = r["term"]
            if t not in seen:
                seen.add(t)
                terms.append(t)
        return tuple(terms)

    def invalidate(self) -> None:
        self._vocab.cache_clear()

    def known(self, term: str) -> bool:
        return term in self._vocab()

    def correct(self, term: str) -> str:
        """Return the closest known vocabulary term, or the original term
        unchanged if it's already known or nothing close enough exists."""
        if self.known(term) or len(term) < 3:
            return term

        best_term, best_dist = term, self.max_distance + 1
        for candidate in self._vocab():
            # cheap length filter before paying for full edit distance
            if abs(len(candidate) - len(term)) > self.max_distance:
                continue
            d = levenshtein(term, candidate)
            if d < best_dist:
                best_term, best_dist = candidate, d
                if d == 1:
                    break  # good enough, stop early
        return best_term if best_dist <= self.max_distance else term
