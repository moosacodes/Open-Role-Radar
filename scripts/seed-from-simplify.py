"""Extract verified Greenhouse/Lever/Ashby/Workable/SR tenants from Simplify listings."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yml"
sys.path.insert(0, str(ROOT / "engine" / "src"))

from openroleradar.discovery.ats_detect import detect_ats_from_url  # noqa: E402

LISTINGS = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
]

API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{tenant}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=5",
}

FAST = frozenset(API)


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


def load_denylist_domains() -> set[str]:
    data = yaml.safe_load(
        (ROOT / "config" / "aggregators-denylist.yml").read_text(encoding="utf-8")
    ) or {}
    domains = data.get("domains") if isinstance(data, dict) else []
    if not isinstance(domains, list):
        return set()
    return {str(item).lower().removeprefix("www.") for item in domains}


def company_domain(name: str, url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host and not any(
        token in host
        for token in (
            "greenhouse.io",
            "lever.co",
            "ashbyhq.com",
            "workable.com",
            "smartrecruiters.com",
            "myworkdayjobs.com",
            "simplify.jobs",
        )
    ):
        return host
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"
    return f"{slug}.com"


async def collect_candidates() -> dict[tuple[str, str], dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for url in LISTINGS:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                apply_url = str(item.get("url") or item.get("apply_url") or "").strip()
                if not apply_url:
                    continue
                match = detect_ats_from_url(apply_url)
                if match is None or match.platform not in FAST or not match.tenant:
                    continue
                tenant = unquote(match.tenant).strip().replace(" ", "-")
                if not tenant:
                    continue
                key = (match.platform, tenant.lower())
                if key in found:
                    continue
                company = str(item.get("company_name") or item.get("company") or tenant)
                found[key] = {
                    "company": company.strip() or tenant,
                    "domain": company_domain(company, apply_url),
                    "adapter": match.platform,
                    "tenant": tenant,
                }
    return found


def _has_jobs(adapter: str, payload: object, content: bytes) -> bool:
    if adapter == "greenhouse":
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        return isinstance(jobs, list) and len(jobs) >= 0 and len(content) > 2
    if adapter == "lever":
        return isinstance(payload, list)
    if adapter == "ashby":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return isinstance(jobs, list)
    if adapter == "workable":
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return isinstance(jobs, list) and len(jobs) > 0
    if adapter == "smartrecruiters":
        if not isinstance(payload, dict):
            return False
        total = payload.get("totalFound")
        content_list = payload.get("content")
        if isinstance(total, int):
            return total > 0
        return isinstance(content_list, list) and len(content_list) > 0
    return len(content) > 80


async def verify(candidates: dict[tuple[str, str], dict[str, str]]) -> list[dict[str, str]]:
    sem = asyncio.Semaphore(24)
    verified: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:

        async def check(entry: dict[str, str]) -> None:
            adapter = entry["adapter"]
            tenant = entry["tenant"]
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
            if not _has_jobs(adapter, payload, response.content):
                return
            verified.append(entry)

        await asyncio.gather(*(check(entry) for entry in candidates.values()))
    return verified


def _yaml_quote(value: str) -> str:
    import json

    text = value.replace("\n", " ").strip()
    if not text:
        return '""'
    if any(ch in text for ch in ":{},[]#&*?|>!%@`'\"") or text[:1].isdigit():
        return json.dumps(text)
    return text


def append_sources(entries: list[dict[str, str]]) -> int:
    if not entries:
        return 0
    existing = load_existing()
    lines = ["", "# --- Seeded from SimplifyJobs listings ---"]
    added = 0
    for entry in sorted(entries, key=lambda item: (item["adapter"], item["tenant"].lower())):
        key = (entry["adapter"].lower(), entry["tenant"].lower())
        if key in existing:
            continue
        existing.add(key)
        lines.append(
            "- { company: "
            + _yaml_quote(entry["company"])
            + ", domain: "
            + _yaml_quote(entry["domain"])
            + ", adapter: "
            + entry["adapter"]
            + ", tenant: "
            + _yaml_quote(entry["tenant"])
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
    existing = load_existing()
    denied = load_denylist_domains()
    candidates = await collect_candidates()
    fresh = {
        key: value
        for key, value in candidates.items()
        if (key[0], key[1]) not in existing
        and value["domain"].lower().removeprefix("www.") not in denied
    }
    print(f"simplify_candidates={len(candidates)} fresh={len(fresh)}")
    verified = await verify(fresh)
    print(f"verified={len(verified)}")
    added = append_sources(verified)
    print(f"added={added} total~={len(existing) + added}")


if __name__ == "__main__":
    asyncio.run(main())
