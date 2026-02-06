#!/usr/bin/env python3
"""
NEWS → VIEWS: Evidence Pre-Score Engine

Scores articles for artifact likelihood BEFORE LLM triage to avoid
wasting credits on low-artifact cases.

Scoring factors:
  - Keyword hits (bodycam, BWC, interrogation, etc.)     +15 each
  - Video platform URLs in article text                   +20
  - Jurisdiction/agency token matches                     +10
  - Lifecycle indicators (sentenced, convicted, etc.)     +5 each
  - Florida region bonus (Sunshine Law)                   +10
  - Court has video capability                            +10
"""

import re
from typing import Dict, List, Tuple

from jurisdiction_portals import (
    get_jurisdiction_config,
    has_court_video,
    is_florida_case,
)

# =============================================================================
# SCORING CONSTANTS
# =============================================================================

# Each unique keyword hit adds this many points
KEYWORD_SCORE = 15

# Artifact-indicating keywords (case-insensitive matching)
ARTIFACT_KEYWORDS = [
    r"\bbodycam\b",
    r"\bbody[\s-]?cam\b",
    r"\bBWC\b",
    r"\bbody[\s-]?worn\s+camera\b",
    r"\bbody\s+camera\b",
    r"\bcustodial\s+interview\b",
    r"\binterrogation\s+video\b",
    r"\bsurveillance\s+footage\b",
    r"\btrial\s+livestream\b",
    r"\bdashcam\b",
    r"\bdash[\s-]?cam\b",
]

# Video platform domains — presence in article text means artifacts may be linked
VIDEO_PLATFORMS = [
    "youtube.com",
    "youtu.be",
    "vimeo.com",
]
VIDEO_PLATFORM_SCORE = 20

# Lifecycle indicators — case is far enough along that artifacts are likely released
LIFECYCLE_KEYWORDS = [
    r"\bsentenced\b",
    r"\bconvicted\b",
    r"\bplea\b",
    r"\btrial\b",
    r"\bverdict\b",
]
LIFECYCLE_SCORE = 5

# Jurisdiction/agency match bonus
AGENCY_MATCH_SCORE = 10

# Florida Sunshine Law bonus
FLORIDA_BONUS = 10

# Court video capability bonus
COURT_VIDEO_BONUS = 10


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def _match_keywords(text: str, patterns: List[str]) -> List[str]:
    """Return list of matched keyword patterns in text."""
    matched = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # Store a clean label for the match
            clean = pattern.replace(r"\b", "").replace("\\s+", " ").replace("\\s", " ")
            clean = re.sub(r"\[.*?\]", "", clean).replace("?", "").strip()
            matched.append(clean)
    return matched


def _check_video_platforms(text: str) -> List[str]:
    """Check if text contains video platform URLs."""
    text_lower = text.lower()
    return [p for p in VIDEO_PLATFORMS if p in text_lower]


def _check_agency_tokens(text: str, region_id: str) -> List[str]:
    """Check if article text mentions agencies from the region."""
    if not region_id:
        return []

    config = get_jurisdiction_config(region_id)
    if not config:
        return []

    text_lower = text.lower()
    matched = []

    for agency in config.get("agencies", []):
        name = agency.get("name", "")
        abbrev = agency.get("abbrev", "")

        if abbrev and abbrev.lower() in text_lower:
            matched.append(abbrev)
        elif name and name.lower() in text_lower:
            matched.append(name)

    return matched


def evidence_prescore(article_text: str, article_url: str = "",
                      region_id: str = "") -> Dict:
    """
    Score an article for artifact likelihood before LLM triage.

    Args:
        article_text: Full article text from Exa
        article_url: Article URL (not currently scored, reserved for future use)
        region_id: Region ID for jurisdiction-aware scoring

    Returns:
        dict with keys:
            artifact_pre_score: int (0-100+, uncapped during calculation)
            matched_keywords: list of matched keyword labels
            breakdown: dict with score components for transparency
    """
    score = 0
    all_matches = []
    breakdown = {}

    # --- Artifact keyword hits (+15 each) ---
    keyword_matches = _match_keywords(article_text, ARTIFACT_KEYWORDS)
    keyword_points = len(keyword_matches) * KEYWORD_SCORE
    score += keyword_points
    all_matches.extend(keyword_matches)
    breakdown["keyword_hits"] = keyword_points

    # --- Video platform URLs in text (+20) ---
    platform_matches = _check_video_platforms(article_text)
    if platform_matches:
        score += VIDEO_PLATFORM_SCORE
        all_matches.extend([f"video:{p}" for p in platform_matches])
    breakdown["video_platform"] = VIDEO_PLATFORM_SCORE if platform_matches else 0

    # --- Jurisdiction/agency token matches (+10) ---
    agency_matches = _check_agency_tokens(article_text, region_id)
    if agency_matches:
        score += AGENCY_MATCH_SCORE
        all_matches.extend([f"agency:{a}" for a in agency_matches])
    breakdown["agency_match"] = AGENCY_MATCH_SCORE if agency_matches else 0

    # --- Lifecycle indicators (+5 each) ---
    lifecycle_matches = _match_keywords(article_text, LIFECYCLE_KEYWORDS)
    lifecycle_points = len(lifecycle_matches) * LIFECYCLE_SCORE
    score += lifecycle_points
    all_matches.extend([f"lifecycle:{m}" for m in lifecycle_matches])
    breakdown["lifecycle"] = lifecycle_points

    # --- Florida bonus (+10) ---
    fl_bonus = 0
    if region_id and is_florida_case(region_id):
        fl_bonus = FLORIDA_BONUS
        score += fl_bonus
        all_matches.append("florida_sunshine")
    breakdown["florida_bonus"] = fl_bonus

    # --- Court video capability (+10) ---
    court_bonus = 0
    if region_id and has_court_video(region_id):
        court_bonus = COURT_VIDEO_BONUS
        score += court_bonus
        all_matches.append("court_has_video")
    breakdown["court_video"] = court_bonus

    return {
        "artifact_pre_score": score,
        "matched_keywords": all_matches,
        "breakdown": breakdown,
    }
