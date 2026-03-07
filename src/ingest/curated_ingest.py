from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.common.models import Candidate, StageDecision, make_candidate_id


REQUIRED_SOURCE_FIELDS = {
    "source_url",
    "title",
    "description",
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


def _route_ingest(item: Dict[str, Any], candidate: Candidate) -> StageDecision:
    title_desc = f"{candidate.title} {candidate.description}".lower()
    has_anchor = any(
        token in title_desc for token in ("bodycam", "officer", "deputy", "arrest", "stop", "pursuit")
    )

    if any(token in title_desc for token in NEGATIVE_TOKENS):
        return StageDecision("ingest", "KILL", "Commentary/compilation signal dominates incident signal.")

    if candidate.raw_footage_likelihood >= 0.6 and has_anchor:
        return StageDecision("ingest", "ROUTE_ENRICH", "Primary-footage signal is strong enough for autonomous routing.")

    if has_anchor:
        return StageDecision("ingest", "ROUTE_REVIEW", "Incident signal present but primary-footage confidence is moderate.")

    if candidate.raw_footage_likelihood >= 0.3:
        return StageDecision("ingest", "ARCHIVE", "Weak incident anchors; retain for possible future revisit.")

    return StageDecision("ingest", "KILL", "No reliable incident anchors in source metadata.")


def ingest_curated_sources(raw_items: List[Dict[str, Any]]) -> List[Tuple[Candidate, StageDecision]]:
    candidates: List[Tuple[Candidate, StageDecision]] = []
    for item in raw_items:
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(item.keys()))
        if missing:
            continue

        source_url = item["source_url"]
        published_date = item["published_date"]
        candidate = Candidate(
            candidate_id=make_candidate_id(source_url, published_date),
            source_url=source_url,
            title=item["title"],
            description=item.get("description", ""),
            publisher=item["publisher"],
            published_date=published_date,
            media_type=item["media_type"],
            transcript_available=_boolish(item.get("transcript_available"), default=False),
            raw_footage_likelihood=float(item.get("raw_footage_likelihood", 0.5)),
            watermark_likelihood=float(item.get("watermark_likelihood", 0.0)),
            raw_text=item.get("raw_text", ""),
            hints=item.get("hints", {}),
        )
        candidates.append((candidate, _route_ingest(item, candidate)))
    return candidates
