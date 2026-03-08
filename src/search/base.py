"""
Common interface for search providers.

All providers return the same SearchResult format.
The orchestrator tries providers in priority order: Brave -> Tavily -> Exa.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    """Unified search result from any provider."""
    url: str
    title: str
    text: str = ""      # snippet or full content; empty if unavailable
    score: float = 0.0  # relevance score, normalized 0-1


@dataclass
class YouTubeResult:
    """Result from YouTube Data API search."""
    video_id: str
    url: str
    title: str
    description: str = ""
    channel_title: str = ""
    channel_id: str = ""
    publish_date: str = ""
    thumbnail_url: str = ""


class ProviderExhausted(Exception):
    """Raised when a provider's quota or rate limit is hit."""
    pass


class SearchProvider(ABC):
    """Abstract base for search providers."""
    name: str = "base"

    @abstractmethod
    def search(self, query: str, num_results: int = 10,
               **kwargs) -> list[SearchResult]:
        """Search for metadata only (URL, title, score)."""
        ...

    @abstractmethod
    def search_and_contents(self, query: str, num_results: int = 10,
                            max_characters: int = 15000,
                            **kwargs) -> list[SearchResult]:
        """Search and return results with text content."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider has valid credentials."""
        ...
