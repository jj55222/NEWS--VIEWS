from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List
import hashlib


INGEST_STATUSES = {"ROUTE_ENRICH", "ROUTE_REVIEW", "ARCHIVE", "KILL"}
NORMALIZE_STATUSES = {"NORMALIZED_STRONG", "NORMALIZED_PARTIAL", "NORMALIZATION_FAILED"}
ENRICH_STATUSES = {"ENRICHED_STRONG", "ENRICHED_PARTIAL", "NEEDS_MANUAL_RESEARCH", "ENRICHMENT_STALLED"}
FINAL_RECOMMENDATIONS = {
    "PRIORITY_PACKET",
    "RESEARCH_PACKET",
    "WATCHLIST_PACKET",
    "MANUAL_REVIEW",
    "ARCHIVE",
    "KILL",
}


@dataclass
class Candidate:
    candidate_id: str
    source_url: str
    title: str
    description: str
    publisher: str
    published_date: str
    media_type: str
    transcript_available: bool
    raw_footage_likelihood: float
    watermark_likelihood: float
    raw_text: str = ""
    hints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Incident:
    incident_id: str
    candidate_id: str
    source_url: str
    title: str
    description: str
    publisher: str
    published_date: str
    media_type: str
    transcript_available: bool
    raw_footage_likelihood: float
    watermark_likelihood: float
    agency: str
    date_range: str
    location: str
    people_entities: List[str]
    incident_category: str
    key_allegations_or_events: List[str]
    transcript_search_anchors: List[str]
    uncertainty_notes: List[str]
    supporting_documents: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    story_value_score: int = 0
    researchability_score: int = 0
    evidence_completeness_score: int = 0
    final_recommendation: str = "MANUAL_REVIEW"

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StageDecision:
    stage: str
    status: str
    reason: str


@dataclass
class AuditEvent:
    candidate_id: str
    timestamp_utc: str
    source_provenance: Dict[str, Any]
    normalization_fields: Dict[str, Any]
    field_confidence: Dict[str, float]
    routing_decisions: List[Dict[str, str]]
    enrichment_queries_attempted: List[str]
    enrichment_results: List[Dict[str, Any]]
    final_scores: Dict[str, Any]
    final_packet_status: str

    @classmethod
    def from_candidate(cls, candidate: Candidate) -> "AuditEvent":
        return cls(
            candidate_id=candidate.candidate_id,
            timestamp_utc=datetime.utcnow().isoformat(),
            source_provenance={
                "source_url": candidate.source_url,
                "title": candidate.title,
                "description": candidate.description,
                "publisher": candidate.publisher,
                "published_date": candidate.published_date,
                "media_type": candidate.media_type,
                "raw_footage_likelihood": candidate.raw_footage_likelihood,
                "watermark_likelihood": candidate.watermark_likelihood,
                "transcript_available": candidate.transcript_available,
            },
            normalization_fields={},
            field_confidence={},
            routing_decisions=[],
            enrichment_queries_attempted=[],
            enrichment_results=[],
            final_scores={},
            final_packet_status="PENDING",
        )

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatchMetrics:
    total_candidates_harvested: int = 0
    candidates_normalized: int = 0
    candidates_enriched: int = 0
    usable_packets: int = 0
    percent_with_1plus_corroborating_source: float = 0.0
    percent_with_2plus_corroborating_sources: float = 0.0
    percent_prematurely_killed: float = 0.0
    average_missing_evidence_count: float = 0.0
    average_story_value_score: float = 0.0
    average_researchability_score: float = 0.0

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


def make_candidate_id(source_url: str, published_date: str) -> str:
    token = f"{source_url}|{published_date}".encode("utf-8")
    return f"CAND-{hashlib.sha1(token).hexdigest()[:12]}"


def make_incident_id(candidate_id: str) -> str:
    return f"INC-{candidate_id.split('-', 1)[-1]}"
