"""
Tavily search provider — secondary fallback.
Built for AI agents, supports full page text extraction.

Free tier: 1,000 searches/month.
Docs: https://docs.tavily.com/
"""

from __future__ import annotations

from src.search.base import SearchProvider, SearchResult, ProviderExhausted


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def search(self, query: str, num_results: int = 10,
               **kwargs) -> list[SearchResult]:
        client = self._get_client()
        try:
            tavily_kwargs = {
                "query": query,
                "max_results": min(num_results, 10),
            }
            if kwargs.get("include_domains"):
                tavily_kwargs["include_domains"] = kwargs["include_domains"]

            response = client.search(**tavily_kwargs)
            results = response.get("results", [])

            return [
                SearchResult(
                    url=r.get("url", ""),
                    title=r.get("title", ""),
                    text=r.get("content", ""),
                    score=r.get("score", 0.5),
                )
                for r in results
            ]
        except Exception as e:
            if "rate" in str(e).lower() or "quota" in str(e).lower() or "limit" in str(e).lower():
                raise ProviderExhausted(f"Tavily: {e}")
            raise

    def search_and_contents(self, query: str, num_results: int = 10,
                            max_characters: int = 15000,
                            **kwargs) -> list[SearchResult]:
        client = self._get_client()
        try:
            tavily_kwargs = {
                "query": query,
                "max_results": min(num_results, 10),
                "include_raw_content": True,
            }
            if kwargs.get("include_domains"):
                tavily_kwargs["include_domains"] = kwargs["include_domains"]

            response = client.search(**tavily_kwargs)
            results = response.get("results", [])

            return [
                SearchResult(
                    url=r.get("url", ""),
                    title=r.get("title", ""),
                    text=(r.get("raw_content", "") or r.get("content", ""))[:max_characters],
                    score=r.get("score", 0.5),
                )
                for r in results
            ]
        except Exception as e:
            if "rate" in str(e).lower() or "quota" in str(e).lower() or "limit" in str(e).lower():
                raise ProviderExhausted(f"Tavily: {e}")
            raise
