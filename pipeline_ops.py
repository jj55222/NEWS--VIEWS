#!/usr/bin/env python3
"""
NEWS → VIEWS: Pipeline Operations Config

Central source of truth for all rubrics, KPI definitions, scoring thresholds,
and reusable templates. All other pipeline modules import from here.

Usage:
    from pipeline_ops import (
        CHANNEL_RUBRIC, INCIDENT_RUBRIC, CLAUDE_OUTPUT_RUBRIC,
        KPI_DEFINITIONS, score_channel_criteria, score_incident_criteria,
        self_evaluate_output,
    )
"""

from typing import Dict, List, Tuple

# =============================================================================
# 1. OPERATIONAL OBJECTIVE
# =============================================================================

OPERATIONAL_OBJECTIVE = [
    "Discover and qualify YouTube channels publishing raw, non-watermarked BWC footage via auto-discovery + manual seeding",
    "Score and rank channels weekly using the Channel Qualification Rubric (0-100, threshold >= 60 to qualify)",
    "Replace PASS/KILL triage with the Incident Selection Rubric (0-100, threshold >= 55 for strong candidate)",
    "Enrich selected incidents with court records, case documents, interrogation footage, and multi-angle BWC",
    "Track pipeline KPIs weekly via local JSON log + Google Sheets sync for operator visibility",
    "Self-evaluate all Claude outputs against the Claude Output Rubric (>= 80 to ship, < 60 must redo)",
]


# =============================================================================
# 2. KPI FRAMEWORK
# =============================================================================

KPI_DEFINITIONS = {
    # --- Channel qualification ---
    "channels_evaluated": {
        "description": "Channels assessed against qualification rubric this week",
        "type": "count",
        "target_min": 10,
        "target_label": ">=10/week",
        "unit": "channels",
        "red_below": 3,
    },
    "channels_qualified": {
        "description": "Channels scoring >=60 on qualification rubric",
        "type": "count",
        "target_min": 3,
        "target_label": ">=3/week",
        "unit": "channels",
        "red_below": 1,
    },
    "channel_qualification_rate": {
        "description": "Ratio of qualified channels to evaluated channels",
        "type": "ratio",
        "formula": "channels_qualified / channels_evaluated",
        "target_min": 0.30,
        "target_label": ">=30%",
        "red_below": 0.15,
    },
    # --- Incident selection ---
    "incidents_scanned": {
        "description": "Videos scanned from qualified channels this week",
        "type": "count",
        "target_min": 50,
        "target_label": ">=50/week",
        "unit": "videos",
        "red_below": 15,
    },
    "incidents_selected": {
        "description": "Incidents scoring >=55 on incident rubric (strong candidate+)",
        "type": "count",
        "target_min": 5,
        "target_label": ">=5/week",
        "unit": "incidents",
        "red_below": 2,
    },
    "incident_conversion_rate": {
        "description": "Ratio of selected incidents to scanned videos",
        "type": "ratio",
        "formula": "incidents_selected / incidents_scanned",
        "target_min": 0.10,
        "target_label": ">=10%",
        "red_below": 0.05,
    },
    # --- Footage & enrichment ---
    "footage_hit_rate": {
        "description": "Selected incidents with 2+ artifact types found",
        "type": "ratio",
        "formula": "incidents_with_2plus_artifacts / incidents_selected",
        "target_min": 0.60,
        "target_label": ">=60%",
        "red_below": 0.40,
    },
    "enrichment_completion_rate": {
        "description": "Selected incidents with court records + case docs located",
        "type": "ratio",
        "formula": "fully_enriched / incidents_selected",
        "target_min": 0.50,
        "target_label": ">=50%",
        "red_below": 0.25,
    },
    # --- Resource usage ---
    "exa_credits_used": {
        "description": "Exa API calls consumed this week",
        "type": "count",
        "target_max": 100,
        "target_label": "<=100/week",
        "unit": "calls",
        "red_above": 150,
    },
    "youtube_api_units_used": {
        "description": "YouTube Data API units consumed this week",
        "type": "count",
        "target_max": 8000,
        "target_label": "<=8000/week",
        "unit": "units",
        "red_above": 9500,
    },
    "llm_calls_used": {
        "description": "OpenRouter LLM calls this week",
        "type": "count",
        "target_max": 200,
        "target_label": "<=200/week",
        "unit": "calls",
        "red_above": 300,
    },
    # --- Output ---
    "content_pieces_produced": {
        "description": "Analysis-over-footage content pieces completed",
        "type": "count",
        "target_min": 2,
        "target_label": ">=2/week",
        "unit": "pieces",
        "red_below": 0,
    },
}


