"""
Evidence enrichment: searches for supporting artifacts and attaches them.

For each normalized incident, searches multiple sources for:
- Bodycam / dashcam footage
- Interrogation video
- Court video / trial footage
- Surveillance footage
- News clips

Missing evidence is tracked explicitly — we record what we looked for
and didn't find, not just what we found.
"""

from __future__ import annotations

import time
from typing import Optional

from src.common.config import Config
from src.common.schema import Incident, Evidence, EvidenceType, EvidenceCompleteness
from src.common.logging import PacketLog


def _init_exa(config: Config):
    from exa_py import Exa
    return Exa(api_key=config.exa_api_key)


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

def _build_queries(incident: Incident) -> list[tuple[str, str, list[str]]]:
    """
    Build search queries from incident data.
    Returns list of (evidence_type, query, include_domains).
    """
    queries = []
    video_domains = ["youtube.com", "vimeo.com", "youtu.be"]

    # Get key names
    defendants = [p["name"] for p in incident.people
                  if p.get("role") == "defendant" and p.get("name")]
    defendant = defendants[0] if defendants else ""

    jurisdiction_str = ""
    if incident.jurisdiction:
        parts = [incident.jurisdiction.get("city", ""),
                 incident.jurisdiction.get("county", ""),
                 incident.jurisdiction.get("state", "")]
        jurisdiction_str = ", ".join(p for p in parts if p)

    agency = incident.agency or ""
    year = incident.incident_date[:4] if incident.incident_date else ""

    if not defendant and not jurisdiction_str:
        return queries

    # Bodycam
    if agency:
        queries.append(("bodycam", f"{agency} bodycam footage {defendant} {year}".strip(), video_domains))
        queries.append(("bodycam", f"{agency} body camera video {defendant}".strip(), video_domains))
    if jurisdiction_str:
        queries.append(("bodycam", f"{jurisdiction_str} police bodycam {defendant}".strip(), video_domains))

    # Interrogation
    if defendant:
        queries.append(("interrogation", f"{defendant} interrogation video", video_domains))
        queries.append(("interrogation", f"{defendant} police interview confession", video_domains))

    # Court video
    if defendant:
        queries.append(("court_video", f"{defendant} trial court video sentencing", video_domains))
        queries.append(("court_video", f"{defendant} hearing courtroom footage", video_domains))

    # Surveillance
    if defendant or jurisdiction_str:
        queries.append(("surveillance", f"{defendant} {jurisdiction_str} surveillance footage".strip(), video_domains))

    # News clips (broader search)
    if defendant:
        queries.append(("news_clip", f"{defendant} case news video", []))

    return queries


def _search_exa(exa, query: str, include_domains: list[str],
                num_results: int = 5) -> list[dict]:
    """Run a single Exa search. Returns list of result dicts."""
    try:
        kwargs = {
            "query": query,
            "type": "auto",
            "num_results": num_results,
        }
        if include_domains:
            kwargs["include_domains"] = include_domains

        results = exa.search(**kwargs)
        return [
            {
                "url": r.url,
                "title": getattr(r, "title", ""),
                "score": getattr(r, "score", 0),
            }
            for r in results.results
        ]
    except Exception as e:
        print(f"    [ERR] Search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Source tier classification
# ---------------------------------------------------------------------------

def _classify_source_tier(url: str, title: str) -> str:
    """Classify a source URL into a trust tier."""
    url_lower = url.lower()
    title_lower = title.lower()

    # Official agency channels
    official_signals = [
        "police", "sheriff", "pd", "department", "gov", "official",
        "district attorney", "court",
    ]
    if any(s in url_lower or s in title_lower for s in official_signals):
        return "official"

    # News outlets
    news_signals = [
        "news", "nbc", "cbs", "abc", "fox", "cnn", "herald",
        "tribune", "times", "post", "chronicle",
    ]
    if any(s in url_lower or s in title_lower for s in news_signals):
        return "news"

    # Repost / user upload
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "repost"

    return "unknown"


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------

def enrich_incident(
    incident: Incident,
    config: Config,
    log: PacketLog,
) -> Incident:
    """
    Search for supporting evidence and attach to incident.
    Tracks both found and missing evidence.
    """
    exa = _init_exa(config)
    queries = _build_queries(incident)

    if not queries:
        log.add("enrich", "skip", detail="No searchable data in incident")
        incident.missing_evidence = ["bodycam", "interrogation", "court_video", "surveillance"]
        return incident

    searched_types = set()
    found_types = set()

    for evidence_type_str, query, domains in queries:
        searched_types.add(evidence_type_str)
        results = _search_exa(exa, query, domains)

        log.add("enrich", "search",
                detail=f"type={evidence_type_str} query={query[:60]} results={len(results)}")

        for r in results:
            tier = _classify_source_tier(r["url"], r["title"])
            evidence = Evidence(
                evidence_type=EvidenceType(evidence_type_str),
                url=r["url"],
                title=r["title"],
                source_tier=tier,
                confidence=min(r.get("score", 0) / 1.0, 1.0),
                found_via=query[:80],
            )
            incident.supporting_artifacts.append(evidence)
            found_types.add(evidence_type_str)

        time.sleep(config.exa_sleep)

    # Track what's missing
    all_types = {"bodycam", "interrogation", "court_video", "surveillance"}
    incident.missing_evidence = list(all_types - found_types)

    # Compute completeness
    if len(found_types) >= 3:
        incident.evidence_completeness_score = 80.0
    elif len(found_types) >= 2:
        incident.evidence_completeness_score = 60.0
    elif len(found_types) >= 1:
        incident.evidence_completeness_score = 35.0
    else:
        incident.evidence_completeness_score = 0.0

    # Bonus for official sources
    has_official = any(
        e.source_tier == "official" for e in incident.supporting_artifacts
        if isinstance(e, Evidence)
    )
    if has_official:
        incident.evidence_completeness_score = min(
            incident.evidence_completeness_score + 15, 100
        )

    log.add("enrich", "complete",
            detail=f"found={len(incident.supporting_artifacts)} "
                   f"types={sorted(found_types)} "
                   f"missing={sorted(incident.missing_evidence)} "
                   f"completeness={incident.evidence_completeness_score}")

    return incident
