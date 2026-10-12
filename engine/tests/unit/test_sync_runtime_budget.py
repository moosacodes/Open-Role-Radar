"""Tests for sync runtime budget / chunked polling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from openroleradar.adapters.base import AdapterFetchResult
from openroleradar.config import find_repo_root
from openroleradar.models.job import Company, Source
from openroleradar.models.state import LiveState
from openroleradar.sync import SyncOrchestrator


def _source(idx: int, now: datetime) -> Source:
    return Source(
        source_id=f"src{idx}",
        company_id=f"co{idx}",
        company_name=f"Acme {idx}",
        company_domain=f"acme{idx}.com",
        careers_url=f"https://acme{idx}.com/careers",
        adapter="greenhouse",
        adapter_tenant=f"acme{idx}",
        first_discovered_at=now,
        last_validated_at=now,
        health_status="healthy",
        enabled=True,
        poll_tier="hot",
    )


@pytest.mark.asyncio
async def test_run_sync_respects_runtime_budget(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    now = datetime.now(UTC)
    sources = {f"src{i}": _source(i, now) for i in range(6)}
    companies = {
        f"co{i}": Company(
            company_id=f"co{i}",
            name=f"Acme {i}",
            normalized_name=f"acme-{i}",
            canonical_domain=f"acme{i}.com",
            first_seen_at=now,
            last_seen_at=now,
        )
        for i in range(6)
    }
    state = LiveState(companies=companies, sources=sources)
    orchestrator.store.save(state)
    monkeypatch.setattr(orchestrator, "load_or_bootstrap", lambda: state)

    # ~0.08 minutes ≈ 5s; with 1-at-a-time 50ms polls we finish early unless
    # we use a tighter budget. 0.002 min ≈ 120ms → a few polls then truncate.
    monkeypatch.setenv("OPENROLERADAR_MAX_SYNC_RUNTIME_MINUTES", "0.002")
    monkeypatch.setitem(orchestrator.config.http, "global_concurrency", 1)

    call_count = {"n": 0}

    async def slow_poll(source: Source, _client: object) -> AdapterFetchResult:
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        return AdapterFetchResult(status="ok", jobs=[])

    monkeypatch.setattr(orchestrator, "_poll_adapter", slow_poll)

    class _DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("openroleradar.sync.SafeHTTPClient", lambda *_a, **_k: _DummyClient())

    summary = await orchestrator.run_sync()

    assert summary["truncated"] is True
    assert summary["skipped_sources"] > 0
    assert 0 < summary["polled_sources"] < summary["sources_due"]
    assert call_count["n"] == summary["polled_sources"]


def test_env_budget_zero_disables_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    monkeypatch.setenv("OPENROLERADAR_MAX_SYNC_RUNTIME_MINUTES", "0")
    assert orchestrator._sync_runtime_limit_minutes() is None
