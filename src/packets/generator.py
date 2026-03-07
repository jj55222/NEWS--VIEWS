"""
Case packet generation: the final pipeline output.

A case packet is a structured, evidence-backed bundle ready for
a human editor to review. It is NOT a final editorial decision —
it's a research deliverable with clear provenance.

Every candidate that survives scoring gets a packet.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from src.common.schema import (
    Incident, CasePacket, Evidence, ScoringResult,
)
from src.common.logging import PacketLog


def _classify_recommendation(
    story_score: float,
    research_score: float,
    risk_flags: list,
) -> str:
    """Classify into STRONG / MODERATE / WEAK / SKIP."""
    composite = (story_score * 0.5) + (research_score * 0.5)

    if composite >= 55 and len(risk_flags) <= 1:
        return "STRONG"
    elif composite >= 40:
        return "MODERATE"
    elif composite >= 25:
        return "WEAK"
    else:
        return "SKIP"


def _build_evidence_summary(incident: Incident) -> str:
    """Human-readable evidence summary."""
    if not incident.supporting_artifacts:
        return "No supporting evidence found."

    lines = []
    # Group by type
    by_type: dict[str, list] = {}
    for a in incident.supporting_artifacts:
        t = a.evidence_type if isinstance(a.evidence_type, str) else a.evidence_type.value
        by_type.setdefault(t, []).append(a)

    for etype, artifacts in sorted(by_type.items()):
        official = [a for a in artifacts if a.source_tier == "official"]
        news = [a for a in artifacts if a.source_tier == "news"]
        other = [a for a in artifacts if a.source_tier not in ("official", "news")]
        parts = []
        if official:
            parts.append(f"{len(official)} official")
        if news:
            parts.append(f"{len(news)} news")
        if other:
            parts.append(f"{len(other)} other")
        lines.append(f"  {etype}: {', '.join(parts)} ({len(artifacts)} total)")

    if incident.missing_evidence:
        lines.append(f"  Missing: {', '.join(incident.missing_evidence)}")

    return "\n".join(lines)


def _find_strongest_type(incident: Incident) -> str:
    """Find the artifact type with the most/best sources."""
    if not incident.supporting_artifacts:
        return ""

    by_type: dict[str, int] = {}
    for a in incident.supporting_artifacts:
        t = a.evidence_type if isinstance(a.evidence_type, str) else a.evidence_type.value
        by_type[t] = by_type.get(t, 0) + 1

    return max(by_type, key=by_type.get) if by_type else ""


def generate_packet(
    incident: Incident,
    scoring: ScoringResult,
    log: PacketLog,
    output_dir: str = "./output",
) -> CasePacket:
    """
    Generate a case packet from a scored, enriched incident.
    Writes the packet to disk as JSON.
    """
    recommendation = _classify_recommendation(
        scoring.story_value_score,
        scoring.researchability_score,
        scoring.risk_flags,
    )

    composite = (scoring.story_value_score * 0.5) + (scoring.researchability_score * 0.5)

    # Build decision reason
    reasons = [scoring.decision_reason]
    if incident.missing_evidence:
        reasons.append(f"Missing evidence: {', '.join(incident.missing_evidence)}")
    if scoring.risk_flags:
        reasons.append(f"Risk flags: {', '.join(scoring.risk_flags)}")

    packet = CasePacket(
        incident=incident,
        evidence_summary=_build_evidence_summary(incident),
        evidence_count=len(incident.supporting_artifacts),
        strongest_artifact_type=_find_strongest_type(incident),
        story_value_score=scoring.story_value_score,
        researchability_score=scoring.researchability_score,
        composite_score=composite,
        missing_evidence=incident.missing_evidence,
        risk_flags=scoring.risk_flags,
        recommendation=recommendation,
        decision_reason="; ".join(reasons),
    )

    log.add("packet", "generated",
            detail=f"recommendation={recommendation} composite={composite:.0f}",
            decision=recommendation,
            reason=packet.decision_reason)

    # Write to disk
    packets_dir = Path(output_dir) / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)

    packet_path = packets_dir / f"{packet.packet_id}.json"
    with open(packet_path, "w") as f:
        json.dump(packet.to_dict(), f, indent=2, default=str)

    log.add("packet", "saved", detail=str(packet_path))

    return packet
