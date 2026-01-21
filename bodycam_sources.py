#!/usr/bin/env python3
"""
Credit-Efficient Bodycam Source Pipeline

Prioritizes FREE sources before falling back to paid Exa:

Source                              Cost        Reliability
-----------------------------------------------------------------
Police dept YouTube channels        FREE        High
YouTube (Police Activity, etc)      FREE        High
State transparency portals (FL,TX)  FREE        High
Local news video embeds             FREE        Medium
Exa search                          $$$         Medium

Pipeline:
    Case from news intake
            |
    [FREE] Pre-filter: Is jurisdiction sunshine state? Post-2015?
            |
    [FREE] YouTube Data API: "{defendant} bodycam" "{defendant} interrogation"
            |
        Found? --> HIGH CONFIDENCE (done, no Exa needed)
        Not found? |
            |
    [FREE] Scrape jurisdiction portal (if FL/TX/etc)
            |
        Found? --> HIGH CONFIDENCE
        Not found? |
            |
    [CHEAP] Single targeted Exa query (only for promising cases)

This can cut Exa usage by 80-90%.

Usage:
    python bodycam_sources.py --defendant "John Doe" --jurisdiction "FL"
    python bodycam_sources.py --check-youtube  # Verify YouTube API key
    python bodycam_sources.py --test           # Run test searches
"""

import os
import re
import json
import time
import argparse
import requests
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote_plus
from dataclasses import dataclass, field, asdict
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
EXA_API_KEY = os.getenv("EXA_API_KEY")

# Sunshine states with strong public records laws
SUNSHINE_STATES = {
    "FL": {"name": "Florida", "foia_strength": "excellent", "bodycam_law": True},
    "TX": {"name": "Texas", "foia_strength": "good", "bodycam_law": True},
    "AZ": {"name": "Arizona", "foia_strength": "good", "bodycam_law": True},
    "CA": {"name": "California", "foia_strength": "good", "bodycam_law": True},
    "WA": {"name": "Washington", "foia_strength": "good", "bodycam_law": True},
    "CO": {"name": "Colorado", "foia_strength": "good", "bodycam_law": True},
}

# YouTube channels known for bodycam/interrogation content
BODYCAM_CHANNELS = {
    "Police Activity": "UCXMYxKMh3prxnM_4kYZuB3g",
    "Real World Police": "UCvKx83gxKuHn4N5i9oLaWbw",
    "Bodycam Watch": "UC1-M3e7v9pspGv1Z9X5xSOw",
    "Law&Crime Network": "UCz8K1occVvDTYDfFo7N5EZw",
    "Court TV": "UC0HCL2RlnUr8Y6dT41HXHPg",
}

INTERROGATION_CHANNELS = {
    "JCS - Criminal Psychology": "UCYwVxWpjeKFWwu8TML-Te9A",
    "Matt Orchard": "UC0v-e2O-hLfVzLx7gMxotZA",
    "Dreading": "UCoxRlpH0GbqwPiGK6_yPi_Q",
    "Explore With Us": "UC_3vJ9Ev9E0YTFe7UMJrq4Q",
}

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class VideoResult:
    """Represents a found video source."""
    url: str
    title: str
    channel: str
    duration_seconds: int = 0
    view_count: int = 0
    published_date: str = ""
    description: str = ""
    source_type: str = ""  # youtube_api, portal, scrape, exa
    confidence: str = "medium"  # high, medium, low
    query_used: str = ""

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60.0

    @property
    def is_full_footage(self) -> bool:
        """Full bodycam/interrogation is typically 10+ minutes."""
        return self.duration_seconds >= 600

    @property
    def is_clip(self) -> bool:
        """Short clips are under 5 minutes."""
        return self.duration_seconds < 300

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    """Aggregate search results across all sources."""
    defendant: str
    jurisdiction: str
    bodycam: List[VideoResult] = field(default_factory=list)
    interrogation: List[VideoResult] = field(default_factory=list)
    court: List[VideoResult] = field(default_factory=list)
    news: List[VideoResult] = field(default_factory=list)
    sources_tried: List[str] = field(default_factory=list)
    exa_queries_used: int = 0
    youtube_queries_used: int = 0
    total_cost_estimate: float = 0.0

    @property
    def has_bodycam(self) -> bool:
        return len(self.bodycam) > 0

    @property
    def has_interrogation(self) -> bool:
        return len(self.interrogation) > 0

    @property
    def has_full_bodycam(self) -> bool:
        return any(v.is_full_footage for v in self.bodycam)

    @property
    def has_full_interrogation(self) -> bool:
        return any(v.is_full_footage for v in self.interrogation)

    @property
    def confidence_level(self) -> str:
        """Overall confidence in video availability."""
        if self.has_full_bodycam or self.has_full_interrogation:
            return "HIGH"
        if self.has_bodycam or self.has_interrogation:
            return "MEDIUM"
        if self.court or self.news:
            return "LOW"
        return "NONE"

    def to_dict(self) -> dict:
        return {
            "defendant": self.defendant,
            "jurisdiction": self.jurisdiction,
            "bodycam": [v.to_dict() for v in self.bodycam],
            "interrogation": [v.to_dict() for v in self.interrogation],
            "court": [v.to_dict() for v in self.court],
            "news": [v.to_dict() for v in self.news],
            "sources_tried": self.sources_tried,
            "exa_queries_used": self.exa_queries_used,
            "youtube_queries_used": self.youtube_queries_used,
            "confidence_level": self.confidence_level,
        }


