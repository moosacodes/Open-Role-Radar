"""Ensure Simplify promotions are polled before cold leftovers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from openroleradar.adapters.base import AdapterFetchResult
from openroleradar.config import find_repo_root
from openroleradar.models.job import Company, RawJob, Source
from openroleradar.models.state import LiveState
from openroleradar.sync import SyncOrchestrator


@pytest.mark.asyncio
async def test_newly_promoted_boards_poll_before_leftovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    now = datetime.now(UTC)

    simplify = Source(
        source_id="simp",
        company_id="co-s",
        company_name="Simplify",
        company_domain="simplify.jobs",
        careers_url="https://simplify.jobs",
        adapter="simplify",
        adapter_tenant="listings",
        first_discovered_at=now,
        poll_tier="hot",
        enabled=True,
    )
    cold = Source(
        source_id="cold-board",
        company_id="co-c",
        company_name="Cold",
        company_domain="cold.com",
        careers_url="https://cold.com/careers",
        adapter="greenhouse",
        adapter_tenant="cold",
        first_discovered_at=now,
        poll_tier="cold",
        enabled=True,
    )
    promoted = Source(
        source_id="promoted-board",
        company_id="co-p",
        company_name="Promoted",
        company_domain="promoted.com",
        careers_url="https://boards.greenhouse.io/promoted",
        adapter="greenhouse",
        adapter_tenant="promoted",
        first_discovered_at=now,
        poll_tier="hot",
        enabled=True,
    )

    state = LiveState(
        companies={
            "co-s": Company(
                company_id="co-s",
                name="Simplify",
                normalized_name="simplify",
                canonical_domain="simplify.jobs",
                first_seen_at=now,
                last_seen_at=now,
            ),
            "co-c": Company(
                company_id="co-c",
                name="Cold",
                normalized_name="cold",
                canonical_domain="cold.com",
                first_seen_at=now,
                last_seen_at=now,
            ),
        },
        sources={"simp": simplify, "cold-board": cold},
    )
    orchestrator.store.save(state)
    monkeypatch.setattr(orchestrator, "load_or_bootstrap", lambda: state)
    monkeypatch.setitem(orchestrator.config.http, "global_concurrency", 1)
    # Unbounded runtime so we observe full poll order.
    monkeypatch.setenv("OPENROLERADAR_MAX_SYNC_RUNTIME_MINUTES", "0")

    order: list[str] = []

    monkeypatch.setattr(
        orchestrator,
        "_promote_boards_from_raw_jobs",
        lambda *_a, **_k: [promoted],
    )

    class _DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("openroleradar.sync.SafeHTTPClient", lambda *_a, **_k: _DummyClient())

    async def fake_poll(_client: object, source: Source) -> AdapterFetchResult:
        order.append(source.source_id)
        if source.adapter == "simplify":
            return AdapterFetchResult(
                status="ok",
                jobs=[
                    RawJob(
                        source_job_id="1",
                        title="Intern",
                        job_url="https://boards.greenhouse.io/promoted/jobs/1",
                        apply_url="https://boards.greenhouse.io/promoted/jobs/1",
                        metadata={
                            "company_name": "Promoted",
                            "company_domain": "promoted.com",
                        },
                    )
                ],
            )
        return AdapterFetchResult(status="ok", jobs=[])

    monkeypatch.setattr(orchestrator, "_poll_adapter", fake_poll)

    summary = await orchestrator.run_sync(
        adapters={"simplify", "greenhouse"},
    )

    assert order[0] == "simp"
    assert order[1] == "promoted-board"
    assert order[2] == "cold-board"
    assert summary["promoted_boards"] >= 1
