"""Build sharded public site data with hash-bucket partitioning."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from openroleradar.config import ProjectConfig, load_project_config
from openroleradar.export.public_filter import is_public_job
from openroleradar.models.job import Job
from openroleradar.models.state import LiveState


@dataclass(frozen=True)
class ShardInfo:
    """Metadata for a single data shard."""

    bucket: int
    filename: str
    job_count: int
    byte_size: int


@dataclass(frozen=True)
class SiteDataManifest:
    """Manifest describing sharded site data."""

    version: int
    generated_at: str
    total_jobs: int
    hash_buckets: int
    shards: list[ShardInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "total_jobs": self.total_jobs,
            "hash_buckets": self.hash_buckets,
            "shards": [
                {
                    "bucket": shard.bucket,
                    "filename": shard.filename,
                    "job_count": shard.job_count,
                    "byte_size": shard.byte_size,
                }
                for shard in self.shards
            ],
        }


class SiteDataBuilder:
    """Shard job records for static site consumption."""

    def __init__(self, config: ProjectConfig | None = None) -> None:
        self.config = config or load_project_config()
        sharding = self.config.sharding
        self.max_shard_bytes = int(sharding.get("max_shard_bytes", 2_097_152))
        self.max_shard_jobs = int(sharding.get("max_shard_jobs", 2000))
        self.hash_buckets = int(sharding.get("hash_buckets", 32))

    @staticmethod
    def bucket_for_job(job_id: str, hash_buckets: int) -> int:
        digest = hashlib.sha256(job_id.encode()).hexdigest()
        return int(digest[:8], 16) % hash_buckets

    def _serialize_jobs(self, jobs: list[Job]) -> bytes:
        payload = [job.to_public_dict() for job in jobs]
        return orjson.dumps(payload)

    def build(self, state: LiveState) -> dict[int, list[Job]]:
        """Partition open jobs into hash buckets."""
        buckets: dict[int, list[Job]] = {index: [] for index in range(self.hash_buckets)}
        for job in state.jobs.values():
            if not is_public_job(job):
                continue
            bucket = self.bucket_for_job(job.job_id, self.hash_buckets)
            buckets[bucket].append(job)
        for bucket_jobs in buckets.values():
            bucket_jobs.sort(key=lambda job: job.last_seen_at, reverse=True)
        return buckets

    def write(self, state: LiveState, output_dir: Path) -> SiteDataManifest:
        """Write sharded JSON files and return manifest metadata."""
        output_dir.mkdir(parents=True, exist_ok=True)
        buckets = self.build(state)
        shards: list[ShardInfo] = []
        total_jobs = 0

        for bucket, jobs in buckets.items():
            if not jobs:
                continue
            chunks: list[list[Job]] = []
            current: list[Job] = []
            for job in jobs:
                trial = current + [job]
                if len(trial) > self.max_shard_jobs:
                    if current:
                        chunks.append(current)
                    current = [job]
                    continue
                if len(self._serialize_jobs(trial)) > self.max_shard_bytes and current:
                    chunks.append(current)
                    current = [job]
                else:
                    current = trial
            if current:
                chunks.append(current)

            for index, chunk in enumerate(chunks):
                suffix = f"-{index}" if len(chunks) > 1 else ""
                filename = f"jobs-bucket-{bucket:02d}{suffix}.json"
                data = self._serialize_jobs(chunk)
                (output_dir / filename).write_bytes(data)
                shards.append(
                    ShardInfo(
                        bucket=bucket,
                        filename=filename,
                        job_count=len(chunk),
                        byte_size=len(data),
                    )
                )
                total_jobs += len(chunk)

        manifest = SiteDataManifest(
            version=self.config.versions.static_api,
            generated_at=state.generated_at.isoformat(),
            total_jobs=total_jobs,
            hash_buckets=self.hash_buckets,
            shards=shards,
        )
        (output_dir / "manifest.json").write_bytes(
            orjson.dumps(manifest.to_dict(), option=orjson.OPT_INDENT_2)
        )

        current_ids = sorted(
            job.job_id for bucket_jobs in buckets.values() for job in bucket_jobs
        )
        previous_ids: set[str] = set()
        ids_path = output_dir / "job-ids.json"
        if ids_path.exists():
            try:
                previous_ids = {
                    str(item) for item in orjson.loads(ids_path.read_bytes()) if item
                }
            except orjson.JSONDecodeError:
                previous_ids = set()
        current_set = set(current_ids)
        opened = sorted(current_set - previous_ids)
        closed = sorted(previous_ids - current_set)
        (output_dir / "delta.json").write_bytes(
            orjson.dumps(
                {
                    "generated_at": state.generated_at.isoformat(),
                    "opened_count": len(opened),
                    "closed_count": len(closed),
                    "opened": opened[:5000],
                    "closed": closed[:5000],
                },
                option=orjson.OPT_INDENT_2,
            )
        )
        ids_path.write_bytes(orjson.dumps(current_ids))
        return manifest
