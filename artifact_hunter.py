#!/usr/bin/env python3
"""
NEWS → VIEWS: Artifact Hunter v2.0
Enhanced artifact discovery with scoring, ground truth benchmarks, and expanded sources

Usage:
    python artifact_hunter.py              # Process all unassessed cases
    python artifact_hunter.py --limit 5    # Process max 5 cases
    python artifact_hunter.py --check      # Check credentials only
    python artifact_hunter.py --benchmark  # Run ground truth benchmark tests
    python artifact_hunter.py --model gpt-4o  # Use specific model
"""

import os
import re
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

from jurisdiction_portals import (
    build_jurisdiction_queries,
    extract_domain,
    get_agency_youtube_channels,
    get_search_domains_for_region,
    get_transparency_portals,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

SHEET_ID = os.getenv("SHEET_ID")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v3.2")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

# =============================================================================
# MULTI-MODEL PROVIDER FRAMEWORK
# =============================================================================

# Supported providers and their models
LLM_PROVIDERS = {
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
        "models": [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-haiku",
            "google/gemini-pro-1.5",
            "meta-llama/llama-3.1-70b-instruct",
        ],
        "headers": {
            "HTTP-Referer": "https://newstoviews.app",
            "X-Title": "NewsToViews-ArtifactHunter-v2",
        }
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "models": [
            "deepseek-chat",         # DeepSeek-V3 (general purpose)
            "deepseek-reasoner",     # DeepSeek-R1 (reasoning)
        ],
        "headers": {}
    }
}

def get_provider_from_model(model_spec: str) -> tuple:
    """
    Parse model specification and return (provider, model).

    Formats supported:
    - "deepseek:deepseek-chat" -> ("deepseek", "deepseek-chat")
    - "openrouter:anthropic/claude-3.5-sonnet" -> ("openrouter", "anthropic/claude-3.5-sonnet")
    - "deepseek-chat" -> ("deepseek", "deepseek-chat")  # Auto-detect
    - "anthropic/claude-3.5-sonnet" -> ("openrouter", "anthropic/claude-3.5-sonnet")  # Default
    """
    if ":" in model_spec and model_spec.split(":")[0] in LLM_PROVIDERS:
        parts = model_spec.split(":", 1)
        return (parts[0], parts[1])

    # Auto-detect provider from model name
    if model_spec.startswith("deepseek"):
        return ("deepseek", model_spec)

    # Default to OpenRouter for slash-format models
    return ("openrouter", model_spec)

# =============================================================================
# EXPANDED VIDEO PLATFORMS
# =============================================================================

# Primary video platforms
VIDEO_PLATFORMS = [
    "youtube.com", "youtu.be", "vimeo.com",
    "dailymotion.com", "rumble.com", "odysee.com",
]

# Archive and document platforms
ARCHIVE_PLATFORMS = [
    "archive.org", "scribd.com", "documentcloud.org",
    "courtlistener.com", "ia601.us.archive.org",
]

# All video sources combined
ALL_VIDEO_SOURCES = VIDEO_PLATFORMS + ["facebook.com", "twitter.com", "x.com"]

# =============================================================================
# GROUND TRUTH BENCHMARK CASES
# =============================================================================

GROUND_TRUTH_CASES = {
    "chris_watts": {
        "defendant": "Chris Watts",
        "jurisdiction": "Frederick, Weld County, Colorado",
        "region_id": "DPD",  # Colorado
        "incident_year": "2018",
        "expected_artifacts": {
            "interrogation": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "10+ hours released by Weld County DA, includes polygraph failure"
            },
            "bodycam": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "200+ hours bodycam released - initial welfare check, neighbor surveillance"
            },
            "court": {
                "exists": "YES",
                "quality": "LIMITED",
                "notes": "Plea deal, no trial"
            },
            "discovery": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "3TB of evidence released - 2000+ pages docs, 650 photos, 211 hours video"
            },
            "call_911": {
                "exists": "YES",
                "quality": "GOOD",
                "notes": "Neighbor's 911 call available"
            }
        },
        "known_sources": [
            "weldda.com/news_room/information_on_watts_case",
            "documentcloud.org/documents/5219206-Christopher-Watts-REDACTED-FINAL",
        ],
        "overall": "ENOUGH"
    },
    "jennifer_pan": {
        "defendant": "Jennifer Pan",
        "jurisdiction": "Markham, York Region, Ontario, Canada",
        "region_id": "CAN_ON",  # Canadian Ontario
        "incident_year": "2010",
        "expected_artifacts": {
            "interrogation": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "3 interrogations: Nov 8 (witness), Nov 10 (recreation), Nov 22 (confrontation/confession)"
            },
            "bodycam": {
                "exists": "NO",
                "quality": "N/A",
                "notes": "Not applicable to this case"
            },
            "court": {
                "exists": "YES",
                "quality": "LIMITED",
                "notes": "Canadian court restrictions - limited video, CBC news coverage"
            },
            "call_911": {
                "exists": "YES",
                "quality": "GOOD",
                "notes": "Full 911 call played at trial, available in Netflix doc and YouTube"
            }
        },
        "known_sources": [
            "cbc.ca/news/canada/toronto/jennifer-pan-interrogation-video",
            "torontolife.com/city/jennifer-pan-revenge/",
        ],
        "overall": "ENOUGH"
    },
    "stephanie_lazarus": {
        "defendant": "Stephanie Lazarus",
        "jurisdiction": "Van Nuys, Los Angeles, California",
        "region_id": "LC",  # Los Angeles County
        "incident_year": "1986",  # Crime date, arrest 2009
        "expected_artifacts": {
            "interrogation": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "Hidden camera interrogation - lured under pretense of art theft consultation"
            },
            "bodycam": {
                "exists": "NO",
                "quality": "N/A",
                "notes": "1986 crime predates bodycams"
            },
            "court": {
                "exists": "YES",
                "quality": "GOOD",
                "notes": "Trial footage available, covered by local news"
            }
        },
        "known_sources": [
            "abcnews.go.com",  # transcripts and video of interrogation
            "nbclosangeles.com/news/local/stephanie-lazarus/",
        ],
        "overall": "ENOUGH"
    },
    "nikolas_cruz": {
        "defendant": "Nikolas Cruz",
        "jurisdiction": "Parkland, Broward County, Florida",
        "region_id": "BC",  # Broward County
        "incident_year": "2018",
        "expected_artifacts": {
            "interrogation": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "12+ hours released by Broward State Attorney - includes brother visit"
            },
            "bodycam": {
                "exists": "YES",
                "quality": "GOOD",
                "notes": "Arrest bodycam released, school surveillance footage at trial"
            },
            "court": {
                "exists": "YES",
                "quality": "EXCELLENT",
                "notes": "Full trial livestreamed by Law&Crime Network, extensive YouTube archive"
            },
            "surveillance": {
                "exists": "YES",
                "quality": "GOOD",
                "notes": "School surveillance shown during trial"
            }
        },
        "known_sources": [
            "sun-sentinel.com",  # full 10-hour interrogation video
            "lawandcrime.com/live-trials/nikolas-cruz/",
            "cbsnews.com/miami",
        ],
        "overall": "ENOUGH"
    }
}

