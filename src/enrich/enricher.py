from __future__ import annotations

from typing import Any, Dict, Tuple

from src.common.models import Incident, StageDecision


def run_lightweight_enrichment(incident: Incident) -> Tuple[Dict[str, Any], StageDecision]:
    queries = [
        f'{incident.agency} {incident.incident_date} {incident.incident_type}',
        f'{incident.location} {incident.incident_date} police press release',
    ]

    results = []
    if incident.agency != "UNKNOWN":
        results.append(
            {
                "artifact_type": "agency_release",
                "title": f"{incident.agency} public information release",
                "url": f"https://records.example/{incident.incident_id}/agency",
                "match_confidence": 0.62,
            }
        )

    if incident.location != "UNKNOWN":
        results.append(
            {
                "artifact_type": "local_context",
                "title": f"Local reporting context for {incident.location}",
                "url": f"https://records.example/{incident.incident_id}/local",
                "match_confidence": 0.55,
            }
        )

    incident.supporting_artifacts.extend(results)

    if len(results) >= 2:
        decision = StageDecision("enrich", "ENRICHED_STRONG", "Multiple corroborating artifacts located.")
    elif len(results) == 1:
        decision = StageDecision("enrich", "ENRICHED_PARTIAL", "Single corroborating artifact located.")
        incident.missing_evidence.append("need second corroborating source")
    else:
        decision = StageDecision("enrich", "NEEDS_MANUAL_RESEARCH", "Automated enrichment found no corroborating artifacts.")
        incident.missing_evidence.append("no corroborating source found")

    return {"queries": queries, "results": results}, decision