# =============================================================================
# 3. CHANNEL QUALIFICATION RUBRIC (0-100)
# =============================================================================

CHANNEL_RUBRIC = {
    "criteria": [
        {
            "name": "raw_footage_ratio",
            "description": "Percentage of recent uploads that are raw/minimally-edited BWC footage (not commentary, reactions, or compilations)",
            "max_points": 25,
            "scoring_rules": [
                {"condition": ">=80% raw BWC", "points": 25},
                {"condition": "60-79% raw BWC", "points": 20},
                {"condition": "40-59% raw BWC", "points": 12},
                {"condition": "20-39% raw BWC", "points": 6},
                {"condition": "<20% raw BWC", "points": 0},
            ],
        },
        {
            "name": "no_watermark",
            "description": "Footage is free of channel-specific overlays, watermarks, or branding beyond what the original agency embedded",
            "max_points": 20,
            "scoring_rules": [
                {"condition": "no channel watermark at all", "points": 20},
                {"condition": "small unobtrusive watermark (corner, transparent)", "points": 12},
                {"condition": "moderate watermark (visible but not blocking footage)", "points": 5},
                {"condition": "heavy branding, overlays, or graphics throughout", "points": 0},
            ],
        },
        {
            "name": "upload_frequency",
            "description": "How often new BWC footage is uploaded to the channel",
            "max_points": 15,
            "scoring_rules": [
                {"condition": "daily or near-daily uploads", "points": 15},
                {"condition": "2-3 uploads per week", "points": 12},
                {"condition": "weekly uploads", "points": 8},
                {"condition": "biweekly uploads", "points": 4},
                {"condition": "monthly or less", "points": 0},
            ],
        },
        {
            "name": "footage_length",
            "description": "Average video duration — longer footage provides more usable material",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "avg >=20 minutes", "points": 10},
                {"condition": "avg 10-19 minutes", "points": 8},
                {"condition": "avg 5-9 minutes", "points": 5},
                {"condition": "avg 2-4 minutes", "points": 2},
                {"condition": "avg <2 minutes", "points": 0},
            ],
        },
        {
            "name": "jurisdiction_coverage",
            "description": "Whether the channel's footage maps to jurisdictions in our portal registry or sunshine law states",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "sunshine state agency (FL, TX, AZ) with portal match", "points": 10},
                {"condition": "known jurisdiction in our registry", "points": 7},
                {"condition": "identifiable US jurisdiction not in registry", "points": 4},
                {"condition": "unclear or non-US jurisdiction", "points": 0},
            ],
        },
        {
            "name": "source_tier",
            "description": "Whether this is an official PD channel, verified aggregator, or unknown re-uploader",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "official police department / agency channel", "points": 10},
                {"condition": "verified aggregator with FOIA-sourced footage", "points": 7},
                {"condition": "known aggregator, unclear sourcing", "points": 4},
                {"condition": "unknown re-uploader", "points": 1},
            ],
        },
        {
            "name": "description_quality",
            "description": "Whether videos include searchable case identifiers (names, dates, agencies, case numbers)",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "full case details: names, date, agency, case number", "points": 10},
                {"condition": "names and agency mentioned", "points": 7},
                {"condition": "basic description (location, crime type)", "points": 4},
                {"condition": "no description or clickbait only", "points": 0},
            ],
        },
    ],
    "thresholds": {
        "elite": {"min_score": 85, "label": "ELITE", "action": "Priority monitoring, daily scan"},
        "qualified": {"min_score": 60, "label": "QUALIFIED", "action": "Include in pipeline, weekly scan"},
        "watchlist": {"min_score": 40, "label": "WATCHLIST", "action": "Re-evaluate monthly"},
        "disqualified": {"min_score": 0, "label": "DISQUALIFIED", "action": "Skip unless channel changes"},
    },
    "total_max": 100,
}

