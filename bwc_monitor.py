#!/usr/bin/env python3
"""
BWC Monitor: check known public BWC sources for new content.

Polls YouTube channels and scrapes HTML portals from the source registry,
deduplicates against a local seen-URLs file, and outputs new candidates.

Usage:
    python bwc_monitor.py                  # Check all scrapable sources
    python bwc_monitor.py --region CSPD    # Check sources for one region
    python bwc_monitor.py --tier 1         # Check only tier 1 sources
    python bwc_monitor.py --youtube-only   # Check only YouTube channels
    python bwc_monitor.py --portals-only   # Check only HTML portals
    python bwc_monitor.py --check          # Show configured sources
    python bwc_monitor.py --dry-run        # Find new items but don't update seen file
"""

import os
import re
import json
import time
import argparse
import datetime as dt
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import List, Dict, Optional

from bwc_sources import (
    BWC_SOURCES,
    get_scrapable_sources,
    get_sources_by_region,
    get_youtube_sources,
    get_portal_sources,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Where to persist seen URLs between runs
SEEN_FILE = os.getenv("BWC_SEEN_FILE", "./bwc_seen_urls.json")

# Maximum age in days for YouTube uploads to consider
YOUTUBE_MAX_AGE_DAYS = int(os.getenv("BWC_YOUTUBE_MAX_AGE_DAYS", "90"))

# Request timeout for portal scraping
HTTP_TIMEOUT = 15


# =============================================================================
# SEEN-URL PERSISTENCE
# =============================================================================

def _load_seen() -> set:
    """Load previously seen URLs from disk."""
    path = Path(SEEN_FILE)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get("urls", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def _save_seen(urls: set):
    """Persist seen URLs to disk."""
    Path(SEEN_FILE).write_text(json.dumps({
        "updated": dt.datetime.utcnow().isoformat() + "Z",
        "count": len(urls),
        "urls": sorted(urls),
    }, indent=2))


# =============================================================================
# YOUTUBE CHANNEL MONITOR
# =============================================================================

def _resolve_channel_id(handle_or_url: str) -> Optional[str]:
    """Resolve a YouTube handle (@Name) or URL to a channel ID via API."""
    if not YOUTUBE_API_KEY:
        return None

    # If it's already a channel ID (starts with UC), return it
    if handle_or_url.startswith("UC") and len(handle_or_url) == 24:
        return handle_or_url

    # Extract handle from URL like youtube.com/@Handle
    match = re.search(r'@([\w-]+)', handle_or_url)
    if not match:
        # Try /channel/UCXXX format
        match = re.search(r'/channel/(UC[\w-]+)', handle_or_url)
        if match:
            return match.group(1)
        # Try /user/Username format
        match = re.search(r'/user/([\w-]+)', handle_or_url)
        if not match:
            return None

    handle = match.group(1)

    # Use YouTube API to resolve handle to channel ID
    params = urllib.parse.urlencode({
        "key": YOUTUBE_API_KEY,
        "part": "id",
        "forHandle": handle,
    })
    url = f"https://www.googleapis.com/youtube/v3/channels?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            if items:
                return items[0]["id"]
    except Exception:
        pass

    # Fallback: try forUsername
    params = urllib.parse.urlencode({
        "key": YOUTUBE_API_KEY,
        "part": "id",
        "forUsername": handle,
    })
    url = f"https://www.googleapis.com/youtube/v3/channels?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            if items:
                return items[0]["id"]
    except Exception:
        pass

    return None


def _get_uploads_playlist(channel_id: str) -> Optional[str]:
    """Get the uploads playlist ID for a channel."""
    if not YOUTUBE_API_KEY or not channel_id:
        return None

    params = urllib.parse.urlencode({
        "key": YOUTUBE_API_KEY,
        "part": "contentDetails",
        "id": channel_id,
    })
    url = f"https://www.googleapis.com/youtube/v3/channels?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            if items:
                return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception:
        pass
    return None


def _list_recent_uploads(playlist_id: str, max_results: int = 20) -> List[Dict]:
    """List recent videos from an uploads playlist."""
    if not YOUTUBE_API_KEY or not playlist_id:
        return []

    params = urllib.parse.urlencode({
        "key": YOUTUBE_API_KEY,
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": min(max_results, 50),
    })
    url = f"https://www.googleapis.com/youtube/v3/playlistItems?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"      YouTube API error: {e}")
        return []

    cutoff = dt.datetime.utcnow() - dt.timedelta(days=YOUTUBE_MAX_AGE_DAYS)
    results = []

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = snippet.get("resourceId", {}).get("videoId", "")
        if not video_id:
            continue

        published = snippet.get("publishedAt", "")
        try:
            pub_dt = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
            if pub_dt.replace(tzinfo=None) < cutoff:
                continue
        except (ValueError, AttributeError):
            pass

        results.append({
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": snippet.get("title", ""),
            "published": published,
            "channel": snippet.get("channelTitle", ""),
            "description": (snippet.get("description", "") or "")[:500],
        })

    return results


def _is_bwc_video(title: str, description: str) -> bool:
    """Check if a YouTube video is likely BWC/critical incident content."""
    text = (title + " " + description).lower()
    bwc_signals = [
        "body cam", "bodycam", "bwc", "body-worn",
        "critical incident", "officer-involved", "officer involved",
        "ois", "use of force", "deadly force",
        "dashcam", "dash cam",
        "significant event", "community briefing",
        "shooting", "in-custody", "in custody",
    ]
    return any(signal in text for signal in bwc_signals)


def check_youtube_source(source: Dict, seen: set) -> List[Dict]:
    """Check a YouTube channel source for new BWC videos.

    Returns list of new candidate dicts.
    """
    source_id = source["source_id"]
    channel_id = source.get("youtube_channel_id", "")

    # Resolve channel ID if not cached
    if not channel_id:
        channel_id = _resolve_channel_id(source["url"])
        if not channel_id:
            print(f"   [{source_id}] Could not resolve channel ID")
            return []

    # Get uploads playlist
    playlist_id = _get_uploads_playlist(channel_id)
    if not playlist_id:
        print(f"   [{source_id}] Could not get uploads playlist")
        return []

    # List recent uploads
    uploads = _list_recent_uploads(playlist_id)

    candidates = []
    for video in uploads:
        url = video["url"]
        if url in seen:
            continue

        # Filter for BWC-related content
        if not _is_bwc_video(video["title"], video.get("description", "")):
            continue

        candidates.append({
            "url": url,
            "title": video["title"],
            "published": video.get("published", ""),
            "source_id": source_id,
            "agency": source["agency"],
            "region_id": source["region_id"],
            "state": source["state"],
            "discovered_via": "youtube_channel",
        })

    return candidates


# =============================================================================
# HTML PORTAL SCRAPER
# =============================================================================

# Patterns that indicate a link is to a BWC / critical incident page
BWC_LINK_PATTERNS = [
    re.compile(r"critical.incident", re.I),
    re.compile(r"officer.involved", re.I),
    re.compile(r"significant.event", re.I),
    re.compile(r"body.?cam", re.I),
    re.compile(r"bodycam", re.I),
    re.compile(r"bwc", re.I),
    re.compile(r"community.briefing", re.I),
    re.compile(r"use.of.force", re.I),
    re.compile(r"deadly.force", re.I),
    re.compile(r"ois", re.I),
    re.compile(r"shooting", re.I),
    re.compile(r"released.recording", re.I),
    re.compile(r"publicly.released", re.I),
    re.compile(r"incident.video", re.I),
]

# Patterns to exclude (admin pages, generic nav)
EXCLUDE_PATTERNS = [
    re.compile(r"login|sign.in|password|account", re.I),
    re.compile(r"privacy.policy|terms.of.service|cookie", re.I),
    re.compile(r"\.(css|js|png|jpg|gif|svg|ico)$", re.I),
]


def _fetch_html(url: str) -> Optional[str]:
    """Fetch HTML from a URL, following redirects."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (NewsToViews BWC Monitor)",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            # Try utf-8, fall back to latin-1
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
    except Exception as e:
        print(f"      Fetch error: {e}")
        return None


def _extract_links(html: str, base_url: str) -> List[Dict]:
    """Extract links from HTML using regex (no BeautifulSoup dependency).

    Returns list of {url, text} dicts.
    """
    links = []
    # Match <a> tags with href
    pattern = re.compile(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.I | re.DOTALL,
    )

    for match in pattern.finditer(html):
        href = match.group(1).strip()
        text = re.sub(r'<[^>]+>', '', match.group(2)).strip()

        # Skip anchors, javascript, mailto
        if href.startswith(("#", "javascript:", "mailto:")):
            continue

        # Resolve relative URLs
        if href.startswith("/"):
            parsed = urllib.parse.urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            href = urllib.parse.urljoin(base_url, href)

        # Skip excluded patterns
        if any(p.search(href) for p in EXCLUDE_PATTERNS):
            continue

        links.append({"url": href, "text": text})

    return links


def check_portal_source(source: Dict, seen: set) -> List[Dict]:
    """Check an HTML portal source for new BWC-related links.

    Returns list of new candidate dicts.
    """
    source_id = source["source_id"]
    url = source["url"]

    html = _fetch_html(url)
    if not html:
        print(f"   [{source_id}] Failed to fetch portal")
        return []

    links = _extract_links(html, url)
    candidates = []

    for link in links:
        link_url = link["url"]
        link_text = link["text"]

        if link_url in seen:
            continue

        # Check if link text or URL matches BWC patterns
        combined = link_text + " " + link_url
        if not any(p.search(combined) for p in BWC_LINK_PATTERNS):
            continue

        candidates.append({
            "url": link_url,
            "title": link_text,
            "published": "",
            "source_id": source_id,
            "agency": source["agency"],
            "region_id": source["region_id"],
            "state": source["state"],
            "discovered_via": "html_portal",
        })

    return candidates


# =============================================================================
# MAIN MONITOR
# =============================================================================

def run_monitor(
    region: str = None,
    tier: int = None,
    youtube_only: bool = False,
    portals_only: bool = False,
    dry_run: bool = False,
) -> Dict:
    """Run the BWC monitor across configured sources.

    Returns:
        Dict with stats and list of new candidates.
    """
    print("=" * 60)
    print("BWC Monitor: Checking public sources")
    print("=" * 60)

    # Select sources to check
    if region:
        sources = get_sources_by_region(region)
        print(f"[FILTER] Region: {region} ({len(sources)} sources)")
    elif tier:
        sources = [s for s in BWC_SOURCES if s["tier"] == tier]
        print(f"[FILTER] Tier {tier} ({len(sources)} sources)")
    else:
        sources = get_scrapable_sources()
        print(f"[ALL] {len(sources)} scrapable sources")

    if youtube_only:
        sources = [s for s in sources if s["scrape_type"] == "youtube_channel"]
        print(f"[FILTER] YouTube only ({len(sources)} sources)")
    elif portals_only:
        sources = [s for s in sources if s["scrape_type"] == "html_listing"]
        print(f"[FILTER] Portals only ({len(sources)} sources)")

    if not sources:
        print("No sources to check.")
        return {"candidates": [], "checked": 0, "new": 0}

    # Check YouTube API availability
    has_youtube = bool(YOUTUBE_API_KEY)
    if not has_youtube:
        yt_count = sum(1 for s in sources if s["scrape_type"] == "youtube_channel")
        if yt_count > 0:
            print(f"\n   ⚠️  YOUTUBE_API_KEY not set — skipping {yt_count} YouTube sources")

    # Load seen URLs
    seen = _load_seen()
    print(f"[INIT] {len(seen)} previously seen URLs")

    all_candidates = []
    stats = {"checked": 0, "youtube_checked": 0, "portal_checked": 0,
             "new": 0, "skipped_seen": 0, "errors": 0}

    for source in sources:
        source_id = source["source_id"]
        scrape_type = source["scrape_type"]

        if scrape_type == "manual":
            continue

        if scrape_type == "youtube_channel" and not has_youtube:
            continue

        print(f"\n   [{source_id}] {source['agency']} ({scrape_type})")
        stats["checked"] += 1

        try:
            if scrape_type == "youtube_channel":
                candidates = check_youtube_source(source, seen)
                stats["youtube_checked"] += 1
            elif scrape_type == "html_listing":
                candidates = check_portal_source(source, seen)
                stats["portal_checked"] += 1
            else:
                continue

            if candidates:
                print(f"      Found {len(candidates)} new candidate(s)")
                all_candidates.extend(candidates)
            else:
                print(f"      No new items")

        except Exception as e:
            print(f"      Error: {e}")
            stats["errors"] += 1

        time.sleep(0.5)

    stats["new"] = len(all_candidates)

    # Update seen URLs (unless dry run)
    if all_candidates and not dry_run:
        new_urls = {c["url"] for c in all_candidates}
        seen.update(new_urls)
        _save_seen(seen)
        print(f"\n[SAVED] {len(new_urls)} new URLs added to seen file")
    elif dry_run and all_candidates:
        print(f"\n[DRY RUN] Would save {len(all_candidates)} new URLs")

    # Print results
    print("\n" + "=" * 60)
    print("MONITOR COMPLETE")
    print("=" * 60)
    print(f"Sources checked:  {stats['checked']}")
    print(f"  YouTube:        {stats['youtube_checked']}")
    print(f"  Portals:        {stats['portal_checked']}")
    print(f"New candidates:   {stats['new']}")
    print(f"Errors:           {stats['errors']}")

    if all_candidates:
        print(f"\nNew BWC candidates:")
        for c in all_candidates:
            print(f"  [{c['region_id']}] {c['agency']}")
            print(f"    {c['title']}")
            print(f"    {c['url']}")
            if c.get("published"):
                print(f"    Published: {c['published']}")
            print()

    return {"candidates": all_candidates, **stats}


# =============================================================================
# CLI
# =============================================================================

def show_sources():
    """Print all configured BWC sources."""
    print("BWC Source Registry")
    print("=" * 60)

    current_state = ""
    for source in BWC_SOURCES:
        if source["state"] != current_state:
            current_state = source["state"]
            print(f"\n  {current_state}:")

        tier_icon = {1: "★", 2: "◆", 3: "○"}[source["tier"]]
        scrape_icon = {
            "youtube_channel": "▶",
            "html_listing": "🔗",
            "rss": "📡",
            "manual": "✋",
        }.get(source["scrape_type"], "?")

        print(f"    {tier_icon} {scrape_icon} [{source['source_id']}] "
              f"{source['agency']} — {source['scrape_type']}")
        print(f"        {source['url']}")
        if source.get("release_cadence"):
            print(f"        Cadence: {source['release_cadence']}")

    print(f"\nTotal: {len(BWC_SOURCES)} sources")
    print(f"  Tier 1: {sum(1 for s in BWC_SOURCES if s['tier'] == 1)}")
    print(f"  Tier 2: {sum(1 for s in BWC_SOURCES if s['tier'] == 2)}")
    print(f"  Tier 3: {sum(1 for s in BWC_SOURCES if s['tier'] == 3)}")

    scrapable = get_scrapable_sources()
    print(f"  Scrapable: {len(scrapable)} "
          f"(YouTube: {sum(1 for s in scrapable if s['scrape_type'] == 'youtube_channel')}, "
          f"Portals: {sum(1 for s in scrapable if s['scrape_type'] == 'html_listing')})")

    if not YOUTUBE_API_KEY:
        print(f"\n  ⚠️  YOUTUBE_API_KEY not set — YouTube sources will be skipped")


def main():
    parser = argparse.ArgumentParser(description="BWC Monitor")
    parser.add_argument("--region", type=str, help="Check sources for one region")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3],
                        help="Check only sources at this tier")
    parser.add_argument("--youtube-only", action="store_true",
                        help="Check only YouTube channels")
    parser.add_argument("--portals-only", action="store_true",
                        help="Check only HTML portals")
    parser.add_argument("--check", action="store_true",
                        help="Show configured sources and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Find new items but don't update seen file")

    args = parser.parse_args()

    if args.check:
        show_sources()
        return

    run_monitor(
        region=args.region,
        tier=args.tier,
        youtube_only=args.youtube_only,
        portals_only=args.portals_only,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
