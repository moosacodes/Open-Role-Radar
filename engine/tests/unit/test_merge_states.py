"""Tests for sharded live-state merge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openroleradar.models.job import Company, Job, JobLifecycle, Provenance, Source
from openroleradar.models.state import LiveState
from openroleradar.storage.merge import merge_states


def _job(job_id: str, seen: datetime) -> Job:
    return Job(
        job_id=job_id,
        source_job_id=job_id,
        company_id="co1",
        company_name="Acme",
        title="Intern",
        job_url=f"https://example.com/{job_id}",
        apply_url=f"https://example.com/{job_id}/apply",
        first_seen_at=seen,
        last_seen_at=seen,
        lifecycle=JobLifecycle.OPEN,
        provenance=Provenance(
            adapter="greenhouse",
            source_id="src1",
            source_job_id=job_id,
            fetched_at=seen,
            content_hash=f"hash-{job_id}-{seen.isoformat()}",
        ),
    )


def test_merge_states_prefers_fresher_job_and_source() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    base = LiveState(
        companies={
            "co1": Company(
                company_id="co1",
                name="Acme",
                normalized_name="acme",
                canonical_domain="acme.com",
                first_seen_at=t0,
                last_seen_at=t0,
            )
        },
        sources={
            "src1": Source(
                source_id="src1",
                company_id="co1",
                company_name="Acme",
                company_domain="acme.com",
                careers_url="https://acme.com/careers",
                adapter="greenhouse",
                adapter_tenant="acme",
                first_discovered_at=t0,
                last_success_at=t0,
            )
        },
        jobs={"j1": _job("j1", t0)},
        generated_at=t0,
    )
    shard = base.model_copy(deep=True)
    shard.sources["src1"].last_success_at = t1
    shard.jobs["j1"] = _job("j1", t1)
    shard.jobs["j2"] = _job("j2", t1)
    shard.generated_at = t1

    merged = merge_states([base, shard])
    assert merged.jobs["j1"].last_seen_at == t1
    assert "j2" in merged.jobs
    assert merged.sources["src1"].last_success_at == t1
    assert merged.generated_at == t1


def test_merge_states_prefers_closed_lifecycle_on_equal_last_seen() -> None:
    """Closing does not bump last_seen_at; stale OPEN shards must not win."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    open_job = _job("j1", t0)
    closed_job = _job("j1", t0).model_copy(
        update={"lifecycle": JobLifecycle.CLOSED, "closed_at": t0 + timedelta(minutes=5)}
    )
    open_state = LiveState(jobs={"j1": open_job}, generated_at=t0)
    closed_state = LiveState(
        jobs={"j1": closed_job},
        generated_at=t0 + timedelta(minutes=5),
        metadata={"miss_counts": {"j1": 4}},
    )
    # Stale OPEN snapshot merged after the closing shard.
    merged = merge_states([closed_state, open_state])
    assert merged.jobs["j1"].lifecycle == JobLifecycle.CLOSED
    assert merged.metadata["miss_counts"]["j1"] == 4


def test_merge_states_merges_miss_counts_across_shards() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = LiveState(generated_at=t0, metadata={"miss_counts": {"a": 2, "shared": 1}})
    b = LiveState(
        generated_at=t0 + timedelta(seconds=1),
        metadata={"miss_counts": {"b": 3, "shared": 4}, "other": "x"},
    )
    merged = merge_states([a, b])
    assert merged.metadata["miss_counts"] == {"a": 2, "b": 3, "shared": 4}
    assert merged.metadata["other"] == "x"


def test_merge_states_copy_first_false_mutates_accumulator() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    base = LiveState(jobs={"j1": _job("j1", t0)}, generated_at=t0)
    incoming = LiveState(jobs={"j2": _job("j2", t0)}, generated_at=t0)
    merged = merge_states([base, incoming], copy_first=False)
    assert merged is base
    assert "j2" in merged.jobs
