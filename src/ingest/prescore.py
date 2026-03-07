"""
Evidence pre-scoring: fast, deterministic signal extraction.

Runs BEFORE any LLM call. Scores articles on artifact likelihood
using keyword matching, URL presence, jurisdiction signals, and
lifecycle indicators.
"""

from __future__ import annotations

import re

# Lazy import to avoid circular deps at module load
_jurisdiction_portals = None


def _get_portals():
    global _jurisdiction_portals
    if _jurisdiction_portals is None:
        from archive.jurisdiction_portals import (
            JURISDICTION_PORTALS,
            is_florida_case,
            has_court_video,
        )
        _jurisdiction_portals = {
            "portals": JURISDICTION_PORTALS,
            "is_florida": is_florida_case,
            "has_court_video": has_court_video,
        }
    return _jurisdiction_portals


# ---------------------------------------------------------------------------
# Keyword banks
# ---------------------------------------------------------------------------

ARTIFACT_KEYWORDS = {
    "bodycam": 15,
    "body cam": 15,
    "body-worn camera": 15,
    "bwc": 15,
    "dashcam": 15,
    "dash cam": 15,
    "custodial interview": 15,
    "interrogation video": 15,
    "interrogation tape": 15,
    "surveillance footage": 15,
    "surveillance video": 15,
    "trial livestream": 15,
    "court video": 15,
    "press conference": 10,
}

VIDEO_PLATFORMS = {
    "youtube.com": 20,
    "youtu.be": 20,
    "vimeo.com": 15,
    "rumble.com": 10,
}

LIFECYCLE_KEYWORDS = {
    "sentenced": 5,
    "convicted": 5,
    "guilty plea": 5,
    "plea deal": 5,
    "trial": 5,
    "verdict": 5,
    "indicted": 3,
    "arraigned": 3,
}


def compute_prescore(
    article_text: str,
    url: str = "",
    region_id: str = "",
) -> dict:
    """
    Compute a 0-100 artifact likelihood pre-score.

    Returns dict with:
      score: int (0-100)
      matches: list of matched keywords
      breakdown: dict of score components
    """
    text_lower = article_text.lower()
    score = 0
    matches = []
    breakdown = {}

    # Artifact keywords
    artifact_pts = 0
    for kw, pts in ARTIFACT_KEYWORDS.items():
        if kw in text_lower:
            artifact_pts += pts
            matches.append(kw)
    artifact_pts = min(artifact_pts, 45)  # cap
    breakdown["artifact_keywords"] = artifact_pts
    score += artifact_pts

    # Video platform URLs in text
    platform_pts = 0
    for platform, pts in VIDEO_PLATFORMS.items():
        if platform in text_lower:
            platform_pts += pts
            matches.append(f"url:{platform}")
    platform_pts = min(platform_pts, 20)
    breakdown["video_platforms"] = platform_pts
    score += platform_pts

    # Lifecycle indicators
    lifecycle_pts = 0
    for kw, pts in LIFECYCLE_KEYWORDS.items():
        if kw in text_lower:
            lifecycle_pts += pts
            matches.append(f"lifecycle:{kw}")
    lifecycle_pts = min(lifecycle_pts, 15)
    breakdown["lifecycle"] = lifecycle_pts
    score += lifecycle_pts

    # Jurisdiction bonuses
    jurisdiction_pts = 0
    if region_id:
        try:
            portals = _get_portals()
            if portals["is_florida"](region_id):
                jurisdiction_pts += 10
                matches.append("florida_sunshine")
            if portals["has_court_video"](region_id):
                jurisdiction_pts += 10
                matches.append("court_video_capable")

            config = portals["portals"].get(region_id, {})
            for agency in config.get("agencies", []):
                abbrev = agency.get("abbrev", "").lower()
                if abbrev and abbrev in text_lower:
                    jurisdiction_pts += 10
                    matches.append(f"agency:{abbrev}")
                    break
        except Exception:
            pass

    jurisdiction_pts = min(jurisdiction_pts, 20)
    breakdown["jurisdiction"] = jurisdiction_pts
    score += jurisdiction_pts

    return {
        "score": min(score, 100),
        "matches": matches,
        "breakdown": breakdown,
    }
