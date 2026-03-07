"""Tests for the scoring module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.schema import Incident, Evidence, EvidenceType
from src.common.config import Config
from src.common.logging import PacketLog
from src.score.scorer import score_incident


def _make_config():
    return Config()


def test_strong_candidate():
    """A well-documented case should score high on both axes."""
    inc = Incident(
        story_summary="A police officer was convicted of murder after bodycam footage contradicted his official report.",
        narrative_hook="The officer's own body camera proved he lied about the shooting that killed an unarmed man.",
        incident_type="shooting",
        agency="Phoenix PD",
        jurisdiction={"city": "Phoenix", "county": "Maricopa", "state": "AZ"},
        people=[
            {"name": "John Doe", "role": "defendant"},
            {"name": "Jane Smith", "role": "victim"},
            {"name": "Officer Jones", "role": "officer"},
        ],
        allegations_or_charges=["second-degree murder", "filing false report", "obstruction"],
        supporting_artifacts=[
            Evidence(evidence_type=EvidenceType.BODYCAM, url="https://yt.com/1",
                     title="BWC footage", source_tier="official", confidence=0.9),
            Evidence(evidence_type=EvidenceType.COURT_VIDEO, url="https://yt.com/2",
                     title="Trial", source_tier="news", confidence=0.7),
            Evidence(evidence_type=EvidenceType.INTERROGATION, url="https://yt.com/3",
                     title="Interview", source_tier="news", confidence=0.6),
        ],
        evidence_completeness_score=80.0,
        missing_evidence=["surveillance"],
    )

    log = PacketLog(incident_id=inc.incident_id)
    result = score_incident(inc, _make_config(), log)

    assert result.story_value_score >= 50, f"Story should be >= 50, got {result.story_value_score}"
    assert result.researchability_score >= 40, f"Research should be >= 40, got {result.researchability_score}"
    print(f"PASS: strong_candidate story={result.story_value_score:.0f} research={result.researchability_score:.0f}")


def test_weak_candidate():
    """A bare-bones case should score low."""
    inc = Incident(
        story_summary="Something happened.",
        incident_type="other",
        people=[],
        supporting_artifacts=[],
        evidence_completeness_score=0.0,
        missing_evidence=["bodycam", "interrogation", "court_video", "surveillance"],
    )

    log = PacketLog(incident_id=inc.incident_id)
    result = score_incident(inc, _make_config(), log)

    assert result.story_value_score < 30, f"Story should be < 30, got {result.story_value_score}"
    assert result.researchability_score < 20, f"Research should be < 20, got {result.researchability_score}"
    assert "zero_evidence_found" in result.risk_flags
    print(f"PASS: weak_candidate story={result.story_value_score:.0f} research={result.researchability_score:.0f}")


def test_scores_are_separate():
    """A great story with no evidence should have high story but low research."""
    inc = Incident(
        story_summary="A horrifying case of child abuse spanning years, with multiple defendants and a cover-up.",
        narrative_hook="Three teachers conspired to hide years of abuse at a prestigious school.",
        incident_type="child_abuse",
        agency="Local PD",
        jurisdiction={"city": "Test", "county": "Test", "state": "TX"},
        people=[
            {"name": "Defendant A", "role": "defendant"},
            {"name": "Defendant B", "role": "defendant"},
            {"name": "Victim C", "role": "victim"},
        ],
        allegations_or_charges=["child abuse", "conspiracy", "failure to report"],
        supporting_artifacts=[],  # No evidence found
        evidence_completeness_score=0.0,
        missing_evidence=["bodycam", "interrogation", "court_video", "surveillance"],
    )

    log = PacketLog(incident_id=inc.incident_id)
    result = score_incident(inc, _make_config(), log)

    # Story should be high (compelling case)
    assert result.story_value_score > result.researchability_score, \
        f"Story ({result.story_value_score}) should exceed research ({result.researchability_score})"
    print(f"PASS: scores_separate story={result.story_value_score:.0f} research={result.researchability_score:.0f}")


if __name__ == "__main__":
    test_strong_candidate()
    test_weak_candidate()
    test_scores_are_separate()
    print("\nAll scorer tests passed!")
