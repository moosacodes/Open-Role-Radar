"""Synchronization orchestrator — fetch, normalize, lifecycle, export."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openroleradar.adapters import get_adapter
from openroleradar.adapters.base import AdapterFetchResult
from openroleradar.config import find_repo_root, load_project_config
from openroleradar.discovery.promote import FAST_ATS_ADAPTERS, promote_ats_board_from_url
from openroleradar.discovery.registry import (
    deterministic_id,
    load_seed_sources,
    seed_to_company,
    seed_to_source,
)
from openroleradar.export.site_data import SiteDataBuilder
from openroleradar.export.static_api import StaticApiExporter
from openroleradar.feeds.generator import FeedGenerator
from openroleradar.health.monitor import HealthMonitor
from openroleradar.http.client import SafeHTTPClient
from openroleradar.lifecycle.manager import LifecycleManager
from openroleradar.models.job import Company, RawJob, Source
from openroleradar.models.state import LiveState
from openroleradar.normalize.pipeline import normalize_raw_job
from openroleradar.normalize.text import canonicalize_text
from openroleradar.storage.local import LocalStateStore

logger = logging.getLogger(__name__)


class SyncOrchestrator:
    """Coordinates source polling, normalization, and state updates."""

    def __init__(self, root: Path | None = None, state_dir: Path | None = None) -> None:
        self.root = root or find_repo_root()
        self.config = load_project_config(self.root)
        self.state_dir = state_dir or (self.root / ".local" / "state")
        self.store = LocalStateStore(self.state_dir)
        self.lifecycle = LifecycleManager()
        self.health = HealthMonitor(self.config)

    def bootstrap_state(self) -> LiveState:
        """Initialize empty state with seed sources."""
        now = datetime.now(UTC)
        state = LiveState(generated_at=now)
        for seed in load_seed_sources(self.root):
            company = seed_to_company(seed, now=now)
            source = seed_to_source(seed, now=now)
            state.companies[company.company_id] = company
            state.sources[source.source_id] = source
        state.metadata["bootstrapped_at"] = now.isoformat()
        return state

    def merge_seeds(self, state: LiveState) -> int:
        """Ensure newly added seed sources appear in existing live state.

        Also disables obsolete seed variants (same company domain, old adapter/tenant)
        so broken Greenhouse→Ashby migrations do not keep failing forever.
        """
        now = datetime.now(UTC)
        added = 0
        seed_ids: set[str] = set()
        seed_domains: set[str] = set()
        for seed in load_seed_sources(self.root):
            company = seed_to_company(seed, now=now)
            source = seed_to_source(seed, now=now)
            seed_ids.add(source.source_id)
            seed_domains.add(seed.domain.lower())
            if company.company_id not in state.companies:
                state.companies[company.company_id] = company
            if source.source_id not in state.sources:
                source.poll_tier = "hot"
                state.sources[source.source_id] = source
                added += 1
            else:
                existing = state.sources[source.source_id]
                tenant_changed = (
                    existing.adapter != source.adapter
                    or existing.adapter_tenant != source.adapter_tenant
                )
                existing.company_name = source.company_name
                existing.company_domain = source.company_domain
                existing.careers_url = source.careers_url
                existing.adapter = source.adapter
                existing.adapter_tenant = source.adapter_tenant
                existing.poll_tier = "hot"
                if tenant_changed:
                    # Seed tenant/adapter fix — give the board a clean slate.
                    existing.enabled = True
                    existing.health_status = "healthy"
                    existing.consecutive_failures = 0
                elif existing.health_status != "disabled":
                    existing.enabled = True

        for existing in state.sources.values():
            domain = (existing.company_domain or "").lower()
            if domain in seed_domains and existing.source_id not in seed_ids:
                existing.enabled = False
                existing.health_status = "disabled"
        return added

    def load_or_bootstrap(self) -> LiveState:
        self.store.ensure_directory()
        if self.store.exists():
            state = self.store.load()
            self.merge_seeds(state)
            return state
        state = self.bootstrap_state()
        self.store.save(state)
        return state

    @staticmethod
    def source_shard(source_id: str, shards: int) -> int:
        """Stable shard assignment for parallel sync workers."""
        if shards <= 1:
            return 0
        digest = hashlib.blake2b(source_id.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % shards

    def _sources_due(
        self,
        state: LiveState,
        now: datetime,
        *,
        adapters: set[str] | None = None,
        shard: int = 0,
        shards: int = 1,
    ) -> list[Source]:
        """Return sources this worker should poll.

        Hot boards (and Simplify discovery feeds) are always eligible so we can
        be first on active employers. Warm/cold/dormant boards only join when
        ``next_due_at`` has elapsed, which rotates coverage across runs without
        burning the whole budget on quiet boards every cycle.

        When shards > 1, only this worker's partition is returned. Simplify
        always runs on shard 0 so discovery stays centralized.
        """
        if shards < 1:
            raise ValueError("shards must be >= 1")
        if shard < 0 or shard >= shards:
            raise ValueError(f"shard must be in [0, {shards})")

        max_sources = int(self.config.polling.get("max_sources_per_sync", 500))
        max_hot = int(self.config.polling.get("max_hot_sources_per_sync", max_sources))
        max_cold = int(self.config.polling.get("max_cold_sources_per_sync", max_sources))

        hot: list[Source] = []
        cold: list[Source] = []
        for source in state.sources.values():
            if not source.enabled:
                continue
            if source.health_status in ("blocked", "disabled", "unsupported"):
                continue
            if adapters is not None and source.adapter not in adapters:
                continue
            if shards > 1:
                if source.adapter == "simplify":
                    if shard != 0:
                        continue
                elif self.source_shard(source.source_id, shards) != shard:
                    continue

            is_priority = source.adapter == "simplify" or source.poll_tier == "hot"
            if is_priority:
                hot.append(source)
                continue
            # Cold/warm rotate by due time — skip boards that are not due yet.
            if source.next_due_at is not None and source.next_due_at > now:
                continue
            cold.append(source)

        hot.sort(
            key=lambda s: (
                0 if s.adapter == "simplify" else 1,
                s.next_due_at or datetime.min.replace(tzinfo=UTC),
            )
        )
        # Fair cold rotation: oldest due first, then stable source_id.
        cold.sort(
            key=lambda s: (
                s.next_due_at or datetime.min.replace(tzinfo=UTC),
                s.source_id,
            )
        )

        selected = [*hot[:max_hot], *cold[:max_cold]]
        return selected[:max_sources]

    def _company_for_raw(
        self,
        state: LiveState,
        source: Source,
        raw: RawJob,
        *,
        now: datetime,
    ) -> Company:
        """Resolve the employer for a raw job (Simplify listings are multi-company)."""
        if source.adapter != "simplify":
            company = state.companies.get(source.company_id)
            if company is None:
                raise KeyError(f"Missing company for source {source.source_id}")
            return company

        company_name = str(raw.metadata.get("company_name") or "").strip() or source.company_name
        domain = str(raw.metadata.get("company_domain") or "").strip().lower()
        if not domain:
            domain = f"{canonicalize_text(company_name).replace(' ', '-') or 'unknown'}.com"
        company_id = deterministic_id("company", domain)
        existing = state.companies.get(company_id)
        if existing is not None:
            existing.last_seen_at = now
            if company_name and existing.name != company_name:
                existing.aliases = sorted(
                    {*(existing.aliases or []), existing.name, company_name}
                )
                existing.name = company_name
            if source.adapter not in existing.ats_sources:
                existing.ats_sources = [*(existing.ats_sources or []), source.adapter]
            return existing

        company = Company(
            company_id=company_id,
            name=company_name,
            normalized_name=canonicalize_text(company_name).replace(" ", "-"),
            canonical_domain=domain,
            careers_urls=[f"https://{domain}/careers"],
            ats_sources=[source.adapter],
            first_seen_at=now,
            last_seen_at=now,
        )
        state.companies[company_id] = company
        return company

    async def _poll_adapter(
        self,
        client: SafeHTTPClient,
        source: Source,
    ) -> AdapterFetchResult | dict[str, Any]:
        """HTTP-only poll — safe to run concurrently across sources."""
        try:
            adapter = get_adapter(source.adapter)
        except KeyError:
            return {"status": "unsupported", "message": "unknown adapter"}

        if not getattr(adapter, "supported", True):
            return {"status": "unsupported", "message": "adapter unsupported"}

        try:
            return await adapter.fetch_jobs(source, client)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def _apply_poll_result(
        self,
        source: Source,
        state: LiveState,
        poll: AdapterFetchResult | dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a poll result to source/health state (serial)."""
        now = datetime.now(UTC)

        if isinstance(poll, dict):
            status = str(poll.get("status") or "error")
            if status == "unsupported":
                source.health_status = "unsupported"
                return {"source_id": source.source_id, "status": "unsupported", "jobs": []}
            message = str(poll.get("message") or status)
            source.last_failure_at = now
            source.consecutive_failures += 1
            if source.consecutive_failures >= 3:
                source.health_status = "degraded"
            if source.consecutive_failures >= 10:
                source.health_status = "failing"
            logger.warning("Source %s failed: %s", source.source_id, message)
            self.health.record_failure(state, source.adapter, message)
            return {
                "source_id": source.source_id,
                "status": "error",
                "error": message,
                "jobs": [],
            }

        result = poll
        if result.not_modified:
            source.last_success_at = now
            source.last_validated_at = now
            source.consecutive_failures = 0
            source.health_status = "healthy"
            if result.etag:
                source.etag = result.etag
            if result.last_modified:
                source.last_modified = result.last_modified
            return {"source_id": source.source_id, "status": "not_modified", "jobs": []}

        if result.status != "ok":
            source.last_failure_at = now
            source.consecutive_failures += 1
            if source.consecutive_failures >= 3:
                source.health_status = "degraded"
            if source.consecutive_failures >= 10:
                source.health_status = "failing"
            message = result.message or result.status
            logger.warning(
                "Source %s adapter status=%s: %s",
                source.source_id,
                result.status,
                message,
            )
            self.health.record_failure(state, source.adapter, message)
            return {
                "source_id": source.source_id,
                "status": "error",
                "error": message,
                "jobs": [],
            }

        source.last_success_at = now
        source.consecutive_failures = 0
        source.health_status = "healthy"
        source.last_validated_at = now
        if result.etag:
            source.etag = result.etag
        if result.last_modified:
            source.last_modified = result.last_modified
        self.health.record_success(state, source.adapter)
        return {
            "source_id": source.source_id,
            "status": "ok",
            "jobs": result.jobs,
            "adapter": source.adapter,
        }

    async def _fetch_source(
        self,
        client: SafeHTTPClient,
        source: Source,
        state: LiveState,
    ) -> dict[str, Any]:
        poll = await self._poll_adapter(client, source)
        return self._apply_poll_result(source, state, poll)

    def _promote_boards_from_raw_jobs(
        self,
        state: LiveState,
        raw_jobs: list[RawJob],
        *,
        now: datetime,
    ) -> list[Source]:
        """Turn Simplify/SWE List apply URLs into first-party ATS boards we poll ourselves."""
        promoted: list[Source] = []
        seen: set[str] = set()
        for raw in raw_jobs:
            apply_url = raw.apply_url or raw.job_url
            if not apply_url:
                continue
            company_name = str(
                (raw.metadata or {}).get("company_name") or raw.department or "Unknown"
            )
            company_domain = (raw.metadata or {}).get("company_domain")
            if not isinstance(company_domain, str):
                company_domain = None
            source = promote_ats_board_from_url(
                state,
                apply_url=apply_url,
                company_name=company_name,
                company_domain=company_domain,
                discovered_via="simplify",
                now=now,
            )
            if source is None or source.source_id in seen:
                continue
            seen.add(source.source_id)
            promoted.append(source)
        return promoted

    def _ingest_raw_jobs(
        self,
        state: LiveState,
        source: Source,
        raw_jobs: list[RawJob],
        *,
        now: datetime,
        summary: dict[str, Any],
    ) -> set[str]:
        source_job_ids: set[str] = set()
        touched_company_ids: set[str] = set()
        for raw in raw_jobs:
            source_job_ids.add(raw.source_job_id)
            try:
                company = self._company_for_raw(state, source, raw, now=now)
                touched_company_ids.add(company.company_id)
                job = normalize_raw_job(
                    raw,
                    company_id=company.company_id,
                    company_name=company.name,
                    adapter=source.adapter,
                    adapter_tenant=source.adapter_tenant,
                    source_id=source.source_id,
                    fetched_at=now,
                    source_health=source.health_status,
                    root=self.root,
                )
            except Exception as exc:
                logger.warning("Normalize failed for %s: %s", raw.source_job_id, exc)
                continue

            existing = state.jobs.get(job.job_id)
            if existing is None:
                state.jobs[job.job_id] = job
                summary["new_jobs"] += 1
                self.lifecycle.record_opened(state, job)
            else:
                changes = self.lifecycle.apply_update(existing, job, now)
                if changes:
                    summary["updated_jobs"] += 1

        if not touched_company_ids and source.company_id in state.companies:
            touched_company_ids.add(source.company_id)
        for company_id in touched_company_ids:
            company = state.companies[company_id]
            company.active_job_count = sum(
                1
                for j in state.jobs.values()
                if j.company_id == company.company_id
                and j.lifecycle.value in ("open", "reopened")
            )
            company.last_seen_at = now
        return source_job_ids

    def _sync_runtime_limit_minutes(self) -> float | None:
        """Return soft sync deadline in minutes, or None when unbounded."""
        raw = os.environ.get("OPENROLERADAR_MAX_SYNC_RUNTIME_MINUTES")
        if raw is not None and raw.strip() != "":
            minutes = float(raw)
        else:
            minutes = float(self.config.polling.get("max_sync_runtime_minutes", 0) or 0)
        if minutes <= 0:
            return None
        return minutes

    async def run_sync(
        self,
        *,
        sample: bool = False,
        adapters: set[str] | None = None,
        shard: int = 0,
        shards: int = 1,
    ) -> dict[str, Any]:
        """Execute a full synchronization cycle."""
        now = datetime.now(UTC)
        state = self.load_or_bootstrap()
        due = self._sources_due(
            state, now, adapters=adapters, shard=shard, shards=shards
        )
        if sample:
            due = due[:3]

        runtime_minutes = self._sync_runtime_limit_minutes()
        deadline = (
            now + timedelta(minutes=runtime_minutes) if runtime_minutes is not None else None
        )

        summary: dict[str, Any] = {
            "sources_due": len(due),
            "shard": shard,
            "shards": shards,
            "fetched": 0,
            "not_modified": 0,
            "new_jobs": 0,
            "updated_jobs": 0,
            "closed": 0,
            "reopened": 0,
            "failures": 0,
            "promoted_boards": 0,
            "truncated": False,
            "skipped_sources": 0,
            "started_at": now.isoformat(),
        }

        seen_by_source: dict[str, set[str]] = {}
        concurrency = max(1, int(self.config.http.get("global_concurrency", 32)))
        # One gather wave at a time so a soft deadline can stop between batches.
        # A single gather over hundreds of Greenhouse boards cannot be interrupted,
        # and that is what was blowing the Sync Fast Path job timeout.
        chunk_size = concurrency
        sem = asyncio.Semaphore(concurrency)

        async def poll_one(source: Source) -> tuple[Source, AdapterFetchResult | dict[str, Any]]:
            async with sem:
                return source, await self._poll_adapter(client, source)

        polled_ids: set[str] = set()
        async with SafeHTTPClient(self.config) as client:
            # Simplify discovery first so promotions can be polled in-cycle before
            # the soft runtime budget is spent on cold boards.
            simplify_due = [source for source in due if source.adapter == "simplify"]
            other_due = [source for source in due if source.adapter != "simplify"]
            pending = [*simplify_due, *other_due]

            while pending:
                if deadline is not None and datetime.now(UTC) >= deadline:
                    skipped = [s for s in pending if s.source_id not in polled_ids]
                    summary["truncated"] = True
                    summary["skipped_sources"] = len(skipped)
                    logger.warning(
                        "Sync runtime budget exhausted after %s sources; skipping %s",
                        len(polled_ids),
                        len(skipped),
                    )
                    break

                remaining = [source for source in pending if source.source_id not in polled_ids]
                pending = []
                if not remaining:
                    break

                batch = remaining[:chunk_size]
                leftover = remaining[chunk_size:]

                polls = await asyncio.gather(
                    *[poll_one(source) for source in batch],
                    return_exceptions=True,
                )

                newly_promoted: list[Source] = []
                for item in polls:
                    if isinstance(item, BaseException):
                        summary["failures"] += 1
                        logger.warning("Parallel poll crashed: %s", item)
                        continue

                    source, poll = item
                    polled_ids.add(source.source_id)
                    tier_key = f"{source.poll_tier}_minutes"
                    tier_minutes = int(self.config.polling.get("tiers", {}).get(tier_key, 60))
                    source.next_due_at = now + timedelta(minutes=tier_minutes)

                    result = self._apply_poll_result(source, state, poll)
                    if result["status"] == "not_modified":
                        summary["not_modified"] += 1
                        continue
                    if result["status"] in ("error", "unsupported"):
                        summary["failures"] += 1
                        continue

                    summary["fetched"] += 1
                    raw_jobs: list[RawJob] = list(result.get("jobs") or [])
                    new_before = int(summary["new_jobs"])
                    seen_by_source[source.source_id] = self._ingest_raw_jobs(
                        state, source, raw_jobs, now=now, summary=summary
                    )
                    # Boards that just produced brand-new roles stay on the hot tier;
                    # quiet boards demote so cold rotation can reclaim budget.
                    if int(summary["new_jobs"]) > new_before:
                        source.poll_tier = "hot"
                    elif source.poll_tier == "hot" and source.adapter != "simplify":
                        source.poll_tier = "warm"

                    if source.adapter == "simplify":
                        promoted = self._promote_boards_from_raw_jobs(
                            state, raw_jobs, now=now
                        )
                        summary["promoted_boards"] += len(promoted)
                        for board in promoted:
                            if board.source_id in polled_ids:
                                continue
                            # Always allow newly discovered public ATS boards in-cycle.
                            if (
                                adapters is not None
                                and board.adapter not in adapters
                                and board.adapter not in FAST_ATS_ADAPTERS
                            ):
                                continue
                            newly_promoted.append(board)

                # Promoted boards jump the queue so discovery converts to
                # first-party polls before cold leftovers burn the budget.
                pending.extend(newly_promoted)
                pending.extend(leftover)

        for source_id, seen_ids in seen_by_source.items():
            src = state.sources.get(source_id)
            if src is None or src.health_status in {"failing", "degraded", "unsupported", "blocked"}:
                continue
            closed, reopened = self.lifecycle.process_missing_jobs(state, src, seen_ids, now)
            summary["closed"] += closed
            summary["reopened"] += reopened

        # Auto-disable boards that keep failing so they stop burning poll budget.
        for source in state.sources.values():
            if source.consecutive_failures >= 10 and source.health_status == "failing":
                source.enabled = False
                source.health_status = "disabled"

        state.generated_at = datetime.now(UTC)
        self.store.save(state)
        summary["finished_at"] = state.generated_at.isoformat()
        summary["total_jobs"] = len(state.jobs)
        summary["polled_sources"] = len(polled_ids)
        return summary

    def build_exports(self) -> dict[str, Any]:
        """Generate static API, site data, and feeds."""
        from openroleradar.classify.refresh import reclassify_state

        state = self.store.load()
        reclassified = reclassify_state(state, root=self.root)
        state.generated_at = datetime.now(UTC)
        self.store.save(state)

        site_root = self.root / "site"
        StaticApiExporter(self.config).export(state, site_root)

        data_dir = site_root / "public" / "data"
        manifest = SiteDataBuilder(self.config).write(state, data_dir)

        feeds_dir = site_root / "public" / "feeds"
        FeedGenerator(self.config).write_all(state, feeds_dir)

        public_jobs = manifest.total_jobs
        return {
            "api_dir": str(site_root / "public" / "api" / "v1"),
            "data_shards": len(manifest.shards),
            "total_jobs": public_jobs,
            "tracked_jobs": len(state.jobs),
            "sources": len(state.sources),
            "reclassified": reclassified,
        }

    def publish_live_state_release(self) -> dict[str, Any]:
        """Publish durable live-state to the GitHub Release (requires GITHUB_TOKEN)."""
        from openroleradar.storage.github_release import GitHubReleaseStore

        state = self.store.load()
        with GitHubReleaseStore(config=self.config) as release_store:
            manifest = release_store.save(state)
        return manifest.to_dict()

    def restore_live_state_release(self) -> bool:
        """Load live-state from GitHub Release when local cache is empty."""
        from openroleradar.storage.github_release import GitHubReleaseStore

        if self.store.exists():
            return False
        try:
            with GitHubReleaseStore(config=self.config) as release_store:
                state = release_store.load()
        except Exception as exc:
            logger.warning("Could not restore live-state release: %s", exc)
            return False
        if not state.sources and not state.jobs:
            return False
        self.store.ensure_directory()
        self.merge_seeds(state)
        self.store.save(state)
        return True
