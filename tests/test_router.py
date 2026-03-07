"""Tests for the candidate routing module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.config import Config
from src.ingest.router import route_candidate


def test_above_threshold():
    """Candidates above threshold should be routed as candidates."""
    config = Config(min_prescore=20)
    prescore = {"score": 45, "matches": ["bodycam", "sentenced"]}
    status, reason = route_candidate(prescore, config)
    assert status == "candidate", f"Expected candidate, got {status}"
    print(f"PASS: above_threshold -> {status}")


def test_below_threshold():
    """Candidates below threshold should be rejected."""
    config = Config(min_prescore=20)
    prescore = {"score": 10, "matches": ["sentenced"]}
    status, reason = route_candidate(prescore, config)
    assert status == "rejected_low_signal", f"Expected rejected, got {status}"
    print(f"PASS: below_threshold -> {status}")


def test_exact_threshold():
    """Candidates at exactly the threshold should pass."""
    config = Config(min_prescore=20)
    prescore = {"score": 20, "matches": ["bodycam"]}
    status, reason = route_candidate(prescore, config)
    assert status == "candidate", f"Expected candidate, got {status}"
    print(f"PASS: exact_threshold -> {status}")


def test_zero_score():
    """Zero score should be rejected."""
    config = Config(min_prescore=20)
    prescore = {"score": 0, "matches": []}
    status, reason = route_candidate(prescore, config)
    assert status == "rejected_low_signal"
    print(f"PASS: zero_score -> {status}")


if __name__ == "__main__":
    test_above_threshold()
    test_below_threshold()
    test_exact_threshold()
    test_zero_score()
    print("\nAll router tests passed!")
