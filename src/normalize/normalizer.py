from __future__ import annotations

from typing import Dict, Tuple

from src.common.models import Candidate, Incident, make_incident_id


def normalize_candidate(candidate: Candidate) -> Tuple[Incident, Dict[str, float]]:
    hints = candidate.hints
    incident = Incident(
        incident_id=make_incident_id(candidate.source_url, candidate.publish_date),
        source_type=candidate.source_type,
        source_url=candidate.source_url,
        source_title=candidate.source_title,
        channel_or_publisher=candidate.channel_or_publisher,
        publish_date=candidate.publish_date,
        raw_footage_flag=bool(hints.get("raw_footage_flag", "bodycam" in candidate.source_title.lower())),
        watermark_flag=bool(hints.get("watermark_flag", False)),
        agency=hints.get("agency", "UNKNOWN"),
        incident_date=hints.get("incident_date", "UNKNOWN"),
        location=hints.get("location", "UNKNOWN"),
        people=hints.get("people", []),
        incident_type=hints.get("incident_type", "UNSPECIFIED"),
        allegations_or_charges=hints.get("allegations_or_charges", []),
        supporting_artifacts=hints.get("supporting_artifacts", []),
        narrative_hook=hints.get("narrative_hook", ""),
        story_value_score=0,
        researchability_score=0,
        evidence_completeness_score=0,
        risk_flags=[],
        missing_evidence=[],
        routing_status="ROUTE_ENRICH",
        decision_reason="Initial normalize complete; awaiting enrichment and scoring.",
    )

    confidence = {
        "agency": 0.9 if incident.agency != "UNKNOWN" else 0.2,
        "incident_date": 0.85 if incident.incident_date != "UNKNOWN" else 0.2,
        "location": 0.85 if incident.location != "UNKNOWN" else 0.2,
        "people": 0.8 if incident.people else 0.3,
        "incident_type": 0.75 if incident.incident_type != "UNSPECIFIED" else 0.3,
    }
    return incident, confidence
