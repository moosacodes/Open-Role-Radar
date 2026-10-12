import { applyDelta, type FeedDelta } from './delta';
import { readCachedFeed, writeCachedFeed, type FeedMode } from './job-cache';
import type { DataManifest, Job, ShardInfo } from './types';
import { bucketForJobId } from './sha256';

declare const __SITE_BASE__: string;
declare const __GITHUB_REPO__: string;

export type { FeedMode };
export type { FeedDelta };
export { applyDelta };

function feedBases(mode: FeedMode): readonly string[] {
  if (mode === 'wide') {
    return [
      `https://raw.githubusercontent.com/${__GITHUB_REPO__}/live-feed/wide/`,
      `https://cdn.jsdelivr.net/gh/${__GITHUB_REPO__}@live-feed/wide/`,
      `${__SITE_BASE__}data-wide/`,
    ] as const;
  }
  return [
    `https://raw.githubusercontent.com/${__GITHUB_REPO__}/live-feed/`,
    `https://cdn.jsdelivr.net/gh/${__GITHUB_REPO__}@live-feed/`,
    `${__SITE_BASE__}data/`,
  ] as const;
}

type FeedBase = string;

const shardCache = new Map<string, Job[]>();
const inflight = new Map<string, Promise<Job[]>>();

let activeMode: FeedMode = 'strict';
let manifestPromise: Promise<DataManifest> | null = null;
let manifestVersion = '';
let activeDataBase: string = feedBases('strict')[2];

export function getDataBaseUrl(): string {
  return activeDataBase;
}

export function getFeedMode(): FeedMode {
  return activeMode;
}

export function setFeedMode(mode: FeedMode): void {
  if (activeMode === mode) return;
  activeMode = mode;
  resetDataLoader();
}

async function fetchManifestFrom(base: string, bust: number): Promise<DataManifest | null> {
  try {
    const response = await fetch(`${base}manifest.json?t=${bust}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    const manifest = (await response.json()) as DataManifest;
    if (!manifest || !Array.isArray(manifest.shards)) return null;
    return manifest;
  } catch {
    return null;
  }
}

function manifestTime(manifest: DataManifest): number {
  const stamp = Date.parse(manifest.generated_at);
  return Number.isNaN(stamp) ? 0 : stamp;
}

async function pickNewestBase(
  bases: readonly string[],
  bust: number,
): Promise<{ base: FeedBase; manifest: DataManifest }> {
  const results = await Promise.all(
    bases.map(async (base) => {
      const manifest = await fetchManifestFrom(base, bust);
      return manifest ? { base, manifest } : null;
    }),
  );
  const available = results.filter(
    (item): item is { base: FeedBase; manifest: DataManifest } => item !== null,
  );
  if (available.length === 0) {
    throw new Error('Failed to load job feed from live-feed, jsDelivr, or Pages');
  }
  available.sort((a, b) => manifestTime(b.manifest) - manifestTime(a.manifest));
  return available[0];
}

export async function loadManifest(mode: FeedMode = activeMode): Promise<DataManifest> {
  if (mode !== activeMode) {
    setFeedMode(mode);
  }
  if (!manifestPromise) {
    const bust = Date.now();
    const bases = feedBases(activeMode);
    manifestPromise = pickNewestBase(bases, bust)
      .then((winner) => {
        activeDataBase = winner.base;
        manifestVersion = winner.manifest.generated_at || String(bust);
        return winner.manifest;
      })
      .catch((error) => {
        manifestPromise = null;
        throw error;
      });
  }
  return manifestPromise;
}

export function resetDataLoader(): void {
  manifestPromise = null;
  manifestVersion = '';
  activeDataBase = feedBases(activeMode)[2];
  shardCache.clear();
  inflight.clear();
}

/** Fetch the newest manifest without clearing the in-memory shard cache. */
export async function peekNewestManifest(
  mode: FeedMode = activeMode,
): Promise<DataManifest> {
  const bust = Date.now();
  const winner = await pickNewestBase(feedBases(mode), bust);
  return winner.manifest;
}

export async function loadDelta(base: string = activeDataBase): Promise<FeedDelta | null> {
  try {
    const response = await fetch(`${base}delta.json?t=${Date.now()}`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    return (await response.json()) as FeedDelta;
  } catch {
    return null;
  }
}

export async function loadShard(filename: string): Promise<Job[]> {
  const cacheKey = `${activeDataBase}::${filename}::${manifestVersion}`;
  const cached = shardCache.get(cacheKey);
  if (cached) return cached;

  const pending = inflight.get(cacheKey);
  if (pending) return pending;

  const version = encodeURIComponent(manifestVersion || String(Date.now()));
  const bust = Date.now();
  const promise = fetch(`${activeDataBase}${filename}?v=${version}&t=${bust}`, {
    cache: 'default',
    headers: { Accept: 'application/json' },
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Failed to load shard ${filename}: ${response.status}`);
      }
      const jobs = (await response.json()) as Job[];
      shardCache.set(cacheKey, jobs);
      inflight.delete(cacheKey);
      return jobs;
    })
    .catch((error) => {
      inflight.delete(cacheKey);
      throw error;
    });

  inflight.set(cacheKey, promise);
  return promise;
}

export async function loadAllJobs(manifest?: DataManifest): Promise<Job[]> {
  const resolvedManifest = manifest ?? (await loadManifest());
  if (resolvedManifest.shards.length === 0) return [];

  const shardResults = await Promise.all(
    resolvedManifest.shards.map((shard) => loadShard(shard.filename)),
  );
  const jobs = shardResults.flat();
  void writeCachedFeed(activeMode, resolvedManifest, jobs);
  return jobs;
}

export async function loadJobsForBuckets(
  buckets: Set<number>,
  manifest?: DataManifest,
): Promise<Job[]> {
  const resolvedManifest = manifest ?? (await loadManifest());
  const shards = resolvedManifest.shards.filter((shard) => buckets.has(shard.bucket));
  if (shards.length === 0) return [];

  const shardResults = await Promise.all(shards.map((shard) => loadShard(shard.filename)));
  return shardResults.flat();
}

export function shardFilenamesForBuckets(
  shards: ShardInfo[],
  buckets: Set<number>,
): string[] {
  return shards.filter((shard) => buckets.has(shard.bucket)).map((shard) => shard.filename);
}

export function bucketsFromJobIds(
  jobIds: Iterable<string>,
  hashBuckets: number,
): Set<number> {
  const buckets = new Set<number>();
  for (const jobId of jobIds) {
    buckets.add(bucketForJobId(jobId, hashBuckets));
  }
  return buckets;
}

export async function hydrateFromCache(mode: FeedMode): Promise<{
  jobs: Job[];
  generatedAt: string;
} | null> {
  const cached = await readCachedFeed(mode);
  if (!cached || cached.jobs.length === 0) return null;
  return { jobs: cached.jobs, generatedAt: cached.generatedAt };
}

export { bucketForJobId, readCachedFeed, writeCachedFeed };
