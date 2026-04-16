"""
Query Memory — Phase 6c

Logs successful queries to SQLite so the system can recognise patterns,
surface similar past searches, and (in future phases) adjust ranking weights
based on what worked well.

Uses the same DB file as the page cache — schema is additive (IF NOT EXISTS).
All functions are synchronous; call sites wrap in asyncio.to_thread() if needed.

Public API
----------
init_query_memory_table(conn)          — create table (idempotent)
log_successful_query(query, intent)    — upsert a search event
get_similar_queries(query, threshold)  — fuzzy recall of past queries
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple

from cache.page_cache import DB_PATH, _connect

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_QUERY_MEMORY = """
CREATE TABLE IF NOT EXISTS query_memory (
    query_hash    TEXT PRIMARY KEY,
    query         TEXT,
    intent        TEXT,
    user_feedback REAL    DEFAULT 0.0,
    search_count  INTEGER DEFAULT 1,
    last_searched TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    embedding     BLOB
);
"""

_CREATE_QUERY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_qmem_count ON query_memory(search_count DESC);
"""


# ── Schema init ───────────────────────────────────────────────────────────────

def init_query_memory_table(conn: sqlite3.Connection) -> None:
    """
    Create query_memory table + index if they don't already exist.
    Safe to call repeatedly (IF NOT EXISTS).
    """
    conn.executescript(_CREATE_QUERY_MEMORY + _CREATE_QUERY_INDEX)
    conn.commit()


# ── Internal ──────────────────────────────────────────────────────────────────

def _hash(query: str) -> str:
    """SHA-256 of the normalised query (lower-cased, stripped)."""
    return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:32]


# ── Public functions ──────────────────────────────────────────────────────────

def log_successful_query(
    query:    str,
    intent:   str,
    feedback: float = 0.0,
    db_path:  Path  = DB_PATH,
) -> None:
    """
    Record (or update) a query event.

    First occurrence:  INSERT with search_count=1.
    Subsequent calls:  increment search_count, update last_searched + intent.

    Args:
        query:    Raw query string.
        intent:   Classified intent (from classify_query_intent).
        feedback: User rating (-1 dislike / 0 neutral / +1 like). Default 0.
        db_path:  SQLite file path (default: shared cache DB).
    """
    qhash = _hash(query)
    now   = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    conn = _connect(db_path)
    init_query_memory_table(conn)
    with conn:
        conn.execute(
            """
            INSERT INTO query_memory
                (query_hash, query, intent, user_feedback, search_count, last_searched)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(query_hash) DO UPDATE SET
                search_count  = search_count + 1,
                last_searched = excluded.last_searched,
                intent        = excluded.intent
            """,
            (qhash, query, intent, feedback, now),
        )
    conn.close()


def get_similar_queries(
    query:     str,
    threshold: float = 0.85,
    db_path:   Path  = DB_PATH,
) -> List[Tuple[str, str, int]]:
    """
    Return past queries similar to `query`, sorted by search_count DESC.

    Similarity is assessed via two complementary criteria (OR logic):
      1. difflib.SequenceMatcher ratio >= threshold
      2. Substring containment (query in past OR past in query) — catches
         short queries like "quantum" that match "quantum computing"

    Args:
        query:     Query to compare against history.
        threshold: Minimum SequenceMatcher ratio for inclusion (default 0.85).
        db_path:   SQLite file path.

    Returns:
        [(past_query, intent, search_count), ...]  sorted by search_count DESC.
    """
    conn = _connect(db_path)
    init_query_memory_table(conn)
    rows = conn.execute(
        "SELECT query, intent, search_count FROM query_memory ORDER BY search_count DESC"
    ).fetchall()
    conn.close()

    q_norm = query.lower().strip()
    matched: List[Tuple[str, str, int]] = []

    for row in rows:
        past  = (row["query"] or "").strip()
        past_norm = past.lower()

        ratio = SequenceMatcher(None, q_norm, past_norm, autojunk=False).ratio()
        is_substring = q_norm in past_norm or past_norm in q_norm

        if ratio >= threshold or is_substring:
            matched.append((past, row["intent"] or "", row["search_count"]))

    # SequenceMatcher filter may have changed relative order — re-sort by count
    matched.sort(key=lambda x: x[2], reverse=True)
    return matched