# =============================================================================
# PRE-FILTERS
# =============================================================================

def is_sunshine_state(state_code: str) -> bool:
    """Check if state has strong public records laws."""
    return state_code.upper() in SUNSHINE_STATES


def get_state_from_jurisdiction(jurisdiction: str) -> Optional[str]:
    """Extract state code from jurisdiction string."""
    if not jurisdiction:
        return None

    # Common patterns: "Miami, FL", "Miami-Dade County, Florida", "Houston, Texas"
    state_map = {
        "florida": "FL", "fl": "FL",
        "texas": "TX", "tx": "TX",
        "arizona": "AZ", "az": "AZ",
        "california": "CA", "ca": "CA",
        "washington": "WA", "wa": "WA",
        "colorado": "CO", "co": "CO",
    }

    jurisdiction_lower = jurisdiction.lower()

    # Check for state names or codes
    for name, code in state_map.items():
        if name in jurisdiction_lower:
            return code
        if f", {name}" in jurisdiction_lower or jurisdiction_lower.endswith(f" {name}"):
            return code

    # Check for 2-letter state code at end
    parts = jurisdiction.split(",")
    if len(parts) >= 2:
        last_part = parts[-1].strip().upper()
        if len(last_part) == 2 and last_part in SUNSHINE_STATES:
            return last_part

    return None


def is_post_2015(incident_year: str) -> bool:
    """Check if incident is recent enough to likely have bodycam footage."""
    if not incident_year:
        return True  # Assume recent if unknown
    try:
        year = int(str(incident_year)[:4])
        return year >= 2015
    except (ValueError, TypeError):
        return True


def calculate_bodycam_likelihood(jurisdiction: str, incident_year: str) -> Dict:
    """Pre-calculate likelihood of finding bodycam footage."""
    state = get_state_from_jurisdiction(jurisdiction)
    is_sunshine = is_sunshine_state(state) if state else False
    is_recent = is_post_2015(incident_year)

    likelihood = "low"
    if is_sunshine and is_recent:
        likelihood = "high"
    elif is_sunshine or is_recent:
        likelihood = "medium"

    return {
        "state": state,
        "is_sunshine_state": is_sunshine,
        "is_post_2015": is_recent,
        "likelihood": likelihood,
        "recommendation": "skip_exa" if likelihood == "high" else "try_free_first",
    }


# =============================================================================
# YOUTUBE DATA API
# =============================================================================

