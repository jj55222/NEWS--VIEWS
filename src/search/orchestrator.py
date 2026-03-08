"""
Search orchestrator: tries providers in priority order.

Brave (primary) -> Tavily (secondary) -> Exa (fallback)

Falls through on:
- ProviderExhausted (quota/rate limit)
- Empty results
- Any exception
"""

from __future__ import annotations

from src.search.base import SearchProvider, SearchResult, ProviderExhausted
from src.search.brave_provider import BraveProvider
from src.search.tavily_provider import TavilyProvider
from src.search.exa_provider import ExaProvider


class SearchOrchestrator:
    def __init__(self, config):
        """Build provider chain from config. Order: Brave -> Tavily -> Exa."""
        self.providers: list[SearchProvider] = []

        if config.brave_api_key:
            self.providers.append(BraveProvider(config.brave_api_key))
        if config.tavily_api_key:
            self.providers.append(TavilyProvider(config.tavily_api_key))
        if config.exa_api_key:
            self.providers.append(ExaProvider(config.exa_api_key))

        if not self.providers:
            print("  [WARN] No search providers configured!")

    def search(self, query: str, num_results: int = 10,
               **kwargs) -> list[SearchResult]:
        return self._try_providers("search", query,
                                   num_results=num_results, **kwargs)

    def search_and_contents(self, query: str, num_results: int = 10,
                            max_characters: int = 15000,
                            **kwargs) -> list[SearchResult]:
        return self._try_providers("search_and_contents", query,
                                   num_results=num_results,
                                   max_characters=max_characters, **kwargs)

    def _try_providers(self, method: str, query: str,
                       **kwargs) -> list[SearchResult]:
        """Try each provider in order until one returns results."""
        last_error = None

        for provider in self.providers:
            if not provider.is_available():
                continue
            try:
                results = getattr(provider, method)(query, **kwargs)
                if results:
                    return results
                # Empty results — try next provider
                print(f"    [{provider.name}] no results, trying next...")
            except ProviderExhausted as e:
                print(f"    [{provider.name}] exhausted: {e}")
                last_error = e
            except Exception as e:
                print(f"    [{provider.name}] error: {e}")
                last_error = e

        if last_error:
            print(f"    [WARN] All providers failed. Last: {last_error}")
        return []

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]
