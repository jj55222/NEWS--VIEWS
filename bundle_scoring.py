#!/usr/bin/env python3
"""
NEWS → VIEWS: Bundle Scoring Engine

Deterministic, LLM-free post-processing that takes raw search results from
artifact_hunter and produces scored, tiered, auditable case bundle candidates.

Workflow:
  1. Normalize entity (defendant variants, jurisdiction tokens)
  2. Classify results into 4 artifact lanes (BWC, INTERROGATION, SURVEILLANCE, COURT_VIDEO)
  3. Score each artifact on source_trust, entity_match, timeline_fit, corroboration
  4. Synthesize bundle-level score and recommendation
  5. Write structured JSON outputs to outputs/case_bundles/<case_id>/

Output files are gitignored (*.json) — they are ephemeral analysis artifacts.

Usage:
    from bundle_scoring import score_case_bundle

    bundle = score_case_bundle(
        results=search_results,       # 6-bucket dict from search_artifacts()
        telemetry=telemetry,          # telemetry dict from search_artifacts()
        case_meta={
            "defendant": "John Smith",
            "jurisdiction": "Phoenix, AZ",
            "incident_year": "2022",
            "region_id": "PPD",
        },
    )
"""

import os
import re
import json
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from search_backends import check_search_credentials

# Conditional import — jurisdiction_portals may not always be available
try:
    from jurisdiction_portals import (
        get_jurisdiction_config,
        get_agency_youtube_channels,
        get_transparency_portals,
        get_search_domains_for_region,
        RECORDS_DOMAINS,
        DISPATCH_DOMAINS,
    )
    _HAS_JURISDICTION = True
except ImportError:
    _HAS_JURISDICTION = False


# =============================================================================
# CONSTANTS
# =============================================================================

# Artifact lanes
LANE_BWC = "BWC"
LANE_INTERROGATION = "INTERROGATION"
LANE_SURVEILLANCE = "SURVEILLANCE"
LANE_COURT_VIDEO = "COURT_VIDEO"
ALL_LANES = [LANE_BWC, LANE_INTERROGATION, LANE_SURVEILLANCE, LANE_COURT_VIDEO]

# Bucket-to-lane mapping (from artifact_hunter.py's 6-bucket system)
BUCKET_TO_LANE = {
    "body_cam": LANE_BWC,
    "interrogation": LANE_INTERROGATION,
    "court": LANE_COURT_VIDEO,
}

SURVEILLANCE_KEYWORDS = [
    "surveillance", "cctv", "security camera", "ring doorbell",
    "store camera", "gas station camera", "parking lot camera",
    "traffic camera", "ring camera", "nest cam",
]

# Source trust levels (duplicated from artifact_hunter PRIMARY_SOURCE_DOMAINS
# to avoid importing artifact_hunter which has side effects)
PRIMARY_SOURCE_DOMAINS = {
    "courtlistener.com", "unicourt.com", "pacermonitor.com",
    "broadcastify.com", "openmhz.com",
}

# Known true crime repost channels (subset — not official sources)
REPOST_CHANNELS = {
    "police activity", "real world police", "body cam watch",
    "law&crime network", "court tv", "law and crime",
    "crimeonline", "true crime daily",
}

# Source trust scores
TRUST_OFFICIAL = 1.0
TRUST_MEDIA = 0.6
TRUST_REPOST = 0.3
TRUST_UNKNOWN = 0.15

# Per-artifact confidence weights
W_SOURCE_TRUST = 0.35
W_ENTITY_MATCH = 0.30
W_TIMELINE_FIT = 0.20
W_CORROBORATION = 0.15

# Bundle synthesis weights
W_COVERAGE = 0.40
W_QUALITY = 0.25
W_TIMELINE = 0.20
W_PROVENANCE = 0.15

# Tier thresholds
T1_THRESHOLD = 80
T2_THRESHOLD = 60

# Bundle status thresholds
READY_THRESHOLD = 75
DISCOVERY_THRESHOLD = 60

# Name suffixes to strip
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq"}


# =============================================================================
# ENTITY NORMALIZATION
# =============================================================================

