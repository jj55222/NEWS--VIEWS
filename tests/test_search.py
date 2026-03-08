"""Tests for the search provider interface and orchestrator."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.search.base import SearchProvider, SearchResult, ProviderExhausted, YouTubeResult
from src.search.orchestrator import SearchOrchestrator
from src.enrich.evidence_search import _infer_evidence_type, _classify_source_tier
from src.common.schema import EvidenceType


# ---------------------------------------------------------------------------
# Mock providers for testing fallback behavior
# ---------------------------------------------------------------------------

class MockProvider(SearchProvider):
    """Provider that returns configurable results."""
    name = "mock"

    def __init__(self, results=None, fail=False, exhausted=False):
        self._results = results or []
        self._fail = fail
        self._exhausted = exhausted

    def is_available(self) -> bool:
        return True

    def search(self, query, num_results=10, **kwargs):
        if self._exhausted:
            raise ProviderExhausted("mock exhausted")
        if self._fail:
            raise RuntimeError("mock error")
        return self._results

    def search_and_contents(self, query, num_results=10, max_characters=15000, **kwargs):
        return self.search(query, num_results, **kwargs)


class UnavailableProvider(SearchProvider):
    name = "unavailable"

    def is_available(self) -> bool:
        return False

    def search(self, query, num_results=10, **kwargs):
        raise RuntimeError("should not be called")

    def search_and_contents(self, query, num_results=10, max_characters=15000, **kwargs):
        raise RuntimeError("should not be called")


# ---------------------------------------------------------------------------
# Tests: SearchResult and YouTubeResult
# ---------------------------------------------------------------------------

def test_search_result():
    r = SearchResult(url="https://example.com", title="Test", text="body", score=0.8)
    assert r.url == "https://example.com"
    assert r.score == 0.8
    print("PASS: search_result")


def test_youtube_result():
    r = YouTubeResult(
        video_id="abc123",
        url="https://youtube.com/watch?v=abc123",
        title="Bodycam footage",
        channel_title="Police Activity",
    )
    assert r.video_id == "abc123"
    assert "youtube.com" in r.url
    print("PASS: youtube_result")


# ---------------------------------------------------------------------------
# Tests: Orchestrator fallback
# ---------------------------------------------------------------------------

def test_orchestrator_first_provider_succeeds():
    """First provider returns results — no fallback needed."""
    results = [SearchResult(url="https://a.com", title="A", score=0.9)]
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(results=results), MockProvider(results=[])]
    got = orch.search("test query")
    assert len(got) == 1
    assert got[0].url == "https://a.com"
    print("PASS: first_provider_succeeds")


def test_orchestrator_fallback_on_empty():
    """First provider returns empty — falls through to second."""
    results = [SearchResult(url="https://b.com", title="B", score=0.7)]
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(results=[]), MockProvider(results=results)]
    got = orch.search("test")
    assert len(got) == 1
    assert got[0].url == "https://b.com"
    print("PASS: fallback_on_empty")


def test_orchestrator_fallback_on_exhausted():
    """First provider exhausted — falls through."""
    results = [SearchResult(url="https://c.com", title="C", score=0.6)]
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(exhausted=True), MockProvider(results=results)]
    got = orch.search("test")
    assert len(got) == 1
    assert got[0].url == "https://c.com"
    print("PASS: fallback_on_exhausted")


def test_orchestrator_fallback_on_error():
    """First provider throws error — falls through."""
    results = [SearchResult(url="https://d.com", title="D", score=0.5)]
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(fail=True), MockProvider(results=results)]
    got = orch.search("test")
    assert len(got) == 1
    print("PASS: fallback_on_error")


def test_orchestrator_all_fail():
    """All providers fail — returns empty list."""
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(fail=True), MockProvider(exhausted=True)]
    got = orch.search("test")
    assert got == []
    print("PASS: all_fail_returns_empty")


def test_orchestrator_skips_unavailable():
    """Unavailable providers are skipped."""
    results = [SearchResult(url="https://e.com", title="E", score=0.8)]
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [UnavailableProvider(), MockProvider(results=results)]
    got = orch.search("test")
    assert len(got) == 1
    print("PASS: skips_unavailable")


def test_orchestrator_provider_names():
    orch = SearchOrchestrator.__new__(SearchOrchestrator)
    orch.providers = [MockProvider(), UnavailableProvider()]
    assert orch.provider_names == ["mock", "unavailable"]
    print("PASS: provider_names")


# ---------------------------------------------------------------------------
# Tests: Evidence type inference
# ---------------------------------------------------------------------------

def test_infer_bodycam():
    assert _infer_evidence_type("SFPD Bodycam Footage Release") == EvidenceType.BODYCAM
    assert _infer_evidence_type("Officer body camera video") == EvidenceType.BODYCAM
    print("PASS: infer_bodycam")


def test_infer_interrogation():
    assert _infer_evidence_type("John Smith Interrogation Video") == EvidenceType.INTERROGATION
    assert _infer_evidence_type("Police interview with suspect") == EvidenceType.INTERROGATION
    print("PASS: infer_interrogation")


def test_infer_court():
    assert _infer_evidence_type("Day 3 of Smith Trial") == EvidenceType.COURT_VIDEO
    assert _infer_evidence_type("Sentencing hearing") == EvidenceType.COURT_VIDEO
    print("PASS: infer_court")


def test_infer_surveillance():
    assert _infer_evidence_type("CCTV captures suspect") == EvidenceType.SURVEILLANCE
    assert _infer_evidence_type("Security camera footage") == EvidenceType.SURVEILLANCE
    print("PASS: infer_surveillance")


def test_infer_default():
    assert _infer_evidence_type("Random video title") == EvidenceType.NEWS_CLIP
    print("PASS: infer_default")


# ---------------------------------------------------------------------------
# Tests: Source tier classification
# ---------------------------------------------------------------------------

def test_tier_official():
    assert _classify_source_tier("https://phoenix.gov/police/video", "PD Release") == "official"
    assert _classify_source_tier("https://youtube.com/@police", "Sheriff bodycam") == "official"
    print("PASS: tier_official")


def test_tier_news():
    assert _classify_source_tier("https://abc7news.com/article", "Breaking story") == "news"
    print("PASS: tier_news")


def test_tier_repost():
    assert _classify_source_tier("https://youtube.com/watch?v=xyz123", "Random upload") == "repost"
    print("PASS: tier_repost")


def test_tier_unknown():
    assert _classify_source_tier("https://example.com", "Some page") == "unknown"
    print("PASS: tier_unknown")


if __name__ == "__main__":
    # Data types
    test_search_result()
    test_youtube_result()

    # Orchestrator fallback
    test_orchestrator_first_provider_succeeds()
    test_orchestrator_fallback_on_empty()
    test_orchestrator_fallback_on_exhausted()
    test_orchestrator_fallback_on_error()
    test_orchestrator_all_fail()
    test_orchestrator_skips_unavailable()
    test_orchestrator_provider_names()

    # Evidence inference
    test_infer_bodycam()
    test_infer_interrogation()
    test_infer_court()
    test_infer_surveillance()
    test_infer_default()

    # Source tiers
    test_tier_official()
    test_tier_news()
    test_tier_repost()
    test_tier_unknown()

    print("\nAll search tests passed!")
