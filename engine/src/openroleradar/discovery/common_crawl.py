"""Common Crawl index discovery for ATS career boards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from openroleradar.config import ProjectConfig, load_project_config
from openroleradar.discovery.ats_detect import detect_ats_from_url
from openroleradar.discovery.github_search import GitHubCodeSearch

COLLECTIONS_API = "https://index.commoncrawl.org/collinfo.json"


@dataclass(frozen=True)
class CommonCrawlCandidate:
    """A URL discovered from a Common Crawl CDX query."""

    url: str
    timestamp: str
    platform: str | None
    tenant: str | None
    confidence: float
    crawl_index: str
    discovered_at: datetime


class CommonCrawlDiscovery:
    """Query Common Crawl CDX indexes for ATS URL patterns."""

    def __init__(
        self,
        *,
        config: ProjectConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_project_config()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(60.0, connect=15.0),
                headers={"User-Agent": self.config.user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CommonCrawlDiscovery:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def latest_index(self) -> str:
        """Return the most recent Common Crawl index name."""
        client = self._get_client()
        response = client.get(COLLECTIONS_API)
        if response.status_code in {429, 500, 502, 503, 504}:
            raise ValueError(f"Common Crawl collinfo unavailable ({response.status_code})")
        response.raise_for_status()
        collections = response.json()
        if not isinstance(collections, list) or not collections:
            raise ValueError("No Common Crawl collections available")
        first = collections[0]
        if not isinstance(first, dict):
            raise ValueError("Invalid Common Crawl collection payload")
        index_id = first.get("id")
        if not isinstance(index_id, str):
            raise ValueError("Common Crawl collection missing id")
        return index_id

    def query_index(
        self,
        index: str,
        pattern: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query a CDX index for URLs matching a host pattern."""
        client = self._get_client()
        url = f"https://index.commoncrawl.org/{index}-index"
        response = client.get(
            url,
            params={
                "url": f"*.{pattern}/*",
                "output": "json",
                "limit": limit,
            },
        )
        # Index.commoncrawl.org flaps with 5xx/429; never fail the whole discover run.
        if response.status_code in {404, 429, 500, 502, 503, 504}:
            return []
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return []
        results: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                import json

                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                results.append(parsed)
        return results

    def discover(
        self,
        *,
        index: str | None = None,
        max_per_run: int | None = None,
    ) -> tuple[list[CommonCrawlCandidate], str]:
        """Discover ATS URLs from Common Crawl."""
        limit = max_per_run or int(self.config.discovery.get("common_crawl_per_run", 100))
        crawl_index = index or self.latest_index()
        patterns = GitHubCodeSearch.load_discovery_patterns()
        now = datetime.now(UTC)
        candidates: list[CommonCrawlCandidate] = []
        seen_urls: set[str] = set()

        for pattern in patterns:
            if len(candidates) >= limit:
                break
            per_pattern = max(1, limit // max(1, len(patterns)))
            rows = self.query_index(crawl_index, pattern, limit=per_pattern)
            for row in rows:
                raw_url = row.get("url")
                if not isinstance(raw_url, str):
                    continue
                normalized = raw_url.split("#", 1)[0]
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                detected = detect_ats_from_url(normalized)
                candidates.append(
                    CommonCrawlCandidate(
                        url=normalized,
                        timestamp=str(row.get("timestamp", "")),
                        platform=detected.platform if detected else None,
                        tenant=detected.tenant if detected else None,
                        confidence=detected.confidence if detected else 0.55,
                        crawl_index=crawl_index,
                        discovered_at=now,
                    )
                )
                if len(candidates) >= limit:
                    break

        return candidates, crawl_index