def normalize_entity(defendant_name: str, jurisdiction: str,
                     year: str = "") -> Dict:
    """Produce normalized name variants and jurisdiction tokens for matching.

    Returns:
        {
            "canonical": "john smith",
            "last_name": "smith",
            "variants": ["john smith", "j smith", "smith", ...],
            "jurisdiction_tokens": ["phoenix", "maricopa", "az"],
            "year": "2022",
        }
    """
    # Handle multi-defendant (comma-separated) — use first defendant
    primary = defendant_name.split(",")[0].strip() if defendant_name else ""
    canonical = primary.lower().strip()

    # Strip suffixes
    parts = canonical.split()
    parts = [p for p in parts if p.rstrip(".") not in NAME_SUFFIXES]

    if not parts:
        return {
            "canonical": canonical,
            "last_name": "",
            "variants": [canonical] if canonical else [],
            "jurisdiction_tokens": _tokenize_jurisdiction(jurisdiction),
            "year": year,
        }

    last = parts[-1]
    first = parts[0] if len(parts) > 1 else ""
    middle_parts = parts[1:-1] if len(parts) > 2 else []

    variants = set()
    # Full name
    full = " ".join(parts)
    variants.add(full)
    # Last name alone
    variants.add(last)

    if first:
        # First Last
        variants.add(f"{first} {last}")
        # Last, First
        variants.add(f"{last} {first}")
        # F. Last (initial)
        variants.add(f"{first[0]} {last}")
        # First M. Last (if middle name)
        if middle_parts:
            variants.add(f"{first} {middle_parts[0][0]} {last}")

    # Handle hyphenated last names
    if "-" in last:
        for part in last.split("-"):
            variants.add(part)
            if first:
                variants.add(f"{first} {part}")

    # Remove empty strings
    variants.discard("")

    return {
        "canonical": canonical,
        "last_name": last,
        "variants": sorted(variants, key=len, reverse=True),  # longest first
        "jurisdiction_tokens": _tokenize_jurisdiction(jurisdiction),
        "year": year,
    }


def _tokenize_jurisdiction(jurisdiction: str) -> List[str]:
    """Split jurisdiction into searchable tokens."""
    if not jurisdiction:
        return []

    # State abbreviation expansion
    STATE_MAP = {
        "ca": "california", "fl": "florida", "az": "arizona",
        "wa": "washington", "co": "colorado", "tx": "texas",
        "oh": "ohio", "ga": "georgia", "ut": "utah",
    }

    tokens = []
    for part in jurisdiction.split(","):
        part = part.strip().lower()
        if not part:
            continue
        tokens.append(part)
        # Expand state abbreviations
        if part in STATE_MAP:
            tokens.append(STATE_MAP[part])

    return tokens


# =============================================================================
# LANE CLASSIFICATION
# =============================================================================

