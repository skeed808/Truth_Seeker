"""
DuckDuckGo scraper — HTML endpoint (no API key required).

Uses DDG's html.duckduckgo.com endpoint directly via httpx with a real browser
User-Agent. This avoids the d.js API that aggressively rate-limits non-browser
clients. Falls back to the duckduckgo_search library if the HTML parse fails.
"""
import re
from typing import List, Dict

import httpx
import tldextract
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://duckduckgo.com/",
}

_DDG_HTML = "https://html.duckduckgo.com/html/"


def _normalize(url: str, title: str, snippet: str) -> Dict:
    ext = tldextract.extract(url)
    domain = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "domain": domain,
        "source": "ddg",
        "content": None,
        "word_count": 0,
        "publish_date": None,
        "author": None,
    }


async def fetch_ddg_results(query: str, max_results: int = 15) -> List[Dict]:
    """Fetch web search results from DuckDuckGo HTML endpoint."""
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=12.0,
        ) as client:
            resp = await client.post(
                _DDG_HTML,
                data={"q": query, "b": "", "kl": "wt-wt"},
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[Dict] = []

        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            # Snippet is in the sibling .result__snippet
            parent = a.find_parent("div", class_="result")
            snippet = ""
            if parent:
                snip_el = parent.select_one(".result__snippet")
                if snip_el:
                    snippet = snip_el.get_text(strip=True)

            # DDG wraps URLs — extract real URL from uddg param if present
            from urllib.parse import unquote, urlparse, parse_qs
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    href = unquote(m.group(1))
            # Some results use /l/?kh=-1&uddg=... format
            if href.startswith("/l/") or "duckduckgo.com/l/" in href:
                parsed = urlparse(href if href.startswith("http") else "https://duckduckgo.com" + href)
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    href = unquote(qs["uddg"][0])

            if href and href.startswith("http") and "duckduckgo.com" not in href:
                results.append(_normalize(href, title, snippet))

            if len(results) >= max_results:
                break

        if results:
            return results

    except Exception as exc:
        print(f"[DDG-HTML] Error: {exc}")

    # Fallback: duckduckgo_search library
    try:
        import asyncio
        from duckduckgo_search import DDGS

        def _sync():
            with DDGS() as d:
                return list(d.text(query, max_results=max_results))

        raw = await asyncio.get_event_loop().run_in_executor(None, _sync)
        return [
            _normalize(r.get("href", ""), r.get("title", ""), r.get("body", ""))
            for r in raw
            if r.get("href", "").startswith("http")
        ]
    except Exception as exc2:
        print(f"[DDG-lib] Error: {exc2}")
        return []
