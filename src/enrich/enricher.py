from __future__ import annotations

from typing import Dict, Any, List

from src.common.models import Incident


def build_enrichment_queries(incident: Incident) -> List[str]:
    return [
        f"{incident.agency} body camera policy",
        f"{incident.location} court docket {incident.incident_date}",
        f"{incident.source_title} complaint affidavit",
    ]


def run_lightweight_enrichment(incident: Incident) -> Dict[str, Any]:
    queries = build_enrichment_queries(incident)
    results = []

    # Hook: deterministic placeholders so pipeline remains auditable in offline mode.
    for q in queries:
        found = "UNKNOWN" not in q
        results.append(
            {
                "query": q,
                "found": found,
                "artifact": {
                    "artifact_type": "search_stub",
                    "title": q,
                    "url": incident.source_url if found else "",
                },
            }
        )

    for result in results:
        if result["found"]:
            incident.supporting_artifacts.append(result["artifact"])

    return {"queries": queries, "results": results}
