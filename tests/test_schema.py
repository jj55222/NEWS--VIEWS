"""Tests for the canonical schema models."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.schema import (
    Incident, Evidence, CasePacket, ScoringResult,
    EvidenceType, RoutingStatus,
)


def test_incident_creation():
    """Incident should have sensible defaults."""
    inc = Incident()
    assert inc.incident_id  # auto-generated
    assert inc.routing_status == "candidate"
    assert inc.people == []
    assert inc.supporting_artifacts == []
    assert inc.missing_evidence == []
    print("PASS: incident_creation")


def test_incident_serialization():
    """Incident should round-trip through dict."""
    inc = Incident(
        source_type="exa",
        source_url="https://example.com",
        people=[{"name": "Test", "role": "defendant"}],
        jurisdiction={"city": "Phoenix", "county": "Maricopa", "state": "AZ"},
    )
    d = inc.to_dict()
    assert d["source_url"] == "https://example.com"
    assert len(d["people"]) == 1
    assert d["jurisdiction"]["state"] == "AZ"

    # Round-trip
    inc2 = Incident.from_dict(d)
    assert inc2.source_url == inc.source_url
    assert inc2.people == inc.people
    print("PASS: incident_serialization")


def test_evidence_creation():
    """Evidence should store type and provenance."""
    ev = Evidence(
        evidence_type=EvidenceType.BODYCAM,
        url="https://youtube.com/watch?v=abc",
        title="Bodycam footage",
        source_tier="official",
        confidence=0.9,
        found_via="test query",
    )
    assert ev.source_tier == "official"
    assert ev.confidence == 0.9
    print("PASS: evidence_creation")


def test_case_packet():
    """CasePacket should hold incident and scoring data."""
    inc = Incident(source_url="https://example.com")
    packet = CasePacket(
        incident=inc,
        recommendation="STRONG",
        composite_score=75.0,
    )
    d = packet.to_dict()
    assert d["recommendation"] == "STRONG"
    assert d["incident"]["source_url"] == "https://example.com"
    print("PASS: case_packet")


def test_scoring_result():
    """ScoringResult should hold separate scores."""
    result = ScoringResult(
        story_value_score=70.0,
        researchability_score=55.0,
        risk_flags=["no_named_people"],
    )
    assert result.story_value_score == 70.0
    assert len(result.risk_flags) == 1
    print("PASS: scoring_result")


if __name__ == "__main__":
    test_incident_creation()
    test_incident_serialization()
    test_evidence_creation()
    test_case_packet()
    test_scoring_result()
    print("\nAll schema tests passed!")
