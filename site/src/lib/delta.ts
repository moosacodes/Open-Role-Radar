import type { Job } from './types';

export interface FeedDelta {
  generated_at: string;
  feed_mode?: string;
  opened_count: number;
  closed_count: number;
  opened: string[];
  closed: string[];
  opened_jobs?: Job[];
  opened_truncated?: boolean;
}

/** Apply a small delta onto an existing job list when possible. */
export function applyDelta(jobs: Job[], delta: FeedDelta): Job[] | null {
  // Truncated openings mean the client would miss roles — force full reload.
  if (delta.opened_truncated) return null;

  const changeCount = (delta.opened?.length || 0) + (delta.closed?.length || 0);
  if (changeCount > 800) return null;

  const openedIds = delta.opened || [];
  const openedJobs = delta.opened_jobs || [];
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