# Auto-discovery keywords for finding BWC channels on YouTube
CHANNEL_DISCOVERY_KEYWORDS = [
    "police bodycam footage",
    "body camera video police",
    "body worn camera full video",
    "bodycam arrest footage",
    "police body camera unedited",
    "BWC footage police",
    "officer body camera incident",
    "police dashcam bodycam",
    "law enforcement body camera",
    "bodycam footage released",
]

# Known channel types to classify during discovery
CHANNEL_SOURCE_TIERS = {
    "official_pd": {
        "indicators": ["police department", "sheriff", "police media", "pd media", "law enforcement"],
        "tier_score": 10,
    },
    "verified_aggregator": {
        "indicators": ["police activity", "bodycam nation", "body cam", "real world police",
                        "police pursuits", "law enforcement", "body camera"],
        "tier_score": 7,
    },
    "known_aggregator": {
        "indicators": ["bodycam", "body cam", "police video", "cop cam"],
        "tier_score": 4,
    },
}

# Keywords indicating raw BWC footage (vs. commentary/reaction/compilation)
RAW_FOOTAGE_INDICATORS = [
    "bodycam", "body cam", "body camera", "body-worn camera", "bwc",
    "dashcam", "dash cam", "police camera", "officer camera",
    "full video", "unedited", "raw footage", "released footage",
    "footage released", "police release", "department releases",
]

# Keywords indicating NON-raw content (commentary, reactions, compilations)
NON_RAW_INDICATORS = [
    "reaction", "reacts to", "commentary", "analysis", "breakdown",
    "top 10", "compilation", "best of", "worst of", "explained",
    "review", "opinion", "my thoughts", "let's talk", "podcast",
]


# =============================================================================
# 4. INCIDENT SELECTION RUBRIC (0-100)
# =============================================================================

