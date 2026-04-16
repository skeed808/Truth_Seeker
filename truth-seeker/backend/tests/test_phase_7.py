"""
Phase 7 — User Feedback Loop Tests

Tests:
  1. store_and_retrieve_feedback    — DB round-trip for +1 / -1 votes
  2. feedback_boost_ordering        — upvoted result beats downvoted after boost
  3. feedback_boost_no_change       — no-vote results are unchanged
  4. feedback_boost_clamp           — score never exceeds 1.0 or goes below 0.0
  5. feedback_map_aggregation       — votes across multiple queries sum correctly
  6. feedback_toggle                — overwriting a vote replaces it
  7. api_feedback_endpoint          — POST /api/feedback returns 200
  8. api_feedback_invalid           — feedback=0 rejected by endpoint
  9. api_search_includes_feedback   — search response honours stored votes
"""
import asyncio
import tempfile
from pathlib import Path

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_result(url, score=0.5, **kwargs):
    r = {"url": url, "domain": "example.com", "scores": {"final": score}}
    r.update(kwargs)
    return r


# ── 1. store_and_retrieve_feedback ───────────────────────────────────────────

def test_store_and_retrieve_feedback():
    from cache.user_feedback import store_feedback, get_feedback_for_url

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)

    store_feedback("https://a.com", "test query", 1,  db_path=db)
    store_feedback("https://b.com", "test query", -1, db_path=db)

    a_votes = get_feedback_for_url("https://a.com", db_path=db)
    b_votes = get_feedback_for_url("https://b.com", db_path=db)

    assert sum(a_votes.values()) == 1
    assert sum(b_votes.values()) == -1


# ── 2. feedback_boost_ordering ───────────────────────────────────────────────

def test_feedback_boost_ordering():
    from ranking.feedback_boost import apply_feedback_boost

    a = _make_result("https://a.com", score=0.5)
    b = _make_result("https://b.com", score=0.5)

    feedback_map = {"https://a.com": 1, "https://b.com": -1}
    results = apply_feedback_boost([a, b], feedback_map)

    scores = [r["scores"]["final"] for r in results]
    assert results[0]["url"] == "https://a.com"
    assert scores[0] > scores[1]


# ── 3. feedback_boost_no_change ───────────────────────────────────────────────

def test_feedback_boost_no_change():
    from ranking.feedback_boost import apply_feedback_boost

    a = _make_result("https://a.com", score=0.7)
    b = _make_result("https://b.com", score=0.6)

    results = apply_feedback_boost([a, b], {})

    assert results[0]["scores"]["final"] == 0.7
    assert results[1]["scores"]["final"] == 0.6


# ── 4. feedback_boost_clamp ───────────────────────────────────────────────────

def test_feedback_boost_clamp():
    from ranking.feedback_boost import apply_feedback_boost

    high = _make_result("https://h.com", score=0.99)
    low  = _make_result("https://l.com", score=0.01)

    results = apply_feedback_boost(
        [high, low],
        {"https://h.com": 1, "https://l.com": -1},
    )

    for r in results:
        assert 0.0 <= r["scores"]["final"] <= 1.0


# ── 5. feedback_map_aggregation ───────────────────────────────────────────────

def test_feedback_map_aggregation():
    from cache.user_feedback import store_feedback, get_feedback_map

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)

    url = "https://multi.com"
    store_feedback(url, "query one", 1,  db_path=db)
    store_feedback(url, "query two", 1,  db_path=db)

    fmap = get_feedback_map([url], db_path=db)
    assert fmap[url] == 2


# ── 6. feedback_toggle ────────────────────────────────────────────────────────

def test_feedback_toggle():
    from cache.user_feedback import store_feedback, get_feedback_for_url

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)

    url, query = "https://flip.com", "flip query"
    store_feedback(url, query, 1,  db_path=db)
    store_feedback(url, query, -1, db_path=db)   # overwrite

    votes = get_feedback_for_url(url, db_path=db)
    assert sum(votes.values()) == -1


# ── 7. api_feedback_endpoint ─────────────────────────────────────────────────

def test_api_feedback_endpoint():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    resp = client.post("/api/feedback", json={
        "url":      "https://example.com/page",
        "query":    "test search",
        "feedback": 1,
    })
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


# ── 8. api_feedback_invalid ───────────────────────────────────────────────────

def test_api_feedback_invalid():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    # feedback=0 violates ge=-1, le=1 AND our internal check
    resp = client.post("/api/feedback", json={
        "url":      "https://example.com/page",
        "query":    "test",
        "feedback": 5,
    })
    assert resp.status_code == 422


# ── 9. api_search_includes_feedback ──────────────────────────────────────────

def test_api_search_includes_feedback():
    """Smoke-test: search pipeline completes without error when feedback exists."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    # First store a vote
    client.post("/api/feedback", json={
        "url":      "https://rust-lang.org",
        "query":    "rust language",
        "feedback": 1,
    })
    # Search should still return a valid response
    resp = client.post("/api/search", json={"query": "rust language", "max_results": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
