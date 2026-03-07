from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List
import json
import os
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _raw_footage_likelihood(title: str, description: str) -> float:
    text = f"{title} {description}".lower()
    score = 0.2
    for token, weight in {
        "bodycam": 0.35,
        "body camera": 0.35,
        "officer": 0.1,
        "deputy": 0.1,
        "pursuit": 0.1,
        "arrest": 0.1,
        "dashcam": 0.05,
    }.items():
        if token in text:
            score += weight
    return max(0.0, min(1.0, round(score, 2)))


def _media_type(url: str, title: str) -> str:
    blob = f"{url} {title}".lower()
    if "youtube.com" in blob or "youtu.be" in blob or "video" in blob or "bodycam" in blob:
        return "video"
    return "web"


def _fetch_brave_results(query: str, count: int, api_key: str) -> List[Dict[str, Any]]:
    req = Request(
        f"{BRAVE_SEARCH_ENDPOINT}?q={quote_plus(query)}&count={count}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
    )
    with urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("web", {}).get("results", [])


def discover_candidates_from_brave(keywords: Iterable[str], count_per_keyword: int = 5) -> List[Dict[str, Any]]:
    api_key = os.getenv("BRAVE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY is required for Brave discovery runs.")

    candidates: List[Dict[str, Any]] = []
    for keyword in keywords:
        for row in _fetch_brave_results(keyword, count_per_keyword, api_key):
            title = row.get("title", "")
            description = row.get("description", "")
            source_url = row.get("url", "")
            if not source_url or not title:
                continue

            candidates.append(
                {
                    "source_url": source_url,
                    "title": title,
                    "description": description,
                    "publisher": row.get("meta_url", {}).get("hostname", "UNKNOWN"),
                    "published_date": row.get("page_age", datetime.utcnow().date().isoformat()),
                    "media_type": _media_type(source_url, title),
                    "transcript_available": False,
                    "raw_footage_likelihood": _raw_footage_likelihood(title, description),
                    "watermark_likelihood": 0.2,
                    "hints": {
                        "transcript_search_anchors": [keyword],
                    },
                }
            )
    return candidates