INCIDENT_RUBRIC = {
    "criteria": [
        {
            "name": "footage_quality",
            "description": "BWC footage clarity, duration, and completeness of incident capture",
            "max_points": 20,
            "scoring_rules": [
                {"condition": "full incident captured, clear audio + video, >=10 min", "points": 20},
                {"condition": "most of incident captured, decent quality, 5-10 min", "points": 15},
                {"condition": "partial capture or mixed quality", "points": 10},
                {"condition": "brief clip or poor quality", "points": 5},
                {"condition": "no usable footage", "points": 0},
            ],
        },
        {
            "name": "multi_source_footage",
            "description": "Multiple footage types available (BWC + interrogation + court + surveillance, etc.)",
            "max_points": 15,
            "scoring_rules": [
                {"condition": "3+ artifact types available", "points": 15},
                {"condition": "2 artifact types available", "points": 10},
                {"condition": "1 type only (BWC)", "points": 5},
                {"condition": "no confirmed footage", "points": 0},
            ],
        },
        {
            "name": "case_documentation",
            "description": "Court records, charging documents, or case files publicly accessible",
            "max_points": 15,
            "scoring_rules": [
                {"condition": "full court record on PACER/CourtListener accessible", "points": 15},
                {"condition": "partial records found (charging docs or docket)", "points": 10},
                {"condition": "news coverage only, no direct court records", "points": 5},
                {"condition": "no documentation found", "points": 0},
            ],
        },
        {
            "name": "narrative_potential",
            "description": "Story has moral complexity, betrayal, authority abuse, twist, or prolonged suffering",
            "max_points": 15,
            "scoring_rules": [
                {"condition": "exceptional: betrayal + authority abuse + twist", "points": 15},
                {"condition": "strong: clear moral abnormality or authority abuse", "points": 12},
                {"condition": "decent: disturbing but straightforward crime", "points": 8},
                {"condition": "routine: standard crime without compelling hook", "points": 3},
                {"condition": "no narrative interest", "points": 0},
            ],
        },
        {
            "name": "legal_stage",
            "description": "Case has progressed far enough for artifacts to be released and story to be complete",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "sentenced or case fully resolved", "points": 10},
                {"condition": "convicted, awaiting sentencing", "points": 8},
                {"condition": "trial completed", "points": 7},
                {"condition": "trial in progress or plea entered", "points": 5},
                {"condition": "pre-trial (charges filed)", "points": 2},
                {"condition": "active investigation (no charges)", "points": 0},
            ],
        },
        {
            "name": "public_interest",
            "description": "Case has demonstrated audience interest via views, coverage, or community discussion",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "viral / national news coverage", "points": 10},
                {"condition": "significant local coverage (multiple outlets)", "points": 7},
                {"condition": "moderate coverage (1-2 outlets)", "points": 4},
                {"condition": "minimal or no coverage", "points": 1},
            ],
        },
        {
            "name": "uniqueness",
            "description": "Not already extensively covered by major true crime channels (JCS, Matt Orchard, etc.)",
            "max_points": 10,
            "scoring_rules": [
                {"condition": "uncovered: no major channel has done this case", "points": 10},
                {"condition": "lightly covered: 1-2 small channels only", "points": 7},
                {"condition": "moderately covered but new angle possible", "points": 4},
                {"condition": "heavily covered by multiple major channels", "points": 0},
            ],
        },
        {
            "name": "jurisdiction_strength",
            "description": "Jurisdiction has strong FOIA / public records access (sunshine states score higher)",
            "max_points": 5,
            "scoring_rules": [
                {"condition": "sunshine state (FL, TX, AZ) with active portal", "points": 5},
                {"condition": "good FOIA state with responsive agencies", "points": 3},
                {"condition": "limited public records access", "points": 1},
                {"condition": "restricted or non-US jurisdiction", "points": 0},
            ],
        },
    ],
    "thresholds": {
        "greenlight": {"min_score": 70, "label": "GREENLIGHT", "action": "Proceed to full enrichment and content production"},
        "strong_candidate": {"min_score": 55, "label": "STRONG CANDIDATE", "action": "Prioritize for artifact hunting"},
        "watchlist": {"min_score": 40, "label": "WATCHLIST", "action": "Hold, revisit if new artifacts surface"},
        "skip": {"min_score": 0, "label": "SKIP", "action": "Do not pursue unless major new information"},
    },
    "total_max": 100,
}

# Sunshine states with strong public records laws
SUNSHINE_STATES = ["FL", "TX", "AZ", "CA", "WA", "CO"]
STRONG_SUNSHINE_STATES = ["FL", "TX", "AZ"]


# =============================================================================
# 5. CLAUDE OUTPUT RUBRIC (Self-Check, 0-100)
# =============================================================================

