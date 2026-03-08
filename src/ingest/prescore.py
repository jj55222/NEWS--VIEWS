"""
Evidence pre-scoring: fast, deterministic signal extraction.

Runs BEFORE any LLM call. Scores articles on artifact likelihood
using keyword matching, URL presence, jurisdiction signals,
lifecycle indicators, and crime severity signals.
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
    # Body-worn / officer camera
    "bodycam": 15,
    "body cam": 15,
    "body-cam": 15,
    "body-worn camera": 15,
    "body worn camera": 15,
    "bwc": 15,
    "officer camera": 12,
    "police video": 12,
    "officer video": 12,
    # Dash / in-car camera
    "dashcam": 15,
    "dash cam": 15,
    "dash-cam": 15,
    "in-car camera": 15,
    "in-car video": 15,
    "patrol car video": 12,
    "cruiser camera": 12,
    # Interrogation / interview
    "custodial interview": 15,
    "interrogation video": 15,
    "interrogation tape": 15,
    "interrogation room": 12,
    "interview room": 10,
    "police interview": 12,
    "confession video": 15,
    "confession tape": 15,
    "recorded confession": 12,
    "recorded interview": 10,
    # Surveillance / security
    "surveillance footage": 15,
    "surveillance video": 15,
    "surveillance camera": 12,
    "security camera": 12,
    "security footage": 12,
    "security video": 12,
    "cctv": 12,
    "ring doorbell": 12,
    "doorbell camera": 12,
    "home security": 8,
    # Court / trial video
    "trial livestream": 15,
    "court video": 15,
    "courtroom video": 15,
    "court footage": 15,
    "gavel to gavel": 12,
    "trial video": 15,
    "sentencing video": 15,
    # Bystander / citizen
    "cell phone video": 12,
    "cellphone video": 12,
    "bystander video": 12,
    "caught on camera": 12,
    "caught on tape": 12,
    "caught on video": 12,
    "witness video": 10,
    "facebook live": 10,
    "instagram live": 10,
    # Audio evidence
    "911 call": 12,
    "911 audio": 12,
    "dispatch audio": 10,
    "radio traffic": 8,
    "jailhouse call": 12,
    "jail call": 10,
    "phone call recording": 10,
    # Aerial / other
    "helicopter footage": 10,
    "aerial footage": 10,
    "news chopper": 8,
    "drone footage": 10,
    # Generic release language
    "released video": 12,
    "released footage": 12,
    "police released": 10,
    "video shows": 8,
    "footage shows": 8,
    "video released": 12,
    "footage released": 12,
    # Press / public statement
    "press conference": 10,
    "news conference": 10,
}

VIDEO_PLATFORMS = {
    "youtube.com": 20,
    "youtu.be": 20,
    "vimeo.com": 15,
    "rumble.com": 10,
    "dailymotion.com": 10,
    "bitchute.com": 8,
    "odysee.com": 8,
    "facebook.com/watch": 8,
    "tiktok.com": 5,
}

LIFECYCLE_KEYWORDS = {
    # Conviction / sentencing
    "sentenced": 5,
    "convicted": 5,
    "found guilty": 5,
    "guilty verdict": 5,
    "life sentence": 5,
    "life in prison": 5,
    "death penalty": 5,
    "death sentence": 5,
    "without parole": 5,
    # Plea
    "guilty plea": 5,
    "plea deal": 5,
    "plea agreement": 5,
    "plea bargain": 5,
    "pleaded guilty": 5,
    "pled guilty": 5,
    "pleaded no contest": 5,
    "nolo contendere": 3,
    # Trial
    "trial": 5,
    "verdict": 5,
    "jury deliberation": 5,
    "jury trial": 5,
    "bench trial": 5,
    "closing arguments": 5,
    "opening statements": 5,
    "testimony": 4,
    "cross-examination": 4,
    "cross examination": 4,
    # Pre-trial
    "indicted": 3,
    "arraigned": 3,
    "arraignment": 3,
    "grand jury": 3,
    "preliminary hearing": 3,
    "bond hearing": 3,
    "bail hearing": 3,
    "pretrial": 3,
    "pre-trial": 3,
    # Charges / legal process
    "charged with": 4,
    "arrested": 3,
    "felony": 3,
    "first degree": 4,
    "first-degree": 4,
    "second degree": 3,
    "second-degree": 3,
    "capital murder": 5,
    "aggravated": 3,
    # Sentencing / post-trial
    "probation": 3,
    "parole": 3,
    "prison": 3,
    "incarcerated": 3,
    # Legal actors
    "prosecutor": 3,
    "district attorney": 3,
    "defense attorney": 3,
    "public defender": 3,
    "judge": 2,
}

CRIME_SEVERITY_KEYWORDS = {
    # Homicide
    "murder": 5,
    "homicide": 5,
    "manslaughter": 5,
    "killed": 4,
    "fatal shooting": 5,
    "fatally shot": 5,
    "shot and killed": 5,
    "stabbed to death": 5,
    "beaten to death": 5,
    # Violent crime
    "shooting": 4,
    "stabbing": 4,
    "armed robbery": 4,
    "carjacking": 4,
    "kidnapping": 5,
    "abduction": 5,
    "hostage": 5,
    "attempted murder": 5,
    # Abuse / exploitation
    "child abuse": 5,
    "child neglect": 5,
    "child endangerment": 5,
    "sexual assault": 5,
    "sexual abuse": 5,
    "domestic violence": 4,
    "aggravated assault": 4,
    "human trafficking": 5,
    "elder abuse": 4,
    # Use of force
    "use of force": 5,
    "excessive force": 5,
    "officer-involved shooting": 5,
    "officer involved shooting": 5,
    "police shooting": 5,
    "police brutality": 5,
    "in-custody death": 5,
    "in custody death": 5,
    "taser": 4,
    "wrongful death": 5,
    # Major crime
    "mass shooting": 5,
    "serial killer": 5,
    "cold case": 4,
    "arson": 4,
    "hate crime": 5,
    "terrorism": 5,
    "drug trafficking": 4,
    "conspiracy": 3,
    "racketeering": 4,
    "rico": 4,
    "gang-related": 3,
    "gang related": 3,
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

    # Crime severity signals
    severity_pts = 0
    for kw, pts in CRIME_SEVERITY_KEYWORDS.items():
        if kw in text_lower:
            severity_pts += pts
            matches.append(f"severity:{kw}")
    severity_pts = min(severity_pts, 15)
    breakdown["crime_severity"] = severity_pts
    score += severity_pts

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
