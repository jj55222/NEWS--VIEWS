from __future__ import annotations

from typing import Any, Dict

from src.common.models import Incident


def _recommended_next_action(recommendation: str) -> str:
    mapping = {
        "PRIORITY_PACKET": "Escalate to editor for immediate scripting and records requests.",
        "RESEARCH_PACKET": "Assign targeted enrichment pass to close top evidence gaps.",
        "WATCHLIST_PACKET": "Track for additional corroboration while preserving current packet.",
        "MANUAL_REVIEW": "Route to analyst for ambiguity and risk adjudication.",
        "ARCHIVE": "Store with rationale; revisit if new sources emerge.",
        "KILL": "Suppress from active queue due to non-incident or duplicate content.",
    }
    return mapping.get(recommendation, "Route to manual review.")


def generate_case_packet(incident: Incident, score_breakdown: Dict[str, Any]) -> Dict[str, Any]:
    evidence_list = [
        {
            "type": doc.get("artifact_type", "unknown"),
            "title": doc.get("title", "Untitled artifact"),
            "url": doc.get("url", ""),
            "match_confidence": doc.get("match_confidence", 0.0),
        }
        for doc in incident.supporting_documents
    ]

    return {
        "packet_id": f"PKT-{incident.incident_id}",
        "incident_id": incident.incident_id,
        "recommendation": incident.final_recommendation,
        "concise_summary": f"{incident.incident_category} incident in {incident.location} involving {incident.agency}.",
        "why_this_case_matters": incident.key_allegations_or_events[:2] or ["Potential accountability/public-interest relevance."],
        "evidence_list": evidence_list,
        "missing_evidence_ledger": incident.unresolved_questions,
        "confidence_notes": {
            "uncertainty_notes": incident.uncertainty_notes,
            "risk_flags": incident.risk_flags,
            "score_snapshot": {
                "story_value_score": incident.story_value_score,
                "researchability_score": incident.researchability_score,
                "evidence_completeness_score": incident.evidence_completeness_score,
            },
        },
        "recommended_next_action": _recommended_next_action(incident.final_recommendation),
        "editor_questions": [
            "What happened?",
            "Where and when did it happen?",
            "Who is involved?",
            "Why does it matter?",
            "What evidence supports this?",
            "What remains unclear?",
            "What should happen next?",
        ],
        "score_breakdown": score_breakdown,
        "incident": incident.asdict(),
    }