CLAUDE_OUTPUT_RUBRIC = {
    "criteria": [
        {
            "name": "specificity",
            "max_points": 20,
            "check_question": "Does the output contain concrete data (names, URLs, numeric scores, dates) rather than generic statements?",
            "fail_indicators": ["vague language", "no specific data points", "generic recommendations"],
        },
        {
            "name": "scoring_thresholds",
            "max_points": 15,
            "check_question": "Are all scoring decisions backed by explicit thresholds from the defined rubrics?",
            "fail_indicators": ["ad-hoc scoring", "no threshold referenced", "subjective judgments without rubric"],
        },
        {
            "name": "source_attribution",
            "max_points": 15,
            "check_question": "Is every factual claim traced to a source (URL, API result, document, search query)?",
            "fail_indicators": ["unsourced claims", "invented URLs", "stated without evidence"],
        },
        {
            "name": "actionability",
            "max_points": 15,
            "check_question": "Can the operator act on this output immediately without asking follow-up questions?",
            "fail_indicators": ["requires clarification", "missing next steps", "ambiguous instructions"],
        },
        {
            "name": "schema_compliance",
            "max_points": 10,
            "check_question": "Does the output match the expected JSON/data schema exactly?",
            "fail_indicators": ["missing required fields", "wrong data types", "extra undefined fields"],
        },
        {
            "name": "no_hallucination",
            "max_points": 10,
            "check_question": "Are there zero invented facts, fabricated URLs, or assumed case details?",
            "fail_indicators": ["invented data", "plausible but unverified claims", "guessed URLs"],
        },
        {
            "name": "rubric_adherence",
            "max_points": 10,
            "check_question": "Were the correct rubrics applied with correct weights and thresholds?",
            "fail_indicators": ["wrong rubric used", "incorrect weights", "threshold misapplied"],
        },
        {
            "name": "conciseness",
            "max_points": 5,
            "check_question": "Is the output free of filler, repetition, unnecessary hedging, or bloat?",
            "fail_indicators": ["repeated information", "excessive qualifiers", "padding"],
        },
    ],
    "thresholds": {
        "ship": {"min_score": 80, "label": "SHIP", "action": "Output is ready for use"},
        "revise": {"min_score": 60, "label": "REVISE", "action": "Fix weak sections before shipping"},
        "redo": {"min_score": 0, "label": "REDO", "action": "Output is not usable, start over"},
    },
    "total_max": 100,
}


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def score_rubric(rubric: Dict, scores: Dict[str, int]) -> Dict:
    """
    Score an item against a rubric.

    Args:
        rubric: One of CHANNEL_RUBRIC, INCIDENT_RUBRIC, or CLAUDE_OUTPUT_RUBRIC
        scores: Dict mapping criterion name -> points awarded (must be <= max_points)

    Returns:
        {
            "total_score": int,
            "max_possible": int,
            "breakdown": [{name, awarded, max, pct}, ...],
            "classification": str (e.g. "QUALIFIED", "GREENLIGHT"),
            "action": str,
        }
    """
    breakdown = []
    total = 0
    max_possible = rubric.get("total_max", 100)

    for criterion in rubric["criteria"]:
        name = criterion["name"]
        max_pts = criterion["max_points"]
        awarded = min(scores.get(name, 0), max_pts)  # Cap at max
        awarded = max(awarded, 0)  # Floor at 0
        total += awarded
        breakdown.append({
            "name": name,
            "awarded": awarded,
            "max": max_pts,
            "pct": round(awarded / max_pts * 100) if max_pts > 0 else 0,
        })

    # Determine classification from thresholds
    classification = ""
    action = ""
    # Sort thresholds by min_score descending to find the highest matching tier
    sorted_tiers = sorted(
        rubric["thresholds"].items(),
        key=lambda x: x[1].get("min_score", 0),
        reverse=True,
    )
    for tier_key, tier_def in sorted_tiers:
        if total >= tier_def.get("min_score", 0):
            classification = tier_def["label"]
            action = tier_def["action"]
            break

    return {
        "total_score": total,
        "max_possible": max_possible,
        "breakdown": breakdown,
        "classification": classification,
        "action": action,
    }


