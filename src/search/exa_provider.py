"""
Exa search provider — wraps existing exa_py logic.
Last-resort fallback in the provider chain.
"""

from __future__ import annotations

from src.search.base import SearchProvider, SearchResult, ProviderExhausted


class ExaProvider(SearchProvider):
    name = "exa"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from exa_py import Exa
            self._client = Exa(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def search(self, query: str, num_results: int = 10,
               **kwargs) -> list[SearchResult]:
        exa = self._get_client()
        try:
            exa_kwargs = {
                "query": query,
                "type": "auto",
                "num_results": num_results,
            }
            if kwargs.get("include_domains"):
                exa_kwargs["include_domains"] = kwargs["include_domains"]

            results = exa.search(**exa_kwargs)
            return [
                SearchResult(
                    url=r.url,
                    title=getattr(r, "title", ""),
                    text="",
                    score=min(getattr(r, "score", 0), 1.0),
                )
                for r in results.results
            ]
        except Exception as e:
            if "rate" in str(e).lower() or "quota" in str(e).lower():
                raise ProviderExhausted(f"Exa: {e}")
            raise

    def search_and_contents(self, query: str, num_results: int = 10,
                            max_characters: int = 15000,
                            **kwargs) -> list[SearchResult]:
        exa = self._get_client()
        try:
            exa_kwargs = {
                "query": query,
                "type": "auto",
                "num_results": num_results,
                "text": {"max_characters": max_characters},
            }
            if kwargs.get("start_published_date"):
                exa_kwargs["start_published_date"] = kwargs["start_published_date"]
            if kwargs.get("end_published_date"):
                exa_kwargs["end_published_date"] = kwargs["end_published_date"]
            if kwargs.get("include_domains"):
                exa_kwargs["include_domains"] = kwargs["include_domains"]

            results = exa.search_and_contents(**exa_kwargs)
            return [
                SearchResult(
                    url=r.url,
                    title=getattr(r, "title", ""),
                    text=getattr(r, "text", "") or "",
                    score=min(getattr(r, "score", 0), 1.0),
                )
                for r in results.results
            ]
        except Exception as e:
            if "rate" in str(e).lower() or "quota" in str(e).lower():
                raise ProviderExhausted(f"Exa: {e}")
            raise
