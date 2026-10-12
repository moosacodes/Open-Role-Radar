"""Merge LiveState snapshots produced by sharded sync workers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from openroleradar.models.enums import JobLifecycle
from openroleradar.models.job import Company, Job, JobEvent, Source
from openroleradar.models.state import AdapterHealthStats, LiveState

_LIFECYCLE_RANK = {
    JobLifecycle.OPEN: 0,
    JobLifecycle.REOPENED: 0,
    JobLifecycle.SUSPECTED_CLOSED: 1,
    JobLifecycle.CLOSED: 2,
}


def _newer(a: datetime | None, b: datetime | None) -> bool:
    """Return True when `a` should replace `b` based on timestamps."""
    if a is None:
        return False
    if b is None:
        return True
    return a >= b


def _source_freshness(source: Source) -> datetime | None:
    candidates = [
        source.last_success_at,
        source.last_failure_at,
        source.last_validated_at,
        source.next_due_at,
        source.first_discovered_at,
    ]
    present = [value for value in candidates if value is not None]
    return max(present) if present else None


def _merge_source(current: Source | None, incoming: Source) -> Source:
    if current is None:
        return incoming
    if _newer(_source_freshness(incoming), _source_freshness(current)):
        return incoming
    return current


def _merge_job(current: Job | None, incoming: Job) -> Job:
    """Prefer fresher sightings; on ties, prefer advanced closure state.

    Closing a role does not bump ``last_seen_at``, so a shard that never
    polled the board still carries a stale OPEN copy with the same timestamp.
    Without lifecycle-aware ties, merge order can resurrect filled roles.
    """
    if current is None:
        return incoming
    if incoming.last_seen_at != current.last_seen_at:
        if _newer(incoming.last_seen_at, current.last_seen_at):
            return incoming
        return current
    # Equal last_seen: prefer newer closed_at, then more terminal lifecycle.
    if incoming.closed_at != current.closed_at:
        if _newer(incoming.closed_at, current.closed_at):
            return incoming
        if _newer(current.closed_at, incoming.closed_at):
            return current
    incoming_rank = _LIFECYCLE_RANK.get(incoming.lifecycle, 0)
    current_rank = _LIFECYCLE_RANK.get(current.lifecycle, 0)
    if incoming_rank > current_rank:
        return incoming
    return current


def _merge_metadata(current: dict, incoming: dict) -> dict:
    """Shallow-merge metadata, deep-merging per-job miss counters."""
    merged = {**current, **incoming}
    miss_a = current.get("miss_counts")
    miss_b = incoming.get("miss_counts")
    if isinstance(miss_a, dict) or isinstance(miss_b, dict):
        out: dict[str, int] = {}
        if isinstance(miss_a, dict):
            for key, value in miss_a.items():
                if isinstance(value, int):
                    out[str(key)] = value
        if isinstance(miss_b, dict):
            for key, value in miss_b.items():
                if not isinstance(value, int):
                    continue
                job_id = str(key)
                out[job_id] = max(out.get(job_id, 0), value)
        merged["miss_counts"] = out
    return merged


def _merge_company(current: Company | None, incoming: Company) -> Company:
    if current is None:
        return incoming
    if _newer(incoming.last_seen_at, current.last_seen_at):
        merged = incoming.model_copy(deep=True)
        aliases = set(current.aliases or []) | set(incoming.aliases or [])
        if current.name and current.name != incoming.name:
            aliases.add(current.name)
        merged.aliases = sorted(aliases)
        careers = list(dict.fromkeys([*(current.careers_urls or []), *(incoming.careers_urls or [])]))
        merged.careers_urls = careers
        ats = list(dict.fromkeys([*(current.ats_sources or []), *(incoming.ats_sources or [])]))
        merged.ats_sources = ats
        merged.active_job_count = max(current.active_job_count, incoming.active_job_count)
        merged.historical_job_count = max(
            current.historical_job_count, incoming.historical_job_count
        )
        return merged
    return current


def _merge_health(
    current: AdapterHealthStats | None, incoming: AdapterHealthStats
) -> AdapterHealthStats:
    if current is None:
        return incoming
    errors = list(dict.fromkeys([*(current.sample_errors or []), *(incoming.sample_errors or [])]))
    return AdapterHealthStats(
        adapter=incoming.adapter or current.adapter,
        success_count=max(current.success_count, incoming.success_count),
        failure_count=max(current.failure_count, incoming.failure_count),
        last_failure_at=(
            incoming.last_failure_at
            if _newer(incoming.last_failure_at, current.last_failure_at)
            else current.last_failure_at
        ),
        sample_errors=errors[:20],
    )


def _event_key(event: JobEvent) -> tuple[str, str, str]:
    return (event.event_type, event.job_id, event.timestamp.isoformat())


def merge_states(states: Iterable[LiveState]) -> LiveState:
    """Merge sharded LiveState snapshots into one coherent state.

    Later snapshots win on equal timestamps. Each shard is assumed to have
    started from the same base and only advanced its own source subset.
    """
    ordered = list(states)
    if not ordered:
        raise ValueError("No states to merge")
    if len(ordered) == 1:
        return ordered[0].model_copy(deep=True)

    merged = ordered[0].model_copy(deep=True)
    for state in ordered[1:]:
        for source_id, source in state.sources.items():
            merged.sources[source_id] = _merge_source(merged.sources.get(source_id), source)
        for job_id, job in state.jobs.items():
            merged.jobs[job_id] = _merge_job(merged.jobs.get(job_id), job)
        for company_id, company in state.companies.items():
            merged.companies[company_id] = _merge_company(
                merged.companies.get(company_id), company
            )
        for adapter, health in state.adapter_health.items():
            merged.adapter_health[adapter] = _merge_health(
                merged.adapter_health.get(adapter), health
            )

        seen_events = {_event_key(event) for event in merged.events}
        for event in state.events:
            key = _event_key(event)
            if key in seen_events:
                continue
            merged.events.append(event)
            seen_events.add(key)

        if state.discovery.last_github_run and _newer(
            state.discovery.last_github_run, merged.discovery.last_github_run
        ):
            merged.discovery.last_github_run = state.discovery.last_github_run
            merged.discovery.github_cursor = state.discovery.github_cursor
        if state.discovery.last_common_crawl_run and _newer(
            state.discovery.last_common_crawl_run, merged.discovery.last_common_crawl_run
        ):
            merged.discovery.last_common_crawl_run = state.discovery.last_common_crawl_run
            merged.discovery.common_crawl_index = state.discovery.common_crawl_index
        if len(state.discovery.quarantined_candidates) >= len(
            merged.discovery.quarantined_candidates
        ):
            merged.discovery.quarantined_candidates = list(
                state.discovery.quarantined_candidates
            )

        merged.metadata = _merge_metadata(merged.metadata or {}, state.metadata or {})
        if _newer(state.generated_at, merged.generated_at):
            merged.generated_at = state.generated_at
        merged.schema_version = max(merged.schema_version, state.schema_version)

    # Cap event log growth after merges.
    if len(merged.events) > 50_000:
        merged.events = merged.events[-50_000:]
    return merged