def score_channel_criteria(channel_data: Dict) -> Dict:
    """
    Score a YouTube channel against CHANNEL_RUBRIC using extracted channel data.

    Args:
        channel_data: {
            "raw_footage_ratio": float (0.0-1.0),
            "watermark_level": str ("none"|"small"|"moderate"|"heavy"),
            "uploads_per_week": float,
            "avg_duration_minutes": float,
            "jurisdiction_type": str ("sunshine"|"known"|"identifiable"|"unclear"),
            "source_tier": str ("official_pd"|"verified_aggregator"|"known_aggregator"|"unknown"),
            "description_quality": str ("full"|"names_agency"|"basic"|"none"),
        }

    Returns:
        score_rubric result dict
    """
    scores = {}

    # raw_footage_ratio
    ratio = channel_data.get("raw_footage_ratio", 0)
    if ratio >= 0.80:
        scores["raw_footage_ratio"] = 25
    elif ratio >= 0.60:
        scores["raw_footage_ratio"] = 20
    elif ratio >= 0.40:
        scores["raw_footage_ratio"] = 12
    elif ratio >= 0.20:
        scores["raw_footage_ratio"] = 6
    else:
        scores["raw_footage_ratio"] = 0

    # no_watermark
    wm = channel_data.get("watermark_level", "heavy")
    wm_map = {"none": 20, "small": 12, "moderate": 5, "heavy": 0}
    scores["no_watermark"] = wm_map.get(wm, 0)

    # upload_frequency
    uploads = channel_data.get("uploads_per_week", 0)
    if uploads >= 5:
        scores["upload_frequency"] = 15
    elif uploads >= 2:
        scores["upload_frequency"] = 12
    elif uploads >= 1:
        scores["upload_frequency"] = 8
    elif uploads >= 0.5:
        scores["upload_frequency"] = 4
    else:
        scores["upload_frequency"] = 0

    # footage_length
    dur = channel_data.get("avg_duration_minutes", 0)
    if dur >= 20:
        scores["footage_length"] = 10
    elif dur >= 10:
        scores["footage_length"] = 8
    elif dur >= 5:
        scores["footage_length"] = 5
    elif dur >= 2:
        scores["footage_length"] = 2
    else:
        scores["footage_length"] = 0

    # jurisdiction_coverage
    jur = channel_data.get("jurisdiction_type", "unclear")
    jur_map = {"sunshine": 10, "known": 7, "identifiable": 4, "unclear": 0}
    scores["jurisdiction_coverage"] = jur_map.get(jur, 0)

    # source_tier
    tier = channel_data.get("source_tier", "unknown")
    tier_map = {"official_pd": 10, "verified_aggregator": 7, "known_aggregator": 4, "unknown": 1}
    scores["source_tier"] = tier_map.get(tier, 1)

    # description_quality
    desc = channel_data.get("description_quality", "none")
    desc_map = {"full": 10, "names_agency": 7, "basic": 4, "none": 0}
    scores["description_quality"] = desc_map.get(desc, 0)

    return score_rubric(CHANNEL_RUBRIC, scores)


def score_incident_criteria(incident_data: Dict) -> Dict:
    """
    Score an incident against INCIDENT_RUBRIC using extracted incident data.

    Args:
        incident_data: {
            "footage_quality": str ("full"|"most"|"partial"|"brief"|"none"),
            "artifact_types_found": int (0-5+),
            "case_docs_level": str ("full"|"partial"|"news_only"|"none"),
            "narrative_level": str ("exceptional"|"strong"|"decent"|"routine"|"none"),
            "legal_stage": str ("sentenced"|"convicted"|"trial_done"|"trial_active"|"pre_trial"|"investigation"),
            "public_interest": str ("viral"|"significant"|"moderate"|"minimal"),
            "uniqueness": str ("uncovered"|"lightly"|"moderate_new_angle"|"heavily_covered"),
            "jurisdiction_type": str ("sunshine"|"good_foia"|"limited"|"restricted"),
        }

    Returns:
        score_rubric result dict
    """
    scores = {}

    # footage_quality
    fq = incident_data.get("footage_quality", "none")
    fq_map = {"full": 20, "most": 15, "partial": 10, "brief": 5, "none": 0}
    scores["footage_quality"] = fq_map.get(fq, 0)

    # multi_source_footage
    art_count = incident_data.get("artifact_types_found", 0)
    if art_count >= 3:
        scores["multi_source_footage"] = 15
    elif art_count >= 2:
        scores["multi_source_footage"] = 10
    elif art_count >= 1:
        scores["multi_source_footage"] = 5
    else:
        scores["multi_source_footage"] = 0

    # case_documentation
    docs = incident_data.get("case_docs_level", "none")
    docs_map = {"full": 15, "partial": 10, "news_only": 5, "none": 0}
    scores["case_documentation"] = docs_map.get(docs, 0)

    # narrative_potential
    narr = incident_data.get("narrative_level", "none")
    narr_map = {"exceptional": 15, "strong": 12, "decent": 8, "routine": 3, "none": 0}
    scores["narrative_potential"] = narr_map.get(narr, 0)

    # legal_stage
    stage = incident_data.get("legal_stage", "investigation")
    stage_map = {
        "sentenced": 10, "convicted": 8, "trial_done": 7,
        "trial_active": 5, "pre_trial": 2, "investigation": 0,
    }
    scores["legal_stage"] = stage_map.get(stage, 0)

    # public_interest
    pub = incident_data.get("public_interest", "minimal")
    pub_map = {"viral": 10, "significant": 7, "moderate": 4, "minimal": 1}
    scores["public_interest"] = pub_map.get(pub, 1)

    # uniqueness
    uniq = incident_data.get("uniqueness", "heavily_covered")
    uniq_map = {"uncovered": 10, "lightly": 7, "moderate_new_angle": 4, "heavily_covered": 0}
    scores["uniqueness"] = uniq_map.get(uniq, 0)

    # jurisdiction_strength
    jur = incident_data.get("jurisdiction_type", "restricted")
    jur_map = {"sunshine": 5, "good_foia": 3, "limited": 1, "restricted": 0}
    scores["jurisdiction_strength"] = jur_map.get(jur, 0)

    return score_rubric(INCIDENT_RUBRIC, scores)


