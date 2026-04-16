"""
Reddit scraper — uses Reddit's public JSON API (no auth required).

Endpoints:
  Search:   https://www.reddit.com/search.json?q={query}&sort=relevance&limit=10
  Comments: https://www.reddit.com/comments/{id}.json

Each result includes:
  • Thread title and selftext (the original post body)
  • Top 3 comments by score (concatenated as content)
  • Link to the thread (reddit.com permalink)

Rate limiting: Reddit's public API allows ~30 req/min without auth.
We make at most 10 search results + 0 comment fetches by default
(comment fetching is opt-in via fetch_comments=True and is gated by a flag
to keep latency reasonable for the search pipeline).
"""
import asyncio
from typing import List, Dict

import httpx

_HEADERS = {
    "User-Agent": "TruthSeeker/1.0 (research tool; no auth)",
    "Accept": "application/json",
}

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"


async def fetch_reddit_results(
    query: str,
    max_results: int = 10,
    fetch_comments: bool = False,
) -> List[Dict]:
    """
    Fetch Reddit threads matching query via the public JSON search API.
    Returns results in the standard pipeline result shape.
    """
    params = {
        "q": query,
        "sort": "relevance",
        "type": "link",
        "limit": max_results,
        "t": "all",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(REDDIT_SEARCH_URL, params=params, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"[Reddit] Search error: {exc}")
        return []

    posts = data.get("data", {}).get("children", [])
    results = []

    comment_tasks = []

    for post in posts:
        p = post.get("data", {})
        if not p:
            continue

        permalink = "https://www.reddit.com" + p.get("permalink", "")
        subreddit = p.get("subreddit_name_prefixed", "r/?")
        title = p.get("title", "")
        selftext = (p.get("selftext", "") or "").strip()
        score = p.get("score", 0)
        post_id = p.get("id", "")

        # Build content: selftext + placeholder for top comments
        content_parts = []
        if selftext and selftext != "[deleted]" and selftext != "[removed]":
            content_parts.append(selftext)

        result = {
            "title": f"[{subreddit}] {title}",
            "url": permalink,
            "snippet": (selftext[:200] + "…") if len(selftext) > 200 else selftext or title,
            "domain": "reddit.com",
            "source": "reddit",
            "content": " ".join(content_parts) if content_parts else None,
            "word_count": len(" ".join(content_parts).split()) if content_parts else 0,
            "publish_date": None,
            "author": p.get("author"),
            "reddit_score": score,
            "reddit_id": post_id,
        }

        results.append(result)

        # Optionally enrich with comments (adds latency)
        if fetch_comments and post_id:
            comment_tasks.append((len(results) - 1, post_id))

    # Fetch comments for threads (parallel, gated)
    if fetch_comments and comment_tasks:
        await _enrich_with_comments(results, comment_tasks)

    return results


async def _enrich_with_comments(results: List[Dict], tasks: List) -> None:
    """Fetch top comments for each thread and append to content."""
    semaphore = asyncio.Semaphore(3)

    async def fetch_thread(idx: int, post_id: str):
        url = f"https://www.reddit.com/comments/{post_id}.json?limit=10&sort=top"
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(url, headers=_HEADERS)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception:
                return

        if not isinstance(data, list) or len(data) < 2:
            return

        comments = data[1].get("data", {}).get("children", [])
        top_comments = []
        for c in comments[:5]:
            cd = c.get("data", {})
            body = (cd.get("body", "") or "").strip()
            c_score = cd.get("score", 0)
            if body and body not in ("[deleted]", "[removed]") and c_score > 2:
                top_comments.append(body)
            if len(top_comments) >= 3:
                break

        if top_comments:
            existing = results[idx].get("content", "") or ""
            combined = existing + "\n\n" + "\n\n".join(top_comments)
            results[idx]["content"] = combined.strip()
            results[idx]["word_count"] = len(combined.split())

    await asyncio.gather(*[fetch_thread(i, pid) for i, pid in tasks], return_exceptions=True)
