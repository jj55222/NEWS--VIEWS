from __future__ import annotations

from typing import Dict, Tuple

from src.common.models import Candidate, Incident, StageDecision, make_incident_id


def normalize_candidate(candidate: Candidate) -> Tuple[Incident, Dict[str, float], StageDecision]:
    hints = candidate.hints
    agency = hints.get("agency", "UNKNOWN")
    incident_date = hints.get("incident_date", hints.get("date_range", "UNKNOWN"))
    location = hints.get("location", "UNKNOWN")
    people = hints.get("people", hints.get("people_entities", []))
    incident_type = hints.get("incident_type", hints.get("incident_category", "UNSPECIFIED"))
    allegations = hints.get("allegations_or_charges", hints.get("key_allegations_or_events", []))
    anchors = hints.get("transcript_search_anchors", [])

    field_confidence = {
        "agency": 0.9 if agency != "UNKNOWN" else 0.2,
        "incident_date": 0.85 if incident_date != "UNKNOWN" else 0.2,
        "location": 0.85 if location != "UNKNOWN" else 0.2,
        "people": 0.8 if people else 0.3,
        "incident_type": 0.75 if incident_type != "UNSPECIFIED" else 0.3,
        "anchors": 0.75 if anchors else 0.25,
    }

    narrative_hook = "; ".join(anchors[:2]) if anchors else (
        allegations[0] if allegations else f"Potential {incident_type.lower()} incident requiring corroboration."
    )

    incident = Incident(
        incident_id=make_incident_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        source_url=candidate.source_url,
        source_title=candidate.source_title,
        description=candidate.description,
        channel_or_publisher=candidate.channel_or_publisher,
        publish_date=candidate.publish_date,
        source_type=candidate.source_type,
        transcript_available=candidate.transcript_available,
        raw_footage_flag=candidate.raw_footage_flag,
        watermark_flag=candidate.watermark_flag,
        raw_footage_likelihood=candidate.raw_footage_likelihood,
        watermark_likelihood=candidate.watermark_likelihood,
        agency=agency,
        incident_date=incident_date,
        location=location,
        people=people,
        incident_type=incident_type,
        allegations_or_charges=allegations,
        narrative_hook=narrative_hook,
        routing_status="NORMALIZED",
        decision_reason="Normalized from source metadata and provided hints.",
    )

    strong_fields = sum(1 for k in ("agency", "incident_date", "location") if field_confidence[k] >= 0.8)
    if strong_fields == 3:
        decision = StageDecision("normalize", "NORMALIZED_STRONG", "Core anchors extracted with strong confidence.")
    elif strong_fields >= 1:
        decision = StageDecision("normalize", "NORMALIZED_PARTIAL", "Partial normalization succeeded with explicit uncertainty.")
    else:
        incident.missing_evidence.extend([
            "agency not confirmed",
            "incident date not confirmed",
            "location not confirmed",
        ])
        decision = StageDecision("normalize", "NORMALIZATION_FAILED", "Insufficient incident anchors for reliable matching.")

    if not people:
        incident.missing_evidence.append("named people not extracted")
    if not anchors:
        incident.missing_evidence.append("transcript-derived hooks not found")

    return incident, field_confidence, decision