# =============================================================================
# ARTIFACT QUALITY SCORING (from rubric)
# =============================================================================

QUALITY_SCORES = {
    "EXCELLENT": 5,  # Direct link to full, unedited primary footage
    "GOOD": 4,       # Direct link to substantial footage (edited but extensive)
    "ADEQUATE": 3,   # Link to short clips or screenshot-heavy coverage
    "MARGINAL": 2,   # Article describes footage but no video accessible
    "POOR": 1,       # Generic case coverage without footage reference
    "FAIL": 0,       # Hallucinated source or dead link
    "N/A": -1,       # Not applicable
}

PRECISION_THRESHOLDS = {
    "EXCELLENT": 0.90,  # >90% valid sources
    "GOOD": 0.75,       # 75-90%
    "MARGINAL": 0.50,   # 50-75%
    "POOR": 0.0,        # <50%
}

COVERAGE_LEVELS = {
    "COMPLETE": 1.0,     # Found all major sources + additional
    "SUBSTANTIAL": 0.75, # Found 75%+ of ground truth
    "PARTIAL": 0.50,     # Found 50-75%
    "MINIMAL": 0.0,      # Found <50%
}

# =============================================================================
# VALIDATION
# =============================================================================

def check_credentials() -> bool:
    errors = []
    if not SHEET_ID:
        errors.append("SHEET_ID not set")
    if not EXA_API_KEY:
        errors.append("EXA_API_KEY not set")
    if not OPENROUTER_API_KEY:
        errors.append("OPENROUTER_API_KEY not set")
    if not Path(SERVICE_ACCOUNT_PATH).exists():
        errors.append(f"Service account not found: {SERVICE_ACCOUNT_PATH}")

    if errors:
        print("❌ Configuration errors:")
        for e in errors:
            print(f"   - {e}")
        return False

    print("✅ Credentials OK")
    return True

# =============================================================================
# CLIENT INITIALIZATION
# =============================================================================

def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
    return gspread.authorize(creds)


def get_exa_client():
    from exa_py import Exa
    return Exa(api_key=EXA_API_KEY)


def get_llm_client(provider: str = "openrouter"):
    """
    Get LLM client for specified provider.

    Args:
        provider: "openrouter" or "deepseek"
    """
    from openai import OpenAI

    provider_config = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["openrouter"])

    if provider == "deepseek":
        api_key = DEEPSEEK_API_KEY
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not set in environment")
    else:
        api_key = OPENROUTER_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")

    return OpenAI(
        api_key=api_key,
        base_url=provider_config["base_url"]
    )


def get_multi_llm_clients(providers: List[str] = None) -> Dict:
    """
    Get multiple LLM clients for ensemble/comparison mode.

    Args:
        providers: List of providers to initialize (default: all available)

    Returns:
        Dict mapping provider name to (client, default_model) tuple
    """
    clients = {}

    if providers is None:
        providers = []
        if OPENROUTER_API_KEY:
            providers.append("openrouter")
        if DEEPSEEK_API_KEY:
            providers.append("deepseek")

    for provider in providers:
        try:
            client = get_llm_client(provider)
            default_model = LLM_PROVIDERS[provider]["default_model"]
            clients[provider] = (client, default_model)
            print(f"  ✅ {LLM_PROVIDERS[provider]['name']} initialized (model: {default_model})")
        except Exception as e:
            print(f"  ⚠️ {provider}: {e}")

    return clients

# =============================================================================
# ARTIFACT SEARCH - EXPANDED
# =============================================================================

def extract_subreddit(url: str) -> str:
    """Extract subreddit name from a Reddit URL."""
    if not url:
        return ""
    match = re.search(r"reddit\.com/r/([^/]+)", url)
    return match.group(1) if match else ""


def check_for_video_links(text: str) -> bool:
    """Check if text mentions video platforms."""
    if not text:
        return False
    platforms = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "facebook.com")
    return any(platform in text.lower() for platform in platforms)


def search_reddit_cases(exa, defendant: str, jurisdiction: str) -> Dict:
    """Search Reddit true crime communities for case discussion."""
    results = {"discussions": [], "ama": [], "updates": []}

    queries = [
        f"site:reddit.com {defendant} case",
        f"site:reddit.com {jurisdiction} murder {defendant}",
    ]

    for query in queries:
        try:
            search_results = exa.search(query=query, num_results=10)
        except Exception as e:
            print(f"      Reddit search error: {e}")
            continue

        for r in search_results.results:
            post_data = {
                "url": r.url,
                "title": getattr(r, "title", ""),
                "subreddit": extract_subreddit(r.url),
                "has_video_links": check_for_video_links(getattr(r, "text", "")),
                "upvotes": None,
            }
            results["discussions"].append(post_data)

    return results


def search_pacer(exa, defendant: str, jurisdiction: str, case_type: str = "cr") -> Dict:
    """Search federal court records via CourtListener (free PACER data)."""
    query = f"site:courtlistener.com {defendant} {jurisdiction} {case_type}"
    case_data = {
        "case_number": "",
        "court": "",
        "judge": "",
        "filing_date": "",
        "docket_entries": [],
        "has_transcripts": False,
        "has_exhibits": False,
        "sources": [],
    }

    try:
        results = exa.search(query=query, num_results=10)
    except Exception as e:
        print(f"      PACER search error: {e}")
        return case_data

    for r in results.results:
        case_data["sources"].append({
            "url": r.url,
            "title": getattr(r, "title", ""),
            "score": getattr(r, "score", 0),
        })

    return case_data


