#!/usr/bin/env python3
"""
NEWS → VIEWS: YouTube Channel Qualification Pipeline

Auto-discovers YouTube channels publishing raw BWC footage, scores them against
the Channel Qualification Rubric (0-100), and builds a qualified channel registry.

Watermark assessment is MANUAL — channels are flagged for operator review after
auto-discovery. Use --set-watermark to record the manual assessment.

Usage:
    python channel_qualify.py --check              # Verify API credentials
    python channel_qualify.py --seed               # Build registry from your channels + videos
    python channel_qualify.py --discover           # Auto-discover + score channels
    python channel_qualify.py --score CHANNEL_ID   # Score a single channel
    python channel_qualify.py --set-watermark CHANNEL_ID none|small|moderate|heavy
    python channel_qualify.py --list               # List qualified channels
    python channel_qualify.py --sync               # Sync registry to Google Sheets
"""

import os
import re
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

from pipeline_ops import (
    CHANNEL_RUBRIC,
    CHANNEL_DISCOVERY_KEYWORDS,
    CHANNEL_SOURCE_TIERS,
    RAW_FOOTAGE_INDICATORS,
    NON_RAW_INDICATORS,
    CHANNEL_REGISTRY_SCHEMA,
    CHANNEL_REGISTRY_COLUMNS,
    SHEETS_TABS,
    score_channel_criteria,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
SHEET_ID = os.getenv("SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

REGISTRY_PATH = Path("qualified_channels.json")
SAMPLE_SIZE = 20  # Videos to sample per channel


# =============================================================================
# CREDENTIAL CHECK
# =============================================================================

def check_credentials() -> bool:
    """Verify required API keys are available."""
    errors = []
    if not YOUTUBE_API_KEY:
        errors.append("YOUTUBE_API_KEY not set in .env")
    if errors:
        print("Configuration errors:")
        for e in errors:
            print(f"  - {e}")
        return False
    print("YouTube API key found")
    return True


# =============================================================================
# YOUTUBE API HELPERS
# =============================================================================

def _yt_get(url: str, params: dict) -> Optional[dict]:
    """Make a YouTube Data API GET request."""
    import requests

    params["key"] = YOUTUBE_API_KEY
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  [ERROR] YouTube API: {e}")
        return None


def search_youtube(query: str, search_type: str = "video",
                   max_results: int = 10) -> List[dict]:
    """Search YouTube for videos or channels."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/search", {
        "part": "snippet",
        "q": query,
        "type": search_type,
        "maxResults": max_results,
        "relevanceLanguage": "en",
        "safeSearch": "none",
    })
    if not data:
        return []
    return data.get("items", [])


def get_channel_details(channel_id: str) -> Optional[dict]:
    """Fetch channel metadata via YouTube Data API."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/channels", {
        "part": "snippet,statistics,contentDetails",
        "id": channel_id,
    })
    if not data or not data.get("items"):
        return None

    item = data["items"][0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})

    return {
        "channel_id": channel_id,
        "channel_name": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "custom_url": snippet.get("customUrl", ""),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "total_videos": int(stats.get("videoCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "uploads_playlist": content.get("relatedPlaylists", {}).get("uploads", ""),
        "published_at": snippet.get("publishedAt", ""),
    }


def get_playlist_videos(playlist_id: str, max_results: int = 20) -> List[dict]:
    """Fetch recent videos from an uploads playlist."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/playlistItems", {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": max_results,
    })
    if not data:
        return []

    video_ids = [
        item["contentDetails"]["videoId"]
        for item in data.get("items", [])
        if "contentDetails" in item
    ]

    if not video_ids:
        return []

    # Fetch full video details (duration, stats)
    details_data = _yt_get("https://www.googleapis.com/youtube/v3/videos", {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(video_ids),
    })
    if not details_data:
        return []

    results = []
    for item in details_data.get("items", []):
        snippet = item.get("snippet", {})
        cd = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        results.append({
            "video_id": item["id"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "published_at": snippet.get("publishedAt", ""),
            "duration_seconds": _parse_duration(cd.get("duration", "")),
            "view_count": int(stats.get("viewCount", 0)),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
        })

    return results


def _parse_duration(duration: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    if not duration:
        return 0
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def sample_channel_videos(channel_id: str, n: int = SAMPLE_SIZE) -> List[dict]:
    """Get N most recent videos from a channel's uploads playlist."""
    details = get_channel_details(channel_id)
    if not details:
        return []

    uploads_playlist = details.get("uploads_playlist", "")
    if not uploads_playlist:
        return []

    return get_playlist_videos(uploads_playlist, max_results=n)


# =============================================================================
# ASSESSMENT FUNCTIONS
# =============================================================================

def assess_raw_footage_ratio(videos: List[dict]) -> float:
    """
    Calculate % of videos that appear to be raw/minimally-edited BWC footage.
    Uses title + description keyword matching.
    """
    if not videos:
        return 0.0

    raw_count = 0
    for v in videos:
        title = v.get("title", "").lower()
        desc = v.get("description", "").lower()
        text = f"{title} {desc}"

        has_raw_indicator = any(kw in text for kw in RAW_FOOTAGE_INDICATORS)
        has_non_raw_indicator = any(kw in text for kw in NON_RAW_INDICATORS)

        # Count as raw if it has raw indicators and no non-raw indicators
        if has_raw_indicator and not has_non_raw_indicator:
            raw_count += 1

    return raw_count / len(videos)


def assess_upload_frequency(videos: List[dict]) -> float:
    """Calculate average uploads per week from publish dates."""
    if len(videos) < 2:
        return 0.0

    dates = []
    for v in videos:
        pub = v.get("published_at", "")
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                dates.append(dt)
            except ValueError:
                continue

    if len(dates) < 2:
        return 0.0

    dates.sort()
    span_days = (dates[-1] - dates[0]).days
    if span_days == 0:
        return len(dates)  # All published same day

    uploads_per_day = len(dates) / span_days
    return uploads_per_day * 7


def assess_avg_duration(videos: List[dict]) -> float:
    """Calculate average video duration in minutes."""
    durations = [v.get("duration_seconds", 0) for v in videos if v.get("duration_seconds", 0) > 0]
    if not durations:
        return 0.0
    return (sum(durations) / len(durations)) / 60


def classify_source_tier(channel_data: dict) -> str:
    """Classify channel as official_pd / verified_aggregator / known_aggregator / unknown."""
    name = channel_data.get("channel_name", "").lower()
    desc = channel_data.get("description", "").lower()
    text = f"{name} {desc}"

    for tier, config in CHANNEL_SOURCE_TIERS.items():
        if any(indicator in text for indicator in config["indicators"]):
            return tier

    return "unknown"


def classify_jurisdiction(channel_data: dict, videos: List[dict]) -> str:
    """
    Determine jurisdiction type based on channel content.
    Returns: "sunshine" | "known" | "identifiable" | "unclear"
    """
    # Import jurisdiction data
    try:
        from jurisdiction_portals import JURISDICTION_PORTALS
    except ImportError:
        return "unclear"

    # Collect all text for matching
    texts = [
        channel_data.get("channel_name", "").lower(),
        channel_data.get("description", "").lower(),
    ]
    for v in videos[:10]:
        texts.append(v.get("title", "").lower())
        texts.append(v.get("description", "").lower())
    combined = " ".join(texts)

    # Check sunshine states first
    sunshine_keywords = {
        "FL": ["florida", "miami", "tampa", "orlando", "jacksonville", "pinellas", "broward",
               "palm beach", "hillsborough", "fhp", "florida highway"],
        "TX": ["texas", "houston", "dallas", "san antonio", "austin", "fort worth", "harris county"],
        "AZ": ["arizona", "phoenix", "tucson", "mesa", "maricopa", "scottsdale"],
    }
    for state, keywords in sunshine_keywords.items():
        if any(kw in combined for kw in keywords):
            return "sunshine"

    # Check known jurisdictions in registry
    for region_id, config in JURISDICTION_PORTALS.items():
        region_name = config.get("name", "").lower()
        if region_name and region_name in combined:
            return "known"
        for agency in config.get("agencies", []):
            agency_name = agency.get("name", "").lower()
            abbrev = agency.get("abbrev", "").lower()
            if (agency_name and agency_name in combined) or (abbrev and abbrev in combined):
                return "known"

    # Check for generic US law enforcement indicators
    us_indicators = ["police", "sheriff", "department", "county", "city of", "state patrol"]
    if any(ind in combined for ind in us_indicators):
        return "identifiable"

    return "unclear"


def assess_description_quality(videos: List[dict]) -> str:
    """
    Assess quality of video descriptions.
    Returns: "full" | "names_agency" | "basic" | "none"
    """
    if not videos:
        return "none"

    # Check patterns in video descriptions
    name_pattern = re.compile(r'[A-Z][a-z]+ [A-Z][a-z]+')  # Proper names
    date_pattern = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}')
    agency_pattern = re.compile(r'(?:police|sheriff|department|pd|county)\b', re.IGNORECASE)
    case_pattern = re.compile(r'case\s*#?\s*\d+|docket|charge[ds]?\b|incident\s*#', re.IGNORECASE)

    scores = []
    for v in videos[:10]:
        desc = v.get("description", "")
        title = v.get("title", "")
        text = f"{title} {desc}"

        has_names = bool(name_pattern.search(text))
        has_dates = bool(date_pattern.search(text))
        has_agency = bool(agency_pattern.search(text))
        has_case = bool(case_pattern.search(text))

        if has_names and has_agency and (has_dates or has_case):
            scores.append("full")
        elif has_names and has_agency:
            scores.append("names_agency")
        elif has_names or has_agency or has_dates:
            scores.append("basic")
        else:
            scores.append("none")

    # Return the most common quality level
    if scores.count("full") >= len(scores) * 0.4:
        return "full"
    elif scores.count("full") + scores.count("names_agency") >= len(scores) * 0.4:
        return "names_agency"
    elif scores.count("none") < len(scores) * 0.6:
        return "basic"
    return "none"


# =============================================================================
# CHANNEL QUALIFICATION
# =============================================================================

def qualify_channel(channel_id: str) -> dict:
    """
    Full qualification pipeline for a single channel.

    Fetches metadata, samples videos, runs all automated assessments,
    scores against CHANNEL_RUBRIC. Watermark defaults to "unknown" (0 pts)
    until manually set by operator.
    """
    print(f"\n{'='*60}")
    print(f"QUALIFYING CHANNEL: {channel_id}")
    print(f"{'='*60}")

    # Step 1: Get channel details
    print("\n[1/6] Fetching channel details...")
    details = get_channel_details(channel_id)
    if not details:
        print("  ERROR: Could not fetch channel details")
        return {}

    print(f"  Name: {details['channel_name']}")
    print(f"  Subscribers: {details['subscriber_count']:,}")
    print(f"  Videos: {details['total_videos']}")

    # Step 2: Sample recent videos
    print(f"\n[2/6] Sampling {SAMPLE_SIZE} recent videos...")
    videos = sample_channel_videos(channel_id, SAMPLE_SIZE)
    print(f"  Got {len(videos)} videos")

    if not videos:
        print("  ERROR: No videos found")
        return {}

    # Step 3: Run automated assessments
    print("\n[3/6] Assessing raw footage ratio...")
    raw_ratio = assess_raw_footage_ratio(videos)
    print(f"  Raw BWC footage: {raw_ratio:.0%}")

    print("\n[4/6] Assessing upload frequency...")
    uploads_per_week = assess_upload_frequency(videos)
    print(f"  Uploads/week: {uploads_per_week:.1f}")

    avg_dur = assess_avg_duration(videos)
    print(f"  Avg duration: {avg_dur:.1f} min")

    print("\n[5/6] Classifying channel...")
    source_tier = classify_source_tier(details)
    print(f"  Source tier: {source_tier}")

    jurisdiction_type = classify_jurisdiction(details, videos)
    print(f"  Jurisdiction: {jurisdiction_type}")

    desc_quality = assess_description_quality(videos)
    print(f"  Description quality: {desc_quality}")

    # Step 4: Score (watermark = unknown, 0 pts)
    print("\n[6/6] Scoring against rubric...")
    channel_data = {
        "raw_footage_ratio": raw_ratio,
        "watermark_level": "unknown",  # Manual step — defaults to 0 pts
        "uploads_per_week": uploads_per_week,
        "avg_duration_minutes": avg_dur,
        "jurisdiction_type": jurisdiction_type,
        "source_tier": source_tier,
        "description_quality": desc_quality,
    }

    score_result = score_channel_criteria(channel_data)

    # Build registry entry
    entry = {
        "channel_id": channel_id,
        "channel_name": details["channel_name"],
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "subscriber_count": details["subscriber_count"],
        "total_videos": details["total_videos"],
        "last_evaluated": datetime.utcnow().isoformat() + "Z",
        "score": score_result["total_score"],
        "score_without_watermark": score_result["total_score"],
        "classification": score_result["classification"],
        "action": score_result["action"],
        "score_breakdown": {b["name"]: b["awarded"] for b in score_result["breakdown"]},
        "channel_data": channel_data,
        "needs_manual_review": True,  # Watermark not yet assessed
        "sample_videos": [
            {"video_id": v["video_id"], "title": v["title"],
             "duration_seconds": v["duration_seconds"]}
            for v in videos[:5]
        ],
        "notes": "Watermark not yet assessed (0 pts). Review channel and use --set-watermark to update.",
    }

    # Print result
    print(f"\n  SCORE: {score_result['total_score']}/100 (max 80 without watermark assessment)")
    print(f"  Classification: {score_result['classification']}")
    print(f"  Action: {score_result['action']}")
    print(f"\n  Breakdown:")
    for b in score_result["breakdown"]:
        marker = " <-- NEEDS MANUAL REVIEW" if b["name"] == "no_watermark" else ""
        print(f"    {b['name']}: {b['awarded']}/{b['max']}{marker}")

    return entry


def set_watermark(channel_id: str, level: str) -> bool:
    """
    Manually set watermark level for a channel and re-score.

    Args:
        channel_id: YouTube channel ID
        level: "none" | "small" | "moderate" | "heavy"
    """
    valid_levels = {"none", "small", "moderate", "heavy"}
    if level not in valid_levels:
        print(f"Invalid watermark level: {level}. Must be one of: {valid_levels}")
        return False

    registry = load_registry()
    if channel_id not in registry:
        print(f"Channel {channel_id} not in registry. Run --score {channel_id} first.")
        return False

    entry = registry[channel_id]
    entry["channel_data"]["watermark_level"] = level
    entry["needs_manual_review"] = False

    # Re-score with watermark
    score_result = score_channel_criteria(entry["channel_data"])
    entry["score"] = score_result["total_score"]
    entry["classification"] = score_result["classification"]
    entry["action"] = score_result["action"]
    entry["score_breakdown"] = {b["name"]: b["awarded"] for b in score_result["breakdown"]}
    entry["notes"] = f"Watermark assessed: {level}. Full score."
    entry["last_evaluated"] = datetime.utcnow().isoformat() + "Z"

    save_registry(registry)

    print(f"Updated {entry['channel_name']}:")
    print(f"  Watermark: {level}")
    print(f"  New score: {score_result['total_score']}/100")
    print(f"  Classification: {score_result['classification']}")
    return True


# =============================================================================
# SEED REGISTRY (your known channels + videos)
# =============================================================================

# Individual videos — channel IDs will be resolved via YouTube API
SEED_VIDEOS = [
    "rykYVUNbAs0", "rK3EyLmXPzQ", "Cl_xpyMkOTQ", "SciU3RCrTe0",
    "IZlrbGlbNjM", "-yiwunSIu6U", "c9PUhtIDVC0", "rWyXq4RdNu4",
    "d_hWzbF6XOM", "hNwyKEDNSR0", "fBw_K0iltFI", "AHwOMhDtnDs",
    "4htpzGlCXWw", "RXlRIXFjMCw", "89ckVvmtNNw",
]

# Explicit channels (handles + raw UC IDs)
SEED_CHANNELS = [
    {"handle": "JAXSHERIFF", "note": "Jacksonville Sheriff's Office", "expected_type": "official_pd"},
    {"handle": "houstonpolice", "note": "Houston Police", "expected_type": "official_pd"},
    {"handle": "DenverPoliceDept", "note": "Denver Police", "expected_type": "official_pd"},
    {"handle": "AustinPolice", "note": "Austin Police", "expected_type": "official_pd"},
    {"handle": "spdblotter", "note": "Seattle Police", "expected_type": "official_pd"},
    {"channel_id": "UCYa23yHE2e1yJrlYUtyEM2w", "note": "use of force reports"},
    {"channel_id": "UC2T9FKndXhkgHLk-lKOiCrQ", "note": "can be triaged"},
    {"channel_id": "UCWu-Puzkp8hv9eSpnWkNxjA", "note": "can be triaged"},
    {"channel_id": "UCmzeK2lzaBSAQQr2Q0hPArw", "note": "can be triaged"},
]


def _get_channel_from_video(video_id: str) -> dict:
    """Resolve a video ID to its channel info via YouTube API."""
    data = _yt_get("https://www.googleapis.com/youtube/v3/videos", {
        "part": "snippet",
        "id": video_id,
    })
    if not data or not data.get("items"):
        return {}
    snippet = data["items"][0].get("snippet", {})
    return {
        "channel_id": snippet.get("channelId", ""),
        "channel_name": snippet.get("channelTitle", ""),
        "video_title": snippet.get("title", ""),
    }


def _resolve_handle_to_id(handle: str) -> str:
    """Resolve @handle to channel ID via YouTube search API."""
    results = search_youtube(handle, search_type="channel", max_results=1)
    if results:
        return results[0].get("id", {}).get("channelId", "")
    return ""


def run_seed():
    """
    Build the registry from your known channels and videos.
    Resolves video IDs → channel IDs, resolves @handles → channel IDs,
    deduplicates, and creates registry entries. Then runs full qualification.
    """
    if not check_credentials():
        return

    print("=" * 60)
    print("BUILDING REGISTRY FROM YOUR SEED CHANNELS + VIDEOS")
    print("=" * 60)

    channels = {}  # channel_id → {name, note, sample_videos, ...}

    # 1) Resolve channels from your 15 videos
    print(f"\n[1/3] Resolving channels from {len(SEED_VIDEOS)} videos...")
    for i, vid in enumerate(SEED_VIDEOS):
        print(f"  [{i+1}/{len(SEED_VIDEOS)}] Video {vid}...", end=" ")
        info = _get_channel_from_video(vid)
        if info and info.get("channel_id"):
            ch_id = info["channel_id"]
            if ch_id not in channels:
                channels[ch_id] = {
                    "channel_id": ch_id,
                    "channel_name": info.get("channel_name", ""),
                    "note": "",
                    "sample_videos": [],
                }
            channels[ch_id]["sample_videos"].append(vid)
            print(f"→ {info['channel_name']}")
        else:
            print("→ could not resolve (check API key)")

    print(f"\n  Unique channels from videos: {len(channels)}")

    # 2) Add explicit channels
    print(f"\n[2/3] Adding {len(SEED_CHANNELS)} explicit channels...")
    for ch in SEED_CHANNELS:
        ch_id = ch.get("channel_id", "")
        handle = ch.get("handle", "")

        if not ch_id and handle:
            print(f"  Resolving @{handle}...", end=" ")
            ch_id = _resolve_handle_to_id(handle)
            if ch_id:
                print(f"→ {ch_id}")
            else:
                print("→ could not resolve")
                continue

        if ch_id and ch_id not in channels:
            channels[ch_id] = {
                "channel_id": ch_id,
                "channel_name": "",
                "note": ch.get("note", ""),
                "sample_videos": [],
            }
        elif ch_id:
            # merge note
            existing = channels[ch_id].get("note", "")
            new_note = ch.get("note", "")
            if new_note and new_note not in existing:
                channels[ch_id]["note"] = f"{existing}; {new_note}".strip("; ")

    print(f"\n  Total unique channels: {len(channels)}")

    # 3) Qualify each channel and save to registry
    print(f"\n[3/3] Qualifying {len(channels)} channels...")
    registry = load_registry()

    for ch_id, ch_info in channels.items():
        entry = qualify_channel(ch_id)
        if entry:
            entry["notes"] = ch_info.get("note", entry.get("notes", ""))
            entry["discovery_source"] = "user_seed"
            registry[ch_id] = entry
        else:
            # API couldn't fetch details — save placeholder
            registry[ch_id] = {
                "channel_id": ch_id,
                "channel_name": ch_info.get("channel_name", ""),
                "channel_url": f"https://www.youtube.com/channel/{ch_id}",
                "subscriber_count": 0,
                "total_videos": 0,
                "last_evaluated": datetime.utcnow().isoformat() + "Z",
                "score": 0,
                "classification": "PENDING",
                "action": "Re-run when API is available",
                "score_breakdown": {},
                "channel_data": {
                    "raw_footage_ratio": 0.0,
                    "watermark_level": "unknown",
                    "uploads_per_week": 0.0,
                    "avg_duration_minutes": 0.0,
                    "jurisdiction_type": "unclear",
                    "source_tier": "unknown",
                    "description_quality": "none",
                },
                "needs_manual_review": True,
                "sample_videos": ch_info.get("sample_videos", []),
                "notes": ch_info.get("note", ""),
                "discovery_source": "user_seed",
            }
        time.sleep(0.3)

    save_registry(registry)

    # Summary
    scored = [e for e in registry.values() if e.get("score", 0) > 0]
    print(f"\n{'='*60}")
    print("REGISTRY BUILT")
    print(f"{'='*60}")
    print(f"Total channels: {len(registry)}")
    print(f"Scored: {len(scored)}")
    print(f"Needs watermark review: {sum(1 for e in registry.values() if e.get('needs_manual_review', True))}")
    list_channels()


# =============================================================================
# CHANNEL DISCOVERY
# =============================================================================

def discover_channels() -> List[dict]:
    """
    Auto-discover YouTube channels publishing BWC footage.

    Sources:
    1. YouTube search with CHANNEL_DISCOVERY_KEYWORDS (search type = channel)
    2. Agency YouTube channels from jurisdiction_portals.py
    3. TRUE_CRIME_CHANNELS list (bodycam type only)
    """
    print("=" * 60)
    print("CHANNEL DISCOVERY")
    print("=" * 60)

    discovered = {}  # channel_id -> basic info

    # Source 1: YouTube channel search
    print(f"\n[1/3] Searching YouTube for BWC channels ({len(CHANNEL_DISCOVERY_KEYWORDS)} queries)...")
    for i, keyword in enumerate(CHANNEL_DISCOVERY_KEYWORDS):
        print(f"  [{i+1}/{len(CHANNEL_DISCOVERY_KEYWORDS)}] \"{keyword}\"")
        results = search_youtube(keyword, search_type="channel", max_results=5)
        for item in results:
            ch_id = item.get("id", {}).get("channelId") or item.get("snippet", {}).get("channelId", "")
            if ch_id and ch_id not in discovered:
                discovered[ch_id] = {
                    "channel_id": ch_id,
                    "channel_name": item.get("snippet", {}).get("title", ""),
                    "source": "youtube_search",
                    "query": keyword,
                }
        time.sleep(0.3)  # Rate limiting

    print(f"  Found {len(discovered)} unique channels from YouTube search")

    # Source 2: Agency YouTube channels from jurisdiction_portals
    print("\n[2/3] Harvesting agency channels from jurisdiction registry...")
    try:
        from jurisdiction_portals import JURISDICTION_PORTALS, get_agency_youtube_channels
        for region_id in JURISDICTION_PORTALS:
            channels = get_agency_youtube_channels(region_id)
            for ch in channels:
                yt_url = ch.get("youtube", "")
                if yt_url:
                    # Extract channel ID or handle from URL
                    # We store the URL for now — will need channel ID lookup
                    ch_id = _extract_channel_id_from_url(yt_url)
                    if ch_id and ch_id not in discovered:
                        discovered[ch_id] = {
                            "channel_id": ch_id,
                            "channel_name": ch.get("name", ""),
                            "source": "jurisdiction_portal",
                            "region": region_id,
                        }
    except ImportError:
        print("  [WARN] jurisdiction_portals not available")

    print(f"  Total after agency channels: {len(discovered)}")

    # Source 3: TRUE_CRIME_CHANNELS (bodycam type)
    print("\n[3/3] Adding known bodycam channels...")
    try:
        from jurisdiction_portals import TRUE_CRIME_CHANNELS
        for ch in TRUE_CRIME_CHANNELS:
            if ch.get("type") == "bodycam":
                yt_url = ch.get("youtube", "")
                ch_id = _extract_channel_id_from_url(yt_url)
                if ch_id and ch_id not in discovered:
                    discovered[ch_id] = {
                        "channel_id": ch_id,
                        "channel_name": ch.get("name", ""),
                        "source": "known_bodycam_channel",
                    }
    except ImportError:
        pass

    print(f"\n  TOTAL DISCOVERED: {len(discovered)} unique channels")
    return list(discovered.values())


def _extract_channel_id_from_url(url: str) -> str:
    """Extract channel ID from YouTube URL. Returns handle if channel ID not found."""
    if not url:
        return ""

    # Direct channel ID: youtube.com/channel/UCxxxxxx
    match = re.search(r'youtube\.com/channel/(UC[a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)

    # Handle: youtube.com/@Handle
    match = re.search(r'youtube\.com/@([a-zA-Z0-9_-]+)', url)
    if match:
        # We'll need to resolve the handle to a channel ID via API
        return _resolve_handle(match.group(1))

    return ""


def _resolve_handle(handle: str) -> str:
    """Resolve a YouTube handle (@Name) to a channel ID via search."""
    results = search_youtube(handle, search_type="channel", max_results=1)
    if results:
        return results[0].get("id", {}).get("channelId", "")
    return ""


# =============================================================================
# REGISTRY MANAGEMENT
# =============================================================================

def load_registry() -> dict:
    """Load qualified_channels.json. Returns dict keyed by channel_id."""
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH, "r") as f:
            return json.load(f)
    return {}


def save_registry(registry: dict):
    """Save registry to qualified_channels.json."""
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"Registry saved to {REGISTRY_PATH} ({len(registry)} channels)")


