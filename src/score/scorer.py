from __future__ import annotations

from typing import Dict, Any

from src.common.models import Incident


CRITICAL_FIELDS = ["agency", "incident_date", "location"]


def score_incident(incident: Incident, field_confidence: Dict[str, float]) -> Dict[str, Any]:
    hook_points = 25 if incident.narrative_hook else 5
    type_points = 25 if incident.incident_type != "UNSPECIFIED" else 10
    people_points = min(len(incident.people) * 10, 20)
    artifact_points = min(len(incident.supporting_artifacts) * 5, 20)
    severity_points = 10 if incident.allegations_or_charges else 5
    story_value = min(hook_points + type_points + people_points + severity_points + artifact_points, 100)

    known_critical = sum(1 for f in CRITICAL_FIELDS if getattr(incident, f) != "UNKNOWN")
    confidence_points = int(sum(field_confidence.values()) / max(len(field_confidence), 1) * 35)
    researchability = min(known_critical * 15 + len(incident.supporting_artifacts) * 8 + confidence_points, 100)

    missing = []
    for field in CRITICAL_FIELDS:
        if getattr(incident, field) == "UNKNOWN":
            missing.append(field)
    if not incident.supporting_artifacts:
        missing.append("supporting_artifacts")

    completeness = max(0, 100 - len(missing) * 20)

    incident.story_value_score = story_value
    incident.researchability_score = researchability
    incident.evidence_completeness_score = completeness
    incident.missing_evidence = missing

    if missing and any(f in missing for f in CRITICAL_FIELDS):
        incident.routing_status = "ROUTE_HOLD"
        incident.decision_reason = f"Missing critical evidence fields: {', '.join(missing)}"
    elif researchability >= 60 and completeness >= 60:
        incident.routing_status = "ROUTE_PACKET"
        incident.decision_reason = "Sufficient evidence and researchability for packet generation."
    else:
        incident.routing_status = "ROUTE_ENRICH"
        incident.decision_reason = "Needs more corroboration before packet finalization."

    return {
        "story_value": {
            "hook_points": hook_points,
            "type_points": type_points,
            "people_points": people_points,
            "severity_points": severity_points,
            "artifact_points": artifact_points,
            "total": story_value,
        },
        "researchability": {
            "known_critical_fields": known_critical,
            "artifact_count": len(incident.supporting_artifacts),
            "confidence_points": confidence_points,
            "total": researchability,
        },
        "evidence_completeness": {
            "missing_evidence": missing,
            "total": completeness,
        },
    }
