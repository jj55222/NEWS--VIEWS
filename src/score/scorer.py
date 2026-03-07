"""
Scoring: separate story value and researchability scores.

Story value = how compelling is this case for content?
Researchability = how likely can we find/obtain the evidence?

These are intentionally separate. A case can be a great story but
hard to research, or easy to research but boring.
"""

from __future__ import annotations

from src.common.schema import Incident, ScoringResult, EvidenceType
from src.common.logging import PacketLog


# ---------------------------------------------------------------------------
# Story value scoring
# ---------------------------------------------------------------------------

def _score_story_value(incident: Incident) -> tuple[float, dict]:
    """
    Score story value (0-100) based on:
    - Narrative hook quality
    - Crime severity
    - People involved (named defendants/victims)
    - Moral weight signals
    """
    score = 0.0
    breakdown = {}

    # Narrative hook present and substantive
    hook = incident.narrative_hook or ""
    if len(hook) > 50:
        breakdown["narrative_hook"] = 20.0
        score += 20.0
    elif len(hook) > 20:
        breakdown["narrative_hook"] = 10.0
        score += 10.0
    else:
        breakdown["narrative_hook"] = 0.0

    # Incident type severity
    severity_map = {
        "homicide": 20.0,
        "child_abuse": 20.0,
        "in_custody_death": 18.0,
        "shooting": 15.0,
        "use_of_force": 12.0,
        "domestic": 10.0,
        "pursuit": 8.0,
        "other": 5.0,
    }
    type_score = severity_map.get(incident.incident_type, 5.0)
    breakdown["incident_severity"] = type_score
    score += type_score

    # Named people (more names = more researchable story)
    people_count = len(incident.people)
    people_score = min(people_count * 5.0, 20.0)
    breakdown["named_people"] = people_score
    score += people_score

    # Charges/allegations specificity
    charges = incident.allegations_or_charges or []
    if len(charges) >= 3:
        breakdown["charges_detail"] = 15.0
        score += 15.0
    elif len(charges) >= 1:
        breakdown["charges_detail"] = 8.0
        score += 8.0
    else:
        breakdown["charges_detail"] = 0.0

    # Summary quality
    summary = incident.story_summary or ""
    if len(summary) > 100:
        breakdown["summary_quality"] = 10.0
        score += 10.0
    elif len(summary) > 40:
        breakdown["summary_quality"] = 5.0
        score += 5.0
    else:
        breakdown["summary_quality"] = 0.0

    # Agency identified (accountability angle)
    if incident.agency:
        breakdown["agency_identified"] = 10.0
        score += 10.0
    else:
        breakdown["agency_identified"] = 0.0

    return min(score, 100.0), breakdown


# ---------------------------------------------------------------------------
# Researchability scoring
# ---------------------------------------------------------------------------

def _score_researchability(incident: Incident) -> tuple[float, dict]:
    """
    Score researchability (0-100) based on:
    - Evidence completeness
    - Number and quality of artifacts found
    - Source tier quality
    - What's missing
    """
    score = 0.0
    breakdown = {}

    # Evidence completeness (already computed by enrich)
    completeness = incident.evidence_completeness_score
    breakdown["evidence_completeness"] = completeness * 0.4
    score += completeness * 0.4

    # Artifact count
    artifact_count = len(incident.supporting_artifacts)
    artifact_score = min(artifact_count * 3.0, 25.0)
    breakdown["artifact_count"] = artifact_score
    score += artifact_score

    # Source tier quality
    tier_scores = {"official": 5.0, "news": 3.0, "repost": 1.0, "unknown": 0.5}
    tier_total = 0.0
    for artifact in incident.supporting_artifacts:
        tier = artifact.source_tier if hasattr(artifact, "source_tier") else "unknown"
        tier_total += tier_scores.get(tier, 0.5)
    tier_total = min(tier_total, 20.0)
    breakdown["source_quality"] = tier_total
    score += tier_total

    # Penalty for missing critical evidence types
    missing = incident.missing_evidence or []
    missing_penalty = len(missing) * 5.0
    breakdown["missing_penalty"] = -missing_penalty
    score -= missing_penalty

    # Bonus: jurisdiction identified (easier to FOIA)
    if incident.jurisdiction and incident.jurisdiction.get("state"):
        breakdown["jurisdiction_known"] = 10.0
        score += 10.0
    else:
        breakdown["jurisdiction_known"] = 0.0

    return max(min(score, 100.0), 0.0), breakdown


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------

def _compute_risk_flags(incident: Incident) -> list[str]:
    """Identify risk factors that a human editor should know about."""
    flags = []

    if not incident.people:
        flags.append("no_named_people")

    if not incident.jurisdiction or not incident.jurisdiction.get("state"):
        flags.append("jurisdiction_unknown")

    if not incident.supporting_artifacts:
        flags.append("zero_evidence_found")

    if incident.incident_type == "other":
        flags.append("unclassified_incident_type")

    # Check for only repost-tier sources
    if incident.supporting_artifacts:
        tiers = {a.source_tier for a in incident.supporting_artifacts
                 if hasattr(a, "source_tier")}
        if tiers == {"repost"} or tiers == {"unknown"}:
            flags.append("no_authoritative_sources")

    return flags


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_incident(
    incident: Incident,
    config,
    log: PacketLog,
) -> ScoringResult:
    """
    Score an incident on story value and researchability.
    Updates the incident in place and returns a ScoringResult.
    """
    story_score, story_breakdown = _score_story_value(incident)
    research_score, research_breakdown = _score_researchability(incident)
    risk_flags = _compute_risk_flags(incident)

    # Decision reason
    reasons = []
    if story_score >= 60 and research_score >= 50:
        reasons.append(f"Strong candidate: story={story_score:.0f}, research={research_score:.0f}")
    elif story_score >= 40 or research_score >= 40:
        reasons.append(f"Moderate candidate: story={story_score:.0f}, research={research_score:.0f}")
    else:
        reasons.append(f"Weak candidate: story={story_score:.0f}, research={research_score:.0f}")

    if risk_flags:
        reasons.append(f"Risks: {', '.join(risk_flags)}")

    result = ScoringResult(
        story_value_score=story_score,
        researchability_score=research_score,
        story_value_breakdown=story_breakdown,
        researchability_breakdown=research_breakdown,
        risk_flags=risk_flags,
        decision_reason="; ".join(reasons),
    )

    # Update incident
    incident.story_value_score = story_score
    incident.researchability_score = research_score
    incident.risk_flags = risk_flags
    incident.decision_reason = result.decision_reason

    log.add("score", "scored",
            detail=f"story={story_score:.0f} research={research_score:.0f} "
                   f"risks={risk_flags}",
            confidence=(story_score + research_score) / 200.0)

    return result
