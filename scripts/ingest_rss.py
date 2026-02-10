#!/usr/bin/env python3
"""Ingest candidates from RSS feeds listed in sources_registry.json.

Reads enabled RSS sources, fetches recent entries, and writes NEW
candidates to the pipeline database.

Usage:
    python -m scripts.ingest_rss                    # all enabled RSS feeds
    python -m scripts.ingest_rss --days 3           # last 3 days only
    python -m scripts.ingest_rss --limit 10         # first 10 feeds
    python -m scripts.ingest_rss --dry-run          # preview, don't write
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser

from scripts.config_loader import get_enabled_sources, setup_logging
from scripts.db import get_connection, init_db, insert_candidate

logger = setup_logging("ingest_rss")


def _parse_rss_date(date_str: str | None) -> str | None:
    """Try to parse an RSS date string to ISO 8601."""
    if not date_str:
        return None
    try:
        parsed = feedparser.datetimes._parse_date(date_str)
        if parsed:
            from time import mktime
            dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return date_str


def _clean_html(text: str | None) -> str:
    """Strip HTML tags from a string."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def make_candidate_id(source_id: str, entry_link: str) -> str:
    """Generate a deterministic candidate ID from source + entry URL."""
    return hashlib.sha256(f"{source_id}:{entry_link}".encode()).hexdigest()[:16]


def fetch_rss_entries(feed_url: str, days: int = 7) -> list[dict]:
    """Fetch and parse RSS entries from a feed URL."""
    try:
        feed = feedparser.parse(feed_url)
    except Exception as exc:
        logger.error("Failed to parse feed %s: %s", feed_url, exc)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("Feed returned no entries (bozo): %s", feed_url)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = []

    for entry in feed.entries:
        pub_date = _parse_rss_date(entry.get("published") or entry.get("updated"))
        # Filter by date if parseable
        if pub_date:
            try:
                entry_dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                if entry_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        link = entry.get("link", "")
        if not link:
            continue

        title = entry.get("title", "")
        summary = _clean_html(entry.get("summary") or entry.get("description", ""))

        results.append({
            "link": link,
            "title": title,
            "description": summary,
            "published_at": pub_date,
        })

    return results


def ingest(days: int = 7, limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the RSS ingestion pipeline.

    Returns:
        dict with keys: feeds_processed, entries_found, candidates_inserted, errors
    """
    sources = get_enabled_sources(source_type="rss")
    if limit:
        sources = sources[:limit]

    stats = {"feeds_processed": 0, "entries_found": 0, "candidates_inserted": 0, "errors": 0}

    conn = None
    if not dry_run:
        init_db()
        conn = get_connection()

    for src in sources:
        source_id = src["source_id"]
        feed_url = src["url"]
        logger.info("Processing RSS feed: %s (%s)", src["name"], source_id)

        try:
            entries = fetch_rss_entries(feed_url, days=days)
        except Exception as exc:
            logger.error("Error fetching RSS %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        stats["feeds_processed"] += 1
        stats["entries_found"] += len(entries)

        for entry in entries:
            cid = make_candidate_id(source_id, entry["link"])
            candidate = {
                "candidate_id": cid,
                "source_id": source_id,
                "url": entry["link"],
                "platform": "rss",
                "published_at": entry["published_at"],
                "title": entry["title"],
                "description": entry["description"],
            }

            if dry_run:
                logger.info("[DRY RUN] Would insert: %s — %s", cid, entry["title"][:80])
                stats["candidates_inserted"] += 1
            else:
                if insert_candidate(conn, candidate):
                    stats["candidates_inserted"] += 1

    if conn:
        conn.close()

    logger.info(
        "RSS ingest complete: %d feeds, %d entries, %d inserted, %d errors",
        stats["feeds_processed"], stats["entries_found"],
        stats["candidates_inserted"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Ingest RSS feed entries into pipeline DB.")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7).")
    parser.add_argument("--limit", type=int, default=None, help="Max feeds to process.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = ingest(days=args.days, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
