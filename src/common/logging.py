"""
Structured pipeline logging.

Every candidate preserves:
- provenance (where it came from)
- extracted fields
- field confidence
- routing decisions and reasons
- enrichment queries attempted
- results found/not found
- score breakdown
- final packet status
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class LogEntry:
    """Single log entry for a pipeline decision or action."""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    stage: str = ""  # ingest | normalize | enrich | score | packet
    incident_id: str = ""
    action: str = ""
    detail: str = ""
    confidence: Optional[float] = None
    fields_extracted: dict = field(default_factory=dict)
    decision: str = ""
    reason: str = ""


@dataclass
class PacketLog:
    """
    Audit trail for one incident through the entire pipeline.
    One PacketLog per candidate.
    """
    incident_id: str = ""
    entries: list = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: str = ""

    def add(self, stage: str, action: str, detail: str = "",
            decision: str = "", reason: str = "", confidence: float = None,
            fields_extracted: dict = None):
        """Append a log entry."""
        self.entries.append(LogEntry(
            stage=stage,
            incident_id=self.incident_id,
            action=action,
            detail=detail,
            decision=decision,
            reason=reason,
            confidence=confidence,
            fields_extracted=fields_extracted or {},
        ))

    def finalize(self):
        self.completed_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "entries": [e.__dict__ for e in self.entries],
        }

    def save(self, log_dir: str):
        """Write log to disk."""
        path = Path(log_dir) / f"{self.incident_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


def log_decision(log: PacketLog, stage: str, decision: str, reason: str):
    """Shorthand for logging a routing/scoring decision."""
    log.add(stage=stage, action="decision", decision=decision, reason=reason)