def get_qualified_channels(min_score: int = 60) -> List[dict]:
    """Get channels scoring >= min_score from the registry."""
    registry = load_registry()
    return [
        entry for entry in registry.values()
        if entry.get("score", 0) >= min_score
    ]


def get_channels_needing_review() -> List[dict]:
    """Get channels that still need manual watermark review."""
    registry = load_registry()
    return [
        entry for entry in registry.values()
        if entry.get("needs_manual_review", True)
    ]


# =============================================================================
# SHEETS SYNC
# =============================================================================

def sync_to_sheets(registry: dict = None):
    """Push channel registry to Google Sheets 'CHANNEL REGISTRY' tab."""
    if not SHEET_ID:
        print("SHEET_ID not set, skipping Sheets sync")
        return

    if registry is None:
        registry = load_registry()

    if not registry:
        print("No channels in registry to sync")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SHEET_ID)

        tab_name = SHEETS_TABS["channel_registry"]

        # Get or create tab
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=tab_name, rows=100, cols=len(CHANNEL_REGISTRY_COLUMNS))

        # Build rows
        rows = [CHANNEL_REGISTRY_COLUMNS]
        for entry in sorted(registry.values(), key=lambda x: x.get("score", 0), reverse=True):
            cd = entry.get("channel_data", {})
            rows.append([
                entry.get("channel_id", ""),
                entry.get("channel_name", ""),
                entry.get("score", 0),
                entry.get("classification", ""),
                f"{cd.get('raw_footage_ratio', 0):.0%}",
                cd.get("watermark_level", "unknown"),
                f"{cd.get('uploads_per_week', 0):.1f}",
                f"{cd.get('avg_duration_minutes', 0):.0f} min",
                cd.get("jurisdiction_type", ""),
                cd.get("source_tier", ""),
                cd.get("description_quality", ""),
                entry.get("last_evaluated", ""),
                entry.get("notes", ""),
            ])

        ws.clear()
        ws.update(range_name="A1", values=rows)
        print(f"Synced {len(rows) - 1} channels to '{tab_name}' sheet tab")

    except ImportError:
        print("gspread not installed. Run: pip install gspread google-auth")
    except Exception as e:
        print(f"Sheets sync error: {e}")


