"""Site data export modes and delta previews."""

from __future__ import annotations

from datetime import UTC, datetime

from openroleradar.config import find_repo_root, load_project_config
from openroleradar.export.site_data import SiteDataBuilder
from openroleradar.models.enums import CareerLevel, JobLifecycle
from openroleradar.models.job import Company, Job, Provenance
from openroleradar.models.state import LiveState


def _job(job_id: str, *, level: CareerLevel, title: str) -> Job:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Job(
        job_id=job_id,
        source_job_id=job_id,
        company_id="co1",
        company_name="Acme",
        title=title,
        job_url=f"https://example.com/{job_id}",
        apply_url=f"https://example.com/{job_id}/apply",
        career_level=level,
        career_level_confidence=0.9,
        first_seen_at=now,
        last_seen_at=now,
        lifecycle=JobLifecycle.OPEN,
        provenance=Provenance(
            adapter="greenhouse",
            source_id="src1",
            source_job_id=job_id,
            fetched_at=now,
            content_hash=job_id,
        ),
    )


def test_strict_and_wide_exports(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = LiveState(
        generated_at=now,
        companies={
            "co1": Company(
                company_id="co1",
                name="Acme",
                normalized_name="acme",
                canonical_domain="acme.com",
                first_seen_at=now,
                last_seen_at=now,
            )
        },
        jobs={
            "swe": _job("swe", level=CareerLevel.INTERNSHIP, title="Software Engineer Intern"),
            "mkt": _job("mkt", level=CareerLevel.INTERNSHIP, title="Marketing Intern"),
        },
    )
    builder = SiteDataBuilder(load_project_config(find_repo_root()))
    strict = builder.write(state, tmp_path / "strict", mode="strict")
    wide = builder.write(state, tmp_path / "wide", mode="wide")
    assert strict.total_jobs == 1
    assert wide.total_jobs == 2
    assert (tmp_path / "strict" / "delta.json").exists()
    assert (tmp_path / "wide" / "manifest.json").exists()
