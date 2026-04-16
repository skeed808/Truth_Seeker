"""
Persistent Page Cache — Selective Memory Layer

Only high-value pages are stored. Low-quality, AI-spam, and commercial-heavy
pages are discarded immediately. Pages auto-expire based on their score tier.
The DB is hard-capped at MAX_DB_SIZE_MB; oldest/worst pages are trimmed first.

Layout:
  ~/.truth-seeker/cache.db

Tables:
  pages         — scored metadata + truncated content (NO full HTML)
  pages_fts     — FTS5 virtual table for BM25 full-text search
  domain_stats  — per-domain quality signals (trust memory + explorer scheduling)

Selective storage rules (all must pass):
  ✓  final_score  >= MIN_SCORE_TO_STORE  (default 0.60)
  ✓  ai_spam      <  MAX_SPAM_TO_STORE   (default 0.65)
  ✓  commercial   <  MAX_COMMERCIAL_TO_STORE (default 0.75)
  ✓  word_count   >= MIN_WORDS_TO_CACHE  (default 80)

Tiered TTL:
  score >= 0.85  →  30 days
  score >= 0.70  →  7 days
  score <  0.70  →  3 days

DB size cap:
  If DB file exceeds MAX_DB_SIZE_MB (50 MB), the 500 lowest-score oldest
  pages are deleted and orphaned FTS entries are cleaned.

Domain memory:
  domain_stats.avg_score is used by the ranking engine to apply a small
  trust boost (+0.04 max) to consistently good domains and a small penalty
  (-0.04 max) to consistently poor ones.

Key public async methods:
  store_batch(results, query)   — persist high-value pages
  search(query, limit)          — BM25 full-text search
  get_by_url(url)               — exact URL lookup
  get_domain_pages(domain)      — cached pages for a domain
  get_domain_trust_map()        — {domain: avg_score} for ranking
  mark_domain_explored(...)     — update after domain exploration
  get_explorable_domains(...)   — domains overdue for re-exploration
  cleanup()                     — manual TTL + size-cap sweep
  stats()                       — DB health stats

All public methods are async; sync work runs in a thread-pool executor.
"""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

# ── Config ────────────────────────────────────────────────────────────────────
DB_DIR  = Path.home() / ".truth-seeker"
DB_PATH = DB_DIR / "cache.db"

# Selective storage gates
MIN_SCORE_TO_STORE       = 0.60   # discard low-quality pages
MAX_SPAM_TO_STORE        = 0.65   # discard AI/SEO-heavy content
MAX_COMMERCIAL_TO_STORE  = 0.75   # discard heavily commercial pages
MIN_WORDS_TO_CACHE       = 80     # discard near-empty stubs

# Tiered TTL (seconds)
_TTL_HIGH   = 30 * 86_400   # score >= 0.85 → 30 days
_TTL_MEDIUM =  7 * 86_400   # score >= 0.70 → 7 days
_TTL_LOW    =  3 * 86_400   # score <  0.70 (min 0.60) → 3 days

# DB size cap
MAX_DB_SIZE_MB = 50     # trigger trim above this
TRIM_BATCH     = 500    # rows deleted per trim pass

# Storage throughput (avoid excessive content)
MAX_CONTENT_WORDS = 3_000   # truncate stored content to keep rows small

# Cleanup throttle: run at most once per hour to avoid overhead on every store
CLEANUP_INTERVAL_S = 3_600
_last_cleanup: float = 0.0

# ── DDL ───────────────────────────────────────────────────────────────────────
_CREATE_PAGES = """
CREATE TABLE IF NOT EXISTS pages (
    url          TEXT PRIMARY KEY,
    domain       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    snippet      TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    word_count   INTEGER NOT NULL DEFAULT 0,
    score        REAL    NOT NULL DEFAULT 0.0,
    publish_date TEXT,
    author       TEXT,
    source       TEXT NOT NULL DEFAULT 'cache',
    cluster      TEXT,
    cached_at    REAL NOT NULL,
    query_tags   TEXT NOT NULL DEFAULT '[]'
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    url      UNINDEXED,
    title,
    body,
    tokenize='porter unicode61'
);
"""

