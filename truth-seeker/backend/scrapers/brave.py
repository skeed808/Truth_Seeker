"""
Brave Search API scraper.
Requires a free API key from https://api.search.brave.com
Set BRAVE_API_KEY in your .env file. If absent, this scraper silently returns [].
"""
import os
import httpx
import tldextract
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def fetch_brave_results(query: str, max_results: int = 15) -> List[Dict]:
    """Fetch web search results from Brave Search API."""
    if not BRAVE_API_KEY:
        print("[Brave] No API key set — skipping Brave source.")
        return []

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }
    params = {
        "q": query,
        "count": min(max_results, 20),   # Brave API max per request
        "search_lang": "en",
        "result_filter": "web",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(BRAVE_SEARCH_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"[Brave] HTTP error {exc.response.status_code}: {exc.response.text[:200]}")
        return []
    except Exception as exc:
        print(f"[Brave] Error: {exc}")
        return []

    results = []
    for r in data.get("web", {}).get("results", []):
        url = r.get("url", "")
        ext = tldextract.extract(url)
        domain = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain

        results.append({
            "title": r.get("title", ""),
            "url": url,
            "snippet": r.get("description", ""),
            "domain": domain,
            "source": "brave",
            # Brave sometimes returns age as "3 days ago" — parse later
            "publish_date": r.get("age"),
            "content": None,
            "word_count": 0,
            "author": None,
        })

    return results
