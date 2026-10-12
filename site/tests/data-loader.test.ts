import { describe, expect, it } from 'vitest';
import { applyDelta, type FeedDelta } from '../src/lib/delta';
import type { Job } from '../src/lib/types';

function makeJob(id: string): Job {
  return {
    job_id: id,
    source_job_id: id,
    company_id: 'co',
    company_name: 'Acme',
    title: 'Intern',
    job_url: `https://example.com/${id}`,
    apply_url: `https://example.com/${id}/apply`,
    career_level: 'internship',
    career_level_confidence: 0.9,
    disciplines: { primary: 'engineering', secondary: [], confidence: 0.8 },
    locations: [],
    workplace_type: 'remote',
    remote_scope: 'worldwide',
    remote_allowed_countries: [],
    remote_allowed_regions: [],
    first_seen_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-01-01T00:00:00Z',
    academic_term: 'unknown',
    eligibility: {
      explicit_allowed_countries: [],
      explicit_excluded_countries: [],
      citizenship_requirements: [],
      residency_requirements: [],
      language_requirements: [],
      origin_match: 'unknown',
    },
    mobility: {
      visa_sponsorship: { status: 'unknown', confidence: 0 },
      immigration_assistance: { status: 'unknown', confidence: 0 },
      international_candidates: { status: 'unknown', confidence: 0 },
      relocation_assistance: { status: 'unknown', confidence: 0 },
      relocation_stipend: { status: 'unknown', confidence: 0 },
      moving_expenses: { status: 'unknown', confidence: 0 },
      airfare: { status: 'unknown', confidence: 0 },
      travel_reimbursement: { status: 'unknown', confidence: 0 },
      housing_provided: { status: 'unknown', confidence: 0 },
      housing_stipend: { status: 'unknown', confidence: 0 },
      temporary_housing: { status: 'unknown', confidence: 0 },
      fully_funded_relocation: { status: 'unknown', confidence: 0 },
    },
    provenance: {
      adapter: 'greenhouse',
      source_id: 'src',
      source_job_id: id,
      fetched_at: '2026-01-01T00:00:00Z',
      parser_version: '1',
      classification_version: '1',
      content_hash: id,
      source_health_at_fetch: 'healthy',
      first_party_verified: true,
    },
    lifecycle: 'open',
  };
}

describe('applyDelta', () => {
  it('patches when every opening has a full record', () => {
    const existing = [makeJob('a'), makeJob('b')];
    const delta: FeedDelta = {
      generated_at: '2026-01-02T00:00:00Z',
      opened_count: 1,
      closed_count: 1,
      opened: ['c'],
      closed: ['b'],
      opened_jobs: [makeJob('c')],
    };
    const patched = applyDelta(existing, delta);
    expect(patched?.map((job) => job.job_id).sort()).toEqual(['a', 'c']);
  });

  it('refuses partial opened_jobs payloads', () => {
    const existing = [makeJob('a')];
    const delta: FeedDelta = {
      generated_at: '2026-01-02T00:00:00Z',
      opened_count: 2,
      closed_count: 0,
      opened: ['b', 'c'],
      closed: [],
      opened_jobs: [makeJob('b')],
    };
    expect(applyDelta(existing, delta)).toBeNull();
  });

  it('refuses truncated opening sets', () => {
    const existing = [makeJob('a')];
    const delta: FeedDelta = {
      generated_at: '2026-01-02T00:00:00Z',
      opened_count: 900,
      closed_count: 0,
      opened: ['b'],
      closed: [],
      opened_jobs: [makeJob('b')],
      opened_truncated: true,
    };
    expect(applyDelta(existing, delta)).toBeNull();
  });
});