def self_evaluate_output(evaluations: Dict[str, int]) -> Dict:
    """
    Self-evaluate a Claude output against CLAUDE_OUTPUT_RUBRIC.

    Args:
        evaluations: Dict mapping criterion name -> points awarded.
            e.g. {"specificity": 18, "scoring_thresholds": 12, ...}

    Returns:
        score_rubric result dict with classification (SHIP/REVISE/REDO)
    """
    return score_rubric(CLAUDE_OUTPUT_RUBRIC, evaluations)


# =============================================================================
# REUSABLE TEMPLATES / SCHEMAS
# =============================================================================

# Schema for qualified_channels.json entries
CHANNEL_REGISTRY_SCHEMA = {
    "channel_id": "",
    "channel_name": "",
    "channel_url": "",
    "subscriber_count": 0,
    "total_videos": 0,
    "last_evaluated": "",
    "score": 0,
    "classification": "",
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
    "sample_videos": [],
    "notes": "",
}

# Schema for run_log.json entries (JSONL)
RUN_LOG_SCHEMA = {
    "timestamp": "",
    "run_type": "",  # channel_qualify | incident_score | artifact_hunt | full_pipeline
    "duration_seconds": 0,
    "metrics": {
        "channels_evaluated": 0,
        "channels_qualified": 0,
        "incidents_scanned": 0,
        "incidents_selected": 0,
        "exa_credits_used": 0,
        "youtube_api_units_used": 0,
        "llm_calls_used": 0,
    },
    "errors": [],
    "notes": "",
}

# Schema for incident scoring output
INCIDENT_SCORE_SCHEMA = {
    "video_id": "",
    "video_title": "",
    "channel_id": "",
    "channel_name": "",
    "scored_at": "",
    "score": 0,
    "classification": "",
    "action": "",
    "score_breakdown": {},
    "incident_data": {
        "footage_quality": "",
        "artifact_types_found": 0,
        "case_docs_level": "",
        "narrative_level": "",
        "legal_stage": "",
        "public_interest": "",
        "uniqueness": "",
        "jurisdiction_type": "",
    },
    "case_details": {
        "defendant_names": [],
        "victim_names": [],
        "jurisdiction": "",
        "state": "",
        "incident_year": "",
        "crime_type": "",
        "case_summary": "",
    },
    "artifacts_found": [],
    "notes": "",
}

