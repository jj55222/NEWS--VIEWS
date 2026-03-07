from __future__ import annotations

from typing import Dict, Tuple

from src.common.models import Candidate, Incident, StageDecision, make_incident_id


def normalize_candidate(candidate: Candidate) -> Tuple[Incident, Dict[str, float], StageDecision]:
    hints = candidate.hints
    agency = hints.get("agency", "UNKNOWN")
    date_range = hints.get("date_range", hints.get("incident_date", "UNKNOWN"))
    location = hints.get("location", "UNKNOWN")
    people_entities = hints.get("people_entities", hints.get("people", []))
    incident_category = hints.get("incident_category", hints.get("incident_type", "UNSPECIFIED"))
    key_allegations = hints.get("key_allegations_or_events", hints.get("allegations_or_charges", []))
    transcript_anchors = hints.get("transcript_search_anchors", [])

    uncertainty_notes = []
    if agency == "UNKNOWN":
        uncertainty_notes.append("Agency not confirmed")
    if date_range == "UNKNOWN":
        uncertainty_notes.append("Incident date/date-range uncertain")
    if location == "UNKNOWN":
        uncertainty_notes.append("Location uncertain")
    if not transcript_anchors:
        uncertainty_notes.append("Transcript-derived search anchors are sparse")

    incident = Incident(
        incident_id=make_incident_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        source_url=candidate.source_url,
        title=candidate.title,
        description=candidate.description,
        publisher=candidate.publisher,
        published_date=candidate.published_date,
        media_type=candidate.media_type,
        transcript_available=candidate.transcript_available,
        raw_footage_likelihood=candidate.raw_footage_likelihood,
        watermark_likelihood=candidate.watermark_likelihood,
        agency=agency,
        date_range=date_range,
        location=location,
        people_entities=people_entities,
        incident_category=incident_category,
        key_allegations_or_events=key_allegations,
        transcript_search_anchors=transcript_anchors,
        uncertainty_notes=uncertainty_notes,
        unresolved_questions=list(uncertainty_notes),
    )

    confidence = {
        "agency": 0.9 if agency != "UNKNOWN" else 0.2,
        "date_range": 0.85 if date_range != "UNKNOWN" else 0.2,
        "location": 0.85 if location != "UNKNOWN" else 0.2,
        "people_entities": 0.8 if people_entities else 0.3,
        "incident_category": 0.75 if incident_category != "UNSPECIFIED" else 0.3,
        "transcript_search_anchors": 0.75 if transcript_anchors else 0.25,
    }

    strong_fields = sum(1 for k in ("agency", "date_range", "location") if confidence[k] >= 0.8)
    if strong_fields == 3:
        decision = StageDecision("normalize", "NORMALIZED_STRONG", "Core incident anchors were extracted with strong confidence.")
    elif strong_fields >= 1:
        decision = StageDecision("normalize", "NORMALIZED_PARTIAL", "Partial incident normalization succeeded; uncertainty explicitly logged.")
    else:
        decision = StageDecision("normalize", "NORMALIZATION_FAILED", "Insufficient coherent anchors to support autonomous enrichment.")

    return incident, confidence, decision
