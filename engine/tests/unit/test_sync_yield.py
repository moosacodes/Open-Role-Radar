"""Yield-based hot/cold demotion."""

from __future__ import annotations

from datetime import UTC, datetime

from openroleradar.config import find_repo_root
from openroleradar.models.enums import CareerLevel, JobLifecycle
from openroleradar.models.job import Company, Job, Provenance, Source
from openroleradar.models.state import LiveState
from openroleradar.sync import SyncOrchestrator


def _source(**overrides: object) -> Source:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base = dict(
        source_id="src1",
        company_id="co1",
        company_name="Acme",
        company_domain="acme.com",
        careers_url="https://acme.com/careers",
        adapter="greenhouse",
        adapter_tenant="acme",
        first_discovered_at=now,
        poll_tier="hot",
    )
    base.update(overrides)
    return Source(**base)  # type: ignore[arg-type]


def test_zero_yield_demotes_hot_to_warm_then_cold(tmp_path) -> None:
    orch = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    state = LiveState(generated_at=now)
    source = _source()
    state.sources[source.source_id] = source

    orch._update_source_yield(state, source, now=now)
    assert source.zero_yield_streak == 1
    assert source.poll_tier == "hot"

    orch._update_source_yield(state, source, now=now)
    assert source.zero_yield_streak == 2
    assert source.poll_tier == "warm"

    orch._update_source_yield(state, source, now=now)
    orch._update_source_yield(state, source, now=now)
    assert source.zero_yield_streak == 4
    assert source.poll_tier == "cold"


def test_public_yield_promotes_to_hot(tmp_path) -> None:
    orch = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    state = LiveState(generated_at=now)
    source = _source(poll_tier="cold", zero_yield_streak=5)
    state.sources[source.source_id] = source
    state.jobs["j1"] = Job(
        job_id="j1",
        source_job_id="j1",
        company_id="co1",
        company_name="Acme",
        title="Software Engineering Intern",
        job_url="https://acme.com/j1",
        apply_url="https://acme.com/j1/apply",
        first_seen_at=now,
        last_seen_at=now,
        lifecycle=JobLifecycle.OPEN,
        career_level=CareerLevel.INTERNSHIP,
        career_level_confidence=0.9,
        provenance=Provenance(
            adapter="greenhouse",
            source_id="src1",
            source_job_id="j1",
            fetched_at=now,
            content_hash="h1",
        ),
    )

    orch._update_source_yield(state, source, now=now)
    assert source.poll_tier == "hot"
    assert source.zero_yield_streak == 0
    assert source.public_open_count >= 1
    assert source.last_public_seen_at == now


def test_merge_seeds_preserves_demoted_tier(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    orch = SyncOrchestrator(root=find_repo_root(), state_dir=tmp_path / "state")

    class FakeSeed:
        company = "Acme"
        domain = "acme.com"
        adapter = "greenhouse"
        tenant = "acme"
        careers_url = "https://acme.com/careers"

    monkeypatch.setattr(
        "openroleradar.sync.load_seed_sources", lambda root=None: [FakeSeed()]
    )
    monkeypatch.setattr(
        "openroleradar.sync.seed_to_company",
        lambda seed, now: Company(
            company_id="co1",
            name=seed.company,
            normalized_name="acme",
            canonical_domain=seed.domain,
            first_seen_at=now,
            last_seen_at=now,
        ),
    )
    monkeypatch.setattr(
        "openroleradar.sync.seed_to_source",
        lambda seed, now: _source(first_discovered_at=now),
    )

    state = LiveState(generated_at=now)
    existing = _source(poll_tier="dormant", zero_yield_streak=9)
    state.sources["src1"] = existing
    state.companies["co1"] = Company(
        company_id="co1",
        name="Acme",
        normalized_name="acme",
        canonical_domain="acme.com",
        first_seen_at=now,
        last_seen_at=now,
    )

    orch.merge_seeds(state)
    assert state.sources["src1"].poll_tier == "dormant"
