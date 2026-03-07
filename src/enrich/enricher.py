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
            results.append(
                {
                    "query": query,
                    "found": found,
                    "reason": "Brave key missing; using deterministic offline fallback",
                    "artifact": artifact,
                }
            )
            continue

        try:
            hits = _run_brave_query(query, api_key)
            if not hits:
                results.append(
                    {
                        "query": query,
                        "found": False,
                        "reason": "Brave search returned no matches",
                        "artifact": {"artifact_type": "search_result", "title": query, "url": "", "match_confidence": 0.1},
                    }
                )
                continue

            for idx, hit in enumerate(hits[:2]):
                artifact = {
                    "artifact_type": "search_result",
                    "title": hit.get("title", query),
                    "url": hit.get("url", ""),
                    "snippet": hit.get("description", ""),
                    "match_confidence": 0.75 - (idx * 0.1),
                }
                results.append(
                    {
                        "query": query,
                        "found": bool(artifact["url"]),
                        "reason": "Brave corroborating result attached",
                        "artifact": artifact,
                    }
                )
        except Exception as exc:  # broad catch to preserve per-query logging trail
            results.append(
                {
                    "query": query,
                    "found": False,
                    "reason": f"Brave query failed: {exc}",
                    "artifact": {"artifact_type": "search_result", "title": query, "url": "", "match_confidence": 0.05},
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
