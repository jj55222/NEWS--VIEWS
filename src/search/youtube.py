"""
YouTube Data API v3 connector for video evidence discovery.

Searches official agency channels and general YouTube for:
- Bodycam / dashcam footage
- Interrogation videos
- Court / trial footage
- Press conferences

Quota: each search().list() costs 100 units. Free tier = 10,000 units/day.
"""

from __future__ import annotations

import re
from typing import Optional

from src.search.base import YouTubeResult


# Lazy import to avoid requiring google-api-python-client when not used
def _build_youtube_client(api_key: str):
    from googleapiclient.discovery import build
    return build("youtube", "v3", developerKey=api_key)


class YouTubeConnector:
    def __init__(self, api_key: str, daily_quota: int = 8000):
        self._api_key = api_key
        self._client = None
        self._units_used = 0
        self._daily_quota = daily_quota
        self._channel_id_cache: dict[str, str] = {}

    def _get_client(self):
        if self._client is None:
            self._client = _build_youtube_client(self._api_key)
        return self._client

    def _check_quota(self, cost: int = 100) -> bool:
        """Return True if we have enough quota remaining."""
        return (self._units_used + cost) <= self._daily_quota

    def _use_quota(self, cost: int):
        self._units_used += cost

    # ------------------------------------------------------------------
    # Channel resolution
    # ------------------------------------------------------------------

    def resolve_channel_id(self, youtube_url: str) -> Optional[str]:
        """
        Resolve a YouTube channel URL to a channel ID.
        Handles @handle format and /channel/ format.
        Costs 1 quota unit per resolution (cached).
        """
        if not youtube_url:
            return None

        # Check cache
        if youtube_url in self._channel_id_cache:
            return self._channel_id_cache[youtube_url]

        # Direct channel ID format: youtube.com/channel/UCxxxxxx
        match = re.search(r'/channel/(UC[\w-]+)', youtube_url)
        if match:
            channel_id = match.group(1)
            self._channel_id_cache[youtube_url] = channel_id
            return channel_id

        # Handle format: youtube.com/@HandleName
        match = re.search(r'/@([\w.-]+)', youtube_url)
        if match:
            handle = match.group(1)
            return self._resolve_handle(handle, youtube_url)

        return None

    def _resolve_handle(self, handle: str, original_url: str) -> Optional[str]:
        """Resolve a @handle to a channel ID via API. Costs 1 unit."""
        if not self._check_quota(1):
            return None

        yt = self._get_client()
        try:
            response = yt.channels().list(
                part="id",
                forHandle=handle,
            ).execute()
            self._use_quota(1)

            items = response.get("items", [])
            if items:
                channel_id = items[0]["id"]
                self._channel_id_cache[original_url] = channel_id
                return channel_id
        except Exception as e:
            print(f"    [YT] Handle resolve failed for @{handle}: {e}")

        return None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_channel(self, channel_id: str, query: str,
                       max_results: int = 5) -> list[YouTubeResult]:
        """Search within a specific channel. Costs 100 units."""
        if not self._check_quota(100):
            print("    [YT] Quota limit reached, skipping channel search")
            return []

        yt = self._get_client()
        try:
            response = yt.search().list(
                part="snippet",
                channelId=channel_id,
                q=query,
                type="video",
                maxResults=max_results,
                order="relevance",
            ).execute()
            self._use_quota(100)
            return self._parse_search_response(response)
        except Exception as e:
            print(f"    [YT] Channel search failed: {e}")
            return []

    def search_general(self, query: str,
                       max_results: int = 5) -> list[YouTubeResult]:
        """General YouTube search. Costs 100 units."""
        if not self._check_quota(100):
            print("    [YT] Quota limit reached, skipping general search")
            return []

        yt = self._get_client()
        try:
            response = yt.search().list(
                part="snippet",
                q=query,
                type="video",
                maxResults=max_results,
                order="relevance",
            ).execute()
            self._use_quota(100)
            return self._parse_search_response(response)
        except Exception as e:
            print(f"    [YT] General search failed: {e}")
            return []

    def _parse_search_response(self, response: dict) -> list[YouTubeResult]:
        """Parse YouTube API search response into YouTubeResults."""
        results = []
        for item in response.get("items", []):
            vid_id = item.get("id", {}).get("videoId", "")
            if not vid_id:
                continue
            snippet = item.get("snippet", {})
            results.append(YouTubeResult(
                video_id=vid_id,
                url=f"https://www.youtube.com/watch?v={vid_id}",
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                channel_title=snippet.get("channelTitle", ""),
                channel_id=snippet.get("channelId", ""),
                publish_date=snippet.get("publishedAt", ""),
                thumbnail_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            ))
        return results

    # ------------------------------------------------------------------
    # High-level incident search
    # ------------------------------------------------------------------

    def search_incident_videos(
        self,
        incident,
        region_id: str = None,
    ) -> list[YouTubeResult]:
        """
        Search YouTube for video evidence related to an incident.
        Uses jurisdiction portals for agency channel targeting.

        Returns deduplicated YouTubeResults.
        """
        # Get defendant name
        defendants = [p["name"] for p in (incident.people or [])
                      if p.get("role") == "defendant" and p.get("name")]
        defendant = defendants[0] if defendants else ""

        agency = incident.agency or ""
        if not defendant and not agency:
            return []

        all_results = []
        seen_ids = set()

        # Search agency channels from jurisdiction portals
        if region_id:
            all_results.extend(
                self._search_agency_channels(region_id, defendant, agency, seen_ids)
            )

        # General YouTube search
        search_terms = []
        if defendant and agency:
            search_terms.append(f"{agency} bodycam {defendant}")
            search_terms.append(f"{defendant} interrogation")
            search_terms.append(f"{defendant} trial court")
        elif defendant:
            search_terms.append(f"{defendant} bodycam footage")
            search_terms.append(f"{defendant} interrogation video")

        for query in search_terms[:2]:  # Limit to 2 general searches (200 units)
            results = self.search_general(query, max_results=5)
            for r in results:
                if r.video_id not in seen_ids:
                    seen_ids.add(r.video_id)
                    all_results.append(r)

        return all_results

    def _search_agency_channels(
        self,
        region_id: str,
        defendant: str,
        agency: str,
        seen_ids: set,
    ) -> list[YouTubeResult]:
        """Search official agency YouTube channels for this jurisdiction."""
        try:
            from archive.jurisdiction_portals import (
                get_agency_youtube_channels,
            )
        except ImportError:
            return []

        channels = get_agency_youtube_channels(region_id)
        results = []

        for ch in channels[:2]:  # Limit to 2 channels (200 units)
            yt_url = ch.get("youtube", "")
            channel_id = self.resolve_channel_id(yt_url)
            if not channel_id:
                continue

            query = f"{defendant} {agency}".strip() or "bodycam"
            ch_results = self.search_channel(channel_id, query, max_results=3)
            for r in ch_results:
                if r.video_id not in seen_ids:
                    seen_ids.add(r.video_id)
                    results.append(r)

        return results

    @property
    def units_remaining(self) -> int:
        return max(self._daily_quota - self._units_used, 0)
