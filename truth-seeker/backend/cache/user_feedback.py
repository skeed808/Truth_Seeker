"""
User Feedback — Phase 7

Persists thumbs-up / thumbs-down votes per (url, query) pair and exposes
an aggregate feedback map consumed by the ranking engine.

Uses the same DB file as the page cache — schema is additive (IF NOT EXISTS).
All functions are synchronous; call sites wrap in asyncio.to_thread() if needed.

Public API
----------
init_feedback_table(conn)                   — create table (idempotent)
store_feedback(url, query, feedback)        — upsert a vote (+1 / -1)
get_feedback_for_url(url)                   — {url_hash: aggregate_score}
get_feedback_map(urls)                      — {url: aggregate_score} for a list
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Dict, List

from cache.page_cache import DB_PATH, _connect

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS result_feedback (
    url_hash    TEXT,
    query_hash  TEXT,
    feedback    INTEGER,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (url_hash, query_hash)
);
"""

_CREATE_FEEDBACK_INDEX = """
CREATE INDEX IF NOT EXISTS idx_feedback_url ON result_feedback(url_hash);
"""


# ── Schema init ───────────────────────────────────────────────────────────────

def init_feedback_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE_FEEDBACK_TABLE + _CREATE_FEEDBACK_INDEX)
    conn.commit()


# ── Internal ──────────────────────────────────────────────────────────────────

def _hash(value: str) -> str:
    return hashlib.sha256(value.lower().strip().encode()).hexdigest()[:32]


# ── Public functions ──────────────────────────────────────────────────────────

def store_feedback(
    url:      str,
    query:    str,
    feedback: int,
    db_path:  Path = DB_PATH,
) -> None:
    """
    Upsert a vote for (url, query). feedback must be +1 or -1.
    Calling again with the same (url, query) replaces the previous vote.
    """
    if feedback not in (1, -1):
        return
    url_hash   = _hash(url)
    query_hash = _hash(query)
    with _connect(db_path) as conn:
        init_feedback_table(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO result_feedback (url_hash, query_hash, feedback)
            VALUES (?, ?, ?)
            """,
            (url_hash, query_hash, feedback),
        )
        conn.commit()


def get_feedback_for_url(url: str, db_path: Path = DB_PATH) -> Dict[str, int]:
    """
    Return {query_hash: feedback} for all votes cast on this URL.
    """
    url_hash = _hash(url)
    try:
        with _connect(db_path) as conn:
            init_feedback_table(conn)
            rows = conn.execute(
                "SELECT query_hash, feedback FROM result_feedback WHERE url_hash = ?",
                (url_hash,),
            ).fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def get_feedback_map(urls: List[str], db_path: Path = DB_PATH) -> Dict[str, int]:
    """
    Return {url: aggregate_feedback_score} for a list of URLs.

    Aggregate = sum of all votes for that URL across every query.
    Positive = net upvoted, negative = net downvoted, 0 = no votes.
    """
    if not urls:
        return {}

    url_hash_to_url = {_hash(u): u for u in urls}
    placeholders    = ",".join("?" * len(url_hash_to_url))

    try:
        with _connect(db_path) as conn:
            init_feedback_table(conn)
            rows = conn.execute(
                f"""
                SELECT url_hash, SUM(feedback)
                FROM result_feedback
                WHERE url_hash IN ({placeholders})
                GROUP BY url_hash
                """,
                list(url_hash_to_url.keys()),
            ).fetchall()
        return {
            url_hash_to_url[row[0]]: int(row[1])
            for row in rows
            if row[0] in url_hash_to_url
        }
    except Exception:
        return {}