def parse_youtube_duration(duration: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    if not duration:
        return 0

    # Remove PT prefix
    duration = duration.replace("PT", "")

    hours = minutes = seconds = 0

    if "H" in duration:
        hours_part, duration = duration.split("H")
        hours = int(hours_part)
    if "M" in duration:
        minutes_part, duration = duration.split("M")
        minutes = int(minutes_part)
    if "S" in duration:
        seconds_part = duration.replace("S", "")
        seconds = int(seconds_part)

    return hours * 3600 + minutes * 60 + seconds


def youtube_search(query: str, max_results: int = 10) -> List[Dict]:
    """
    Search YouTube using the Data API.

    FREE tier: 10,000 queries/day
    Each search costs ~100 quota units, so effectively ~100 searches/day free.
    """
    if not YOUTUBE_API_KEY:
        print("    [WARN] No YOUTUBE_API_KEY set, skipping YouTube search")
        return []

    search_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": YOUTUBE_API_KEY,
        "relevanceLanguage": "en",
        "safeSearch": "none",
    }

    try:
        response = requests.get(search_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        video_ids = [item["id"]["videoId"] for item in data.get("items", [])]

        if not video_ids:
            return []

        # Get video details (duration, view count)
        details_url = "https://www.googleapis.com/youtube/v3/videos"
        details_params = {
            "part": "contentDetails,statistics,snippet",
            "id": ",".join(video_ids),
            "key": YOUTUBE_API_KEY,
        }

        details_response = requests.get(details_url, params=details_params, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()

        results = []
        for item in details_data.get("items", []):
            video_id = item["id"]
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            statistics = item.get("statistics", {})

            results.append({
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration_seconds": parse_youtube_duration(content_details.get("duration", "")),
                "view_count": int(statistics.get("viewCount", 0)),
            })

        return results

    except requests.RequestException as e:
        print(f"    [ERROR] YouTube API error: {e}")
        return []


def search_youtube_channels(defendant: str, channel_ids: Dict[str, str],
                           max_per_channel: int = 5) -> List[Dict]:
    """Search specific YouTube channels for defendant name."""
    if not YOUTUBE_API_KEY:
        return []

    results = []

    for channel_name, channel_id in channel_ids.items():
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": defendant,
            "channelId": channel_id,
            "type": "video",
            "maxResults": max_per_channel,
            "key": YOUTUBE_API_KEY,
        }

        try:
            response = requests.get(search_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            for item in data.get("items", []):
                video_id = item["id"]["videoId"]
                snippet = item.get("snippet", {})
                results.append({
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": snippet.get("title", ""),
                    "channel": channel_name,
                    "description": snippet.get("description", ""),
                    "published_at": snippet.get("publishedAt", ""),
                })

            time.sleep(0.2)  # Rate limiting

        except requests.RequestException as e:
            print(f"    [WARN] YouTube channel search error ({channel_name}): {e}")
            continue

    return results


def youtube_bodycam_search(defendant: str, jurisdiction: str = "") -> List[VideoResult]:
    """
    Comprehensive YouTube search for bodycam footage.

    Queries used (each ~100 quota units):
    1. "{defendant} bodycam"
    2. "{defendant} body camera"
    3. "{defendant} police footage"
    4. Channel-specific searches on Police Activity, Real World Police, etc.
    """
    results = []
    queries_used = 0

    # Build search queries
    queries = [
        f'"{defendant}" bodycam',
        f'"{defendant}" body camera police',
        f'"{defendant}" police footage',
    ]

    if jurisdiction:
        queries.append(f'"{defendant}" {jurisdiction} police')

    # General YouTube search
    for query in queries[:3]:  # Limit to 3 general queries
        print(f"    [YouTube] Searching: {query}")
        videos = youtube_search(query, max_results=5)
        queries_used += 1

        for v in videos:
            # Check if title/description mentions bodycam/police footage
            text = f"{v.get('title', '')} {v.get('description', '')}".lower()
            if any(kw in text for kw in ["bodycam", "body cam", "body camera", "police footage", "dashcam", "dash cam"]):
                results.append(VideoResult(
                    url=v["url"],
                    title=v.get("title", ""),
                    channel=v.get("channel", ""),
                    duration_seconds=v.get("duration_seconds", 0),
                    view_count=v.get("view_count", 0),
                    published_date=v.get("published_at", ""),
                    description=v.get("description", "")[:500],
                    source_type="youtube_api",
                    confidence="high" if v.get("duration_seconds", 0) > 600 else "medium",
                    query_used=query,
                ))

        time.sleep(0.3)

    # Search known bodycam channels
    print(f"    [YouTube] Searching bodycam channels...")
    channel_results = search_youtube_channels(defendant, BODYCAM_CHANNELS, max_per_channel=3)
    queries_used += len(BODYCAM_CHANNELS)

    for v in channel_results:
        results.append(VideoResult(
            url=v["url"],
            title=v.get("title", ""),
            channel=v.get("channel", ""),
            published_date=v.get("published_at", ""),
            description=v.get("description", "")[:500],
            source_type="youtube_channel",
            confidence="high",  # Known bodycam channel = high confidence
            query_used=f"channel:{v.get('channel', '')}",
        ))

    return results, queries_used


def youtube_interrogation_search(defendant: str) -> Tuple[List[VideoResult], int]:
    """
    Search YouTube for interrogation footage.
    """
    results = []
    queries_used = 0

    queries = [
        f'"{defendant}" interrogation',
        f'"{defendant}" police interview',
        f'"{defendant}" confession',
    ]

    for query in queries[:2]:  # Limit queries
        print(f"    [YouTube] Searching: {query}")
        videos = youtube_search(query, max_results=5)
        queries_used += 1

        for v in videos:
            text = f"{v.get('title', '')} {v.get('description', '')}".lower()
            if any(kw in text for kw in ["interrogation", "interview", "confession", "questioning"]):
                results.append(VideoResult(
                    url=v["url"],
                    title=v.get("title", ""),
                    channel=v.get("channel", ""),
                    duration_seconds=v.get("duration_seconds", 0),
                    view_count=v.get("view_count", 0),
                    published_date=v.get("published_at", ""),
                    description=v.get("description", "")[:500],
                    source_type="youtube_api",
                    confidence="high" if v.get("duration_seconds", 0) > 1200 else "medium",
                    query_used=query,
                ))

        time.sleep(0.3)

    # Search interrogation-focused channels
    print(f"    [YouTube] Searching interrogation channels...")
    channel_results = search_youtube_channels(defendant, INTERROGATION_CHANNELS, max_per_channel=3)
    queries_used += len(INTERROGATION_CHANNELS)

    for v in channel_results:
        results.append(VideoResult(
            url=v["url"],
            title=v.get("title", ""),
            channel=v.get("channel", ""),
            published_date=v.get("published_at", ""),
            description=v.get("description", "")[:500],
            source_type="youtube_channel",
            confidence="high",
            query_used=f"channel:{v.get('channel', '')}",
        ))

    return results, queries_used


# =============================================================================
# PORTAL SCRAPERS
# =============================================================================

def scrape_florida_portal(defendant: str, county: str = "") -> List[VideoResult]:
    """
    Scrape Florida public records portals.

    Florida's Sunshine Law makes bodycam footage highly accessible.
    Key sources:
    - Sheriff office websites
    - Clerk of Court records
    - Local news embeds
    """
    results = []

    # Florida sheriff YouTube channels (free to search via API)
    fl_sheriff_channels = {
        "Broward Sheriff": "UCwM2bWqU1UGXU7aXKPnS0cQ",
        "Orange County Sheriff FL": "UC1ux_ZvN0VqVoYvzIZA5qsQ",
        "Jacksonville Sheriff": "UCt1Pc6w-TmVaU12P8R3N0_A",
    }

    if YOUTUBE_API_KEY:
        channel_results = search_youtube_channels(defendant, fl_sheriff_channels, max_per_channel=3)
        for v in channel_results:
            results.append(VideoResult(
                url=v["url"],
                title=v.get("title", ""),
                channel=v.get("channel", ""),
                description=v.get("description", "")[:500],
                source_type="florida_portal",
                confidence="high",
                query_used=f"FL sheriff channel: {v.get('channel', '')}",
            ))

    # Note: Actual portal scraping would require Selenium/Playwright
    # For now, we build targeted search queries for portals
    portal_queries = []

    if county:
        portal_queries.append(f"site:sheriff.org {defendant} bodycam {county}")
        portal_queries.append(f"site:clerk.org {defendant} video {county}")
    else:
        portal_queries.append(f"site:sheriff.org {defendant} bodycam Florida")

    # These can be used with Exa if YouTube doesn't find results
    return results, portal_queries


def scrape_texas_portal(defendant: str, city: str = "") -> List[VideoResult]:
    """
    Scrape Texas public records portals.

    Texas has strong public records laws.
    Key sources:
    - Police department video releases
    - DPS records
    - Local news
    """
    results = []

    # Texas PD YouTube channels
    tx_pd_channels = {
        "Austin Police": "UCaVfqbZG8_fU8a_c6a0x_Ug",
        "Houston Police": "UCZ9IzMnPAY1cV-7n6U9xRAg",
    }

    if YOUTUBE_API_KEY:
        channel_results = search_youtube_channels(defendant, tx_pd_channels, max_per_channel=3)
        for v in channel_results:
            results.append(VideoResult(
                url=v["url"],
                title=v.get("title", ""),
                channel=v.get("channel", ""),
                description=v.get("description", "")[:500],
                source_type="texas_portal",
                confidence="high",
                query_used=f"TX PD channel: {v.get('channel', '')}",
            ))

    portal_queries = [
        f"site:austintexas.gov {defendant} video",
        f"site:houstontx.gov {defendant} video",
    ]

    if city:
        portal_queries.append(f"{defendant} bodycam {city} Texas police")

    return results, portal_queries


def scrape_jurisdiction_portal(state: str, defendant: str,
                               locality: str = "") -> Tuple[List[VideoResult], List[str]]:
    """Route to appropriate state portal scraper."""
    if state == "FL":
        return scrape_florida_portal(defendant, locality)
    elif state == "TX":
        return scrape_texas_portal(defendant, locality)
    else:
        return [], []


# =============================================================================
# EXA SEARCH (FALLBACK)
# =============================================================================

def exa_targeted_search(exa_client, query: str, include_domains: List[str] = None,
                        num_results: int = 5) -> List[VideoResult]:
    """
    Single targeted Exa query - use sparingly!

    Cost: ~$0.01-0.05 per query depending on plan
    """
    results = []

    try:
        search_params = {
            "query": query,
            "num_results": num_results,
            "type": "auto",
        }

        if include_domains:
            search_params["include_domains"] = include_domains

        search_results = exa_client.search(**search_params)

        for r in search_results.results:
            results.append(VideoResult(
                url=r.url,
                title=getattr(r, 'title', ''),
                channel="",
                source_type="exa",
                confidence="medium",
                query_used=query,
            ))

    except Exception as e:
        print(f"    [ERROR] Exa search failed: {e}")

    return results


# =============================================================================
# SMART ROUTER
# =============================================================================

class BodycamSourceRouter:
    """
    Smart router that prioritizes free sources before paid Exa.

    Pipeline:
    1. Pre-filter (sunshine state + post-2015)
    2. YouTube Data API search
    3. Portal scrapers (if sunshine state)
    4. Exa (only if needed and promising)
    """

    def __init__(self, exa_client=None):
        self.exa_client = exa_client
        self.stats = {
            "youtube_queries": 0,
            "portal_queries": 0,
            "exa_queries": 0,
            "cases_resolved_free": 0,
            "cases_needed_exa": 0,
        }

    def search(self, defendant: str, jurisdiction: str,
               incident_year: str = "", force_exa: bool = False) -> SearchResult:
        """
        Execute credit-efficient search pipeline.

        Returns SearchResult with videos found and metadata about search cost.
        """
        result = SearchResult(defendant=defendant, jurisdiction=jurisdiction)

        defendant = defendant.split(",")[0].strip() if defendant else ""
        if not defendant:
            print("    [SKIP] No defendant name")
            return result

        # Step 1: Pre-filter
        print(f"\n[SEARCH] {defendant}")
        print(f"    Jurisdiction: {jurisdiction}")

        likelihood = calculate_bodycam_likelihood(jurisdiction, incident_year)
        state = likelihood["state"]

        print(f"    State: {state or 'Unknown'}")
        print(f"    Sunshine state: {likelihood['is_sunshine_state']}")
        print(f"    Post-2015: {likelihood['is_post_2015']}")
        print(f"    Likelihood: {likelihood['likelihood'].upper()}")

        # Step 2: YouTube API search (FREE)
        print("\n    [STAGE 1] YouTube Data API (FREE)")

        bodycam_results, bc_queries = youtube_bodycam_search(defendant, jurisdiction)
        result.bodycam.extend(bodycam_results)
        result.youtube_queries_used += bc_queries
        self.stats["youtube_queries"] += bc_queries

        interrogation_results, int_queries = youtube_interrogation_search(defendant)
        result.interrogation.extend(interrogation_results)
        result.youtube_queries_used += int_queries
        self.stats["youtube_queries"] += int_queries

        result.sources_tried.append("youtube_api")

        print(f"    Found: {len(result.bodycam)} bodycam, {len(result.interrogation)} interrogation")

        # Check if we have enough
        if result.has_full_bodycam or result.has_full_interrogation:
            print("    [DONE] High-confidence results from YouTube - skipping paid sources")
            self.stats["cases_resolved_free"] += 1
            return result

        # Step 3: Portal scrapers (FREE for sunshine states)
        if state and likelihood["is_sunshine_state"]:
            print(f"\n    [STAGE 2] {SUNSHINE_STATES[state]['name']} Portal Search (FREE)")

            portal_results, portal_queries = scrape_jurisdiction_portal(
                state, defendant, jurisdiction
            )
            result.bodycam.extend(portal_results)
            result.sources_tried.append(f"{state.lower()}_portal")
            self.stats["portal_queries"] += 1

            print(f"    Found: {len(portal_results)} from portals")

            if result.has_bodycam or result.has_interrogation:
                print("    [DONE] Found results from free sources")
                self.stats["cases_resolved_free"] += 1
                return result

        # Step 4: Exa fallback (PAID - use sparingly)
        if force_exa or (self.exa_client and likelihood["likelihood"] != "low"):
            print("\n    [STAGE 3] Exa Search (PAID - targeted)")

            if not self.exa_client:
                print("    [SKIP] No Exa client configured")
            else:
                # Single targeted query
                exa_query = f"{defendant} bodycam police video"
                print(f"    Query: {exa_query}")

                exa_results = exa_targeted_search(
                    self.exa_client,
                    exa_query,
                    include_domains=["youtube.com", "facebook.com", "twitter.com"],
                    num_results=5
                )

                result.bodycam.extend(exa_results)
                result.exa_queries_used += 1
                self.stats["exa_queries"] += 1
                result.sources_tried.append("exa")

                print(f"    Found: {len(exa_results)} from Exa")

                if exa_results:
                    self.stats["cases_needed_exa"] += 1
        else:
            print("\n    [SKIP] Skipping Exa (low likelihood or no client)")
            self.stats["cases_resolved_free"] += 1

        return result

    def get_stats(self) -> Dict:
        """Get usage statistics."""
        total_cases = self.stats["cases_resolved_free"] + self.stats["cases_needed_exa"]
        exa_savings = 0
        if total_cases > 0:
            exa_savings = (self.stats["cases_resolved_free"] / total_cases) * 100

        return {
            **self.stats,
            "total_cases": total_cases,
            "exa_savings_percent": round(exa_savings, 1),
        }

    def print_stats(self):
        """Print usage statistics."""
        stats = self.get_stats()
        print("\n" + "=" * 50)
        print("SEARCH STATISTICS")
        print("=" * 50)
        print(f"YouTube queries:      {stats['youtube_queries']}")
        print(f"Portal queries:       {stats['portal_queries']}")
        print(f"Exa queries:          {stats['exa_queries']}")
        print(f"Cases resolved FREE:  {stats['cases_resolved_free']}")
        print(f"Cases needed Exa:     {stats['cases_needed_exa']}")
        print(f"Exa savings:          {stats['exa_savings_percent']}%")


# =============================================================================
# INTEGRATION WITH ARTIFACT HUNTER
# =============================================================================

def smart_artifact_search(defendant: str, jurisdiction: str,
                          incident_year: str = "", region_id: str = "",
                          exa_client=None) -> Dict:
    """
    Drop-in replacement for artifact_hunter's search_artifacts function.

    Returns same structure but uses credit-efficient pipeline.
    """
    router = BodycamSourceRouter(exa_client=exa_client)
    result = router.search(defendant, jurisdiction, incident_year)

    # Convert to artifact_hunter format
    return {
        "body_cam": [v.to_dict() for v in result.bodycam],
        "interrogation": [v.to_dict() for v in result.interrogation],
        "court": [v.to_dict() for v in result.court],
        "other": [],
        "portal": [],
        "reddit": [],
        "pacer": [],
        "_meta": {
            "youtube_queries": result.youtube_queries_used,
            "exa_queries": result.exa_queries_used,
            "sources_tried": result.sources_tried,
            "confidence": result.confidence_level,
        }
    }


# =============================================================================
# CLI
# =============================================================================

def check_youtube_api():
    """Verify YouTube API key is configured and working."""
    if not YOUTUBE_API_KEY:
        print("YOUTUBE_API_KEY not set in environment")
        print("Get a free API key at: https://console.developers.google.com/")
        return False

    # Test query
    print("Testing YouTube API...")
    results = youtube_search("police bodycam", max_results=1)

    if results:
        print(f"YouTube API working")
        print(f"Test result: {results[0].get('title', 'N/A')}")
        return True
    else:
        print("YouTube API test failed - check your API key")
        return False


def run_test_searches():
    """Run test searches to demonstrate the pipeline."""
    test_cases = [
        {"defendant": "Derek Chauvin", "jurisdiction": "Minneapolis, MN", "year": "2020"},
        {"defendant": "Alex Murdaugh", "jurisdiction": "Colleton County, SC", "year": "2021"},
        {"defendant": "Nikolas Cruz", "jurisdiction": "Broward County, FL", "year": "2018"},
    ]

    router = BodycamSourceRouter()

    for case in test_cases:
        print("\n" + "=" * 60)
        result = router.search(
            case["defendant"],
            case["jurisdiction"],
            case["year"]
        )
        print(f"\nConfidence: {result.confidence_level}")
        print(f"Bodycam found: {len(result.bodycam)}")
        print(f"Interrogation found: {len(result.interrogation)}")

        if result.bodycam:
            print("\nTop bodycam results:")
            for v in result.bodycam[:3]:
                duration_str = f"{v.duration_minutes:.1f}min" if v.duration_seconds else "?"
                print(f"  - [{duration_str}] {v.title[:60]}")
                print(f"    {v.url}")

    router.print_stats()


def main():
    parser = argparse.ArgumentParser(
        description="Credit-efficient bodycam source search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --defendant "John Doe" --jurisdiction "Miami, FL"
    %(prog)s --check-youtube
    %(prog)s --test
        """
    )

    parser.add_argument("--defendant", "-d", help="Defendant name to search")
    parser.add_argument("--jurisdiction", "-j", help="Jurisdiction (city, state)")
    parser.add_argument("--year", "-y", help="Incident year")
    parser.add_argument("--check-youtube", action="store_true", help="Check YouTube API key")
    parser.add_argument("--test", action="store_true", help="Run test searches")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.check_youtube:
        check_youtube_api()
        return

    if args.test:
        run_test_searches()
        return

    if args.defendant:
        router = BodycamSourceRouter()
        result = router.search(
            args.defendant,
            args.jurisdiction or "",
            args.year or ""
        )

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"\nConfidence: {result.confidence_level}")
            print(f"Bodycam: {len(result.bodycam)}")
            print(f"Interrogation: {len(result.interrogation)}")

            if result.bodycam:
                print("\nBodycam results:")
                for v in result.bodycam[:5]:
                    print(f"  - {v.title}")
                    print(f"    {v.url}")

            if result.interrogation:
                print("\nInterrogation results:")
                for v in result.interrogation[:5]:
                    print(f"  - {v.title}")
                    print(f"    {v.url}")

            router.print_stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
