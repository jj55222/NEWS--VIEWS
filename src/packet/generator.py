from __future__ import annotations

from typing import Dict, Any

from src.common.models import Incident


def generate_case_packet(incident: Incident) -> Dict[str, Any]:
    return {
        "packet_id": f"PKT-{incident.incident_id}",
        "incident": incident.asdict(),
        "editor_summary": {
            "narrative_hook": incident.narrative_hook,
            "why_now": incident.decision_reason,
            "risks": incident.risk_flags,
        },
        "research_status": {
            "story_value_score": incident.story_value_score,
            "researchability_score": incident.researchability_score,
            "evidence_completeness_score": incident.evidence_completeness_score,
            "missing_evidence": incident.missing_evidence,
        },
    }
