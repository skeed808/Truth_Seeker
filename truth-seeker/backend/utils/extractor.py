"""
Page content extractor.
Fetches raw HTML with httpx (async, concurrent, timeout-bounded),
then uses trafilatura to extract main article text + metadata.
Falls back gracefully: if extraction fails the result keeps its snippet.

When include_links=True is passed to extract_content_batch, each result
will also carry `_outbound_links`: a list of absolute cross-domain URLs
found on the page, used by the seed expander.
"""
import asyncio
import json
from typing import List, Dict, Optional
from urllib.parse import urlparse, urljoin

import httpx
import trafilatura

# Realistic browser headers to avoid trivial bot detection
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


async def _fetch_html(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch page HTML with a hard 8-second timeout."""
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=8.0, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _extract_outbound_links(html: str, page_url: str) -> List[str]:
    """
    Extract cross-domain absolute URLs from anchor tags.
    Returns up to 60 distinct links; keeps only http(s) URLs.
    """
    try:
        from bs4 import BeautifulSoup
        from crawler.link_filter import should_skip
        page_domain = urlparse(page_url).netloc.lstrip("www.")
        soup = BeautifulSoup(html, "lxml")
        seen: set[str] = set()
        links: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith("#"):
                continue
            abs_url = urljoin(page_url, href)
            if not abs_url.startswith(("http://", "https://")):
                continue
            link_domain = urlparse(abs_url).netloc.lstrip("www.")
            if link_domain == page_domain:
                continue          # internal link — skip
            if abs_url in seen:
                continue
            if should_skip(abs_url):
                continue
            seen.add(abs_url)
            links.append(abs_url)
            if len(links) >= 60:
                break
        return links
    except Exception:
        return []


def _extract_from_html(html: str, url: str) -> Dict:
    """
    Run trafilatura extraction. Try JSON mode (gets author + date) first,
    fall back to plain text if that fails.
    """
    # --- Pass 1: structured JSON extraction ---
    try:
        raw_json = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,       # Maximize text recall over precision
            output_format="json",
            with_metadata=True,
        )
        if raw_json:
            data = json.loads(raw_json)
            content = data.get("text") or ""
            return {
                "content": content,
                "word_count": len(content.split()),
                "author": data.get("author"),
                "publish_date": data.get("date"),
            }
    except Exception:
        pass

    # --- Pass 2: plain text fallback ---
    content = trafilatura.extract(html, favor_recall=True) or ""
    return {
        "content": content,
        "word_count": len(content.split()),
        "author": None,
        "publish_date": None,
    }


async def extract_content_batch(
    results: List[Dict],
    concurrency: int = 5,
    include_links: bool = False,
) -> List[Dict]:
    """
    Concurrently fetch and extract page content for a list of results.
    Uses a semaphore to cap simultaneous connections (polite + memory-safe).
    Results that fail extraction keep their original snippet but get word_count=0.

    Args:
        results:       List of result dicts (must have 'url').
        concurrency:   Max simultaneous HTTP connections.
        include_links: If True, each result will also get `_outbound_links`
                       populated with cross-domain URLs found on the page.
                       Used by the seed expander.  Adds negligible overhead
                       since HTML is already fetched.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def process(result: Dict) -> Dict:
        async with semaphore:
            try:
                # ssl=False to handle self-signed certs on obscure sites
                async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                    html = await _fetch_html(result["url"], client)

                if html:
                    meta = _extract_from_html(html, result["url"])
                    result.update(meta)
                    if include_links:
                        result["_outbound_links"] = _extract_outbound_links(
                            html, result["url"]
                        )
            except Exception as exc:
                print(f"[Extractor] Failed {result.get('url', '')[:60]}: {exc}")
        return result

    tasks = [process(r) for r in results]
    enriched = await asyncio.gather(*tasks, return_exceptions=True)

    # If gather returned an exception for a slot, fall back to original
    final = []
    for i, item in enumerate(enriched):
        if isinstance(item, Exception):
            final.append(results[i])
        else:
            final.append(item)

    return final
