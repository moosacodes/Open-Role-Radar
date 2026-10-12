"""Promote validated discovery candidates into live state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from openroleradar.discovery.ats_detect import detect_ats_from_url
from openroleradar.discovery.registry import deterministic_id
from openroleradar.discovery.validate import ValidationResult
from openroleradar.models.job import Company, Source
from openroleradar.models.state import LiveState
from openroleradar.normalize.text import canonicalize_text

# Same public JSON APIs Simplify scrapes for their curated lists.
FAST_ATS_ADAPTERS = frozenset(
    {"greenhouse", "lever", "ashby", "workable", "smartrecruiters"}
)


def _company_name_from_domain(domain: str) -> str:
    host = domain.lower().removeprefix("www.")
    label = host.split(".")[0] if host else "unknown"
    return label.replace("-", " ").title()


def _ats_host_domain(domain: str) -> bool:
    host = domain.lower().removeprefix("www.")
    return any(
        token in host
        for token in (
            "greenhouse.io",
            "lever.co",
            "ashbyhq.com",
            "myworkdayjobs.com",
            "workable.com",
            "smartrecruiters.com",
        )
    )


def promote_validation(
    state: LiveState,
    result: ValidationResult,
    *,
    discovered_via: str,
    company_name: str | None = None,
    now: datetime | None = None,
) -> Source | None:
    """Insert or refresh a Source (+ Company) when validation accepted the candidate."""
    if not result.accepted or not result.adapter or not result.tenant or not result.company_domain:
        return None

    timestamp = now or datetime.now(UTC)
    domain = result.company_domain.lower().removeprefix("www.")
    company_id = deterministic_id("company", domain)
    source_id = result.source_id or deterministic_id(
        "source", domain, result.adapter, result.tenant
    )
    name = company_name or _company_name_from_domain(domain)
    careers_url = result.careers_url or f"https://{domain}/careers"

    company = state.companies.get(company_id)
    if company is None:
        state.companies[company_id] = Company(
            company_id=company_id,
            name=name,
            normalized_name=canonicalize_text(name).replace(" ", "-"),
            canonical_domain=domain,
            careers_urls=[careers_url],
            ats_sources=[result.adapter],
            first_seen_at=timestamp,
            last_seen_at=timestamp,
        )
    else:
        company.last_seen_at = timestamp
        if careers_url not in company.careers_urls:
            company.careers_urls.append(careers_url)
        if result.adapter not in company.ats_sources:
            company.ats_sources.append(result.adapter)

    existing = state.sources.get(source_id)
    if existing is None:
        source = Source(
            source_id=source_id,
            company_id=company_id,
            company_name=name,
            company_domain=domain,
            careers_url=careers_url,
            adapter=result.adapter,
            adapter_tenant=result.tenant,
            discovered_via=discovered_via,
            discovery_confidence=result.confidence,
            first_discovered_at=timestamp,
            last_validated_at=timestamp,
            health_status="healthy",
            poll_tier="hot",
            enabled=True,
        )
        state.sources[source_id] = source
        return source

    existing.last_validated_at = timestamp
    existing.discovery_confidence = max(existing.discovery_confidence, result.confidence)
    existing.enabled = True
    if existing.health_status in {"failing", "degraded", "unsupported"}:
        existing.health_status = "healthy"
        existing.consecutive_failures = 0
    return existing


def promote_ats_board_from_url(
    state: LiveState,
    *,
    apply_url: str,
    company_name: str,
    company_domain: str | None = None,
    discovered_via: str = "simplify",
    now: datetime | None = None,
) -> Source | None:
    """Promote a Greenhouse/Lever/Ashby board discovered from a first-party apply URL.

    Mirrors how Simplify grows coverage: find ATS apply links, then poll the public
    board API directly instead of waiting on their GitHub dump.
    """
    match = detect_ats_from_url(apply_url)
    if (
        match is None
        or not match.supported
        or match.platform not in FAST_ATS_ADAPTERS
        or not match.tenant
    ):
        return None

    timestamp = now or datetime.now(UTC)
    name = company_name.strip() or match.tenant
    domain = (company_domain or "").strip().lower().removeprefix("www.")
    if not domain or _ats_host_domain(domain):
        domain = f"{canonicalize_text(name).replace(' ', '-') or match.tenant}.com"

    careers_url = match.careers_url or apply_url
    source_id = deterministic_id("source", domain, match.platform, match.tenant)
    already = source_id in state.sources
    accepted = ValidationResult(
        accepted=True,
        confidence=0.9,
        reason="ats_url",
        adapter=match.platform,
        tenant=match.tenant,
        company_domain=domain,
        careers_url=careers_url,
        source_id=source_id,
        issues=[],
    )
    source = promote_validation(
        state,
        accepted,
        discovered_via=discovered_via,
        company_name=name,
        now=timestamp,
    )
    if source is None or already:
        return None
    source.poll_tier = "hot"
    return source


def quarantine_candidate(
    state: LiveState,
    *,
    url: str,
    result: ValidationResult,
    discovered_via: str,
    now: datetime | None = None,
) -> None:
    """Record a rejected candidate for later retry."""
    timestamp = now or datetime.now(UTC)
    entry: dict[str, Any] = {
        "url": url,
        "reason": result.reason,
        "confidence": result.confidence,
        "adapter": result.adapter,
        "tenant": result.tenant,
        "company_domain": result.company_domain,
        "discovered_via": discovered_via,
        "issues": list(result.issues),
        "quarantined_at": timestamp.isoformat(),
    }
    # Keep quarantine list bounded.
    state.discovery.quarantined_candidates = [
        item
        for item in state.discovery.quarantined_candidates
        if isinstance(item, dict) and item.get("url") != url
    ]
    state.discovery.quarantined_candidates.append(entry)
    state.discovery.quarantined_candidates = state.discovery.quarantined_candidates[-200:]


def domain_from_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host or None