def classify_into_lanes(results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Reclassify raw search buckets into 4 artifact lanes + supporting.

    Input buckets:  body_cam, interrogation, court, docket, dispatch, other
    Output lanes:   BWC, INTERROGATION, SURVEILLANCE, COURT_VIDEO,
                    _supporting (docket + dispatch), _unclassified
    """
    lanes = {lane: [] for lane in ALL_LANES}
    lanes["_supporting"] = []
    lanes["_unclassified"] = []

    for bucket, items in results.items():
        if bucket in BUCKET_TO_LANE:
            lane = BUCKET_TO_LANE[bucket]
            lanes[lane].extend(items)
        elif bucket in ("docket", "dispatch"):
            lanes["_supporting"].extend(items)
        elif bucket == "other":
            for item in items:
                if _match_surveillance(item):
                    lanes[LANE_SURVEILLANCE].append(item)
                else:
                    lanes["_unclassified"].append(item)

    return lanes


def _match_surveillance(result: Dict) -> bool:
    """Check if a result belongs in the surveillance lane."""
    text = (result.get("title", "") + " " + result.get("snippet", "") +
            " " + result.get("url", "")).lower()
    return any(kw in text for kw in SURVEILLANCE_KEYWORDS)


# =============================================================================
# PER-ARTIFACT CONFIDENCE SCORING
# =============================================================================

def _classify_source_tier(result: Dict, region_id: str = "") -> str:
    """Classify a result as official / media / repost / unknown."""
    url = result.get("url", "").lower()
    channel = result.get("channel", "").lower()
    source = result.get("source", "")

    # Check primary source domains
    if any(domain in url for domain in PRIMARY_SOURCE_DOMAINS):
        return "official"

    # Check jurisdiction-specific portals
    if _HAS_JURISDICTION and region_id:
        try:
            portals = get_transparency_portals(region_id)
            if any(portal.lower() in url for portal in portals):
                return "official"
        except Exception:
            pass

        # Check agency YouTube channels
        try:
            agency_channels = get_agency_youtube_channels(region_id)
            for ch_info in agency_channels:
                ch_name = ch_info.get("name", "").lower() if isinstance(ch_info, dict) else str(ch_info).lower()
                if ch_name and ch_name in channel:
                    return "official"
        except Exception:
            pass

    # Check known repost/media channels
    if channel:
        for repost_name in REPOST_CHANNELS:
            if repost_name in channel:
                return "media"  # Law&Crime etc. are media, not repost

    # Government domains
    if ".gov" in url or ".us" in url:
        return "official"

    # PDF (likely court document)
    if url.endswith(".pdf"):
        return "media"

    # News domains (known patterns)
    news_patterns = ["news", "press", "media", "post", "times", "tribune",
                     "herald", "gazette", "journal", "abc", "nbc", "cbs",
                     "fox", "cnn", "ap"]
    if any(p in url for p in news_patterns):
        return "media"

    return "unknown"


def _score_source_trust(result: Dict, region_id: str = "") -> Tuple[float, str]:
    """Score source trustworthiness. Returns (score, tier_name)."""
    tier = _classify_source_tier(result, region_id)
    scores = {
        "official": TRUST_OFFICIAL,
        "media": TRUST_MEDIA,
        "repost": TRUST_REPOST,
        "unknown": TRUST_UNKNOWN,
    }
    return scores.get(tier, TRUST_UNKNOWN), tier


def _score_entity_match(result: Dict, entity_info: Dict) -> Tuple[float, str]:
    """Score how well result matches the defendant entity. Returns (score, level)."""
    text = (result.get("title", "") + " " + result.get("snippet", "")).lower()

    if not text.strip():
        return 0.0, "none"

    # Check variants (longest first — most specific match wins)
    for variant in entity_info.get("variants", []):
        if variant in text:
            # If full canonical name is in title specifically
            if variant == entity_info.get("canonical") and variant in result.get("title", "").lower():
                return 1.0, "exact"
            return 0.85, "strong"

    # Check last name + jurisdiction token
    last_name = entity_info.get("last_name", "")
    if last_name and last_name in text:
        juris_tokens = entity_info.get("jurisdiction_tokens", [])
        if any(tok in text for tok in juris_tokens):
            return 0.6, "partial"
        return 0.25, "weak"

    return 0.0, "none"


def _score_timeline_fit(result: Dict, incident_year: str) -> float:
    """Score temporal alignment between result and incident."""
    published = result.get("published_at", "")

    # Try to extract year from published_at
    pub_year = None
    if published:
        match = re.search(r"(\d{4})", published)
        if match:
            pub_year = int(match.group(1))

    # If no published date, try snippet
    if pub_year is None:
        snippet = result.get("snippet", "") + " " + result.get("title", "")
        years = re.findall(r"\b(20\d{2})\b", snippet)
        if years:
            pub_year = int(years[-1])  # latest year mentioned

    if not incident_year:
        return 0.5  # neutral — no reference year

    try:
        inc_year = int(incident_year)
    except (ValueError, TypeError):
        return 0.5

    if pub_year is None:
        return 0.5  # neutral — no date signal

    diff = pub_year - inc_year

    if diff < -1:
        return 0.1   # well before incident — probably wrong case
    elif diff == -1:
        return 0.3   # year before — possible pre-arrest coverage
    elif diff == 0 or diff == 1:
        return 1.0   # same year or next — peak artifact release window
    elif diff <= 3:
        return 0.7   # trial/sentencing window
    else:
        return 0.4   # old case, documentary reupload


def _score_corroboration(lane_results: List[Dict],
                         supporting_results: List[Dict]) -> float:
    """Score based on quantity and cross-source corroboration."""
    n_lane = len(lane_results)
    n_support = len(supporting_results)

    # Base score from lane count
    if n_lane == 0:
        base = 0.0
    elif n_lane == 1:
        base = 0.3
    elif n_lane == 2:
        base = 0.6
    else:
        base = 0.8

    # Bonus for supporting materials (docket, dispatch)
    if n_support > 0:
        base = min(1.0, base + 0.15)

    # Bonus for multi-source agreement (YouTube + Brave both found artifacts)
    sources = {r.get("source", "") for r in lane_results}
    if len(sources) >= 2:
        base = min(1.0, base + 0.1)

    return base


def score_artifact(result: Dict, entity_info: Dict, incident_year: str,
                   region_id: str, lane: str, lane_results: List[Dict],
                   supporting_results: List[Dict],
                   degraded_apis: Dict[str, bool] = None) -> Dict:
    """Score a single artifact result.

    Returns scored artifact dict with confidence, tier, breakdown, and flags.
    """
    st_score, st_tier = _score_source_trust(result, region_id)
    em_score, em_level = _score_entity_match(result, entity_info)
    tf_score = _score_timeline_fit(result, incident_year)
    co_score = _score_corroboration(lane_results, supporting_results)

    confidence = (st_score * W_SOURCE_TRUST + em_score * W_ENTITY_MATCH +
                  tf_score * W_TIMELINE_FIT + co_score * W_CORROBORATION) * 100

    # Tier assignment (conjunctive — not just threshold)
    if confidence >= T1_THRESHOLD and st_tier == "official":
        tier = "T1"
    elif confidence >= T2_THRESHOLD and co_score >= 0.3:
        tier = "T2"
    else:
        tier = "T3"

    # API degradation flags
    flags = []
    if degraded_apis:
        video_lanes = {LANE_BWC, LANE_INTERROGATION, LANE_COURT_VIDEO, LANE_SURVEILLANCE}
        if (lane in video_lanes and
                degraded_apis.get("youtube", False) and
                degraded_apis.get("vimeo", False) and
                result.get("source") in ("brave", "exa_fallback", "exa")):
            flags.append("PRIMARY_VIDEO_UNVERIFIED")

    return {
        "artifact_id": _make_artifact_id(result),
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "lane": lane,
        "source": result.get("source", ""),
        "source_class": st_tier,
        "confidence": round(confidence, 1),
        "tier": tier,
        "breakdown": {
            "source_trust": {"raw": round(st_score, 2), "weighted": round(st_score * W_SOURCE_TRUST * 100, 1)},
            "entity_match": {"raw": round(em_score, 2), "level": em_level,
                             "weighted": round(em_score * W_ENTITY_MATCH * 100, 1)},
            "timeline_fit": {"raw": round(tf_score, 2), "weighted": round(tf_score * W_TIMELINE_FIT * 100, 1)},
            "corroboration": {"raw": round(co_score, 2), "weighted": round(co_score * W_CORROBORATION * 100, 1)},
        },
        "matched_entities": {
            "entity_match_level": em_level,
        },
        "discovered_at": dt.datetime.utcnow().isoformat() + "Z",
        "corroborated_by": [],
        "flags": flags,
    }


def _make_artifact_id(result: Dict) -> str:
    """Generate a short deterministic ID from URL."""
    url = result.get("url", "unknown")
    # Simple hash — last 8 chars of hex digest
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:8]


# =============================================================================
# API DEGRADATION DETECTION
# =============================================================================

def detect_api_degradation(telemetry: Dict) -> Dict[str, bool]:
    """Detect which APIs returned zero results despite being configured.

    Returns: {"youtube": True, ...} where True = degraded.
    """
    creds = check_search_credentials()
    degraded = {}

    degraded["youtube"] = (creds.get("youtube", False) and
                           telemetry.get("youtube_hits", 0) == 0)
    degraded["vimeo"] = (creds.get("vimeo", False) and
                         telemetry.get("vimeo_hits", 0) == 0)
    degraded["brave"] = (creds.get("brave", False) and
                         telemetry.get("brave_hits", 0) == 0)

    return degraded


# =============================================================================
# BUNDLE SYNTHESIS
# =============================================================================

def synthesize_bundle(lane_scores: Dict[str, List[Dict]],
                      entity_info: Dict,
                      degradation: Dict) -> Dict:
    """Compute bundle-level score from per-artifact lane scores.

    Returns bundle dict with score, status, component scores, and lane summary.
    """
    # Lane summary — best artifact per lane
    lane_summary = {}
    best_per_lane = {}

    for lane in ALL_LANES:
        artifacts = lane_scores.get(lane, [])
        if artifacts:
            best = max(artifacts, key=lambda a: a["confidence"])
            lane_summary[lane] = {
                "best_tier": best["tier"],
                "best_confidence": best["confidence"],
                "count": len(artifacts),
                "best_url": best["url"],
                "t1_count": sum(1 for a in artifacts if a["tier"] == "T1"),
                "t2_count": sum(1 for a in artifacts if a["tier"] == "T2"),
            }
            best_per_lane[lane] = best
        else:
            lane_summary[lane] = {
                "best_tier": None,
                "best_confidence": 0,
                "count": 0,
                "best_url": None,
                "t1_count": 0,
                "t2_count": 0,
            }

    # Coverage: proportion of lanes with at least 1 T1/T2 artifact
    covered_lanes = sum(
        1 for lane in ALL_LANES
        if lane_summary[lane]["t1_count"] > 0 or lane_summary[lane]["t2_count"] > 0
    )
    coverage = covered_lanes / len(ALL_LANES)

    # Quality: average confidence of best artifact per covered lane
    if best_per_lane:
        quality = sum(a["confidence"] for a in best_per_lane.values()) / (len(ALL_LANES) * 100)
    else:
        quality = 0.0

    # Timeline: average timeline_fit across best artifacts
    if best_per_lane:
        timeline_scores = [a["breakdown"]["timeline_fit"]["raw"]
                           for a in best_per_lane.values()]
        timeline = sum(timeline_scores) / len(timeline_scores)
    else:
        timeline = 0.0

    # Provenance: proportion of best artifacts from official/media sources
    if best_per_lane:
        official_media = sum(1 for a in best_per_lane.values()
                             if a["source_class"] in ("official", "media"))
        provenance = official_media / len(ALL_LANES)
    else:
        provenance = 0.0

    # Bundle score
    bundle_score = (coverage * W_COVERAGE + quality * W_QUALITY +
                    timeline * W_TIMELINE + provenance * W_PROVENANCE) * 100

    # Status
    if bundle_score >= READY_THRESHOLD:
        status = "READY_FOR_BUNDLE"
    elif bundle_score >= DISCOVERY_THRESHOLD:
        status = "NEEDS_MORE_DISCOVERY"
    else:
        status = "HOLD"

    # Identify gaps
    gaps = [lane for lane in ALL_LANES if lane_summary[lane]["count"] == 0]

    # Collect all flags
    all_flags = set()
    for artifacts in lane_scores.values():
        for a in artifacts:
            all_flags.update(a.get("flags", []))

    degraded_apis = [api for api, is_degraded in degradation.items() if is_degraded]

    return {
        "bundle_score": round(bundle_score, 1),
        "status": status,
        "component_scores": {
            "coverage": round(coverage, 2),
            "quality": round(quality, 2),
            "timeline": round(timeline, 2),
            "provenance": round(provenance, 2),
        },
        "lane_summary": lane_summary,
        "gaps": gaps,
        "flags": sorted(all_flags),
        "degraded_apis": degraded_apis,
    }


# =============================================================================
# OUTPUT WRITER
# =============================================================================

def write_bundle_outputs(case_id: str, raw_results: Dict,
                         scored_artifacts: Dict[str, List[Dict]],
                         bundle: Dict, case_meta: Dict,
                         output_root: str = "outputs/case_bundles") -> str:
    """Write structured JSON outputs to disk.

    Returns path to output directory.
    """
    out_dir = Path(output_root) / case_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. raw_hits.jsonl — one line per raw result
    with open(out_dir / "raw_hits.jsonl", "w") as f:
        for bucket, items in raw_results.items():
            for item in items:
                record = {**item, "_bucket": bucket}
                f.write(json.dumps(record) + "\n")

    # 2. artifact_scores.json — scored artifacts by lane
    scores_doc = {
        "case_id": case_id,
        "scored_at": dt.datetime.utcnow().isoformat() + "Z",
        "entity_info": case_meta.get("_entity_info", {}),
        "lanes": {
            lane: artifacts for lane, artifacts in scored_artifacts.items()
            if not lane.startswith("_")
        },
    }
    with open(out_dir / "artifact_scores.json", "w") as f:
        json.dump(scores_doc, f, indent=2)

    # 3. case_bundle_candidate.json — the final bundle
    bundle_doc = {
        "case_id": case_id,
        "defendant": case_meta.get("defendant", ""),
        "jurisdiction": case_meta.get("jurisdiction", ""),
        "year": case_meta.get("incident_year", ""),
        **bundle,
        "scored_at": dt.datetime.utcnow().isoformat() + "Z",
    }
    with open(out_dir / "case_bundle_candidate.json", "w") as f:
        json.dump(bundle_doc, f, indent=2)

    return str(out_dir)


# =============================================================================
# CASE ID GENERATION
# =============================================================================

def generate_case_id(entity_info: Dict) -> str:
    """Generate a case_id from entity info: lastname_jurisdiction_year."""
    last = re.sub(r"[^a-z0-9]", "", entity_info.get("last_name", "unknown"))
    juris_tokens = entity_info.get("jurisdiction_tokens", [])
    juris = re.sub(r"[^a-z0-9]", "", juris_tokens[0]) if juris_tokens else "unk"
    year = entity_info.get("year", "")
    return f"{last}_{juris}_{year}" if year else f"{last}_{juris}"


# =============================================================================
# PUBLIC ORCHESTRATOR
# =============================================================================

def score_case_bundle(results: Dict, telemetry: Dict,
                      case_meta: Dict,
                      emit_files: bool = True) -> Dict:
    """Score a case bundle from raw search results.

    This is the single entry point for the bundle scoring engine.

    Args:
        results: The 6-bucket results dict from search_artifacts()
        telemetry: The telemetry dict from search_artifacts()
        case_meta: {
            "defendant": "John Smith",
            "jurisdiction": "Phoenix, AZ",
            "incident_year": "2022",
            "region_id": "PPD",
            "case_id": "smith_phx_2022",  # optional
        }
        emit_files: Whether to write JSON outputs to disk (default True)

    Returns:
        The full bundle candidate dict.
    """
    defendant = case_meta.get("defendant", "")
    jurisdiction = case_meta.get("jurisdiction", "")
    incident_year = case_meta.get("incident_year", "")
    region_id = case_meta.get("region_id", "")

    # Step 1: Normalize entity
    entity_info = normalize_entity(defendant, jurisdiction, incident_year)
    case_meta["_entity_info"] = entity_info

    # Step 2: Classify into lanes
    lanes = classify_into_lanes(results)
    supporting = lanes.get("_supporting", [])

    # Step 3: Detect API degradation
    degradation = detect_api_degradation(telemetry)

    # Step 4: Score each artifact
    lane_scores = {lane: [] for lane in ALL_LANES}

    for lane in ALL_LANES:
        lane_results = lanes.get(lane, [])
        for result in lane_results:
            scored = score_artifact(
                result=result,
                entity_info=entity_info,
                incident_year=incident_year,
                region_id=region_id,
                lane=lane,
                lane_results=lane_results,
                supporting_results=supporting,
                degraded_apis=degradation,
            )
            lane_scores[lane].append(scored)

    # Step 5: Synthesize bundle
    bundle = synthesize_bundle(lane_scores, entity_info, degradation)

    # Step 6: Generate case_id
    case_id = case_meta.get("case_id") or generate_case_id(entity_info)

    # Step 7: Write outputs
    output_path = None
    if emit_files:
        output_path = write_bundle_outputs(
            case_id=case_id,
            raw_results=results,
            scored_artifacts=lane_scores,
            bundle=bundle,
            case_meta=case_meta,
        )

    # Combine into final result
    bundle["case_id"] = case_id
    bundle["output_path"] = output_path
    bundle["total_artifacts_scored"] = sum(len(v) for v in lane_scores.values())

    return bundle