def search_artifacts(exa, defendant: str, jurisdiction: str,
                     crime_type: str = "", custom_queries: List[str] = None,
                     region_id: str = None, incident_year: str = None) -> Dict:
    """Search for video artifacts."""
    results = {
        "body_cam": [],
        "interrogation": [],
        "court": [],
        "other": [],
        "portal": [],
        "reddit": [],
        "pacer": [],
    }
    
    defendant = defendant.split(",")[0].strip() if defendant else ""
    jurisdiction = str(jurisdiction).strip() if jurisdiction else ""

    if not defendant and not jurisdiction:
        return results
    
    video_domains = [
        "youtube.com", "vimeo.com", "youtu.be", "facebook.com", "twitter.com"
    ]
    queries = []
    
    # Body cam
    if jurisdiction:
        queries.append(("body_cam", f"{jurisdiction} police body camera footage", video_domains))
        queries.append(("body_cam", f"{jurisdiction} bodycam video incident", video_domains))
    
    # Interrogation
    if defendant:
        queries.append(("interrogation", f"{defendant} interrogation video police interview", video_domains))
        queries.append(("interrogation", f"{defendant} confession interview recording", video_domains))
    
    # Court
    if defendant:
        queries.append(("court", f"{defendant} court video trial sentencing", video_domains))
    
    # Custom queries
    for q in (custom_queries or [])[:3]:
        queries.append(("other", q, video_domains))

    # Jurisdiction-aware queries
    if region_id:
        jurisdiction_queries = build_jurisdiction_queries(region_id, defendant, incident_year)
        region_domains = get_search_domains_for_region(region_id)
        for q in jurisdiction_queries.get("bodycam", []):
            queries.append(("body_cam", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("interrogation", []):
            queries.append(("interrogation", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("court", []):
            queries.append(("court", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("news", []):
            queries.append(("portal", q, region_domains))

        for channel in get_agency_youtube_channels(region_id)[:3]:
            queries.append((
                "body_cam",
                f"{defendant} site:youtube.com {channel.get('name', '')}",
                ["youtube.com"],
            ))

        for portal in get_transparency_portals(region_id):
            domain = extract_domain(portal.get("url", ""))
            if domain:
                portal_query = f"site:{domain} {defendant} video"
                queries.append(("portal", portal_query, [domain]))
    
    # Execute searches
    for qtype, query, include_domains in queries:
        try:
            search_results = exa.search(
                query=query,
                type="auto",
                use_autoprompt=True,
                num_results=5,
                include_domains=include_domains,
            )
            
            for r in search_results.results:
                results[qtype].append({
                    "url": r.url,
                    "title": getattr(r, 'title', ''),
                    "score": getattr(r, 'score', 0),
                    "query": query,
                    "source_type": _classify_source(r.url),
                    "platform": _get_platform(r.url)
                })

            time.sleep(0.25)

        except Exception as e:
            print(f"      Search error: {e}")

    if defendant or jurisdiction:
        reddit_results = search_reddit_cases(exa, defendant, jurisdiction)
        results["reddit"] = reddit_results.get("discussions", [])

        pacer_results = search_pacer(exa, defendant, jurisdiction)
        results["pacer"] = pacer_results.get("sources", [])
    
    return results


def _build_comprehensive_queries(defendant: str, jurisdiction: str,
                                   incident_year: str, victim_name: str,
                                   agency: str, region_id: str,
                                   custom_queries: List[str]) -> List[tuple]:
    """Build comprehensive queries following the research methodology."""
    queries = []

    year_suffix = f" {incident_year}" if incident_year else ""
    agency_str = agency or ""

    # Get jurisdiction config if available
    config = {}
    if HAS_JURISDICTION_DATA and region_id:
        config = get_jurisdiction_config(region_id) or {}
        agencies = config.get("agencies", [])
        if agencies and not agency_str:
            agency_str = agencies[0].get("abbrev", agencies[0].get("name", ""))

    # ==========================================================================
    # 1. INTERROGATION (Highest Priority for JCS-style content)
    # ==========================================================================
    queries.append(("interrogation",
        f"{defendant} interrogation video full",
        VIDEO_PLATFORMS))
    queries.append(("interrogation",
        f"{defendant} police interview confession",
        VIDEO_PLATFORMS))
    queries.append(("interrogation",
        f"{defendant} interrogation{year_suffix}",
        VIDEO_PLATFORMS))
    # Search true crime channels
    queries.append(("interrogation",
        f"{defendant} JCS Criminal Psychology OR Matt Orchard OR Dreading",
        ["youtube.com"]))

    # ==========================================================================
    # 2. OFFICIAL SOURCES (DA/District Attorney releases)
    # ==========================================================================
    if jurisdiction:
        # Extract county from jurisdiction
        county = ""
        if "county" in jurisdiction.lower():
            parts = jurisdiction.lower().split("county")
            if parts:
                county = parts[0].strip().split(",")[-1].strip().title()

        if county:
            queries.append(("discovery_docs",
                f"{county} County DA {defendant} release OR discovery",
                ARCHIVE_PLATFORMS + ["youtube.com"]))
            queries.append(("discovery_docs",
                f"{county} District Attorney {defendant} evidence",
                None))

    # ==========================================================================
    # 3. BODYCAM SEARCHES
    # ==========================================================================
    if agency_str:
        queries.append(("bodycam",
            f"{agency_str} bodycam {defendant}{year_suffix}",
            VIDEO_PLATFORMS))
        queries.append(("bodycam",
            f"{defendant} body camera {agency_str} footage",
            VIDEO_PLATFORMS))

    if jurisdiction:
        queries.append(("bodycam",
            f"{jurisdiction} police body camera {defendant}",
            VIDEO_PLATFORMS))

    # Florida Sunshine Law - stronger public records
    if HAS_JURISDICTION_DATA and region_id and is_florida_case(region_id):
        queries.append(("bodycam",
            f"{defendant} Florida bodycam released public records",
            VIDEO_PLATFORMS))

    # Search bodycam channels specifically
    queries.append(("bodycam",
        f"{defendant} Police Activity OR Real World Police bodycam",
        ["youtube.com"]))

    # ==========================================================================
    # 4. 911 CALLS
    # ==========================================================================
    queries.append(("call_911",
        f"{defendant} 911 call audio",
        VIDEO_PLATFORMS + ["soundcloud.com"]))
    if victim_name:
        queries.append(("call_911",
            f"{victim_name} 911 call audio",
            VIDEO_PLATFORMS))
    queries.append(("call_911",
        f"{defendant} emergency call recording",
        VIDEO_PLATFORMS))

    # ==========================================================================
    # 5. COURT VIDEO
    # ==========================================================================
    queries.append(("court",
        f"{defendant} trial video",
        VIDEO_PLATFORMS))
    queries.append(("court",
        f"{defendant} sentencing hearing verdict",
        VIDEO_PLATFORMS))
    queries.append(("court",
        f"Law Crime Network {defendant} OR Court TV {defendant}",
        ["youtube.com"]))

    # Check if jurisdiction has court video capability
    if HAS_JURISDICTION_DATA and region_id and has_court_video(region_id):
        state = config.get("state", "")
        if state:
            queries.append(("court",
                f"{defendant} {state} trial court video",
                VIDEO_PLATFORMS))

    # ==========================================================================
    # 6. DISCOVERY DOCUMENTS
    # ==========================================================================
    queries.append(("discovery_docs",
        f"{defendant} discovery documents release",
        ARCHIVE_PLATFORMS))
    queries.append(("discovery_docs",
        f"{defendant} court documents evidence",
        ["documentcloud.org", "courtlistener.com", "scribd.com"]))
    queries.append(("discovery_docs",
        f"{defendant} case file evidence release",
        ARCHIVE_PLATFORMS))

    # ==========================================================================
    # 7. NEWS COVERAGE (Local outlets)
    # ==========================================================================
    news_domains = config.get("search_domains", []) if config else []
    if news_domains:
        queries.append(("news",
            f"{defendant} arrest charged video",
            news_domains[:4]))
    else:
        queries.append(("news",
            f"{defendant} arrest news video footage",
            None))

    # ==========================================================================
    # 8. TRUE CRIME COVERAGE CHECK (Competition analysis)
    # ==========================================================================
    queries.append(("true_crime_coverage",
        f"{defendant} documentary Netflix Hulu",
        None))
    queries.append(("true_crime_coverage",
        f"{defendant} true crime YouTube",
        ["youtube.com"]))
    queries.append(("true_crime_coverage",
        f"{defendant} podcast true crime",
        ["spotify.com", "apple.com"]))

    # ==========================================================================
    # 9. ARCHIVE.ORG SPECIFIC SEARCHES
    # ==========================================================================
    queries.append(("other",
        f"site:archive.org {defendant} video",
        None))

    # ==========================================================================
    # 10. VIMEO SPECIFIC (Documentary/Journalism)
    # ==========================================================================
    queries.append(("other",
        f"site:vimeo.com {defendant}",
        None))

    # ==========================================================================
    # 11. CUSTOM QUERIES
    # ==========================================================================
    for q in (custom_queries or [])[:3]:
        queries.append(("other", q, ALL_VIDEO_SOURCES))

    return queries


def _classify_source(url: str) -> str:
    """Classify the source type from URL."""
    url_lower = url.lower()

    # Official channels
    if any(ch in url_lower for ch in ["policeactivity", "lawcrime", "courttv",
                                       "realworldpolice", "bodycamwatch"]):
        return "official_channel"

    # Platform classification
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "vimeo.com" in url_lower:
        return "vimeo"
    elif "dailymotion.com" in url_lower:
        return "dailymotion"
    elif "rumble.com" in url_lower:
        return "rumble"
    elif "odysee.com" in url_lower:
        return "odysee"
    elif "archive.org" in url_lower:
        return "archive"
    elif "documentcloud.org" in url_lower:
        return "documentcloud"
    elif "courtlistener.com" in url_lower:
        return "courtlistener"
    elif "scribd.com" in url_lower:
        return "scribd"
    elif any(gov in url_lower for gov in [".gov", "police.org", "sheriff"]):
        return "official_govt"
    elif any(news in url_lower for news in ["news", "chronicle", "times", "post",
                                             "cbs", "nbc", "abc", "fox", "cnn"]):
        return "news_outlet"
    else:
        return "other"


def _get_platform(url: str) -> str:
    """Extract platform name from URL."""
    url_lower = url.lower()
    platforms = {
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "vimeo.com": "Vimeo", "dailymotion.com": "Dailymotion",
        "rumble.com": "Rumble", "odysee.com": "Odysee",
        "archive.org": "Internet Archive", "documentcloud.org": "DocumentCloud",
        "courtlistener.com": "CourtListener", "scribd.com": "Scribd",
        "facebook.com": "Facebook", "twitter.com": "Twitter", "x.com": "X",
    }
    for domain, name in platforms.items():
        if domain in url_lower:
            return name
    return "Web"


def search_transparency_portals(region_id: str, defendant: str) -> List[Dict]:
    """Search transparency portals for a region."""
    if not HAS_JURISDICTION_DATA:
        return []

    portals = get_transparency_portals(region_id)
    results = []

    for portal in portals:
        results.append({
            "agency": portal["name"],
            "portal_type": portal["type"],
            "url": portal["url"],
            "search_suggestion": f"Search for: {defendant}",
            "notes": "Manual search required - FOIA portals don't allow API access"
        })

    return results

# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def calculate_artifact_quality_score(assessment: Dict) -> Dict:
    """Calculate artifact quality scores based on rubric (50% weight)."""
    scores = {}
    total_score = 0
    max_possible = 0

    artifact_types = ["interrogation", "bodycam", "court", "call_911", "discovery_docs"]

    for atype in artifact_types:
        exists = assessment.get(f"{atype}_exists", assessment.get(atype, {}).get("exists", "NO"))
        quality = assessment.get(f"{atype}_quality", assessment.get(atype, {}).get("quality", "POOR"))

        if exists == "NO" or quality == "N/A":
            scores[atype] = {"score": 0, "quality": "N/A", "max": 0}
            continue

        quality_upper = quality.upper() if quality else "POOR"

        # Map quality strings to scores
        if quality_upper in ["EXCELLENT", "FULL"]:
            score = 5
        elif quality_upper in ["GOOD", "EXTENSIVE"]:
            score = 4
        elif quality_upper in ["ADEQUATE", "PARTIAL", "CLIPS"]:
            score = 3
        elif quality_upper in ["MARGINAL", "LIMITED"]:
            score = 2
        elif quality_upper == "POOR":
            score = 1
        else:
            score = 0

        # Adjust for existence confidence
        if exists == "MAYBE":
            score = score * 0.6  # Reduce score for uncertain existence

        scores[atype] = {"score": score, "quality": quality, "max": 5}
        total_score += score
        max_possible += 5

    # Calculate weighted score (50% of total)
    weighted_score = (total_score / max_possible * 100) if max_possible > 0 else 0

    return {
        "artifact_scores": scores,
        "total_score": total_score,
        "max_possible": max_possible,
        "weighted_score": weighted_score * 0.5,  # 50% weight
        "rating": _get_quality_rating(weighted_score)
    }


def calculate_precision_score(assessment: Dict, total_sources: int) -> Dict:
    """Calculate precision score (30% weight)."""
    valid_sources = 0
    total_reported = 0

    for key in ["interrogation_sources", "bodycam_sources", "court_sources",
                "call_911_sources", "discovery_sources"]:
        sources = assessment.get(key, [])
        if isinstance(sources, list):
            total_reported += len(sources)
            # Assume sources with official_channel or news_outlet are more likely valid
            for src in sources:
                if isinstance(src, str) and any(x in src.lower() for x in
                    ["youtube.com", "lawcrime", "police", "gov", "news", "documentcloud"]):
                    valid_sources += 1

    precision = valid_sources / total_reported if total_reported > 0 else 0

    if precision >= 0.90:
        rating = "EXCELLENT"
    elif precision >= 0.75:
        rating = "GOOD"
    elif precision >= 0.50:
        rating = "MARGINAL"
    else:
        rating = "POOR"

    return {
        "valid_sources": valid_sources,
        "total_reported": total_reported,
        "precision": precision,
        "weighted_score": precision * 100 * 0.3,  # 30% weight
        "rating": rating
    }


def calculate_coverage_score(assessment: Dict, ground_truth: Dict = None) -> Dict:
    """Calculate coverage score against ground truth (20% weight)."""
    if not ground_truth:
        # Without ground truth, estimate based on artifact diversity
        found_types = 0
        total_types = 5  # interrogation, bodycam, court, 911, discovery

        for atype in ["interrogation", "bodycam", "court", "call_911", "discovery_docs"]:
            exists = assessment.get(f"{atype}_exists", assessment.get(atype, {}).get("exists", "NO"))
            if exists in ["YES", "MAYBE"]:
                found_types += 1

        coverage = found_types / total_types
    else:
        # Compare against ground truth
        expected = ground_truth.get("expected_artifacts", {})
        found_count = 0
        expected_count = 0

        for atype, expected_data in expected.items():
            if expected_data.get("exists") == "YES":
                expected_count += 1
                exists = assessment.get(f"{atype}_exists",
                    assessment.get(atype, {}).get("exists", "NO"))
                if exists in ["YES", "MAYBE"]:
                    found_count += 1

        coverage = found_count / expected_count if expected_count > 0 else 0

    if coverage >= 1.0:
        level = "COMPLETE"
    elif coverage >= 0.75:
        level = "SUBSTANTIAL"
    elif coverage >= 0.50:
        level = "PARTIAL"
    else:
        level = "MINIMAL"

    return {
        "coverage": coverage,
        "weighted_score": coverage * 100 * 0.2,  # 20% weight
        "level": level
    }


def calculate_total_score(assessment: Dict, search_results: Dict,
                          ground_truth: Dict = None) -> Dict:
    """Calculate total composite score."""
    total_sources = sum(len(v) for v in search_results.values())

    quality = calculate_artifact_quality_score(assessment)
    precision = calculate_precision_score(assessment, total_sources)
    coverage = calculate_coverage_score(assessment, ground_truth)

    total_weighted = quality["weighted_score"] + precision["weighted_score"] + coverage["weighted_score"]

    # Determine overall rating
    if total_weighted >= 80:
        overall_rating = "EXCELLENT"
    elif total_weighted >= 60:
        overall_rating = "GOOD"
    elif total_weighted >= 40:
        overall_rating = "MARGINAL"
    else:
        overall_rating = "POOR"

    return {
        "quality": quality,
        "precision": precision,
        "coverage": coverage,
        "total_score": total_weighted,
        "overall_rating": overall_rating,
        "confidence": total_weighted / 100
    }


def _get_quality_rating(score: float) -> str:
    """Get quality rating from score."""
    if score >= 80:
        return "EXCELLENT"
    elif score >= 60:
        return "GOOD"
    elif score >= 40:
        return "ADEQUATE"
    elif score >= 20:
        return "MARGINAL"
    else:
        return "POOR"

# =============================================================================
# LLM ASSESSMENT - ENHANCED
# =============================================================================

def assess_artifacts(llm, case_info: Dict, search_results: Dict,
                     model: str = None, provider: str = "openrouter") -> Dict:
    """Use LLM to assess artifact availability with enhanced output format."""

    # Prepare search results summary
    interrogation_results = search_results.get('interrogation', [])[:7]
    bodycam_results = search_results.get('bodycam', [])[:7]
    court_results = search_results.get('court', [])[:7]
    call_911_results = search_results.get('call_911', [])[:5]
    discovery_results = search_results.get('discovery_docs', [])[:5]
    news_results = search_results.get('news', [])[:5]
    coverage_results = search_results.get('true_crime_coverage', [])[:5]

    prompt = f"""You are an expert TRUE CRIME researcher assessing video artifact availability.

CASE INFORMATION:
- Defendant: {case_info.get('defendant', 'Unknown')}
- Jurisdiction: {case_info.get('jurisdiction', 'Unknown')}
- Crime Type: {case_info.get('crime_type', 'Unknown')}
- Incident Year: {case_info.get('incident_year', 'Unknown')}

SEARCH RESULTS:
Body Cam: {json.dumps(search_results.get('body_cam', [])[:5], indent=2)}
Interrogation: {json.dumps(search_results.get('interrogation', [])[:5], indent=2)}
Court: {json.dumps(search_results.get('court', [])[:5], indent=2)}
Portal/Local News: {json.dumps(search_results.get('portal', [])[:5], indent=2)}
Reddit: {json.dumps(search_results.get('reddit', [])[:5], indent=2)}
PACER/CourtListener: {json.dumps(search_results.get('pacer', [])[:5], indent=2)}

INTERROGATION / POLICE INTERVIEW:
{json.dumps(interrogation_results, indent=2) if interrogation_results else "No results"}

BODYCAM / DASHCAM:
{json.dumps(bodycam_results, indent=2) if bodycam_results else "No results"}

COURT / TRIAL VIDEO:
{json.dumps(court_results, indent=2) if court_results else "No results"}

911 CALLS:
{json.dumps(call_911_results, indent=2) if call_911_results else "No results"}

DISCOVERY DOCUMENTS:
{json.dumps(discovery_results, indent=2) if discovery_results else "No results"}

NEWS COVERAGE:
{json.dumps(news_results, indent=2) if news_results else "No results"}

EXISTING TRUE CRIME COVERAGE:
{json.dumps(coverage_results, indent=2) if coverage_results else "No results"}

ASSESSMENT INSTRUCTIONS:
1. Verify URLs/titles match THIS SPECIFIC defendant and case
2. For "exists": YES = confident match, MAYBE = possible match, NO = not found
3. For "quality": FULL/EXCELLENT (complete footage), PARTIAL/GOOD (substantial), CLIPS/ADEQUATE (short), LIMITED/MARGINAL (minimal), NONE
4. Prioritize official sources (police, courts, DA offices, news)
5. Note any red flags (commentary videos, wrong case, dead links suspected)

DECISION TREE for overall_assessment:
- 10+ min interrogation available? → Likely ENOUGH
- Interrogation + (bodycam OR court)? → Definitely ENOUGH
- Only short news clips (<2 min)? → BORDERLINE
- No video, only documents? → INSUFFICIENT
- Exception: Exceptionally compelling story can be BORDERLINE

Return this EXACT JSON structure:
{{
    "defendant": "{case_info.get('defendant', 'Unknown')}",
    "jurisdiction": "{case_info.get('jurisdiction', 'Unknown')}",
    "interrogation": {{
        "exists": "YES/MAYBE/NO",
        "quality": "FULL/PARTIAL/CLIPS/LIMITED/NONE",
        "sources": ["url1", "url2"],
        "notes": "Brief description of what's available"
    }},
    "bodycam": {{
        "exists": "YES/MAYBE/NO",
        "quality": "EXTENSIVE/LIMITED/NONE",
        "sources": ["url1"],
        "notes": "Description"
    }},
    "court": {{
        "exists": "YES/MAYBE/NO",
        "quality": "FULL_TRIAL/SENTENCING/CLIPS/NONE",
        "sources": ["url1"],
        "notes": "Description"
    }},
    "call_911": {{
        "exists": "YES/MAYBE/NO",
        "quality": "FULL/PARTIAL/NONE",
        "sources": ["url1"],
        "notes": "Description"
    }},
    "discovery_docs": {{
        "exists": "YES/MAYBE/NO",
        "quality": "EXTENSIVE/LIMITED/NONE",
        "sources": ["url1"],
        "notes": "Description"
    }},
    "existing_coverage": {{
        "has_documentary": true/false,
        "has_podcast": true/false,
        "has_youtube_coverage": true/false,
        "competition_level": "NONE/LOW/MEDIUM/HIGH"
    }},
    "overall_assessment": "ENOUGH/BORDERLINE/INSUFFICIENT",
    "confidence": 0.85,
    "content_viability": "Brief assessment of content creation potential",
    "red_flags": ["Any concerns about sources"],
    "recommended_manual_checks": ["Specific portals or sources to verify manually"]
}}

JSON only, no other text:"""

    # Parse model spec to get provider and model
    if model:
        parsed_provider, use_model = get_provider_from_model(model)
        provider = parsed_provider
    else:
        use_model = LLM_PROVIDERS[provider]["default_model"]

    provider_config = LLM_PROVIDERS.get(provider, LLM_PROVIDERS["openrouter"])

    try:
        # Build request kwargs
        request_kwargs = {
            "model": use_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,  # Lower for more consistent output
            "max_tokens": 2000,
        }

        # Add provider-specific headers (OpenRouter needs these)
        if provider_config.get("headers"):
            request_kwargs["extra_headers"] = provider_config["headers"]

        response = llm.chat.completions.create(**request_kwargs)

        content = response.choices[0].message.content.strip()

        # Extract JSON from possible markdown wrapper
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        result = json.loads(content)

        # Add metadata about which model was used
        result["_model_used"] = use_model
        result["_provider"] = provider

        return result

    except json.JSONDecodeError as e:
        print(f"      JSON parse error: {e}")
        print(f"      Raw content: {content[:500]}...")
        return {}
    except Exception as e:
        print(f"      Assessment error ({provider}/{use_model}): {e}")
        return {}


def assess_artifacts_ensemble(clients: Dict, case_info: Dict,
                               search_results: Dict) -> Dict:
    """
    Run assessment with multiple models and combine results.

    Uses all available providers to assess, then aggregates:
    - Takes consensus on exists (YES if any says YES)
    - Averages confidence scores
    - Combines sources from all models
    - Uses majority vote for overall_assessment
    """
    assessments = []

    for provider, (client, default_model) in clients.items():
        print(f"      Running {provider}...")
        result = assess_artifacts(
            client, case_info, search_results,
            model=default_model, provider=provider
        )
        if result:
            assessments.append(result)

    if not assessments:
        return {}

    if len(assessments) == 1:
        return assessments[0]

    # Combine assessments
    combined = {
        "defendant": case_info.get("defendant", "Unknown"),
        "jurisdiction": case_info.get("jurisdiction", "Unknown"),
        "_ensemble": True,
        "_models_used": [a.get("_model_used", "unknown") for a in assessments],
    }

    # Combine artifact assessments
    artifact_types = ["interrogation", "bodycam", "court", "call_911", "discovery_docs"]

    for atype in artifact_types:
        exists_votes = []
        qualities = []
        all_sources = []
        all_notes = []

        for assessment in assessments:
            artifact = assessment.get(atype, {})
            if isinstance(artifact, dict):
                exists_votes.append(artifact.get("exists", "NO"))
                qualities.append(artifact.get("quality", "NONE"))
                all_sources.extend(artifact.get("sources", []))
                if artifact.get("notes"):
                    all_notes.append(artifact.get("notes"))

        # Consensus: YES if any model says YES
        if "YES" in exists_votes:
            consensus_exists = "YES"
        elif "MAYBE" in exists_votes:
            consensus_exists = "MAYBE"
        else:
            consensus_exists = "NO"

        # Best quality from any model
        quality_order = ["FULL", "EXCELLENT", "EXTENSIVE", "GOOD", "PARTIAL",
                         "CLIPS", "ADEQUATE", "LIMITED", "MARGINAL", "POOR", "NONE"]
        best_quality = "NONE"
        for q in quality_order:
            if any(qu.upper() == q for qu in qualities):
                best_quality = q
                break

        combined[atype] = {
            "exists": consensus_exists,
            "quality": best_quality,
            "sources": list(set(all_sources))[:5],  # Dedupe and limit
            "notes": " | ".join(all_notes[:2]) if all_notes else ""
        }

    # Overall assessment - majority vote
    overall_votes = [a.get("overall_assessment", "INSUFFICIENT") for a in assessments]
    vote_counts = {}
    for vote in overall_votes:
        vote_counts[vote] = vote_counts.get(vote, 0) + 1
    combined["overall_assessment"] = max(vote_counts, key=vote_counts.get)

    # Average confidence
    confidences = [a.get("confidence", 0.5) for a in assessments]
    combined["confidence"] = sum(confidences) / len(confidences)

    # Combine red flags
    all_flags = []
    for a in assessments:
        all_flags.extend(a.get("red_flags", []))
    combined["red_flags"] = list(set(all_flags))[:5]

    # Combine recommendations
    all_recs = []
    for a in assessments:
        all_recs.extend(a.get("recommended_manual_checks", []))
    combined["recommended_manual_checks"] = list(set(all_recs))[:5]

    return combined

# =============================================================================
# BENCHMARK TESTING
# =============================================================================

def run_benchmark(exa, llm, model: str = None) -> Dict:
    """Run benchmark tests against ground truth cases."""
    print("\n" + "=" * 60)
    print("BENCHMARK TEST - Ground Truth Validation")
    print("=" * 60)

    results = {}

    for case_id, ground_truth in GROUND_TRUTH_CASES.items():
        print(f"\n[BENCHMARK] {ground_truth['defendant']}")
        print(f"  Jurisdiction: {ground_truth['jurisdiction']}")

        # Search
        search_results = search_artifacts(
            exa,
            defendant=ground_truth["defendant"],
            jurisdiction=ground_truth["jurisdiction"],
            region_id=ground_truth.get("region_id"),
            incident_year=ground_truth.get("incident_year")
        )

        total = sum(len(v) for v in search_results.values())
        print(f"  Found {total} potential sources")

        # Assess
        assessment = assess_artifacts(llm, {
            "defendant": ground_truth["defendant"],
            "jurisdiction": ground_truth["jurisdiction"],
            "incident_year": ground_truth.get("incident_year", "")
        }, search_results, model=model)

        if not assessment:
            print(f"  ❌ Assessment failed")
            results[case_id] = {"error": "Assessment failed"}
            continue

        # Score against ground truth
        scores = calculate_total_score(assessment, search_results, ground_truth)

        # Compare to expected
        expected_overall = ground_truth.get("overall", "ENOUGH")
        actual_overall = assessment.get("overall_assessment", "INSUFFICIENT")
        match = actual_overall == expected_overall

        results[case_id] = {
            "defendant": ground_truth["defendant"],
            "expected": expected_overall,
            "actual": actual_overall,
            "match": match,
            "scores": scores,
            "assessment": assessment
        }

        status = "✅" if match else "❌"
        print(f"  {status} Expected: {expected_overall}, Got: {actual_overall}")
        print(f"  Total Score: {scores['total_score']:.1f}/100")
        print(f"  Quality: {scores['quality']['rating']}, "
              f"Precision: {scores['precision']['rating']}, "
              f"Coverage: {scores['coverage']['level']}")

        time.sleep(1)

    # Summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)

    total_cases = len(results)
    matches = sum(1 for r in results.values() if r.get("match", False))
    accuracy = matches / total_cases if total_cases > 0 else 0

    print(f"Accuracy: {matches}/{total_cases} ({accuracy:.1%})")

    avg_score = sum(r.get("scores", {}).get("total_score", 0)
                    for r in results.values() if "scores" in r) / total_cases
    print(f"Average Score: {avg_score:.1f}/100")

    return results

# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_artifact_hunter(limit: int = None, model: str = None, ensemble: bool = False):
    """Hunt for artifacts for cases in CASE ANCHOR."""
    print("=" * 60)
    print("NEWS → VIEWS: Artifact Hunter v2.0")
    print("=" * 60)

    if not check_credentials():
        return {"error": "Invalid credentials"}

    # Determine provider and model
    provider = "openrouter"
    if model:
        provider, use_model = get_provider_from_model(model)
    else:
        use_model = OPENROUTER_MODEL

    if ensemble:
        print("Mode: ENSEMBLE (multi-model)")
    else:
        print(f"Provider: {LLM_PROVIDERS.get(provider, {}).get('name', provider)}")
        print(f"Model: {use_model}")

    # Initialize
    print("\n[INIT] Connecting...")
    try:
        gc = get_gspread_client()
        exa = get_exa_client()

        if ensemble:
            llm_clients = get_multi_llm_clients()
            if not llm_clients:
                print("❌ No LLM providers available for ensemble mode")
                return {"error": "No providers"}
            llm = None  # Will use ensemble function
        else:
            llm_clients = None
            llm = get_llm_client(provider)
    except Exception as e:
        print(f"❌ Init failed: {e}")
        return {"error": str(e)}

    # Open sheet
    try:
        sh = gc.open_by_key(SHEET_ID)
        ws_anchor = sh.worksheet("CASE ANCHOR & FOOTAGE CHECK")
        ws_intake = sh.worksheet("NEWS INTAKE")
    except Exception as e:
        print(f"❌ Sheet error: {e}")
        return {"error": str(e)}

    # Get cases
    cases = ws_anchor.get_all_records()
    print(f"[INIT] {len(cases)} cases in CASE ANCHOR")

    # Get intake data for artifact queries
    intake_records = ws_intake.get_all_records()
    intake_by_id = {str(i): r for i, r in enumerate(intake_records, start=2)}

    stats = {
        "processed": 0, "enough": 0, "borderline": 0, "insufficient": 0,
        "errors": 0, "total_score": 0
    }

    for row_idx, case in enumerate(cases, start=2):
        # Skip already assessed
        if str(case.get("Footage Assessment", "")).strip():
            continue

        if limit and stats["processed"] >= limit:
            print(f"\n[LIMIT] Reached {limit} cases")
            break

        defendant = str(case.get("Defendant Name(s)", "")).strip()
        jurisdiction = str(case.get("Jurisdiction", "")).strip()
        intake_id = str(case.get("Intake_ID", "")).strip()

        print(f"\n[{row_idx}] {defendant[:40]}...")
        print(f"    Jurisdiction: {jurisdiction}")

        # Get custom queries and metadata from intake
        custom_queries = []
        crime_type = ""
        region_id = ""
        incident_year = ""
        if intake_id and intake_id in intake_by_id:
            intake_row = intake_by_id[intake_id]
            queries_str = str(intake_row.get("Artifact Queries", ""))
            if queries_str:
                custom_queries = [q.strip() for q in queries_str.split("|") if q.strip()]
            crime_type = intake_row.get("Crime Type", "")
            region_id = (
                intake_row.get("Region_ID")
                or intake_row.get("Region ID")
                or intake_row.get("Region")
                or ""
            )
            triage_json = intake_row.get("Triage JSON") or intake_row.get("Triage") or ""
            if triage_json:
                try:
                    triage = json.loads(triage_json)
                    incident_year = triage.get("incident_year", "")
                except json.JSONDecodeError:
                    incident_year = ""
        
        # Search
        search_results = search_artifacts(
            exa,
            defendant,
            jurisdiction,
            crime_type,
            custom_queries,
            region_id=region_id,
            incident_year=incident_year,
        )
        total = sum(len(v) for v in search_results.values())
        print(f"    Found {total} potential sources")

        # Show breakdown by type
        for stype, sresults in search_results.items():
            if sresults:
                platforms = set(r.get("platform", "Unknown") for r in sresults)
                print(f"      - {stype}: {len(sresults)} ({', '.join(platforms)})")

        # Assess with specified model (or ensemble)
        case_info = {
            "defendant": defendant,
            "jurisdiction": jurisdiction,
            "crime_type": crime_type,
            "incident_year": incident_year
        }

        if ensemble and llm_clients:
            print("    Running ensemble assessment...")
            assessment = assess_artifacts_ensemble(llm_clients, case_info, search_results)
        else:
            assessment = assess_artifacts(llm, case_info, search_results,
                                          model=use_model, provider=provider)

        if not assessment:
            stats["errors"] += 1
            continue

        # Calculate scores
        scores = calculate_total_score(assessment, search_results)
        print(f"    Score: {scores['total_score']:.1f}/100 ({scores['overall_rating']})")

        # Update sheet
        try:
            # Extract values from new format
            interrog = assessment.get("interrogation", {})
            bodycam = assessment.get("bodycam", {})
            court = assessment.get("court", {})

            ws_anchor.update_cell(row_idx, 7, interrog.get("exists", "NO"))
            ws_anchor.update_cell(row_idx, 8, bodycam.get("exists", "NO"))
            ws_anchor.update_cell(row_idx, 9, court.get("exists", "NO"))

            # Collect all sources
            all_sources = (
                interrog.get("sources", []) +
                bodycam.get("sources", []) +
                court.get("sources", [])
            )
            ws_anchor.update_cell(row_idx, 10, "\n".join(all_sources[:5]))

            overall = assessment.get("overall_assessment", "INSUFFICIENT")
            ws_anchor.update_cell(row_idx, 11, overall)

            stats["processed"] += 1
            stats["total_score"] += scores["total_score"]

            if overall == "ENOUGH":
                stats["enough"] += 1
                print(f"    ✅ ENOUGH (confidence: {assessment.get('confidence', 0):.0%})")
            elif overall == "BORDERLINE":
                stats["borderline"] += 1
                print(f"    ⚠️ BORDERLINE")
            else:
                stats["insufficient"] += 1
                print(f"    ❌ INSUFFICIENT")

            # Show red flags if any
            red_flags = assessment.get("red_flags", [])
            if red_flags:
                print(f"    ⚠️ Red flags: {', '.join(red_flags[:2])}")

        except Exception as e:
            print(f"    Sheet update error: {e}")
            stats["errors"] += 1

        time.sleep(1)

    # Report
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"Processed:    {stats['processed']}")
    print(f"  ENOUGH:     {stats['enough']}")
    print(f"  BORDERLINE: {stats['borderline']}")
    print(f"  INSUFFICIENT: {stats['insufficient']}")
    print(f"Errors:       {stats['errors']}")

    if stats["processed"] > 0:
        avg_score = stats["total_score"] / stats["processed"]
        print(f"Avg Score:    {avg_score:.1f}/100")

    return stats

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Artifact Hunter v2.0 - Multi-model True Crime Research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Model specification formats:
  --model deepseek-chat           # Auto-detected as DeepSeek
  --model deepseek-reasoner       # DeepSeek R1 (reasoning)
  --model anthropic/claude-3.5-sonnet  # OpenRouter (auto)
  --model openrouter:openai/gpt-4o     # Explicit provider
  --model deepseek:deepseek-chat       # Explicit provider

Ensemble mode (uses all available providers):
  --ensemble                      # Use both DeepSeek and OpenRouter

Examples:
  python artifact_hunter.py --model deepseek-chat
  python artifact_hunter.py --model anthropic/claude-3.5-sonnet
  python artifact_hunter.py --ensemble --limit 5
  python artifact_hunter.py --benchmark --model deepseek-reasoner
"""
    )
    parser.add_argument("--limit", type=int, help="Max cases to process")
    parser.add_argument("--check", action="store_true", help="Check credentials only")
    parser.add_argument("--benchmark", action="store_true", help="Run ground truth benchmark")
    parser.add_argument("--model", type=str,
        help="Model to use (e.g., deepseek-chat, anthropic/claude-3.5-sonnet)")
    parser.add_argument("--ensemble", action="store_true",
        help="Use all available providers (DeepSeek + OpenRouter) and combine results")
    parser.add_argument("--list-models", action="store_true",
        help="List available models and providers")

    args = parser.parse_args()

    if args.list_models:
        print("\n📋 Available LLM Providers and Models")
        print("=" * 50)
        for provider, config in LLM_PROVIDERS.items():
            api_key_name = config["env_key"]
            has_key = bool(os.getenv(api_key_name))
            status = "✅" if has_key else "❌"
            print(f"\n{status} {config['name']} ({provider})")
            print(f"   API Key: {api_key_name} {'(set)' if has_key else '(not set)'}")
            print(f"   Default: {config['default_model']}")
            print(f"   Models:")
            for model in config["models"]:
                print(f"     - {model}")
        print()
        return

    if args.check:
        check_credentials()
        print("\n📋 Provider Status:")
        for provider, config in LLM_PROVIDERS.items():
            api_key = os.getenv(config["env_key"])
            status = "✅" if api_key else "❌"
            print(f"  {status} {config['name']}: {config['env_key']}")
        return

    if args.benchmark:
        if not check_credentials():
            return
        exa = get_exa_client()

        if args.ensemble:
            print("\n[INIT] Ensemble mode - initializing all providers...")
            clients = get_multi_llm_clients()
            if not clients:
                print("❌ No LLM providers available")
                return
            # For benchmark, use first available provider
            provider, (llm, default_model) = next(iter(clients.items()))
            run_benchmark(exa, llm, model=args.model or default_model)
        else:
            provider = "openrouter"
            if args.model:
                provider, _ = get_provider_from_model(args.model)
            try:
                llm = get_llm_client(provider)
            except ValueError as e:
                print(f"❌ {e}")
                return
            run_benchmark(exa, llm, model=args.model)
        return

    run_artifact_hunter(limit=args.limit, model=args.model, ensemble=args.ensemble)


if __name__ == "__main__":
    main()
