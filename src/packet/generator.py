from __future__ import annotations

from typing import Any, Dict

from src.common.models import Incident


def generate_case_packet(incident: Incident, score_breakdown: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "packet_id": f"PKT-{incident.incident_id}",
        "incident_id": incident.incident_id,
        "routing_status": incident.routing_status,
        "decision_reason": incident.decision_reason,
        "summary": {
            "concise_case_summary": f"{incident.incident_type} incident tied to {incident.agency} in {incident.location}.",
            "why_it_matters": incident.allegations_or_charges[:2] or ["Public-interest accountability potential."],
            "narrative_hook": incident.narrative_hook,
        },
        "evidence_list": incident.supporting_artifacts,
        "open_questions": incident.missing_evidence,
        "recommended_next_steps": [
            "Validate agency records and case identifiers.",
            "Resolve top missing-evidence items before scripting.",
        ],
        "score_snapshot": {
            "story_value_score": incident.story_value_score,
            "researchability_score": incident.researchability_score,
            "evidence_completeness_score": incident.evidence_completeness_score,
            "risk_flags": incident.risk_flags,
        },
        "score_breakdown": score_breakdown,
        "incident_record": incident.asdict(),
    }
