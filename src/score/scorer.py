from __future__ import annotations

from typing import Any, Dict, Tuple

from src.common.models import Incident, StageDecision


def _story_value_dimensions(incident: Incident) -> Dict[str, int]:
    return {
        "stakes": min(5, 1 + len(incident.allegations_or_charges)),
        "clarity": 5 if incident.incident_type != "UNSPECIFIED" else 2,
        "tension": 4 if incident.allegations_or_charges else 2,
        "novelty": 4 if "pursuit" in incident.source_title.lower() or "force" in incident.incident_type.lower() else 2,
        "follow_on": 5 if incident.supporting_artifacts else 2,
    }


def _researchability_dimensions(incident: Incident, field_confidence: Dict[str, float]) -> Dict[str, int]:
    confidence_avg = sum(field_confidence.values()) / max(1, len(field_confidence))
    return {
        "source_provenance": 5 if incident.channel_or_publisher else 2,
        "search_anchors": min(5, max(1, int(confidence_avg * 5))),
        "artifact_availability": min(5, len(incident.supporting_artifacts) + 1),
        "cross_source_consistency": 4 if len(incident.supporting_artifacts) >= 2 else 2,
        "gap_tractability": 4 if len(incident.missing_evidence) <= 3 else 2,
    }


def score_incident(incident: Incident, field_confidence: Dict[str, float]) -> Tuple[Dict[str, Any], StageDecision]:
    story_dims = _story_value_dimensions(incident)
    research_dims = _researchability_dimensions(incident, field_confidence)

    incident.story_value_score = sum(story_dims.values())
    incident.researchability_score = sum(research_dims.values())

    evidence_checks = {
        "primary_footage": incident.raw_footage_flag,
        "agency_identified": incident.agency != "UNKNOWN",
        "location_identified": incident.location != "UNKNOWN",
        "date_narrowed": incident.incident_date != "UNKNOWN",
        "one_corroborating_source": len(incident.supporting_artifacts) >= 1,
        "two_plus_corroborating_sources": len(incident.supporting_artifacts) >= 2,
        "provenance_linked": all(bool(a.get("url")) for a in incident.supporting_artifacts) if incident.supporting_artifacts else False,
    }
    incident.evidence_completeness_score = sum(1 for ok in evidence_checks.values() if ok)

    if incident.watermark_flag:
        incident.risk_flags.append("watermark present; authenticity chain review required")
    if incident.agency == "UNKNOWN" and incident.location == "UNKNOWN":
        incident.risk_flags.append("weak jurisdiction anchoring")

    if incident.story_value_score >= 20 and incident.researchability_score >= 20:
        recommendation = "PRIORITY_PACKET"
        reason = "High story value and high researchability."
    elif incident.researchability_score >= 14 and incident.story_value_score >= 14:
        recommendation = "RESEARCH_PACKET"
        reason = "Solid tractability with worthwhile editorial signal."
    elif incident.story_value_score >= 14:
        recommendation = "WATCHLIST_PACKET"
        reason = "Interesting incident; evidence still developing."
    elif incident.risk_flags:
        recommendation = "MANUAL_REVIEW"
        reason = "Risk and ambiguity require human adjudication."
    else:
        recommendation = "ARCHIVE"
        reason = "Low current value; retain for future matching."

    incident.routing_status = recommendation
    incident.decision_reason = reason

    return {
        "story_value": {"dimensions": story_dims, "total": incident.story_value_score},
        "researchability": {"dimensions": research_dims, "total": incident.researchability_score},
        "evidence_completeness": {"checklist": evidence_checks, "total": incident.evidence_completeness_score},
        "risk_flags": incident.risk_flags,
        "missing_evidence": incident.missing_evidence,
        "final_recommendation": recommendation,
    }, StageDecision("score", recommendation, reason)
