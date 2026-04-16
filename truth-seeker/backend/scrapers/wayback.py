"""
Wayback Machine fallback scraper.

Used in two modes:

  1. Thin-content fallback:
     If a result was fetched but word_count < MIN_WORD_COUNT,
     try fetching its latest archived snapshot from the Wayback Machine.
     This is useful for pages that have been paywalled, taken down, or
     serve JavaScript-gated content that trafilatura can't extract.

  2. Dead page fallback:
     If a page returns 4xx/5xx, check if an archive snapshot exists.

API used:
  CDX (availability check):
    http://web.archive.org/cdx/search/cdx?url={url}&output=json&limit=1&fl=timestamp,statuscode&filter=statuscode:200
  Archive fetch:
    https://web.archive.org/web/{timestamp}id_/{url}
    (id_ flag strips Wayback banners and toolbar from the HTML)

Rate limiting: The CDX API is public and rate-limited conservatively;
we only call it for thin/failed pages, not all results.
"""
import asyncio
from typing import List, Dict, Optional, Tuple
import json

import httpx

MIN_WORD_COUNT = 150   # below this, try Wayback fallback
CDX_TIMEOUT    = 5.0
FETCH_TIMEOUT  = 10.0

_HEADERS = {
    "User-Agent": "TruthSeeker/1.0 (research archival tool)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


async def _check_availability(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Query CDX API for the most recent successful snapshot.
    Returns timestamp string (e.g. "20231015123045") or None.
    """
    cdx_url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": url,
        "output": "json",
        "limit": "1",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
        "from": "20150101",   # ignore very old archives
    }
    try:
        resp = await client.get(cdx_url, params=params, timeout=CDX_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # data is [[header_row], [timestamp, statuscode]] or [[header_row]] if not found
        if isinstance(data, list) and len(data) >= 2:
            row = data[1]
            if isinstance(row, list) and len(row) >= 1:
                return str(row[0])   # timestamp
    except Exception:
        pass
    return None


async def _fetch_archived(url: str, timestamp: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch the archived snapshot HTML (id_ flag strips Wayback toolbar)."""
    archive_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    try:
        resp = await client.get(
            archive_url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True
        )
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


async def wayback_fallback(result: Dict) -> Dict:
    """
    Attempt to enrich a thin/failed result using the Wayback Machine.
    Modifies result in-place if an archive snapshot provides better content.
    Returns the (possibly updated) result.
    """
    # Only run for thin content — don't burn quota on already-good results
    if result.get("word_count", 0) >= MIN_WORD_COUNT:
        return result

    url = result.get("url", "")
    if not url:
        return result

    try:
        async with httpx.AsyncClient(timeout=CDX_TIMEOUT + FETCH_TIMEOUT) as client:
            timestamp = await _check_availability(url, client)
            if not timestamp:
                return result

            html = await _fetch_archived(url, timestamp, client)
            if not html:
                return result

        # Extract content from archived HTML
        try:
            import trafilatura
            raw_json = trafilatura.extract(
                html, url=url, include_comments=False, include_tables=True,
                no_fallback=False, favor_recall=True,
                output_format="json", with_metadata=True,
            )
            if raw_json:
                data = json.loads(raw_json)
                content = data.get("text") or ""
                if len(content.split()) > result.get("word_count", 0):
                    result["content"] = content
                    result["word_count"] = len(content.split())
                    result["author"] = result.get("author") or data.get("author")
                    result["publish_date"] = result.get("publish_date") or data.get("date")
                    result["wayback_used"] = True
        except Exception:
            pass

    except Exception as exc:
        print(f"[Wayback] Error for {url[:60]}: {exc}")

    return result


async def wayback_fallback_batch(results: List[Dict], max_concurrent: int = 3) -> List[Dict]:
    """
    Run wayback fallback on all thin results concurrently.
    Only queries Wayback for results where word_count < MIN_WORD_COUNT.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _guarded(r: Dict) -> Dict:
        if r.get("word_count", 0) >= MIN_WORD_COUNT:
            return r
        async with semaphore:
            return await wayback_fallback(r)

    updated = await asyncio.gather(*[_guarded(r) for r in results], return_exceptions=True)

    final = []
    for i, item in enumerate(updated):
        if isinstance(item, Exception):
            final.append(results[i])
        else:
            final.append(item)
    return final
