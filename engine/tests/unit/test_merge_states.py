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
