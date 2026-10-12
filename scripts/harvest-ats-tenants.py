"""Harvest Greenhouse/Lever/Ashby tenants from GitHub code search and append verified seeds."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yml"

PATTERNS = {
    "greenhouse": [
        r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([a-z0-9][a-z0-9_-]{1,64})",
        r"job-boards\.greenhouse\.io/([a-z0-9][a-z0-9_-]{1,64})",
    ],
    "lever": [
        r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9][a-z0-9_-]{1,64})",
    ],
    "ashby": [
        r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([a-z0-9][a-z0-9_-]{1,64})",
    ],
    "workable": [
        r"apply\.workable\.com/api/v1/widget/accounts/([a-z0-9][a-z0-9_-]{1,64})",
        r"apply\.workable\.com/([a-z0-9][a-z0-9_-]{1,64})(?:/|/j/)?",
    ],
    "smartrecruiters": [
        r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9][A-Za-z0-9_-]{1,64})",
        r"careers\.smartrecruiters\.com/([A-Za-z0-9][A-Za-z0-9_-]{1,64})",
    ],
}

QUERIES = [
    "boards.greenhouse.io",
    "boards-api.greenhouse.io/v1/boards",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "api.ashbyhq.com/posting-api/job-board",
    "apply.workable.com/api/v1/widget/accounts",
    "api.smartrecruiters.com/v1/companies",
]

API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{tenant}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
}


def load_existing() -> set[tuple[str, str]]:
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or []
    keys: set[tuple[str, str]] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        adapter = str(entry.get("adapter", "")).lower()
        tenant = str(entry.get("tenant", "")).lower()
        if adapter and tenant:
            keys.add((adapter, tenant))
    return keys


def extract_candidates(text: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    lowered = text.lower()
    for adapter, patterns in PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, lowered, flags=re.I):
                tenant = match.group(1).strip("-_").lower()
                if tenant in {"v1", "v0", "boards", "postings", "job-board", "api", "www"}:
                    continue
                if len(tenant) < 2:
                    continue
                found.add((adapter, tenant))
    return found


async def github_harvest(token: str | None) -> set[tuple[str, str]]:
    headers = {
        "Accept": "application/vnd.github.text-match+json",
        "User-Agent": "OpenRoleRadar-harvest",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    found: set[tuple[str, str]] = set()
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        for query in QUERIES:
            for page in range(1, 6):
                response = await client.get(
                    "https://api.github.com/search/code",
                    params={"q": f'"{query}" in:file', "per_page": 100, "page": page},
                )
                if response.status_code in {403, 422, 429}:
                    # Secondary rate limits / abuse detection — stop this query.
                    break
                response.raise_for_status()
                payload = response.json()
                items = payload.get("items", []) if isinstance(payload, dict) else []
                if not items:
                    break
                for item in items:
                    blob = " ".join(
                        [
                            str(item.get("html_url", "")),
                            str(item.get("path", "")),
                            str(item.get("name", "")),
                        ]
                    )
                    # text_matches may include snippets with board URLs
                    matches = item.get("text_matches") or []
                    if isinstance(matches, list):
                        for match in matches:
                            if isinstance(match, dict):
                                blob += " " + str(match.get("fragment", ""))
                    found |= extract_candidates(blob)
                if len(items) < 100:
                    break
                await asyncio.sleep(2.0)
    return found


def _payload_has_jobs(adapter: str, payload: object, content: bytes) -> bool:
    """Reject empty public boards that still return HTTP 200."""
    if len(content) < 20:
        return False
    if adapter == "greenhouse":
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        return isinstance(jobs, list) and len(jobs) > 0
    if adapter == "lever":
        return isinstance(payload, list) and len(payload) > 0
    if adapter == "ashby":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    if adapter == "workable":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    if adapter == "smartrecruiters":
        if not isinstance(payload, dict):
            return False
        total = payload.get("totalFound")
        rows = payload.get("content")
        if isinstance(total, int):
            return total > 0
        return isinstance(rows, list) and len(rows) > 0
    return len(content) > 80


async def verify(candidates: set[tuple[str, str]]) -> list[dict[str, str]]:
    sem = asyncio.Semaphore(20)
    verified: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:

        async def check(adapter: str, tenant: str) -> None:
            url = API[adapter].format(tenant=tenant)
            async with sem:
                try:
                    response = await client.get(url)
                except httpx.HTTPError:
                    return
            if response.status_code != 200:
                return
            try:
                payload = response.json()
            except ValueError:
                return
            if not _payload_has_jobs(adapter, payload, response.content):
                return
            company = tenant.replace("-", " ").replace("_", " ").title()
            domain = f"{tenant.replace('_', '-')}.com"
            verified.append(
                {
                    "company": company,
                    "domain": domain,
                    "adapter": adapter,
                    "tenant": tenant,
                }
            )

        await asyncio.gather(*(check(adapter, tenant) for adapter, tenant in sorted(candidates)))
    return verified


def append_sources(entries: list[dict[str, str]]) -> int:
    if not entries:
        return 0
    existing = load_existing()
    lines = ["", "# --- Harvested GitHub ATS boards ---"]
    added = 0
    for entry in sorted(entries, key=lambda item: (item["adapter"], item["tenant"])):
        key = (entry["adapter"], entry["tenant"])
        if key in existing:
            continue
        existing.add(key)
        lines.append(
            "- { company: "
            + entry["company"]
            + ", domain: "
            + entry["domain"]
            + ", adapter: "
            + entry["adapter"]
            + ", tenant: "
            + entry["tenant"]
            + " }"
        )
        added += 1
    if added:
        SOURCES.write_text(
            SOURCES.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    return added


async def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    existing = load_existing()
    harvested = await github_harvest(token)
    fresh = {item for item in harvested if item not in existing}
    print(f"harvested={len(harvested)} fresh={len(fresh)}")
    verified = await verify(fresh)
    print(f"verified={len(verified)}")
    added = append_sources(verified)
    print(f"added={added} total~={len(existing) + added}")


if __name__ == "__main__":
    asyncio.run(main())
