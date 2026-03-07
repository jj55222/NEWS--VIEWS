from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import hashlib


CANONICAL_FIELDS = [
    "incident_id",
    "source_type",
    "source_url",
    "source_title",
    "channel_or_publisher",
    "publish_date",
    "raw_footage_flag",
    "watermark_flag",
    "agency",
    "incident_date",
    "location",
    "people",
    "incident_type",
    "allegations_or_charges",
    "supporting_artifacts",
    "narrative_hook",
    "story_value_score",
    "researchability_score",
    "evidence_completeness_score",
    "risk_flags",
    "missing_evidence",
    "routing_status",
    "decision_reason",
]


@dataclass
class Candidate:
    source_type: str
    source_url: str
    source_title: str
    channel_or_publisher: str
    publish_date: str
    raw_text: str = ""
    hints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Incident:
    incident_id: str
    source_type: str
    source_url: str
    source_title: str
    channel_or_publisher: str
    publish_date: str
    raw_footage_flag: bool
    watermark_flag: bool
    agency: str
    incident_date: str
    location: str
    people: List[str]
    incident_type: str
    allegations_or_charges: List[str]
    supporting_artifacts: List[Dict[str, Any]]
    narrative_hook: str
    story_value_score: int
    researchability_score: int
    evidence_completeness_score: int
    risk_flags: List[str]
    missing_evidence: List[str]
    routing_status: str
    decision_reason: str

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEvent:
    candidate_url: str
    timestamp_utc: str
    provenance: Dict[str, Any]
    extracted_fields: Dict[str, Any]
    field_confidence: Dict[str, float]
    routing_decisions: List[Dict[str, str]]
    enrichment_queries_attempted: List[str]
    enrichment_results: List[Dict[str, Any]]
    score_breakdown: Dict[str, Any]
    final_packet_status: str

    @classmethod
    def from_candidate(cls, candidate_url: str) -> "AuditEvent":
        return cls(
            candidate_url=candidate_url,
            timestamp_utc=datetime.utcnow().isoformat(),
            provenance={},
            extracted_fields={},
            field_confidence={},
            routing_decisions=[],
            enrichment_queries_attempted=[],
            enrichment_results=[],
            score_breakdown={},
            final_packet_status="PENDING",
        )

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


def make_incident_id(source_url: str, publish_date: str) -> str:
    token = f"{source_url}|{publish_date}".encode("utf-8")
    return f"INC-{hashlib.sha1(token).hexdigest()[:12]}"
