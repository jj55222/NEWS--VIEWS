#!/usr/bin/env python3
"""Scrape press release pages and transparency portals for candidate items.

Reads enabled 'webpage' sources from sources_registry.json, scrapes them
for links to press releases / incident reports, and writes NEW candidates.

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

from scripts.config_loader import get_enabled_sources, setup_logging
from scripts.db import get_connection, init_db, insert_candidate

logger = setup_logging("scrape_pages")

# Keywords that suggest a page links to an incident/case press release
INCIDENT_KEYWORDS = re.compile(
    r"(bodycam|body[\s-]?cam|dashcam|dash[\s-]?cam|officer[\s-]?involved|shooting|"
    r"pursuit|chase|critical[\s-]?incident|use[\s-]?of[\s-]?force|arrest|"
    r"homicide|domestic|dui|dwi|welfare[\s-]?check|missing|amber[\s-]?alert|"
    r"press[\s-]?release|media[\s-]?release|briefing|investigation)",
    re.IGNORECASE,
)


def make_candidate_id(source_id: str, link: str) -> str:
    return hashlib.sha256(f"{source_id}:{link}".encode()).hexdigest()[:16]


def scrape_page_links(page_url: str) -> list[dict]:
    """Scrape a page for links that match incident keywords.

    Returns a list of dicts with keys: link, title, snippet.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; FOIAFreePipeline/1.0; "
            "+https://github.com/news-views)"
        )
    }
    try:
        resp = requests.get(page_url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch %s: %s", page_url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    results = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        # Combine link text + nearby text for keyword matching
        parent_text = a_tag.parent.get_text(strip=True) if a_tag.parent else ""
        combined = f"{text} {parent_text}"

        if not INCIDENT_KEYWORDS.search(combined):
            continue

        full_url = urljoin(base_url, href)
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
        dict with keys: pages_processed, links_found, candidates_inserted, errors
    """
    sources = get_enabled_sources(source_type="webpage")
    if limit:
        sources = sources[:limit]

    stats = {"pages_processed": 0, "links_found": 0, "candidates_inserted": 0, "errors": 0}

    conn = None
    if not dry_run:
        init_db()
        conn = get_connection()

    for src in sources:
        source_id = src["source_id"]
        page_url = src["url"]
        logger.info("Scraping page: %s (%s)", src["name"], source_id)

        try:
            links = scrape_page_links(page_url)
        except Exception as exc:
            logger.error("Error scraping %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        stats["pages_processed"] += 1
        stats["links_found"] += len(links)

        for link_info in links:
            cid = make_candidate_id(source_id, link_info["link"])
            candidate = {
                "candidate_id": cid,
                "source_id": source_id,
                "url": link_info["link"],
                "platform": "web",
                "title": link_info["title"],
                "description": link_info["snippet"],
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
        "Page scrape complete: %d pages, %d links, %d inserted, %d errors",
        stats["pages_processed"], stats["links_found"],
        stats["candidates_inserted"], stats["errors"],
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
