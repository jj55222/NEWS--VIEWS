from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.common.models import Candidate, StageDecision, make_candidate_id


REQUIRED_SOURCE_FIELDS = {
    "source_url",
    "title",
    "publisher",
    "published_date",
    "media_type",
}

NEGATIVE_TOKENS = ("commentary", "compilation", "meme", "reaction")


def _boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return default


def _route_ingest(candidate: Candidate) -> StageDecision:
    title_desc = f"{candidate.source_title} {candidate.description}".lower()
    has_incident_anchor = any(
        token in title_desc for token in ("bodycam", "officer", "deputy", "arrest", "stop", "pursuit", "shooting")
    )

    if candidate.raw_footage_likelihood >= 0.6 and has_incident_anchor:
        return StageDecision("ingest", "ROUTE_ENRICH", "Strong primary-footage signal; route to full enrichment lane.")

    if any(token in title_desc for token in NEGATIVE_TOKENS):
        return StageDecision("ingest", "ROUTE_ARCHIVE", "Likely commentary/compilation; retain trace in archive lane.")

    if has_incident_anchor or candidate.raw_footage_likelihood >= 0.3:
        return StageDecision("ingest", "ROUTE_REVIEW", "Promising but uncertain; continue normalization with review routing.")

    return StageDecision("ingest", "ROUTE_ARCHIVE", "Low incident signal; preserve for future reprocessing.")


def ingest_curated_sources(raw_items: List[Dict[str, Any]]) -> List[Tuple[Candidate, StageDecision]]:
    candidates: List[Tuple[Candidate, StageDecision]] = []
    for item in raw_items:
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(item.keys()))
        if missing:
            continue

        source_url = item["source_url"]
        publish_date = item["published_date"]
        raw_like = float(item.get("raw_footage_likelihood", 0.5))
        watermark_like = float(item.get("watermark_likelihood", 0.0))

        candidate = Candidate(
            candidate_id=make_candidate_id(source_url, publish_date),
            source_url=source_url,
            source_title=item["title"],
            description=item.get("description", ""),
            channel_or_publisher=item["publisher"],
            publish_date=publish_date,
            source_type=item["media_type"],
            transcript_available=_boolish(item.get("transcript_available"), default=False),
            raw_footage_flag=raw_like >= 0.6,
            watermark_flag=watermark_like >= 0.6,
            raw_footage_likelihood=raw_like,
            watermark_likelihood=watermark_like,
            raw_text=item.get("raw_text", ""),
            hints=item.get("hints", {}),
        )
        candidates.append((candidate, _route_ingest(candidate)))
    return candidates
