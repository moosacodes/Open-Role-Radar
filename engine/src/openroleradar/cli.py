"""OpenRoleRadar command-line interface."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from openroleradar.config import find_repo_root, load_project_config
from openroleradar.discovery.career_site import CareerSiteInspector
from openroleradar.discovery.common_crawl import CommonCrawlDiscovery
from openroleradar.discovery.github_search import GitHubCodeSearch
from openroleradar.discovery.promote import (
    domain_from_url,
    promote_validation,
    quarantine_candidate,
)
from openroleradar.discovery.registry import load_seed_sources
from openroleradar.discovery.validate import validate_candidate
from openroleradar.export.archive import ArchiveExporter
from openroleradar.export.readme import ReadmeGenerator
from openroleradar.health.monitor import HealthMonitor
from openroleradar.models.state import LiveState
from openroleradar.storage.local import LocalStateStore
from openroleradar.sync import SyncOrchestrator

app = typer.Typer(
    name="openroleradar",
    help="OpenRoleRadar — global early-career opportunity intelligence",
    no_args_is_help=True,
)
console = Console()


def _repo_root() -> Path:
    try:
        return find_repo_root()
    except FileNotFoundError:
        console.print("[red]Could not find repository root (config/project.yml)[/red]")
        raise typer.Exit(1) from None


def _state_store(root: Path) -> LocalStateStore:
    store = LocalStateStore(root / ".local" / "state")
    store.ensure_directory()
    return store


@app.command()
def sync(
    sample: Annotated[bool, typer.Option("--sample", help="Sync only a few sources")] = False,
    adapters: Annotated[
        str | None,
        typer.Option(
            "--adapters",
            help="Comma-separated adapter allowlist (e.g. simplify). Defaults to all.",
        ),
    ] = None,
    shard: Annotated[
        int,
        typer.Option("--shard", help="Zero-based shard index for parallel sync workers."),
    ] = 0,
    shards: Annotated[
        int,
        typer.Option("--shards", help="Total shard count. Use 1 for an unsharded sync."),
    ] = 1,
) -> None:
    """Poll due sources, normalize jobs, update lifecycle state."""
    root = _repo_root()
    orchestrator = SyncOrchestrator(root=root)
    restored = orchestrator.restore_live_state_release()
    if restored:
        console.print("[cyan]Restored live state from GitHub Release[/cyan]")
    allow = None
    raw_adapters = adapters or os.environ.get("OPENROLERADAR_ADAPTERS")
    if raw_adapters:
        allow = {part.strip().lower() for part in raw_adapters.split(",") if part.strip()}
        console.print(f"[cyan]Adapter filter:[/cyan] {', '.join(sorted(allow))}")
    env_shards = os.environ.get("OPENROLERADAR_SHARDS")
    env_shard = os.environ.get("OPENROLERADAR_SHARD")
    if env_shards and shards == 1:
        shards = int(env_shards)
    if env_shard and shard == 0 and env_shards:
        shard = int(env_shard)
    if shards > 1:
        console.print(f"[cyan]Shard:[/cyan] {shard}/{shards}")
    console.print("[bold]Starting synchronization...[/bold]")
    summary = asyncio.run(
        orchestrator.run_sync(sample=sample, adapters=allow, shard=shard, shards=shards)
    )
    table = Table(title="Sync Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for key, value in summary.items():
        table.add_row(str(key), str(value))
    console.print(table)
    if summary.get("failures", 0) > 0 and summary.get("fetched", 0) == 0:
        raise typer.Exit(1)


def _load_shard_state(path: Path) -> LiveState | None:
    """Load one shard state directory/file, or None when missing/unrecognized."""
    if path.is_dir():
        store = LocalStateStore(path)
        if not store.exists():
            console.print(f"[yellow]Skipping empty shard dir:[/yellow] {path}")
            return None
        return store.load()
    if path.name.endswith(".zst") or path.name.endswith(".json"):
        if path.name == "state.json.zst" or (path.parent / "state.json.zst").exists():
            return LocalStateStore(path.parent).load()
        console.print(f"[yellow]Skipping unrecognized state file:[/yellow] {path}")
        return None
    console.print(f"[yellow]Skipping:[/yellow] {path}")
    return None


@app.command("merge-states")
def merge_states_cmd(
    inputs: Annotated[
        list[Path],
        typer.Argument(help="State directories or state.json.zst files from sync shards"),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", help="Output state directory"),
    ] = Path(".local/state"),
) -> None:
    """Merge sharded live-state snapshots into one directory for publish."""
    import gc

    from openroleradar.storage.merge import merge_states

    # Fold shards one-by-one so peak memory stays ~2 states (each compressed
    # artifact is ~75MB; holding four fully inflated models OOMs free runners).
    merged: LiveState | None = None
    shard_count = 0
    for raw in inputs:
        state = _load_shard_state(raw)
        if state is None:
            continue
        shard_count += 1
        console.print(
            f"Loaded shard {shard_count}: {len(state.jobs):,} jobs / "
            f"{len(state.sources):,} sources from {raw}"
        )
        if merged is None:
            merged = state
        else:
            console.print(f"Merging into accumulator ({len(merged.jobs):,} jobs)...")
            merged = merge_states([merged, state], copy_first=False)
            del state
            gc.collect()

    if merged is None or shard_count == 0:
        console.print("[red]No shard states found to merge[/red]")
        raise typer.Exit(1)

    out_store = LocalStateStore(out)
    artifact = out_store.save(merged)
    console.print(
        f"[green]Merged {shard_count} shards[/green] → {artifact.path} "
        f"({len(merged.jobs):,} jobs, {len(merged.sources):,} sources)"
    )


@app.command("publish-state")
def publish_state() -> None:
    """Publish local live-state to the durable GitHub Release."""
    root = _repo_root()
    result = SyncOrchestrator(root=root).publish_live_state_release()
    console.print_json(json.dumps(result, indent=2))


@app.command("build-data")
def build_data(
    slim: Annotated[
        bool,
        typer.Option(
            "--slim/--full",
            help="Skip heavy Static API export (fast path). Env: OPENROLERADAR_SLIM_EXPORT=1",
        ),
    ] = False,
) -> None:
    """Export static API, site data shards, and feeds."""
    root = _repo_root()
    env_slim = os.environ.get("OPENROLERADAR_SLIM_EXPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    result = SyncOrchestrator(root=root).build_exports(slim=slim or env_slim)
    console.print_json(json.dumps(result, indent=2))


@app.command("build-site-data")
def build_site_data(
    slim: Annotated[
        bool,
        typer.Option("--slim/--full", help="Skip heavy Static API export."),
    ] = False,
) -> None:
    """Alias for site data export portion of build-data."""
    build_data(slim=slim)


@app.command()
def discover(
    github: Annotated[bool, typer.Option("--github/--no-github")] = True,
    common_crawl: Annotated[
        bool, typer.Option("--common-crawl/--no-common-crawl", help="Query Common Crawl CDX")
    ] = False,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish/--no-publish",
            help="Publish live-state release when GITHUB_TOKEN is set",
        ),
    ] = True,
) -> None:
    """Discover ATS boards, validate candidates, and promote into live state."""
    root = _repo_root()
    config = load_project_config(root)
    store = _state_store(root)
    orchestrator = SyncOrchestrator(root=root)
    if store.exists():
        state = store.load()
    else:
        restored = orchestrator.restore_live_state_release()
        state = store.load() if restored and store.exists() else orchestrator.bootstrap_state()

    counts = {
        "github_candidates": 0,
        "common_crawl_candidates": 0,
        "career_inspected": 0,
        "promoted": 0,
        "quarantined": 0,
        "already_tracked": 0,
    }

    def _handle_candidate(
        *,
        url: str,
        platform: str | None,
        tenant: str | None,
        confidence: float,
        discovered_via: str,
        company_domain: str | None = None,
        company_name: str | None = None,
        has_json_ld: bool = False,
    ) -> None:
        domain = company_domain or domain_from_url(url)
        result = validate_candidate(
            url=url,
            company_domain=domain,
            platform=platform,
            tenant=tenant,
            base_confidence=confidence,
            has_json_ld=has_json_ld,
            config=config,
            root=root,
        )
        if result.source_id and result.source_id in state.sources:
            counts["already_tracked"] += 1
            return
        if result.accepted:
            promoted = promote_validation(
                state,
                result,
                discovered_via=discovered_via,
                company_name=company_name,
            )
            if promoted is not None:
                counts["promoted"] += 1
            return
        quarantine_candidate(
            state,
            url=url,
            result=result,
            discovered_via=discovered_via,
        )
        counts["quarantined"] += 1

    if github:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        with GitHubCodeSearch(config=config, token=token) as search:
            candidates, cursor = search.discover(cursor=state.discovery.github_cursor)
            counts["github_candidates"] = len(candidates)
            state.discovery.github_cursor = cursor
            state.discovery.last_github_run = datetime.now(UTC)
            for gh_candidate in candidates:
                _handle_candidate(
                    url=gh_candidate.url,
                    platform=gh_candidate.platform,
                    tenant=gh_candidate.tenant,
                    confidence=gh_candidate.confidence,
                    discovered_via="github",
                )

    if common_crawl:
        try:
            with CommonCrawlDiscovery(config=config) as discovery:
                crawl_candidates, crawl_index = discovery.discover(
                    index=state.discovery.common_crawl_index
                )
                counts["common_crawl_candidates"] = len(crawl_candidates)
                state.discovery.common_crawl_index = crawl_index
                state.discovery.last_common_crawl_run = datetime.now(UTC)
                for crawl_candidate in crawl_candidates:
                    _handle_candidate(
                        url=crawl_candidate.url,
                        platform=crawl_candidate.platform,
                        tenant=crawl_candidate.tenant,
                        confidence=crawl_candidate.confidence,
                        discovered_via="common_crawl",
                    )
        except Exception as exc:  # noqa: BLE001 — CC index outages must not fail discover
            console.print(f"[yellow]Common Crawl skipped:[/yellow] {exc}")
            counts["common_crawl_error"] = str(exc)

    with CareerSiteInspector(config=config) as inspector:
        for seed in load_seed_sources(root)[:5]:
            inspection = inspector.inspect(seed.domain)
            counts["career_inspected"] += 1
            if inspection.ats_match is None:
                continue
            match = inspection.ats_match
            url = inspection.careers_urls[0] if inspection.careers_urls else seed.careers_url
            _handle_candidate(
                url=url,
                platform=match.platform,
                tenant=match.tenant,
                confidence=max(match.confidence, inspection.confidence),
                discovered_via="career_site",
                company_domain=seed.domain,
                company_name=seed.company,
                has_json_ld=inspection.has_json_ld_jobs,
            )

    state.generated_at = datetime.now(UTC)
    store.save(state)
    console.print(f"Discovery complete: {counts}")

    if publish and (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        published = orchestrator.publish_live_state_release()
        console.print_json(json.dumps(published, indent=2))


@app.command()
def validate() -> None:
    """Validate seed sources and current state."""
    root = _repo_root()
    seeds = load_seed_sources(root)
    console.print(f"[green]Validated {len(seeds)} seed sources[/green]")
    store = _state_store(root)
    if store.exists():
        state = store.load()
        console.print(f"State: {len(state.jobs)} jobs, {len(state.sources)} sources")
    else:
        console.print("[yellow]No local state found — run sync first[/yellow]")


@app.command()
def archive(
    month: Annotated[str | None, typer.Option(help="YYYY-MM")] = None,
) -> None:
    """Create monthly historical dataset archive."""
    root = _repo_root()
    store = _state_store(root)
    if not store.exists():
        console.print("[red]No state to archive[/red]")
        raise typer.Exit(1)
    state = store.load()
    out = root / ".local" / "archives"
    out.mkdir(parents=True, exist_ok=True)
    path = ArchiveExporter(config=load_project_config(root)).export(state, out, month=month)
    console.print(f"Archive written: {path.path}")


@app.command()
def health() -> None:
    """Show adapter and source health statistics."""
    root = _repo_root()
    store = _state_store(root)
    if not store.exists():
        console.print("[yellow]No state — run sync first[/yellow]")
        raise typer.Exit(0)
    state = store.load()
    monitor = HealthMonitor(load_project_config(root))
    regressions = monitor.analyze(state).regressions
    table = Table(title="Adapter Health")
    table.add_column("Adapter")
    table.add_column("Success")
    table.add_column("Failure")
    table.add_column("Rate")
    for name, stats in state.adapter_health.items():
        total = stats.success_count + stats.failure_count
        rate = stats.success_count / total if total else 0
        table.add_row(name, str(stats.success_count), str(stats.failure_count), f"{rate:.1%}")
    console.print(table)
    if regressions:
        console.print(f"[red]{len(regressions)} adapter regression(s) detected[/red]")


@app.command("inspect-source")
def inspect_source(source_id: str) -> None:
    """Inspect a source by ID."""
    root = _repo_root()
    store = _state_store(root)
    if not store.exists():
        raise typer.Exit(1)
    state = store.load()
    source = state.sources.get(source_id)
    if source is None:
        console.print(f"[red]Source not found: {source_id}[/red]")
        raise typer.Exit(1)
    console.print_json(source.model_dump_json(indent=2))


@app.command("inspect-job")
def inspect_job(job_id: str) -> None:
    """Inspect a job by ID."""
    root = _repo_root()
    store = _state_store(root)
    if not store.exists():
        raise typer.Exit(1)
    state = store.load()
    job = state.jobs.get(job_id)
    if job is None:
        console.print(f"[red]Job not found: {job_id}[/red]")
        raise typer.Exit(1)
    console.print_json(job.model_dump_json(indent=2))


@app.command("update-readme")
def update_readme() -> None:
    """Update generated README stats section."""
    root = _repo_root()
    store = _state_store(root)
    state = store.load() if store.exists() else SyncOrchestrator(root=root).bootstrap_state()
    readme = root / "README.md"
    ReadmeGenerator(load_project_config(root)).update_readme(readme, state)
    console.print("[green]README stats updated[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