# =============================================================================
# MAIN WORKFLOWS
# =============================================================================

def run_discovery():
    """Full discovery workflow: discover channels, qualify each, save registry."""
    if not check_credentials():
        return

    discovered = discover_channels()
    if not discovered:
        print("No channels discovered")
        return

    registry = load_registry()
    qualified_count = 0
    skipped_count = 0

    print(f"\n{'='*60}")
    print(f"QUALIFYING {len(discovered)} DISCOVERED CHANNELS")
    print(f"{'='*60}")

    for i, ch_info in enumerate(discovered):
        ch_id = ch_info["channel_id"]

        # Skip if already in registry and recently evaluated
        if ch_id in registry:
            last_eval = registry[ch_id].get("last_evaluated", "")
            if last_eval:
                try:
                    eval_date = datetime.fromisoformat(last_eval.replace("Z", "+00:00"))
                    if datetime.now(eval_date.tzinfo) - eval_date < timedelta(days=7):
                        print(f"\n[{i+1}/{len(discovered)}] SKIPPING {ch_info['channel_name']} (evaluated within 7 days)")
                        skipped_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

        print(f"\n[{i+1}/{len(discovered)}] Processing: {ch_info.get('channel_name', ch_id)}")

        entry = qualify_channel(ch_id)
        if entry:
            entry["discovery_source"] = ch_info.get("source", "unknown")
            registry[ch_id] = entry

            if entry.get("score", 0) >= 40:  # Potential qualifier (before watermark)
                qualified_count += 1

        time.sleep(0.5)  # Rate limiting between channels

    save_registry(registry)

    # Summary
    print(f"\n{'='*60}")
    print("DISCOVERY SUMMARY")
    print(f"{'='*60}")
    print(f"Channels discovered: {len(discovered)}")
    print(f"Channels skipped (recent): {skipped_count}")
    print(f"Channels scoring >=40 (potential w/ watermark): {qualified_count}")
    print(f"Registry total: {len(registry)} channels")

    needs_review = [e for e in registry.values() if e.get("needs_manual_review", True)]
    if needs_review:
        print(f"\n  MANUAL REVIEW NEEDED: {len(needs_review)} channels")
        print("  Review each channel for watermarks, then run:")
        print("    python channel_qualify.py --set-watermark CHANNEL_ID none|small|moderate|heavy")
        print("\n  Channels needing review:")
        for e in sorted(needs_review, key=lambda x: x.get("score", 0), reverse=True):
            print(f"    [{e.get('score', 0):2d}] {e.get('channel_name', '')} ({e.get('channel_id', '')})")
            print(f"        {e.get('channel_url', '')}")


