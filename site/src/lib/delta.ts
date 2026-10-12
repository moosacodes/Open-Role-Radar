import type { Job } from './types';

export interface FeedDelta {
  generated_at: string;
  feed_mode?: string;
  opened_count: number;
  closed_count: number;
  opened: string[];
  closed: string[];
  opened_jobs?: Job[];
}

/** Apply a small delta onto an existing job list when possible. */
export function applyDelta(jobs: Job[], delta: FeedDelta): Job[] | null {
  const changeCount = (delta.opened_count || 0) + (delta.closed_count || 0);
  if (changeCount > 800) return null;

  const openedIds = delta.opened || [];
  const openedJobs = delta.opened_jobs || [];
  // Exporter caps opened_jobs at 100 while opened can list thousands.
  // Only patch when every listed opening has a full record.
  if (openedIds.length > openedJobs.length) {
    return null;
  }
  if (openedIds.length > 0) {
    const have = new Set(openedJobs.map((job) => job.job_id));
    for (const jobId of openedIds) {
      if (!have.has(jobId)) return null;
    }
  }

  const closed = new Set(delta.closed || []);
  const byId = new Map<string, Job>();
  for (const job of jobs) {
    if (!closed.has(job.job_id)) {
      byId.set(job.job_id, job);
    }
  }
  for (const job of openedJobs) {
    byId.set(job.job_id, job);
  }
  return [...byId.values()];
}
