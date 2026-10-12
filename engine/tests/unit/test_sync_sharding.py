"""Tests for sync source sharding."""

from __future__ import annotations

from datetime import UTC, datetime

from openroleradar.config import find_repo_root
from openroleradar.models.job import Source
from openroleradar.models.state import LiveState
from openroleradar.sync import SyncOrchestrator


def test_source_shard_is_stable_and_partitioned() -> None:
    shards = 4
    seen: dict[int, int] = {i: 0 for i in range(shards)}
    for i in range(200):
        idx = SyncOrchestrator.source_shard(f"src-{i}", shards)
        assert 0 <= idx < shards
        seen[idx] += 1
    assert SyncOrchestrator.source_shard("src-1", shards) == SyncOrchestrator.source_shard(
        "src-1", shards
    )
    assert all(count > 0 for count in seen.values())


def test_sources_due_keeps_simplify_on_shard_zero_only(tmp_path) -> None:
    orchestrator = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    now = datetime.now(UTC)
    state = LiveState(
        sources={
            "simp": Source(
                source_id="simp",
                company_id="co",
                company_name="Simplify",
                company_domain="simplify.jobs",
                careers_url="https://simplify.jobs",
                adapter="simplify",
                adapter_tenant="listings",
                first_discovered_at=now,
                enabled=True,
            ),
            "gh": Source(
                source_id="aaaaaaaaaaaaaaaa",
                company_id="co2",
                company_name="Acme",
                company_domain="acme.com",
                careers_url="https://acme.com/careers",
                adapter="greenhouse",
                adapter_tenant="acme",
                first_discovered_at=now,
                enabled=True,
            ),
        }
    )
    shard0 = orchestrator._sources_due(state, now, shard=0, shards=4)
    assert any(s.adapter == "simplify" for s in shard0)

    for shard in (1, 2, 3):
        due = orchestrator._sources_due(state, now, shard=shard, shards=4)
        assert all(s.adapter != "simplify" for s in due)
