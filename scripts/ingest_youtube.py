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
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from scripts.config_loader import (
    get_enabled_sources,
    get_env,
    setup_logging,
)
from scripts.db import get_connection, init_db, insert_candidate

logger = setup_logging("ingest_youtube")

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_RSS_URL = "https://www.youtube.com/feeds/videos.xml"


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


def _extract_channel_id_from_url(url: str) -> str | None:
    """Extract channel ID from URL without API (for RSS fallback)."""
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0].split("?")[0]
    return None


def _resolve_handle_to_channel_id(url: str) -> str | None:
    """Resolve a @handle or /c/ URL to a channel ID by scraping the page."""
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FOIAFreePipeline/2.0)"
        })
        if not resp.ok:
            return None
        # Look for channel ID in page meta or canonical
        m = re.search(r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]+)"', resp.text)
        if m:
            return m.group(1)
        m = re.search(r'/channel/(UC[a-zA-Z0-9_-]+)', resp.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def fetch_uploads_via_rss(channel_id: str, published_after: str) -> list[dict]:
    """Fetch recent uploads via YouTube RSS feed (no API key needed)."""
    feed_url = f"{YOUTUBE_RSS_URL}?channel_id={channel_id}"
    try:
        resp = requests.get(feed_url, timeout=15)
        if not resp.ok:
            logger.warning("RSS fetch failed for channel %s: HTTP %d", channel_id, resp.status_code)
            return []
    except requests.RequestException as exc:
        logger.warning("RSS fetch error for channel %s: %s", channel_id, exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        logger.warning("RSS XML parse failed for channel %s", channel_id)
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }

    cutoff = datetime.fromisoformat(published_after.replace("Z", "+00:00"))
    results = []

    for entry in root.findall("atom:entry", ns):
        video_id_el = entry.find("yt:videoId", ns)
        if video_id_el is None:
            continue
        video_id = video_id_el.text

        title_el = entry.find("atom:title", ns)
        title = title_el.text if title_el is not None else ""

        published_el = entry.find("atom:published", ns)
        published = published_el.text if published_el is not None else ""

        # Filter by date
        if published:
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if pub_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Get description from media:group/media:description
        media_group = entry.find("media:group", ns)
        description = ""
        if media_group is not None:
            desc_el = media_group.find("media:description", ns)
            if desc_el is not None:
                description = desc_el.text or ""

        channel_el = entry.find("atom:author/atom:name", ns)
        channel_title = channel_el.text if channel_el is not None else ""

        results.append({
            "video_id": video_id,
            "title": title,
            "description": description,
            "published_at": published,
            "channel_title": channel_title,
            "duration_sec": 0,  # Not available in RSS
            "view_count": 0,    # Not available in RSS
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return results


def make_candidate_id(source_id: str, video_id: str) -> str:
    """Generate a deterministic candidate ID."""
    return hashlib.sha256(f"{source_id}:{video_id}".encode()).hexdigest()[:16]


def ingest(days: int = 7, limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the YouTube ingestion pipeline.

    Uses YouTube Data API v3 if YOUTUBE_API_KEY is set, otherwise falls back
    to YouTube RSS feeds (no API key required, but no duration/view data).

    Returns:
        dict with keys: channels_processed, videos_found, candidates_inserted, errors, method
    """
    api_key = get_env("YOUTUBE_API_KEY")
    use_api = bool(api_key)
    method = "api" if use_api else "rss"

    if not use_api:
        logger.info("YOUTUBE_API_KEY not set — using RSS fallback (no duration/view data).")

    sources = get_enabled_sources(source_type="youtube_channel")
    if limit:
        sources = sources[:limit]

    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {"channels_processed": 0, "videos_found": 0, "candidates_inserted": 0,
             "errors": 0, "method": method}

    conn = None
    if not dry_run:
        init_db()
        conn = get_connection()

    for src in sources:
        source_id = src["source_id"]
        url = src["url"]
        source_class = src.get("source_class", "secondary")
        logger.info("Processing source: %s (%s) [%s]", src["name"], source_id, method)

        # Resolve channel ID
        if use_api:
            channel_id = _extract_channel_id(url, api_key)
        else:
            channel_id = _extract_channel_id_from_url(url)
            if not channel_id:
                channel_id = _resolve_handle_to_channel_id(url)

        if not channel_id:
            logger.warning("Could not resolve channel ID for %s (%s)", src["name"], url)
            stats["errors"] += 1
            continue

        # Fetch videos
        try:
            if use_api:
                videos = fetch_recent_uploads(channel_id, api_key, published_after)
            else:
                videos = fetch_uploads_via_rss(channel_id, published_after)
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
                "source_class": source_class,
                "url": v["url"],
                "platform": "youtube",
                "published_at": v["published_at"],
                "title": v["title"],
                "description": v["description"],
                "duration_sec": v["duration_sec"],
                "quality_signals_json": {
                    "view_count": v["view_count"],
                    "channel": v["channel_title"],
                    "ingest_method": method,
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
        "Ingest complete (%s): %d channels, %d videos found, %d inserted, %d errors",
        method, stats["channels_processed"], stats["videos_found"],
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
