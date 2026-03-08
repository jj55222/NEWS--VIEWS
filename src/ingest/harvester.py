"""
Candidate harvesting: searches for crime articles in configured regions.

Ingest routes — it does NOT perform final editorial judgment.
It finds raw candidates and passes them through prescore + routing.
"""

from __future__ import annotations

import time
from typing import Optional

from src.common.config import Config
from src.common.schema import Incident, SourceType
from src.common.logging import PacketLog
from src.ingest.prescore import compute_prescore
from src.ingest.router import route_candidate
from src.search.orchestrator import SearchOrchestrator


def _build_query(metro_tokens: str) -> str:
    """Build semantic search query from metro tokens."""
    metros = [m.strip() for m in metro_tokens.split("|") if m.strip()]
    primary = metros[0] if metros else "local"
    return (
        f"{primary} criminal case court documents defendant sentenced prison "
        "heinous crime murder abuse neglect charged convicted guilty plea sentencing"
    )


def search_region(
    orchestrator: SearchOrchestrator,
    region_id: str,
    metro_tokens: str,
    start_date: str,
    end_date: str,
    max_results: int,
    min_length: int,
) -> list[dict]:
    """Search for crime articles in one region. Returns raw article dicts."""
    query = _build_query(metro_tokens)
    print(f"\n[{region_id}] Searching: {query[:60]}...")
    print(f"  Providers: {orchestrator.provider_names}")

    try:
        results = orchestrator.search_and_contents(
            query=query,
            num_results=max_results,
            max_characters=15000,
            start_published_date=start_date,
            end_published_date=end_date,
        )
    except Exception as e:
        print(f"  [ERR] Search failed: {e}")
        return []

    articles = []
    for r in results:
        if len(r.text) < min_length:
            continue
        articles.append({
            "url": r.url,
            "title": r.title,
            "text": r.text,
            "published_date": "",
            "score": r.score,
        })

    print(f"  Found {len(articles)} articles (>={min_length} chars)")
    return articles


def harvest_candidates(
    config: Config,
    regions: list[dict],
    seen_urls: Optional[set] = None,
) -> list[tuple[Incident, PacketLog]]:
    """
    Harvest candidates from all provided regions.

    Returns list of (Incident, PacketLog) tuples for candidates
    that pass prescore and routing.
    """
    orchestrator = SearchOrchestrator(config)
    seen = seen_urls or set()
    results = []

    for region in regions:
        region_id = region.get("Region_ID", "").strip()
        if not region_id:
            continue

        metro_tokens = region.get("Metro_Tokens", "").strip()
        start_date = str(region.get("Start_Date") or config.default_start_date)[:10]
        end_date = str(region.get("End_Date") or config.default_end_date)[:10]

        articles = search_region(
            orchestrator, region_id, metro_tokens,
            start_date, end_date,
            config.max_results_per_region,
            config.min_article_length,
        )

        for article in articles:
            url = article.get("url", "")
            if url in seen:
                continue
            seen.add(url)

            # Build a raw incident stub
            incident = Incident(
                source_type=SourceType.WEB_SEARCH.value,
                source_url=url,
                source_title=article.get("title", ""),
                publish_date=article.get("published_date", ""),
            )

            log = PacketLog(incident_id=incident.incident_id)
            log.add("ingest", "harvested",
                     detail=f"region={region_id} url={url}")

            # Prescore
            prescore = compute_prescore(
                article_text=article.get("text", ""),
                url=url,
                region_id=region_id,
            )
            log.add("ingest", "prescore",
                     detail=f"score={prescore['score']} matches={prescore['matches']}",
                     confidence=prescore["score"] / 100.0)

            # Route
            status, reason = route_candidate(prescore, config)
            incident.routing_status = status
            incident.routing_reason = reason
            log.add("ingest", "routed",
                     decision=status, reason=reason)

            if status == "candidate":
                # Stash raw text for normalize stage
                incident._raw_text = article.get("text", "")
                incident._raw_title = article.get("title", "")
                incident._region_id = region_id
                incident._prescore = prescore
                results.append((incident, log))
            else:
                print(f"  [{status.upper()}] {article.get('title', '')[:50]}")

            time.sleep(config.search_sleep)

        time.sleep(config.region_sleep)

    print(f"\n[INGEST] {len(results)} candidates from {len(regions)} regions")
    return results