_CREATE_DOMAIN_STATS = """
CREATE TABLE IF NOT EXISTS domain_stats (
    domain             TEXT PRIMARY KEY,
    page_count         INTEGER NOT NULL DEFAULT 0,
    avg_score          REAL    NOT NULL DEFAULT 0.0,
    last_seen          REAL    NOT NULL DEFAULT 0.0,
    last_explored      REAL    NOT NULL DEFAULT 0.0,
    exploration_count  INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pages_domain    ON pages(domain);
CREATE INDEX IF NOT EXISTS idx_pages_cached_at ON pages(cached_at);
CREATE INDEX IF NOT EXISTS idx_pages_score     ON pages(score);
CREATE INDEX IF NOT EXISTS idx_pages_cluster   ON pages(cluster);
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
    return conn


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
CREATE INDEX IF NOT EXISTS idx_qmem_count ON query_memory(search_count DESC);
"""


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        _CREATE_PAGES + _CREATE_FTS + _CREATE_DOMAIN_STATS
        + _CREATE_INDEXES + _CREATE_QUERY_MEMORY
    )
    conn.commit()
    # Schema migrations for databases created before these columns existed
    for stmt in [
        "ALTER TABLE pages ADD COLUMN score REAL NOT NULL DEFAULT 0.0",
        "ALTER TABLE domain_stats ADD COLUMN last_seen REAL NOT NULL DEFAULT 0.0",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already present


def _truncate_content(text: str) -> str:
    words = text.split()
    if len(words) > MAX_CONTENT_WORDS:
        return " ".join(words[:MAX_CONTENT_WORDS])
    return text


def _row_to_result(row) -> Dict:
    r = dict(row)
    return {
        "url":          r["url"],
        "domain":       r["domain"],
        "title":        r["title"],
        "snippet":      r["snippet"],
        "content":      r.get("content", ""),
        "word_count":   r["word_count"],
        "publish_date": r.get("publish_date"),
        "author":       r.get("author"),
        "source":       "cache",
        "cluster":      r.get("cluster"),
        "from_cache":   True,
        "cached_at":    r["cached_at"],
    }


# ── Synchronous core operations ───────────────────────────────────────────────

def _sync_store_batch(results: List[Dict], query: str, db_path: Path) -> int:
    """
    Persist a batch of results, enforcing all quality gates.
    Runs TTL cleanup and size-cap check after each store (throttled).
    Returns the number of pages actually stored.
    """
    global _last_cleanup

    conn = _connect(db_path)
    _init_db(conn)
    now = time.time()
    stored = 0

    with conn:
        for r in results:
            url = r.get("url", "").strip()
            if not url:
                continue

            word_count = r.get("word_count", 0)
            if word_count < MIN_WORDS_TO_CACHE:
                continue

            # ── Quality gate: only store high-value pages ──────────────────────
            scores = r.get("scores") or {}
            if isinstance(scores, dict):
                final_score = scores.get("final", 0.0)
                ai_spam     = scores.get("ai_spam", 0.0)
                commercial  = scores.get("commercial_bias", 0.0)
            else:
                final_score = ai_spam = commercial = 0.0

            if final_score < MIN_SCORE_TO_STORE:
                continue   # low-value — discard
            if ai_spam > MAX_SPAM_TO_STORE:
                continue   # AI/SEO spam — discard
            if commercial > MAX_COMMERCIAL_TO_STORE:
                continue   # commercial-heavy — discard

            content_raw = r.get("content") or r.get("snippet", "")
            content = _truncate_content(content_raw)
            title   = r.get("title", "")[:512]
            snippet = r.get("snippet", "")[:512]
            domain  = r.get("domain", "")

            # Accumulate query tags (remember which searches found this page)
            existing = conn.execute(
                "SELECT query_tags FROM pages WHERE url = ?", (url,)
            ).fetchone()

            if existing:
                try:
                    tags = json.loads(existing["query_tags"])
                except Exception:
                    tags = []
                if query and query not in tags:
                    tags.append(query)
                conn.execute(
                    """UPDATE pages SET
                        title=?, snippet=?, content=?, word_count=?,
                        score=?, cached_at=?, query_tags=?, cluster=?
                       WHERE url=?""",
                    (title, snippet, content, word_count,
                     final_score, now, json.dumps(tags[:20]),
                     r.get("cluster"), url),
                )
                # Refresh FTS entry
                conn.execute("DELETE FROM pages_fts WHERE url = ?", (url,))
            else:
                tags = [query] if query else []
                conn.execute(
                    """INSERT INTO pages
                       (url, domain, title, snippet, content,
                        word_count, score, publish_date, author, source, cluster,
                        cached_at, query_tags)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (url, domain, title, snippet, content,
                     word_count, final_score,
                     r.get("publish_date"), r.get("author"),
                     r.get("source", "cache"), r.get("cluster"),
                     now, json.dumps(tags)),
                )

            # FTS insert (title + content for BM25 matching)
            body = f"{title} {content}"[:8_000]
            conn.execute(
                "INSERT INTO pages_fts(url, title, body) VALUES (?,?,?)",
                (url, title, body),
            )

            # Update domain quality memory
            conn.execute(
                """INSERT INTO domain_stats(domain, page_count, avg_score, last_seen)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                     page_count = page_count + 1,
                     avg_score  = (avg_score * page_count + excluded.avg_score)
                                  / (page_count + 1),
                     last_seen  = excluded.last_seen
                """,
                (domain, final_score, now),
            )
            stored += 1

    conn.close()

    # Throttled maintenance: TTL cleanup + size cap (at most once per hour)
    if time.time() - _last_cleanup > CLEANUP_INTERVAL_S:
        _sync_cleanup_expired(db_path)
        _sync_enforce_size_cap(db_path)
        _last_cleanup = time.time()

    return stored


def _sync_cleanup_expired(db_path: Path) -> int:
    """
    Delete pages past their score-tiered TTL.
    High-value pages (≥0.85) survive 30 days.
    Medium (≥0.70) survive 7 days.
    Low (≥0.60, the store minimum) survive 3 days.
    Returns total rows deleted.
    """
    conn = _connect(db_path)
    _init_db(conn)
    now = time.time()
    deleted = 0

    with conn:
        # 30-day tier: high-score discoveries
        r = conn.execute(
            "DELETE FROM pages WHERE score >= 0.85 AND cached_at < ?",
            (now - _TTL_HIGH,),
        )
        deleted += r.rowcount

        # 7-day tier: solid medium-quality pages
        r = conn.execute(
            "DELETE FROM pages WHERE score >= 0.70 AND score < 0.85 AND cached_at < ?",
            (now - _TTL_MEDIUM,),
        )
        deleted += r.rowcount

        # 3-day tier: marginal pages just above the store threshold
        r = conn.execute(
            "DELETE FROM pages WHERE score < 0.70 AND cached_at < ?",
            (now - _TTL_LOW,),
        )
        deleted += r.rowcount

        if deleted > 0:
            # Purge orphaned FTS entries for deleted pages
            conn.execute(
                "DELETE FROM pages_fts WHERE url NOT IN (SELECT url FROM pages)"
            )

    conn.close()
    return deleted


def _sync_enforce_size_cap(db_path: Path) -> int:
    """
    If the DB file exceeds MAX_DB_SIZE_MB, delete the lowest-scoring and
    oldest pages in a single batch (TRIM_BATCH rows). Cleans orphaned FTS
    entries. Does NOT vacuum — reclaimed space is reused by new inserts.
    Returns rows deleted (0 if under the cap).
    """
    try:
        size_mb = db_path.stat().st_size / 1_048_576
    except Exception:
        return 0

    if size_mb <= MAX_DB_SIZE_MB:
        return 0

    conn = _connect(db_path)
    _init_db(conn)
    deleted = 0

    with conn:
        r = conn.execute(
            """DELETE FROM pages WHERE url IN (
                SELECT url FROM pages
                ORDER BY score ASC, cached_at ASC
                LIMIT ?
            )""",
            (TRIM_BATCH,),
        )
        deleted = r.rowcount
        if deleted > 0:
            conn.execute(
                "DELETE FROM pages_fts WHERE url NOT IN (SELECT url FROM pages)"
            )

    # Flush WAL to reclaim some disk space without a full VACUUM
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return deleted


def _sync_search(query: str, limit: int, db_path: Path) -> List[Dict]:
    """
    Improved local search with:
      - Title-weighted BM25: title column gets 5× weight vs body.
      - Phrase boost: exact phrase match in title/snippet lifts result to top.
      - Fallback to LIKE when FTS5 is absent.
    """
    import re
    conn = _connect(db_path)
    _init_db(conn)

    fts_query = re.sub(r"[^\w\s]", " ", query).strip()
    if not fts_query:
        conn.close()
        return []

    phrase_urls: set = set()
    rows = []

    try:
        # Pass 1: exact phrase match (FTS5 quoted phrase syntax).
        # Over-fetch so we can merge with the word-match pass.
        if " " in fts_query:  # only meaningful for multi-word queries
            try:
                phrase_rows = conn.execute(
                    """SELECT p.url
                       FROM pages_fts f
                       JOIN pages p ON f.url = p.url
                       WHERE pages_fts MATCH ?
                       ORDER BY bm25(pages_fts, 5.0, 1.0)
                       LIMIT ?""",
                    (f'"{fts_query}"', limit),
                ).fetchall()
                phrase_urls = {r["url"] for r in phrase_rows}
            except sqlite3.OperationalError:
                pass  # phrase syntax not supported in older FTS5

        # Pass 2: all-words match with 5× title weighting.
        rows = conn.execute(
            """SELECT p.*
               FROM pages_fts f
               JOIN pages p ON f.url = p.url
               WHERE pages_fts MATCH ?
               ORDER BY bm25(pages_fts, 5.0, 1.0)
               LIMIT ?""",
            (fts_query, limit * 2),  # over-fetch; phrase boost may reorder
        ).fetchall()

    except sqlite3.OperationalError:
        # FTS5 unavailable — degrade to LIKE keyword search
        words = [w for w in fts_query.split() if len(w) > 3][:5]
        if not words:
            conn.close()
            return []
        cond = " AND ".join("(title LIKE ? OR content LIKE ?)" for _ in words)
        params = [p for w in words for p in (f"%{w}%", f"%{w}%")]
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM pages WHERE {cond} ORDER BY score DESC, word_count DESC LIMIT ?",
            params,
        ).fetchall()
        conn.close()
        return [_row_to_result(r) for r in rows]

    # Post-process: apply phrase boost + title-word hit bonus
    query_lower  = query.lower()
    query_words  = {w for w in query_lower.split() if len(w) > 2}
    results = []
    for row in rows:
        r = _row_to_result(row)
        boost = 0.0

        # Exact phrase in title → strongest signal
        title_lower = r.get("title", "").lower()
        if query_lower in title_lower:
            boost += 4.0
        elif r["url"] in phrase_urls:
            boost += 2.5   # phrase in body
        elif query_words and all(w in title_lower for w in query_words):
            boost += 1.5   # all query words in title (different order)
        elif query_words:
            # Partial title word match
            hits = sum(1 for w in query_words if w in title_lower)
            boost += hits * 0.4

        # Small bonus for phrase in snippet
        if query_lower in r.get("snippet", "").lower():
            boost += 0.5

        r["_phrase_boost"] = boost
        results.append(r)

    # Stable sort: phrase boosts first, BM25 order preserved for equal boosts
    results.sort(key=lambda r: -r.pop("_phrase_boost", 0.0))

    conn.close()
    return results[:limit]


def _sync_get_domain_pages(domain: str, limit: int, db_path: Path) -> List[Dict]:
    conn = _connect(db_path)
    _init_db(conn)
    rows = conn.execute(
        "SELECT * FROM pages WHERE domain = ? ORDER BY score DESC, cached_at DESC LIMIT ?",
        (domain, limit),
    ).fetchall()
    conn.close()
    return [_row_to_result(r) for r in rows]


def _decay_trust(avg_score: float, last_seen: float) -> float:
    """
    Decay domain trust toward neutral (0.5) based on days since last seen.

    Fresh domains (seen within 7 days) retain full trust.
    Older domains drift toward 0.5 — not penalised, just less certain.

      < 7 days  → no decay (full trust)
      7–30 days → 15% pull toward 0.5
      30–90 days → 40% pull toward 0.5
      > 90 days  → 65% pull toward 0.5
    """
    if not last_seen or last_seen <= 0:
        return avg_score  # no timestamp — return as-is
    days = (time.time() - last_seen) / 86_400
    if days < 7:
        decay = 0.0
    elif days < 30:
        decay = 0.15
    elif days < 90:
        decay = 0.40
    else:
        decay = 0.65
    return round(avg_score + (0.5 - avg_score) * decay, 4)


def _sync_get_domain_trust_map(db_path: Path) -> Dict[str, float]:
    """
    Return {domain: decayed_trust_score} for domains with ≥ 2 stored pages.

    Trust is the domain's historical avg_score decayed toward 0.5 based on
    how recently it was last seen.  Used by:
      - ranking engine (±0.04 score adjustment)
      - CrawlBudget (depth limits for domain exploration)
    """
    conn = _connect(db_path)
    _init_db(conn)
    rows = conn.execute(
        "SELECT domain, avg_score, last_seen FROM domain_stats WHERE page_count >= 2"
    ).fetchall()
    conn.close()
    return {
        r["domain"]: _decay_trust(r["avg_score"], r["last_seen"] or 0)
        for r in rows
    }


def _sync_get_explorable_domains(
    min_score: float, days_since_explored: int, limit: int, db_path: Path
) -> List[Dict]:
    """Domains that score well but haven't been crawled recently."""
    conn = _connect(db_path)
    _init_db(conn)
    cutoff = time.time() - days_since_explored * 86_400
    rows = conn.execute(
        """SELECT domain, page_count, avg_score, last_explored
           FROM domain_stats
           WHERE avg_score >= ?
             AND (last_explored = 0.0 OR last_explored < ?)
           ORDER BY avg_score DESC
           LIMIT ?""",
        (min_score, cutoff, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sync_mark_explored(domain: str, pages_added: int, db_path: Path) -> None:
    conn = _connect(db_path)
    _init_db(conn)
    now = time.time()
    with conn:
        conn.execute(
            """INSERT INTO domain_stats(domain, page_count, last_explored, exploration_count)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(domain) DO UPDATE SET
                 page_count        = page_count + ?,
                 last_explored     = ?,
                 exploration_count = exploration_count + 1""",
            (domain, pages_added, now, pages_added, now),
        )
    conn.close()


def _sync_stats(db_path: Path) -> Dict:
    conn = _connect(db_path)
    _init_db(conn)
    page_count   = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    domain_count = conn.execute("SELECT COUNT(*) FROM domain_stats").fetchone()[0]
    oldest       = conn.execute("SELECT MIN(cached_at) FROM pages").fetchone()[0]
    avg_score    = conn.execute("SELECT AVG(score) FROM pages").fetchone()[0]
    high_value   = conn.execute(
        "SELECT COUNT(*) FROM pages WHERE score >= 0.85"
    ).fetchone()[0]
    try:
        db_bytes = db_path.stat().st_size
    except Exception:
        db_bytes = 0
    conn.close()
    return {
        "total_pages":     page_count,
        "total_domains":   domain_count,
        "high_value_pages": high_value,
        "avg_score":       round(avg_score or 0.0, 3),
        "oldest_cached":   oldest,
        "db_size_mb":      round(db_bytes / 1_048_576, 2),
        "db_path":         str(db_path),
    }


def _sync_cleanup(db_path: Path) -> Dict:
    """Run full maintenance: TTL sweep + size cap. Returns deletion counts."""
    expired = _sync_cleanup_expired(db_path)
    trimmed = _sync_enforce_size_cap(db_path)
    return {"expired": expired, "trimmed": trimmed}


# ── Public async API ──────────────────────────────────────────────────────────

class PageCache:
    """
    Async wrapper around synchronous SQLite operations.
    Singleton per process — share via get_cache().
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Warm-up: ensure schema exists at startup
        conn = _connect(db_path)
        _init_db(conn)
        conn.close()

    async def _run(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def store_batch(self, results: List[Dict], query: str = "") -> int:
        """
        Persist high-value pages from a ranked result set.
        Quality gates filter out low-score, spam, and commercial pages.
        TTL cleanup and size cap run in the background (throttled).
        Returns count actually stored.
        """
        try:
            return await self._run(_sync_store_batch, results, query, self._db_path)
        except Exception as exc:
            print(f"[Cache] store_batch error: {exc}")
            return 0

    async def search(self, query: str, limit: int = 15) -> List[Dict]:
        """BM25 full-text search. Returns high-quality cached result dicts."""
        try:
            return await self._run(_sync_search, query, limit, self._db_path)
        except Exception as exc:
            print(f"[Cache] search error: {exc}")
            return []

    async def get_domain_pages(self, domain: str, limit: int = 5) -> List[Dict]:
        try:
            return await self._run(_sync_get_domain_pages, domain, limit, self._db_path)
        except Exception:
            return []

    async def get_domain_trust_map(self) -> Dict[str, float]:
        """
        Return {domain: avg_score} for trusted/known domains.
        Used by rank_results() to apply a small domain reputation signal.
        """
        try:
            return await self._run(_sync_get_domain_trust_map, self._db_path)
        except Exception:
            return {}

    async def get_explorable_domains(
        self,
        min_score: float = 0.55,
        days_since: int = 3,
        limit: int = 5,
    ) -> List[Dict]:
        """Domains with good avg_score not recently crawled."""
        try:
            return await self._run(
                _sync_get_explorable_domains, min_score, days_since, limit, self._db_path
            )
        except Exception:
            return []

    async def mark_domain_explored(self, domain: str, pages_added: int) -> None:
        try:
            await self._run(_sync_mark_explored, domain, pages_added, self._db_path)
        except Exception:
            pass

    async def cleanup(self) -> Dict:
        """Run TTL expiry + size cap manually. Returns {expired, trimmed}."""
        try:
            return await self._run(_sync_cleanup, self._db_path)
        except Exception as exc:
            return {"error": str(exc)}

    async def stats(self) -> Dict:
        try:
            return await self._run(_sync_stats, self._db_path)
        except Exception:
            return {"total_pages": 0, "total_domains": 0, "db_size_mb": 0}


# ── Module-level singleton ────────────────────────────────────────────────────

_cache_instance: Optional[PageCache] = None


def get_cache() -> "PageCache":
    global _cache_instance
    if _cache_instance is None:
        try:
            _cache_instance = PageCache()
        except Exception as exc:
            print(f"[Cache] Init failed ({exc}) — running without persistence")
            _cache_instance = _NoOpCache()
    return _cache_instance


class _NoOpCache:
    """Fallback when SQLite is unavailable — silently no-ops everything."""
    async def store_batch(self, *a, **kw):           return 0
    async def search(self, *a, **kw):                return []
    async def get_domain_pages(self, *a, **kw):      return []
    async def get_domain_trust_map(self, *a, **kw):  return {}
    async def get_explorable_domains(self, *a, **kw): return []
    async def mark_domain_explored(self, *a, **kw):  pass
    async def cleanup(self, *a, **kw):               return {"expired": 0, "trimmed": 0}
    async def stats(self, *a, **kw):
        return {"total_pages": 0, "total_domains": 0, "db_size_mb": 0}
