#!/usr/bin/env python3
"""Scrape press release pages and transparency portals for candidate items.

Reads enabled 'webpage' sources from sources_registry.json, scrapes them
for links to press releases / incident reports, and writes NEW candidates.
Includes source-health tracking (403 cooldown, 404 dead-url marking) and
an exclude-keywords filter to drop off-topic links.

Usage:
    python -m scripts.scrape_pages                  # all enabled page sources
    python -m scripts.scrape_pages --limit 5        # first 5 sources
    python -m scripts.scrape_pages --dry-run        # preview, don't write
"""

import argparse
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import cloudscraper
    _cloudscraper_available = True
except ImportError:
    _cloudscraper_available = False

from scripts.config_loader import get_enabled_sources, setup_logging
from scripts.db import get_connection, init_db, insert_candidate
from scripts.source_health import (
    is_source_ok,
    record_blocked,
    record_dead_url,
    record_success,
)

logger = setup_logging("scrape_pages")

# Keywords that suggest a page links to an incident/case press release
INCIDENT_KEYWORDS = re.compile(
    r"(bodycam|body[\s-]?cam|dashcam|dash[\s-]?cam|officer[\s-]?involved|shooting|"
    r"pursuit|chase|critical[\s-]?incident|use[\s-]?of[\s-]?force|arrest|"
    r"homicide|domestic|dui|dwi|welfare[\s-]?check|missing|amber[\s-]?alert|"
    r"press[\s-]?release|media[\s-]?release|briefing|investigation)",
    re.IGNORECASE,
)

# Links matching these keywords are off-topic noise — drop them
EXCLUDE_KEYWORDS = re.compile(
    r"(sports?|weather|forecast|stock|finance|entertainment|horoscope|"
    r"lifestyle|recipe|fashion|celebrity|real[\s-]?estate|classifieds|"
    r"advertisement|subscribe|login|sign[\s-]?up|cookie[\s-]?policy|"
    r"privacy[\s-]?policy|terms[\s-]?of[\s-]?service)",
    re.IGNORECASE,
)

# Browser-like headers
_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def make_candidate_id(source_id: str, link: str) -> str:
    return hashlib.sha256(f"{source_id}:{link}".encode()).hexdigest()[:16]


def scrape_page_links(page_url: str, source_id: str | None = None) -> list[dict]:
    """Scrape a page for links that match incident keywords.

    Returns a list of dicts with keys: link, title, snippet.
    On 403/404, records health events and returns [].
    """
    resp = None
    try:
        resp = requests.get(page_url, headers=_PAGE_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(resp, "status_code", None)

        # Record health events when we have a source_id
        if source_id and status == 403:
            record_blocked(source_id, reason=f"403 from {page_url}")
        elif source_id and status == 404:
            record_dead_url(source_id, reason=f"404 from {page_url}")

        # If blocked (403/404) and cloudscraper is available, retry with it
        if _cloudscraper_available and status in (403, 404):
            logger.info("Retrying %s with cloudscraper (got %s)...", page_url, status)
            try:
                scraper = cloudscraper.create_scraper()
                resp = scraper.get(page_url, timeout=30)
                resp.raise_for_status()
            except Exception as retry_exc:
                logger.warning("Failed to fetch %s (cloudscraper retry): %s", page_url, retry_exc)
                return []
        else:
            logger.warning("Failed to fetch %s: %s", page_url, exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        # Combine link text + nearby text for keyword matching
        parent_text = a_tag.parent.get_text(strip=True) if a_tag.parent else ""
        combined = f"{text} {parent_text}"

        if not INCIDENT_KEYWORDS.search(combined):
            continue

        # Exclude off-topic noise
        if EXCLUDE_KEYWORDS.search(combined):
            continue

        # Use page_url as base for safer relative URL resolution
        full_url = urljoin(page_url, href)
        # Skip anchors, javascript, etc.
        if not full_url.startswith("http"):
            continue

        snippet = parent_text[:300] if parent_text else ""
        results.append({
            "link": full_url,
            "title": text[:200] or "Untitled",
            "snippet": snippet,
        })

    # Deduplicate by link
    seen = set()
    deduped = []
    for r in results:
        if r["link"] not in seen:
            seen.add(r["link"])
            deduped.append(r)

    return deduped


def ingest(limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the page-scraping ingestion pipeline.

    Returns:
        dict with keys: pages_processed, links_found, candidates_inserted,
                        skipped_unhealthy, errors
    """
    sources = get_enabled_sources(source_type="webpage")
    if limit:
        sources = sources[:limit]

    stats = {
        "pages_processed": 0,
        "links_found": 0,
        "candidates_inserted": 0,
        "skipped_unhealthy": 0,
        "errors": 0,
    }

    conn = None
    if not dry_run:
        init_db()
        conn = get_connection()

    for src in sources:
        source_id = src["source_id"]
        page_url = src["url"]

        # ── Health gate ──────────────────────────────────────────
        ok, reason = is_source_ok(source_id)
        if not ok:
            logger.info("Skipping %s — %s", source_id, reason)
            stats["skipped_unhealthy"] += 1
            continue

        logger.info("Scraping page: %s (%s)", src["name"], source_id)

        try:
            links = scrape_page_links(page_url, source_id=source_id)
        except Exception as exc:
            logger.error("Error scraping %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        stats["pages_processed"] += 1
        stats["links_found"] += len(links)

        # Record success if we got any links (resets health counters)
        if links:
            record_success(source_id)

        # Routing metadata from the registry entry
        src_class = src.get("source_class", "discovery_only")
        routing_meta = {
            "source_type": "webpage",
            "next_actions_hint": ["TRIAGE"],
        }

        for link_info in links:
            cid = make_candidate_id(source_id, link_info["link"])
            candidate = {
                "candidate_id": cid,
                "source_id": source_id,
                "source_class": src_class,
                "url": link_info["link"],
                "platform": "web",
                "title": link_info["title"],
                "description": link_info["snippet"],
                "quality_signals_json": routing_meta,
            }

            if dry_run:
                logger.info("[DRY RUN] Would insert: %s — %s", cid, link_info["title"][:80])
                stats["candidates_inserted"] += 1
            else:
                if insert_candidate(conn, candidate):
                    stats["candidates_inserted"] += 1

    if conn:
        conn.close()

    logger.info(
        "Page scrape complete: %d pages, %d links, %d inserted, %d skipped, %d errors",
        stats["pages_processed"], stats["links_found"],
        stats["candidates_inserted"], stats["skipped_unhealthy"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Scrape press/transparency pages for candidates.")
    parser.add_argument("--limit", type=int, default=None, help="Max pages to process.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = ingest(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
