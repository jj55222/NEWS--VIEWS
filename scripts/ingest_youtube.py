#!/usr/bin/env python3
"""Ingest recent uploads from YouTube channels listed in sources_registry.json.

Reads enabled youtube_channel sources, fetches recent uploads via the
YouTube Data API v3, and writes NEW candidates to the pipeline database.

Usage:
    python -m scripts.ingest_youtube                   # all enabled channels
    python -m scripts.ingest_youtube --days 3          # last 3 days
    python -m scripts.ingest_youtube --limit 10        # first 10 channels
    python -m scripts.ingest_youtube --dry-run         # preview, don't write
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

import requests

from scripts.config_loader import (
    get_enabled_sources,
    get_youtube_api_key,
    setup_logging,
)
from scripts.db import get_connection, init_db, insert_candidate

logger = setup_logging("ingest_youtube")

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def _parse_duration_iso8601(iso_dur: str) -> int:
    """Convert ISO 8601 duration (PT1H2M3S) to seconds."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_dur or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _extract_channel_id(url: str, api_key: str) -> str | None:
    """Resolve a YouTube channel URL to a channel ID."""
    # Direct channel ID
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0].split("?")[0]

    # Handle /@handle or /c/name or /user/name
    handle = None
    for prefix in ["/@", "/c/", "/user/"]:
        if prefix in url:
            handle = url.split(prefix)[-1].split("/")[0].split("?")[0]
            break

    if not handle:
        return None

    # Use forHandle parameter for @handles
    if "/@" in url:
        resp = requests.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "id", "forHandle": handle, "key": api_key},
            timeout=15,
        )
        if resp.ok:
            items = resp.json().get("items", [])
            if items:
                return items[0]["id"]

    # Fallback: search for channel by name
    resp = requests.get(
        YOUTUBE_SEARCH_URL,
        params={"part": "id", "q": handle, "type": "channel", "maxResults": 1, "key": api_key},
        timeout=15,
    )
    if resp.ok:
        items = resp.json().get("items", [])
        if items:
            return items[0]["id"]["channelId"]

    return None


def fetch_recent_uploads(channel_id: str, api_key: str, published_after: str,
                         max_results: int = 50) -> list[dict]:
    """Fetch recent video uploads for a channel via YouTube Data API."""
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "order": "date",
        "publishedAfter": published_after,
        "maxResults": min(max_results, 50),
        "key": api_key,
    }
    resp = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=30)
    if not resp.ok:
        logger.warning("YouTube search failed for channel %s: %s", channel_id, resp.text[:200])
        return []

    video_ids = [
        item["id"]["videoId"]
        for item in resp.json().get("items", [])
        if item["id"].get("videoId")
    ]
    if not video_ids:
        return []

    # Fetch video details (duration, etc.)
    details_resp = requests.get(
        YOUTUBE_VIDEOS_URL,
        params={
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        timeout=30,
    )
    if not details_resp.ok:
        logger.warning("YouTube video details failed: %s", details_resp.text[:200])
        return []

    results = []
    for item in details_resp.json().get("items", []):
        snippet = item["snippet"]
        content = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        results.append({
            "video_id": item["id"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "published_at": snippet.get("publishedAt", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "duration_sec": _parse_duration_iso8601(content.get("duration", "")),
            "view_count": int(stats.get("viewCount", 0)),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
        })

    return results


def make_candidate_id(source_id: str, video_id: str) -> str:
    """Generate a deterministic candidate ID."""
    return hashlib.sha256(f"{source_id}:{video_id}".encode()).hexdigest()[:16]


def ingest(days: int = 7, limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the YouTube ingestion pipeline.

    Returns:
        dict with keys: channels_processed, videos_found, candidates_inserted, errors
    """
    api_key = get_youtube_api_key()
    sources = get_enabled_sources(source_type="youtube_channel")
    if limit:
        sources = sources[:limit]

    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {"channels_processed": 0, "videos_found": 0, "candidates_inserted": 0, "errors": 0}

    conn = None
    if not dry_run:
        init_db()
        conn = get_connection()

    for src in sources:
        source_id = src["source_id"]
        url = src["url"]
        logger.info("Processing source: %s (%s)", src["name"], source_id)

        channel_id = _extract_channel_id(url, api_key)
        if not channel_id:
            logger.warning("Could not resolve channel ID for %s (%s)", src["name"], url)
            stats["errors"] += 1
            continue

        try:
            videos = fetch_recent_uploads(channel_id, api_key, published_after)
        except Exception as exc:
            logger.error("Error fetching uploads for %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        stats["channels_processed"] += 1
        stats["videos_found"] += len(videos)

        for v in videos:
            cid = make_candidate_id(source_id, v["video_id"])
            candidate = {
                "candidate_id": cid,
                "source_id": source_id,
                "url": v["url"],
                "platform": "youtube",
                "published_at": v["published_at"],
                "title": v["title"],
                "description": v["description"],
                "duration_sec": v["duration_sec"],
                "quality_signals_json": {
                    "view_count": v["view_count"],
                    "channel": v["channel_title"],
                },
            }

            if dry_run:
                logger.info("[DRY RUN] Would insert candidate: %s — %s", cid, v["title"][:80])
                stats["candidates_inserted"] += 1
            else:
                if insert_candidate(conn, candidate):
                    stats["candidates_inserted"] += 1

    if conn:
        conn.close()

    logger.info(
        "Ingest complete: %d channels, %d videos found, %d inserted, %d errors",
        stats["channels_processed"], stats["videos_found"],
        stats["candidates_inserted"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Ingest YouTube uploads into pipeline DB.")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7).")
    parser.add_argument("--limit", type=int, default=None, help="Max channels to process.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = ingest(days=args.days, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