def list_channels(min_score: int = 0):
    """List channels in registry sorted by score."""
    registry = load_registry()
    if not registry:
        print("No channels in registry. Run --discover first.")
        return

    entries = sorted(registry.values(), key=lambda x: x.get("score", 0), reverse=True)
    if min_score > 0:
        entries = [e for e in entries if e.get("score", 0) >= min_score]

    print(f"\n{'Score':>5} {'Classification':<16} {'WM?':<4} {'Channel'}")
    print("-" * 70)
    for e in entries:
        wm = "Y" if not e.get("needs_manual_review", True) else "N"
        print(f"{e.get('score', 0):>5} {e.get('classification', ''):.<16} {wm:<4} {e.get('channel_name', '')}")

    print(f"\nTotal: {len(entries)} channels")

    needs_review = [e for e in entries if e.get("needs_manual_review", True)]
    if needs_review:
        print(f"Pending manual watermark review: {len(needs_review)}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="YouTube Channel Qualification Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --check                          # Verify credentials
    %(prog)s --seed                           # Build registry from your channels + videos
    %(prog)s --discover                       # Auto-discover + score channels
    %(prog)s --score UC1234...                # Score single channel
    %(prog)s --set-watermark UC1234... none   # Set watermark after review
    %(prog)s --list                           # List all qualified channels
    %(prog)s --sync                           # Sync to Google Sheets
        """
    )

    parser.add_argument("--check", action="store_true", help="Check credentials")
    parser.add_argument("--seed", action="store_true",
                        help="Build registry from your known channels + videos")
    parser.add_argument("--discover", action="store_true", help="Auto-discover + qualify channels")
    parser.add_argument("--score", metavar="CHANNEL_ID", help="Score a single channel")
    parser.add_argument("--set-watermark", nargs=2, metavar=("CHANNEL_ID", "LEVEL"),
                        help="Set watermark level (none|small|moderate|heavy)")
    parser.add_argument("--list", action="store_true", help="List channels in registry")
    parser.add_argument("--sync", action="store_true", help="Sync registry to Google Sheets")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score filter for --list")

    args = parser.parse_args()

    if args.check:
        check_credentials()
    elif args.seed:
        run_seed()
    elif args.discover:
        run_discovery()
    elif args.score:
        if not check_credentials():
            return
        entry = qualify_channel(args.score)
        if entry:
            registry = load_registry()
            registry[args.score] = entry
            save_registry(registry)
    elif args.set_watermark:
        set_watermark(args.set_watermark[0], args.set_watermark[1])
    elif args.list:
        list_channels(min_score=args.min_score)
    elif args.sync:
        sync_to_sheets()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
