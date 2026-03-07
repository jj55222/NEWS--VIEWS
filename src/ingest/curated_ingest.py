from __future__ import annotations

from typing import List, Dict, Any

from src.common.models import Candidate


REQUIRED_SOURCE_FIELDS = {
    "source_type",
    "source_url",
    "source_title",
    "channel_or_publisher",
    "publish_date",
}


def ingest_curated_sources(raw_items: List[Dict[str, Any]]) -> List[Candidate]:
    candidates: List[Candidate] = []
    for item in raw_items:
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(item.keys()))
        if missing:
            # invalid candidate: ingest can route out later, but keep provenance via placeholder
            continue
        candidates.append(
            Candidate(
                source_type=item["source_type"],
                source_url=item["source_url"],
                source_title=item["source_title"],
                channel_or_publisher=item["channel_or_publisher"],
                publish_date=item["publish_date"],
                raw_text=item.get("raw_text", ""),
                hints=item.get("hints", {}),
            )
        )
    return candidates
