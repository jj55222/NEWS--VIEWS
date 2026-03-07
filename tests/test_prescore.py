"""Tests for the evidence pre-scoring module."""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.prescore import compute_prescore


def test_high_signal_article():
    """Article with bodycam, interrogation, and trial mentions should score high."""
    text = """
    Body camera footage released by the Phoenix Police Department shows officers
    responding to a domestic violence call. The interrogation video was later
    published on YouTube. The defendant was convicted and sentenced to 30 years.
    Surveillance footage from a nearby store confirmed the timeline.
    The trial was livestreamed on Court TV.
    """
    result = compute_prescore(text, region_id="PPD")
    assert result["score"] >= 40, f"Expected >= 40, got {result['score']}"
    assert len(result["matches"]) >= 3, f"Expected >= 3 matches, got {result['matches']}"
    print(f"PASS: high_signal score={result['score']} matches={result['matches']}")


def test_low_signal_article():
    """Generic crime article without footage mentions should score low."""
    text = """
    A man was arrested yesterday in connection with a robbery at a local
    convenience store. Police are investigating the incident. The suspect
    was taken into custody without incident.
    """
    result = compute_prescore(text, region_id="")
    assert result["score"] < 20, f"Expected < 20, got {result['score']}"
    print(f"PASS: low_signal score={result['score']} matches={result['matches']}")


def test_florida_bonus():
    """Florida cases should get a jurisdiction bonus."""
    text = """
    A Broward County Sheriff deputy was involved in a shooting. The officer
    was wearing a body camera at the time. The defendant was sentenced.
    """
    fl_result = compute_prescore(text, region_id="BC")
    non_fl_result = compute_prescore(text, region_id="SPD")
    assert fl_result["score"] > non_fl_result["score"], \
        f"Florida ({fl_result['score']}) should score higher than non-FL ({non_fl_result['score']})"
    print(f"PASS: florida_bonus FL={fl_result['score']} non-FL={non_fl_result['score']}")


def test_youtube_url_bonus():
    """Articles mentioning youtube.com should get platform bonus."""
    text_with = "Bodycam video available at youtube.com/watch?v=abc123"
    text_without = "Bodycam video was released by the department"
    with_score = compute_prescore(text_with)["score"]
    without_score = compute_prescore(text_without)["score"]
    assert with_score > without_score, \
        f"YouTube mention ({with_score}) should score higher than without ({without_score})"
    print(f"PASS: youtube_bonus with={with_score} without={without_score}")


def test_lifecycle_keywords():
    """Lifecycle keywords should add points."""
    text_early = "A suspect was arrested in the incident."
    text_late = "The defendant was convicted and sentenced after a guilty plea."
    early_score = compute_prescore(text_early)["score"]
    late_score = compute_prescore(text_late)["score"]
    assert late_score > early_score, \
        f"Late lifecycle ({late_score}) should score higher than early ({early_score})"
    print(f"PASS: lifecycle early={early_score} late={late_score}")


def test_score_capped_at_100():
    """Score should never exceed 100."""
    text = """
    bodycam body cam body-worn camera bwc dashcam custodial interview
    interrogation video surveillance footage trial livestream court video
    youtube.com vimeo.com sentenced convicted guilty plea trial verdict
    """
    result = compute_prescore(text, region_id="BC")
    assert result["score"] <= 100, f"Score should be <= 100, got {result['score']}"
    print(f"PASS: score_cap score={result['score']}")


def test_empty_text():
    """Empty text should score 0."""
    result = compute_prescore("")
    assert result["score"] == 0
    assert result["matches"] == []
    print(f"PASS: empty_text score={result['score']}")


if __name__ == "__main__":
    test_high_signal_article()
    test_low_signal_article()
    test_florida_bonus()
    test_youtube_url_bonus()
    test_lifecycle_keywords()
    test_score_capped_at_100()
    test_empty_text()
    print("\nAll prescore tests passed!")
