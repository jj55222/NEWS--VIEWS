from __future__ import annotations

from typing import Any, Dict, Tuple

from src.common.models import Incident, StageDecision


def _story_value_dimensions(incident: Incident) -> Dict[str, int]:
    return {
        "stakes": min(5, 1 + len(incident.key_allegations_or_events)),
        "clarity": 5 if incident.incident_category != "UNSPECIFIED" else 2,
        "tension": 4 if incident.key_allegations_or_events else 2,
        "novelty": 4 if "pursuit" in incident.title.lower() or "force" in incident.incident_category.lower() else 2,
        "follow_on": 5 if incident.supporting_documents else 2,
    }


def _researchability_dimensions(incident: Incident, field_confidence: Dict[str, float]) -> Dict[str, int]:
    provenance = 5 if incident.publisher else 2
    search_anchors = min(5, max(1, int(sum(field_confidence.values()) / max(len(field_confidence), 1) * 5)))
    artifact_availability = min(5, len(incident.supporting_documents) + 1)
    cross_source_consistency = 4 if len(incident.supporting_documents) >= 2 else 2
    gap_tractability = 4 if len(incident.unresolved_questions) <= 3 else 2
    return {
        "source_provenance": provenance,
        "search_anchors": search_anchors,
        "artifact_availability": artifact_availability,
        "cross_source_consistency": cross_source_consistency,
        "gap_tractability": gap_tractability,
    }


def score_incident(incident: Incident, field_confidence: Dict[str, float]) -> Tuple[Dict[str, Any], StageDecision]:
    story_dims = _story_value_dimensions(incident)
    research_dims = _researchability_dimensions(incident, field_confidence)

    story_value = sum(story_dims.values())
    researchability = sum(research_dims.values())

    evidence_checks = {
        "primary_footage": incident.raw_footage_likelihood >= 0.6,
        "agency_identified": incident.agency != "UNKNOWN",
        "location_identified": incident.location != "UNKNOWN",
        "date_narrowed": incident.date_range != "UNKNOWN",
        "one_corroborating_source": len(incident.supporting_documents) >= 1,
        "two_plus_corroborating_sources": len(incident.supporting_documents) >= 2,
        "legal_or_case_context": any("docket" in d.get("title", "").lower() for d in incident.supporting_documents),
        "post_incident_outcome_context": any("outcome" in q.lower() for q in incident.unresolved_questions) is False,
        "ambiguities_explicit": bool(incident.uncertainty_notes or incident.unresolved_questions),
        "provenance_linked": all(bool(d.get("url")) for d in incident.supporting_documents) if incident.supporting_documents else False,
    }
    completeness = sum(1 for ok in evidence_checks.values() if ok)

    missing_evidence = []
    if incident.date_range == "UNKNOWN":
        missing_evidence.append("exact date still uncertain")
    if incident.agency == "UNKNOWN":
        missing_evidence.append("agency not confirmed")
    if not incident.people_entities:
        missing_evidence.append("suspect identity unclear")
    if not any("docket" in d.get("title", "").lower() for d in incident.supporting_documents):
        missing_evidence.append("no docket hit yet")
    if len(incident.supporting_documents) < 1:
        missing_evidence.append("no corroborating source found")
    if len(incident.supporting_documents) < 2:
        missing_evidence.append("need second corroborating source")

    incident.story_value_score = story_value
    incident.researchability_score = researchability
    incident.evidence_completeness_score = completeness
    incident.unresolved_questions.extend(m for m in missing_evidence if m not in incident.unresolved_questions)

    risk_flags = []
    if incident.watermark_likelihood > 0.7:
        risk_flags.append("unclear footage authenticity")
    if "juvenile" in incident.description.lower():
        risk_flags.append("juvenile involvement")
    if incident.agency == "UNKNOWN" and incident.location == "UNKNOWN":
        risk_flags.append("weak source anchoring")
    incident.risk_flags = risk_flags

    if story_value >= 20 and researchability >= 20:
        recommendation = "PRIORITY_PACKET"
        reason = "High story value with strong researchability."
    elif researchability >= 14 and story_value >= 14:
        recommendation = "RESEARCH_PACKET"
        reason = "Workable editorial value with tractable research path."
    elif story_value >= 14 and researchability < 14:
        recommendation = "WATCHLIST_PACKET"
        reason = "Compelling clip value but evidence remains incomplete."
    elif risk_flags or researchability <= 8:
        recommendation = "MANUAL_REVIEW"
        reason = "Ambiguity/risk requires human judgment rather than hard kill."
    else:
        recommendation = "ARCHIVE"
        reason = "Low present value while preserving candidate history."

    incident.final_recommendation = recommendation

    return (
        {
            "story_value": {"dimensions": story_dims, "total": story_value},
            "researchability": {"dimensions": research_dims, "total": researchability},
            "evidence_completeness": {"checklist": evidence_checks, "total": completeness},
            "risk_flags": risk_flags,
            "missing_evidence_ledger": missing_evidence,
            "final_recommendation": recommendation,
        },
        StageDecision("score", recommendation, reason),
    )
