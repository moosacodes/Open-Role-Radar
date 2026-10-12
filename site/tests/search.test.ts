import { describe, expect, it } from 'vitest';
import { DEFAULT_FILTERS } from '../src/lib/filters';
import {
  buildSearchIndex,
  filterJobs,
  matchesQuery,
  normalizeCountryCode,
  paginateJobs,
  tokenizeQuery,
  totalPages,
} from '../src/lib/search';
import type { Job } from '../src/lib/types';

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: 'job-1',
    source_job_id: 'src-1',
    company_id: 'co-1',
    company_name: 'Acme Corp',
    title: 'Software Engineering Intern',
    job_url: 'https://example.com/job',
    apply_url: 'https://example.com/apply',
    career_level: 'internship',
    career_level_confidence: 0.9,
    disciplines: { primary: 'engineering', secondary: [], confidence: 0.8 },
    locations: [{ city: 'Toronto', country: 'Canada', country_code: 'CA' }],
    workplace_type: 'hybrid',
    remote_scope: 'country_limited',
    remote_allowed_countries: [],
    remote_allowed_regions: [],
    first_seen_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    academic_term: 'summer',
    eligibility: {
      explicit_allowed_countries: ['Canada'],
      explicit_excluded_countries: [],
      citizenship_requirements: [],
      residency_requirements: [],
      language_requirements: [],
      origin_match: 'explicit_match',
    },
    mobility: {
      visa_sponsorship: { status: 'confirmed', confidence: 0.9 },
      immigration_assistance: { status: 'unknown', confidence: 0 },
      international_candidates: { status: 'unknown', confidence: 0 },
      relocation_assistance: { status: 'confirmed', confidence: 0.8 },
      relocation_stipend: { status: 'unknown', confidence: 0 },
      moving_expenses: { status: 'unknown', confidence: 0 },
      airfare: { status: 'not_available', confidence: 0 },
      travel_reimbursement: { status: 'unknown', confidence: 0 },
      housing_provided: { status: 'unknown', confidence: 0 },
      housing_stipend: { status: 'unknown', confidence: 0 },
      temporary_housing: { status: 'unknown', confidence: 0 },
      fully_funded_relocation: { status: 'unknown', confidence: 0 },
    },
    provenance: {
      adapter: 'greenhouse',
      source_id: 'src',
      source_job_id: 'src-1',
      fetched_at: new Date().toISOString(),
      parser_version: '1.0.0',
      classification_version: '1.0.0',
      content_hash: 'abc',
      source_health_at_fetch: 'healthy',
      first_party_verified: true,
    },
    lifecycle: 'open',
    ...overrides,
  };
}

describe('search utilities', () => {
  it('tokenizes and matches queries', () => {
    const tokens = tokenizeQuery('Software Intern');
    expect(tokens).toEqual(['software', 'intern']);

    const index = buildSearchIndex([makeJob()])[0];
    expect(matchesQuery(index, tokens)).toBe(true);
    expect(matchesQuery(index, ['designer'])).toBe(false);
  });

  it('filters by mobility and origin country', () => {
    const jobs = [makeJob(), makeJob({ job_id: 'job-2', title: 'Designer Intern' })];
    const index = buildSearchIndex(jobs);
    const filtered = filterJobs(
      jobs,
      index,
      {
        ...DEFAULT_FILTERS,
        freshness: 'all',
        mobility: ['visa'],
        originCountry: 'Canada',
        sort: 'diverse',
        view: 'table',
      },
      {},
    );
    expect(filtered).toHaveLength(2);
  });

  it('paginates results', () => {
    const items = Array.from({ length: 30 }, (_, i) => `item-${i}`);
    expect(paginateJobs(items, 2, 10)).toHaveLength(10);
    expect(paginateJobs(items, 2, 10)[0]).toBe('item-10');
    expect(totalPages(30, 10)).toBe(3);
  });

  it('sorts by ATS posted date when present', () => {
    const older = makeJob({
      job_id: 'job-old',
      source_posted_at: '2026-01-01T00:00:00Z',
      first_seen_at: '2026-09-08T00:00:00Z',
    });
    const newer = makeJob({
      job_id: 'job-new',
      title: 'New Grad Engineer',
      career_level: 'new_grad',
      source_posted_at: '2026-08-01T00:00:00Z',
      first_seen_at: '2026-09-08T00:00:00Z',
    });
    const jobs = [older, newer];
    const index = buildSearchIndex(jobs);
    const filtered = filterJobs(
      jobs,
      index,
      {
        ...DEFAULT_FILTERS,
        freshness: 'all',
        sort: 'newest',
        view: 'table',
      },
      {},
    );
    expect(filtered.map((job) => job.job_id)).toEqual(['job-new', 'job-old']);
  });

  it('interleaves companies in diverse sort', () => {
    const jobs = [
      makeJob({ job_id: 'a1', company_id: 'co-a', company_name: 'Alpha', source_posted_at: '2026-09-01T00:00:00Z' }),
      makeJob({ job_id: 'a2', company_id: 'co-a', company_name: 'Alpha', source_posted_at: '2026-08-01T00:00:00Z' }),
      makeJob({ job_id: 'b1', company_id: 'co-b', company_name: 'Beta', source_posted_at: '2026-09-02T00:00:00Z' }),
    ];
    const index = buildSearchIndex(jobs);
    const filtered = filterJobs(
      jobs,
      index,
      {
        ...DEFAULT_FILTERS,
        freshness: 'all',
        sort: 'diverse',
        view: 'table',
      },
      {},
    );
    expect(filtered.map((job) => job.job_id)).toEqual(['b1', 'a1', 'a2']);
  });

  it('filters by academic season and startups', () => {
    const summerStartup = makeJob({
      job_id: 'summer-startup',
      company_name: 'PostHog',
      company_id: 'posthog',
      academic_term: 'summer',
    });
    const fallBigCo = makeJob({
      job_id: 'fall-big',
      company_name: 'NVIDIA',
      company_id: 'nvidia',
      academic_term: 'fall',
      title: 'Fall Intern',
    });
    const jobs = [summerStartup, fallBigCo];
    const index = buildSearchIndex(jobs);

    const bySeason = filterJobs(jobs, index, {
      ...DEFAULT_FILTERS,
      freshness: 'all',
      academicTerms: ['summer'],
      sort: 'newest',
      view: 'table',
    });
    expect(bySeason.map((job) => job.job_id)).toEqual(['summer-startup']);

    const startups = filterJobs(jobs, index, {
      ...DEFAULT_FILTERS,
      freshness: 'all',
      startupsOnly: true,
      sort: 'newest',
      view: 'table',
    });
    expect(startups.map((job) => job.job_id)).toEqual(['summer-startup']);
  });

  it('normalizes origin country aliases to ISO codes', () => {
    expect(normalizeCountryCode('Canada')).toBe('CA');
    expect(normalizeCountryCode('uk')).toBe('GB');
    expect(normalizeCountryCode('US')).toBe('US');
  });
});
