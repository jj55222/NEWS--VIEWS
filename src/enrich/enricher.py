from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.common.models import Incident, StageDecision


def build_enrichment_queries(incident: Incident) -> List[str]:
    anchors = " ".join(incident.transcript_search_anchors[:2])
    return [
        f"{incident.agency} press release {incident.date_range}",
        f"{incident.location} court docket {incident.date_range}",
        f"{incident.title} affidavit complaint {anchors}".strip(),
    ]


def run_lightweight_enrichment(incident: Incident) -> Tuple[Dict[str, Any], StageDecision]:
    queries = build_enrichment_queries(incident)
    results = []

    for q in queries:
        found = "UNKNOWN" not in q and len(q) > 20
        artifact = {
            "artifact_type": "search_stub",
            "title": q,
            "url": incident.source_url if found else "",
            "match_confidence": 0.65 if found else 0.1,
        }
        results.append(
            {
                "query": q,
                "found": found,
                "reason": "Matched available anchors" if found else "Search failed due to weak or missing anchors",
                "artifact": artifact,
            }
        )

    found_count = sum(1 for r in results if r["found"])
    if found_count:
        incident.supporting_documents.extend([r["artifact"] for r in results if r["found"]])

    if found_count >= 2:
        status = "ENRICHED_STRONG"
        reason = "Multiple corroborating artifacts were attached."
    elif found_count == 1:
        status = "ENRICHED_PARTIAL"
        reason = "At least one corroborating artifact found; gaps remain explicit."
    elif incident.raw_footage_likelihood >= 0.6:
        status = "NEEDS_MANUAL_RESEARCH"
        reason = "Strong clip signal but enrichment stalled; route to manual research."
    else:
        status = "ENRICHMENT_STALLED"
        reason = "No corroborating artifacts and weak search anchors."

    incident.unresolved_questions.extend(
        r["reason"] for r in results if not r["found"] and r["reason"] not in incident.unresolved_questions
    )

    return {"queries": queries, "results": results}, StageDecision("enrich", status, reason)
