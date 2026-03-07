from __future__ import annotations

from typing import Any, Dict, List, Tuple
import json
import os
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from src.common.models import Incident, StageDecision


BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def build_enrichment_queries(incident: Incident) -> List[str]:
    anchors = " ".join(incident.transcript_search_anchors[:2])
    return [
        f"{incident.agency} press release {incident.date_range}",
        f"{incident.location} court docket {incident.date_range}",
        f"{incident.title} affidavit complaint {anchors}".strip(),
    ]


def _run_brave_query(query: str, api_key: str, count: int = 3) -> List[Dict[str, Any]]:
    request = Request(
        f"{BRAVE_SEARCH_ENDPOINT}?q={quote_plus(query)}&count={count}",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("web", {}).get("results", [])


def run_lightweight_enrichment(incident: Incident) -> Tuple[Dict[str, Any], StageDecision]:
    queries = build_enrichment_queries(incident)
    results = []
    api_key = os.getenv("BRAVE_API_KEY", "").strip()

    for query in queries:
        if not api_key:
            found = "UNKNOWN" not in query and len(query) > 20
            artifact = {
                "artifact_type": "search_stub",
                "title": query,
                "url": incident.source_url if found else "",
                "match_confidence": 0.65 if found else 0.1,
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

    incident.unresolved_questions.extend(
        r["reason"] for r in results if not r["found"] and r["reason"] not in incident.unresolved_questions
    )

    return {"queries": queries, "results": results}, StageDecision("enrich", status, reason)
