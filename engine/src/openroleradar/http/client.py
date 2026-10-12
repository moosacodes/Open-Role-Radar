"""SSRF-safe async HTTP client with retries, rate limiting, and caching headers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from openroleradar.config import ProjectConfig, load_project_config
from openroleradar.http.url import URLValidationError, validate_http_url
from openroleradar.security.first_party import assert_first_party_url
from openroleradar.security.ssrf import SSRFError, validate_url_target


class HTTPClientError(RuntimeError):
    """Raised when an HTTP request fails safety or transport checks."""


class RetryableHTTPError(HTTPClientError):
    """Raised for transient HTTP failures that should be retried."""


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Normalized HTTP response returned by SafeHTTPClient."""

    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    etag: str | None = None
    last_modified: str | None = None
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def is_not_modified(self) -> bool:
        return self.status_code == 304


class SafeHTTPClient:
    """Async HTTP client with SSRF protection and per-host rate limiting."""

    def __init__(
        self,
        config: ProjectConfig | None = None,
        *,
        user_agent: str | None = None,
        timeout_seconds: float | None = None,
        max_redirects: int | None = None,
        max_response_bytes: int | None = None,
        per_host_concurrency: int | None = None,
        retry_max_attempts: int | None = None,
        retry_base_delay_seconds: float | None = None,
        retry_max_delay_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or load_project_config()
        http_cfg = self._config.http

        self.user_agent = user_agent or self._config.user_agent
        self.timeout_seconds = float(timeout_seconds or http_cfg.get("timeout_seconds", 30))
        self.max_redirects = int(max_redirects or http_cfg.get("max_redirects", 5))
        self.max_response_bytes = int(
            max_response_bytes or http_cfg.get("max_response_bytes", 10_485_760)
        )
        self.per_host_concurrency = int(
            per_host_concurrency or http_cfg.get("per_host_concurrency", 3)
        )
        self.retry_max_attempts = int(retry_max_attempts or http_cfg.get("retry_max_attempts", 3))
        self.retry_base_delay_seconds = float(
            retry_base_delay_seconds or http_cfg.get("retry_base_delay_seconds", 1.0)
        )
        self.retry_max_delay_seconds = float(
            retry_max_delay_seconds or http_cfg.get("retry_max_delay_seconds", 60.0)
        )

        self._host_semaphores: dict[str, asyncio.Semaphore] = {}
        self._owns_client = client is None
        # Keepalive pool so shared ATS hosts reuse TCP connections across boards.
        limits = httpx.Limits(
            max_connections=max(64, self.per_host_concurrency * 8),
            max_keepalive_connections=max(32, self.per_host_concurrency * 4),
            keepalive_expiry=30.0,
        )
        http2 = False
        try:
            import h2  # noqa: F401

            http2 = True
        except ImportError:
            http2 = False
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": self.user_agent},
            limits=limits,
            http2=http2,
        )

    async def __aenter__(self) -> SafeHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _semaphore_for_host(self, hostname: str) -> asyncio.Semaphore:
        host = hostname.lower()
        if host not in self._host_semaphores:
            self._host_semaphores[host] = asyncio.Semaphore(self.per_host_concurrency)
        return self._host_semaphores[host]

    async def _validate_request_url(self, url: str) -> str:
        try:
            canonical = validate_http_url(url)
            canonical = assert_first_party_url(canonical)
        except (URLValidationError, ValueError) as exc:
            raise HTTPClientError(str(exc)) from exc

        parsed = urlparse(canonical)
        hostname = parsed.hostname
        if not hostname:
            raise HTTPClientError(f"URL missing hostname: {canonical}")

        try:
            await validate_url_target(hostname, parsed.port)
        except SSRFError as exc:
            raise HTTPClientError(str(exc)) from exc
        return canonical

    async def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise HTTPClientError(
                    f"Response exceeded max size of {self.max_response_bytes} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _normalize_headers(self, headers: httpx.Headers) -> dict[str, str]:
        return {key.lower(): value for key, value in headers.items()}

    async def _request_once(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        current_url = await self._validate_request_url(url)
        redirects = 0
        request_headers = dict(headers or {})
        request_content = content

        while True:
            parsed = urlparse(current_url)
            hostname = parsed.hostname
            if not hostname:
                raise HTTPClientError(f"URL missing hostname: {current_url}")

            semaphore = self._semaphore_for_host(hostname)
            async with semaphore:
                response = await self._client.request(
                    method,
                    current_url,
                    headers=request_headers,
                    params=params,
                    content=request_content,
                )

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise HTTPClientError(
                        f"Redirect response {response.status_code} missing Location header"
                    )
                redirects += 1
                if redirects > self.max_redirects:
                    raise HTTPClientError(f"Exceeded maximum redirects ({self.max_redirects})")

                next_url = httpx.URL(current_url).join(location)
                current_url = await self._validate_request_url(str(next_url))
                if response.status_code in {301, 302, 303}:
                    method = "GET"
                    request_content = None
                await response.aclose()
                continue

            response.headers["x-final-url"] = current_url
            return response

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        etag: str | None = None,
        if_modified_since: str | None = None,
    ) -> HTTPResponse:
        """Perform an SSRF-safe HTTP request with retries and conditional headers."""
        request_headers = dict(headers or {})
        if etag:
            request_headers["If-None-Match"] = etag
        if if_modified_since:
            request_headers["If-Modified-Since"] = if_modified_since

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.retry_max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_base_delay_seconds,
                max=self.retry_max_delay_seconds,
            ),
            retry=retry_if_exception_type(
                (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                    RetryableHTTPError,
                )
            ),
            reraise=True,
        )

        async for attempt in retrying:
            with attempt:
                response = await self._request_once(
                    method,
                    url,
                    headers=request_headers,
                    params=params,
                    content=content,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    await response.aclose()
                    raise RetryableHTTPError(f"Retryable HTTP status: {response.status_code}")

                final_url = response.headers.get("x-final-url", str(response.url))
                normalized = self._normalize_headers(response.headers)

                if response.status_code == 304:
                    body = b""
                else:
                    body = await self._read_limited(response)
                await response.aclose()

                return HTTPResponse(
                    url=final_url,
                    status_code=response.status_code,
                    headers=normalized,
                    content=body,
                    etag=normalized.get("etag"),
                    last_modified=normalized.get("last-modified"),
                    from_cache=response.status_code == 304,
                )

        raise HTTPClientError("Request failed after retries")

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        etag: str | None = None,
        if_modified_since: str | None = None,
    ) -> HTTPResponse:
        """Perform a GET request."""
        return await self.request(
            "GET",
            url,
            headers=headers,
            params=params,
            etag=etag,
            if_modified_since=if_modified_since,
        )

    async def post(
        self,
        url: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> HTTPResponse:
        """Perform a POST request with an optional body."""
        return await self.request(
            "POST",
            url,
            headers=headers,
            params=params,
            content=content,
        )

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        etag: str | None = None,
        if_modified_since: str | None = None,
    ) -> tuple[Any, HTTPResponse]:
        """Perform a GET request and parse JSON content."""
        response = await self.get(
            url,
            headers=headers,
            params=params,
            etag=etag,
            if_modified_since=if_modified_since,
        )
        if response.is_not_modified:
            return None, response
        return httpx.Response(200, content=response.content).json(), response
