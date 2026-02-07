#!/usr/bin/env python3
"""
NEWS → VIEWS: Search Backends

Unified search interface for Brave Search API (web),
YouTube Data API, and Vimeo API. Each function returns a consistent
result schema for easy integration with artifact_hunter.py.

All clients use stdlib urllib + json to avoid heavy dependencies.
Basic retry with exponential backoff for 429/5xx errors.
"""

import os
import gzip
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Optional


# =============================================================================
# CONFIGURATION
# =============================================================================

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
VIMEO_ACCESS_TOKEN = os.getenv("VIMEO_ACCESS_TOKEN", "")

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


# =============================================================================
# RETRY HELPER
# =============================================================================

def _fetch_json(url: str, headers: Optional[Dict] = None) -> Optional[Dict]:
    """Fetch JSON from URL with retry/backoff for 429 and 5xx errors."""
    backoff = INITIAL_BACKOFF

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)

            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                # Decompress gzip if needed (Brave sends gzip when requested)
                if raw[:2] == b'\x1f\x8b':
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))

        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            print(f"      HTTP {e.code}: {e.reason}")
            return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            print(f"      Fetch error: {e}")
            return None

    return None


# =============================================================================
# BRAVE SEARCH API (web)
# =============================================================================

def web_search_brave(query: str, num: int = 10) -> List[Dict]:
    """Search the web using Brave Search API.

    Args:
        query: Search query string
        num: Number of results (max 20 per API call)

    Returns:
        List of result dicts with url, title, snippet, source.
    """
    if not BRAVE_API_KEY:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        "count": min(num, 20),
    })
    url = f"https://api.search.brave.com/res/v1/web/search?{params}"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    data = _fetch_json(url, headers=headers)
    if not data:
        return []

    results = []
    for item in data.get("web", {}).get("results", []):
        results.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "snippet": item.get("description", "")[:500],
            "source": "brave",
        })

    return results


# =============================================================================
# YOUTUBE DATA API
# =============================================================================

def youtube_search(defendant: str, jurisdiction: str = "",
                   incident_year: str = None,
                   hints: List[str] = None) -> List[Dict]:
    """Search YouTube for case-related videos.

    Builds multiple targeted queries and deduplicates results.

    Args:
        defendant: Primary defendant name
        jurisdiction: City/county/state string
        incident_year: Year of incident for date filtering
        hints: Additional search terms (e.g., agency name, crime type)

    Returns:
        List of result dicts with url, title, snippet, channel, published_at, source.
    """
    if not YOUTUBE_API_KEY:
        return []

    year_str = f" {incident_year}" if incident_year else ""
    juris_str = f" {jurisdiction}" if jurisdiction else ""

    queries = [
        f"{defendant} bodycam{juris_str}{year_str}",
        f"{defendant} interrogation{juris_str}",
        f"{defendant} trial OR sentencing{juris_str}",
    ]

    # Add hint-based queries
    for hint in (hints or [])[:2]:
        queries.append(f"{defendant} {hint}")

    seen_ids = set()
    results = []

    for query in queries:
        params = {
            "key": YOUTUBE_API_KEY,
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 5,
            "order": "relevance",
        }

        # Date filter: if we have incident_year, search from that year onward
        if incident_year:
            params["publishedAfter"] = f"{incident_year}-01-01T00:00:00Z"

        url = f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(params)}"
        data = _fetch_json(url)
        if not data:
            continue

        for item in data.get("items", []):
            video_id = item.get("id", {}).get("videoId", "")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            snippet = item.get("snippet", {})
            results.append({
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": snippet.get("title", ""),
                "snippet": snippet.get("description", "")[:500],
                "channel": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt", ""),
                "source": "youtube",
            })

        time.sleep(0.2)

    return results


# =============================================================================
# VIMEO API
# =============================================================================

def vimeo_search(defendant: str, jurisdiction: str = "",
                 incident_year: str = None,
                 hints: List[str] = None) -> List[Dict]:
    """Search Vimeo for case-related videos.

    Args:
        defendant: Primary defendant name
        jurisdiction: City/county/state string
        incident_year: Year of incident
        hints: Additional search terms

    Returns:
        List of result dicts with url, title, snippet, user, published_at, source.
    """
    if not VIMEO_ACCESS_TOKEN:
        return []

    juris_str = f" {jurisdiction}" if jurisdiction else ""

    queries = [
        f"{defendant} bodycam{juris_str}",
        f"{defendant} interrogation",
        f"{defendant} hearing OR trial",
    ]

    for hint in (hints or [])[:1]:
        queries.append(f"{defendant} {hint}")

    seen_ids = set()
    results = []

    for query in queries:
        params = urllib.parse.urlencode({
            "query": query,
            "per_page": 5,
            "sort": "relevant",
        })
        url = f"https://api.vimeo.com/videos?{params}"
        headers = {"Authorization": f"Bearer {VIMEO_ACCESS_TOKEN}"}

        data = _fetch_json(url, headers=headers)
        if not data:
            continue

        for item in data.get("data", []):
            vimeo_uri = item.get("uri", "")  # e.g. "/videos/123456"
            video_id = vimeo_uri.split("/")[-1] if vimeo_uri else ""
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            user = item.get("user", {})
            results.append({
                "url": item.get("link", f"https://vimeo.com/{video_id}"),
                "title": item.get("name", ""),
                "snippet": (item.get("description", "") or "")[:500],
                "user": user.get("name", ""),
                "published_at": item.get("release_time", ""),
                "source": "vimeo",
            })

        time.sleep(0.2)

    return results


# =============================================================================
# CREDENTIAL VALIDATION
# =============================================================================

def check_search_credentials() -> Dict[str, bool]:
    """Check which search backends have credentials configured.

    Returns dict mapping backend name to availability.
    """
    return {
        "brave": bool(BRAVE_API_KEY),
        "youtube": bool(YOUTUBE_API_KEY),
        "vimeo": bool(VIMEO_ACCESS_TOKEN),
    }


def print_search_credential_status():
    """Print which search backends are available."""
    status = check_search_credentials()
    for backend, available in status.items():
        icon = "✅" if available else "⚠️"
        label = {
            "brave": "Brave Search API (BRAVE_API_KEY)",
            "youtube": "YouTube Data API (YOUTUBE_API_KEY)",
            "vimeo": "Vimeo API (VIMEO_ACCESS_TOKEN)",
        }[backend]
        print(f"   {icon} {label}")
    return status
