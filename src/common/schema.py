"""
Canonical data models for the bodycam bot pipeline.

Every candidate gets an Incident. Every piece of supporting material
gets an Evidence record attached to that Incident. Scoring produces
a ScoringResult. The final output is a CasePacket.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    RSS = "rss"
    EXA = "exa"
    WEB_SEARCH = "web_search"
    MANUAL = "manual"
    YOUTUBE = "youtube"
    COURTLISTENER = "courtlistener"


class IncidentType(str, Enum):
    SHOOTING = "shooting"
    USE_OF_FORCE = "use_of_force"
    PURSUIT = "pursuit"
    IN_CUSTODY_DEATH = "in_custody_death"
    DOMESTIC = "domestic"
    HOMICIDE = "homicide"
    CHILD_ABUSE = "child_abuse"
    OTHER = "other"


class EvidenceType(str, Enum):
    BODYCAM = "bodycam"
    DASHCAM = "dashcam"
    INTERROGATION = "interrogation"
    SURVEILLANCE = "surveillance"
    COURT_VIDEO = "court_video"
    NEWS_CLIP = "news_clip"
    PRESS_CONFERENCE = "press_conference"
    DOCUMENT = "document"


class RoutingStatus(str, Enum):
    """Ingest decides routing, not final editorial judgment."""
    CANDIDATE = "candidate"
    DUPLICATE = "duplicate"
    REJECTED_LOW_SIGNAL = "rejected_low_signal"
    REJECTED_TOO_RECENT = "rejected_too_recent"
    REJECTED_NO_CRIME = "rejected_no_crime"


class EvidenceCompleteness(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    MISSING = "missing"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """A single piece of supporting evidence linked to an incident."""
    evidence_type: EvidenceType
    url: str
    title: str = ""
    source_tier: str = "unknown"  # official | news | repost | unknown
    confidence: float = 0.0  # 0-1
    found_via: str = ""  # which enrichment query found this
    notes: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ScoringResult:
    """Separate story-value and researchability scores."""
    story_value_score: float = 0.0  # 0-100
    researchability_score: float = 0.0  # 0-100
    story_value_breakdown: dict = field(default_factory=dict)
    researchability_breakdown: dict = field(default_factory=dict)
    risk_flags: list = field(default_factory=list)
    decision_reason: str = ""


@dataclass
class Incident:
    """
    Canonical incident model. Every viable candidate gets one.

    This is the single object that flows through the entire pipeline:
    ingest -> normalize -> enrich -> score -> packet.
    """
    # Identity
    incident_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Source
    source_type: str = ""
    source_url: str = ""
    source_title: str = ""
    channel_or_publisher: str = ""
    publish_date: str = ""
    raw_footage_flag: bool = False
    watermark_flag: bool = False

    # Incident details
    agency: str = ""
    incident_date: str = ""
    incident_type: str = ""
    people: list = field(default_factory=list)  # list of dicts: {name, role}
    allegations_or_charges: list = field(default_factory=list)
    jurisdiction: dict = field(default_factory=dict)  # {city, county, state}

    # Narrative
    narrative_hook: str = ""
    story_summary: str = ""

    # Scoring (filled by score stage)
    story_value_score: float = 0.0
    researchability_score: float = 0.0

    # Evidence (filled by enrich stage)
    supporting_artifacts: list = field(default_factory=list)  # list of Evidence
    evidence_completeness_score: float = 0.0

    # Pipeline status
    routing_status: str = RoutingStatus.CANDIDATE.value
    routing_reason: str = ""
    risk_flags: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)  # what we looked for but didn't find
    decision_reason: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, list):
                d[k] = [
                    item.to_dict() if hasattr(item, "to_dict") else
                    (item.__dict__ if hasattr(item, "__dict__") and not isinstance(item, (str, int, float, bool)) else item)
                    for item in v
                ]
            elif isinstance(v, Enum):
                d[k] = v.value
            else:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Incident":
        """Deserialize from dict."""
        artifacts = data.pop("supporting_artifacts", [])
        inc = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        inc.supporting_artifacts = [
            Evidence(**a) if isinstance(a, dict) else a for a in artifacts
        ]
        return inc


@dataclass
class CasePacket:
    """
    Final output: a structured, evidence-backed case packet
    ready for a human editor to review.
    """
    packet_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    incident: Optional[Incident] = None
    evidence_summary: str = ""
    evidence_count: int = 0
    strongest_artifact_type: str = ""
    story_value_score: float = 0.0
    researchability_score: float = 0.0
    composite_score: float = 0.0
    missing_evidence: list = field(default_factory=list)
    risk_flags: list = field(default_factory=list)
    recommendation: str = ""  # STRONG | MODERATE | WEAK | SKIP
    decision_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        if self.incident:
            d["incident"] = self.incident.to_dict()
        return d
