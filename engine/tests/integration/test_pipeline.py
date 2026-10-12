"""End-to-end pipeline integration tests with fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest

from openroleradar.config import find_repo_root
from openroleradar.discovery.registry import load_seed_sources, seed_to_company, seed_to_source
from openroleradar.export.archive import ArchiveExporter
from openroleradar.export.readme import ReadmeGenerator
from openroleradar.export.static_api import StaticApiExporter
from openroleradar.feeds.generator import FeedGenerator
from openroleradar.health.monitor import HealthMonitor
from openroleradar.models.state import AdapterHealthStats, LiveState
from openroleradar.storage.local import LocalStateStore
from openroleradar.storage.serialize import deserialize_state, serialize_state
from tests.conftest import _sample_state, sample_job


@pytest.fixture
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture
def pipeline_state(repo_root: Path) -> LiveState:
    state = _sample_state(jobs={}, companies={}, sources={})
    now = datetime.now(UTC)
    for seed in load_seed_sources(repo_root)[:3]:
        company = seed_to_company(seed, now=now)
        source = seed_to_source(seed, now=now)
        state.companies[company.company_id] = company
        state.sources[source.source_id] = source
        job = sample_job(
            job_id=f"job-{seed.tenant}",
            company_id=company.company_id,
            company_name=seed.company,
            job_url=f"https://boards.greenhouse.io/{seed.tenant}/jobs/1",
            apply_url=f"https://boards.greenhouse.io/{seed.tenant}/jobs/1/apply",
        )
        state.jobs[job.job_id] = job
    return state


def test_full_export_pipeline(tmp_path: Path, pipeline_state: LiveState) -> None:
    site_root = tmp_path / "site"
    feeds_dir = tmp_path / "feeds"
    archive_dir = tmp_path / "archives"
    readme_path = tmp_path / "README.md"

    store = LocalStateStore(tmp_path / "state")
    store.save(pipeline_state)

    loaded = store.load()
    assert len(loaded.sources) == 3

    # Static API
    api_exporter = StaticApiExporter()
    manifest = api_exporter.export(loaded, site_root)
    assert (site_root / "public" / "api" / "v1" / "manifest.json").exists()
    assert (site_root / "public" / "api" / "v1" / "meta.json").exists()
    jobs_manifest = orjson.loads(
        (site_root / "public" / "api" / "v1" / "jobs" / "manifest.json").read_bytes()
    )
    assert jobs_manifest["total_jobs"] == 3
    assert manifest.endpoints["meta"].endswith("meta.json")

    # Feeds (strict + wide formats, plus early-career.atom alias)
    feed_gen = FeedGenerator()
    artifacts = feed_gen.write_all(loaded, feeds_dir)
    assert len(artifacts) == 7
    feed_json = orjson.loads((feeds_dir / "jobs.json").read_bytes())
    assert len(feed_json["items"]) == 3
    assert (feeds_dir / "jobs-wide.json").exists()
    assert (feeds_dir / "early-career.atom").exists()

    # Archive
    archive = ArchiveExporter()
    artifact = archive.export(loaded, archive_dir, month="2026-01")
    assert artifact.path.exists()
    assert artifact.job_count == 3

    # README stats (only when markers are present)
    readme_path.write_text(
        "# Test\n\n<!-- GENERATED_STATS:START -->\nold\n<!-- GENERATED_STATS:END -->\n",
        encoding="utf-8",
    )
    readme_gen = ReadmeGenerator()
    content = readme_gen.update_readme(readme_path, loaded)
    assert "Public early-career roles" in content
    assert "GENERATED_STATS:START" in content


def test_health_monitor_regression(pipeline_state: LiveState) -> None:
    monitor = HealthMonitor()
    state = pipeline_state.model_copy(
        update={
            "adapter_health": {
                "greenhouse": AdapterHealthStats(
                    adapter="greenhouse",
                    success_count=2,
                    failure_count=18,
                    sample_errors=["timeout", "500 error"],
                )
            }
        }
    )
    report = monitor.analyze(state)
    assert len(report.regressions) == 1
    assert report.regressions[0].adapter == "greenhouse"
    updated = monitor.apply_source_health(state)
    for source in updated.sources.values():
        if source.adapter == "greenhouse":
            assert source.health_status == "degraded"


def test_serialize_pipeline_state_roundtrip(pipeline_state: LiveState) -> None:
    raw = serialize_state(pipeline_state)
    restored = deserialize_state(raw)
    assert len(restored.jobs) == len(pipeline_state.jobs)
    assert len(restored.sources) == len(pipeline_state.sources)