# Google Sheets tab names for sync
SHEETS_TABS = {
    "channel_registry": "CHANNEL REGISTRY",
    "incident_scores": "INCIDENT SCORES",
    "kpi_dashboard": "KPI DASHBOARD",
    "news_intake": "NEWS INTAKE",
    "case_anchor": "CASE ANCHOR & FOOTAGE CHECK",
}

# Column headers for CHANNEL REGISTRY sheet tab
CHANNEL_REGISTRY_COLUMNS = [
    "Channel ID", "Channel Name", "Score", "Classification",
    "Raw %", "Watermark", "Uploads/Wk", "Avg Duration",
    "Jurisdiction", "Source Tier", "Desc Quality",
    "Last Evaluated", "Notes",
]

# Column headers for INCIDENT SCORES sheet tab
INCIDENT_SCORES_COLUMNS = [
    "Video ID", "Title", "Channel", "Score", "Classification",
    "Footage Quality", "Multi-Source", "Case Docs", "Narrative",
    "Legal Stage", "Public Interest", "Uniqueness", "Jurisdiction",
    "Defendant", "Crime Type", "State", "Scored At", "Action",
]

# Column headers for KPI DASHBOARD sheet tab
KPI_DASHBOARD_COLUMNS = [
    "Week", "Channels Evaluated", "Channels Qualified", "Qual Rate",
    "Incidents Scanned", "Incidents Selected", "Conv Rate",
    "Footage Hit Rate", "Enrichment Rate",
    "Exa Credits", "YT API Units", "LLM Calls",
    "Content Pieces", "Health",
]


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_classification(rubric: Dict, score: int) -> Tuple[str, str]:
    """Get classification label and action for a given score."""
    sorted_tiers = sorted(
        rubric["thresholds"].items(),
        key=lambda x: x[1].get("min_score", 0),
        reverse=True,
    )
    for _, tier_def in sorted_tiers:
        if score >= tier_def.get("min_score", 0):
            return tier_def["label"], tier_def["action"]
    return "UNKNOWN", ""


def validate_rubric_integrity():
    """Verify all rubrics sum to expected max points. Run as sanity check."""
    errors = []
    for name, rubric in [
        ("CHANNEL_RUBRIC", CHANNEL_RUBRIC),
        ("INCIDENT_RUBRIC", INCIDENT_RUBRIC),
        ("CLAUDE_OUTPUT_RUBRIC", CLAUDE_OUTPUT_RUBRIC),
    ]:
        total = sum(c["max_points"] for c in rubric["criteria"])
        expected = rubric.get("total_max", 100)
        if total != expected:
            errors.append(f"{name}: criteria sum to {total}, expected {expected}")
    return errors


if __name__ == "__main__":
    # Quick validation
    errors = validate_rubric_integrity()
    if errors:
        print("RUBRIC INTEGRITY ERRORS:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("All rubrics validated: criteria sum to expected max (100)")

    # Example: score a hypothetical channel
    print("\n--- Example Channel Score ---")
    result = score_channel_criteria({
        "raw_footage_ratio": 0.85,
        "watermark_level": "none",
        "uploads_per_week": 3,
        "avg_duration_minutes": 15,
        "jurisdiction_type": "sunshine",
        "source_tier": "verified_aggregator",
        "description_quality": "names_agency",
    })
    print(f"Score: {result['total_score']}/100 -> {result['classification']}")
    for b in result["breakdown"]:
        print(f"  {b['name']}: {b['awarded']}/{b['max']}")

    # Example: score a hypothetical incident
    print("\n--- Example Incident Score ---")
    result = score_incident_criteria({
        "footage_quality": "full",
        "artifact_types_found": 3,
        "case_docs_level": "partial",
        "narrative_level": "strong",
        "legal_stage": "sentenced",
        "public_interest": "significant",
        "uniqueness": "lightly",
        "jurisdiction_type": "sunshine",
    })
    print(f"Score: {result['total_score']}/100 -> {result['classification']}")
    for b in result["breakdown"]:
        print(f"  {b['name']}: {b['awarded']}/{b['max']}")
