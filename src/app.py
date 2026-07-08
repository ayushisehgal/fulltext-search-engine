"""
app.py
------
Thin Flask layer over SearchEngine. Kept deliberately small: one search
endpoint, one stats endpoint, static file serving for the UI. All the
actual search logic stays in src/search.py so the API layer is easy to
swap (e.g. for FastAPI) without touching the engine.

Run:
    python3 -m src.app
Then open http://localhost:5000
"""

from flask import Flask, jsonify, request, send_from_directory
import os

from .db import get_connection
from .search import SearchEngine

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

_conn = get_connection("search.db")
_engine = SearchEngine(_conn)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "")
    limit = int(request.args.get("limit", 10))
    flag_boost = float(request.args.get("flag_boost", _engine.flag_boost))

    engine = SearchEngine(_conn, flag_boost=flag_boost)
    resp = engine.search(query, limit=limit)

    return jsonify({
        "query": resp.query,
        "elapsed_ms": resp.elapsed_ms,
        "corrected_terms": resp.corrected_terms,
        "flag_boost": flag_boost,
        "results": [
            {
                "id": r.id,
                "title": r.title,
                "body": r.body,
                "flagged": r.flagged,
                "score": r.score,
                "base_score": r.base_score,
                "boost_score": r.boost_score,
            }
            for r in resp.results
        ],
    })


@app.route("/api/stats")
def api_stats():
    row = _conn.execute("SELECT COUNT(*) AS n, SUM(flagged) AS f FROM documents").fetchone()
    return jsonify({"total_documents": row["n"] or 0, "flagged_documents": row["f"] or 0})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
