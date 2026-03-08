"""
Brave Search provider — primary search provider.
Uses the Brave Web Search API directly via requests.

Free tier: 2,000 queries/month.
Docs: https://api.search.brave.com/app/documentation/web-search
"""

from __future__ import annotations

import requests

from src.search.base import SearchProvider, SearchResult, ProviderExhausted


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str):
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _request(self, query: str, count: int = 10,
                 extra_snippets: bool = False) -> dict:
        """Make a request to the Brave Web Search API."""
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        params = {
            "q": query,
            "count": min(count, 20),  # Brave max is 20
        }
        if extra_snippets:
            params["extra_snippets"] = "true"

        resp = requests.get(BRAVE_ENDPOINT, headers=headers, params=params,
                            timeout=15)

        if resp.status_code == 429:
            raise ProviderExhausted("Brave: rate limit exceeded")
        if resp.status_code == 401:
            raise ProviderExhausted("Brave: invalid API key")

        resp.raise_for_status()
        return resp.json()

    def _parse_results(self, data: dict, include_text: bool = False) -> list[SearchResult]:
        """Parse Brave API response into SearchResults."""
        results = []
        web_results = data.get("web", {}).get("results", [])

        for r in web_results:
            text = ""
            if include_text:
                # Combine description + extra_snippets for maximum text
                parts = []
                if r.get("description"):
                    parts.append(r["description"])
                for snippet in r.get("extra_snippets", []):
                    parts.append(snippet)
                text = "\n\n".join(parts)

            # Brave doesn't provide a relevance score; use position-based
            position = web_results.index(r)
            score = max(1.0 - (position * 0.1), 0.1)

            results.append(SearchResult(
                url=r.get("url", ""),
                title=r.get("title", ""),
                text=text,
                score=score,
            ))

        return results

    def _apply_domain_filter(self, query: str, domains: list[str]) -> str:
        """Prepend site: filters to query for domain targeting."""
        if not domains:
            return query
        # Brave supports site: operator
        # For multiple domains, use OR: (site:a.com OR site:b.com) query
        if len(domains) == 1:
            return f"site:{domains[0]} {query}"
        site_clause = " OR ".join(f"site:{d}" for d in domains[:5])
        return f"({site_clause}) {query}"

    def search(self, query: str, num_results: int = 10,
               **kwargs) -> list[SearchResult]:
        domains = kwargs.get("include_domains", [])
        q = self._apply_domain_filter(query, domains)
        data = self._request(q, count=num_results, extra_snippets=False)
        return self._parse_results(data, include_text=False)

    def search_and_contents(self, query: str, num_results: int = 10,
                            max_characters: int = 15000,
                            **kwargs) -> list[SearchResult]:
        domains = kwargs.get("include_domains", [])
        q = self._apply_domain_filter(query, domains)
        data = self._request(q, count=num_results, extra_snippets=True)
        return self._parse_results(data, include_text=True)
