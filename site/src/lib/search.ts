import { postedAt } from './dates';
import type { FilterState } from './filters';
import { isStartupCompany, jobMatchesRegions, regionsForJob, type RegionId } from './labels';
import { bucketForJobId } from './sha256';
import type {
  AcademicTerm,
  CareerLevel,
  CompanySummary,
  Job,
  MobilityBenefits,
  MobilityFlag,
  SearchIndexEntry,
  SiteStats,
  SortMode,
  WorkplaceType,
} from './types';
import {
  getMobilityFlags,
  MOBILITY_CLAIM_KEYS,
  slugifyCompany,
} from './types';

const FRESHNESS_MS: Record<string, number> = {
  '2h': 2 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
};

/** Map free-text origin country input to ISO-3166 alpha-2 used in eligibility fields. */
const COUNTRY_ALIASES: Record<string, string> = {
  canada: 'CA',
  ca: 'CA',
  'united states': 'US',
  usa: 'US',
  us: 'US',
  'u.s.': 'US',
  'u.s.a.': 'US',
  america: 'US',
  india: 'IN',
  in: 'IN',
  'united kingdom': 'GB',
  uk: 'GB',
  britain: 'GB',
  england: 'GB',
  gb: 'GB',
  germany: 'DE',
  de: 'DE',
  france: 'FR',
  fr: 'FR',
  australia: 'AU',
  au: 'AU',
  ireland: 'IE',
  ie: 'IE',
  netherlands: 'NL',
  nl: 'NL',
  singapore: 'SG',
  sg: 'SG',
  brazil: 'BR',
  br: 'BR',
  mexico: 'MX',
  mx: 'MX',
  japan: 'JP',
  jp: 'JP',
  china: 'CN',
  cn: 'CN',
  'south korea': 'KR',
  korea: 'KR',
  kr: 'KR',
};

export function normalizeCountryCode(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return '';
  const alias = COUNTRY_ALIASES[trimmed.toLowerCase()];
  if (alias) return alias;
  if (/^[a-z]{2}$/i.test(trimmed)) return trimmed.toUpperCase();
  return trimmed.toUpperCase();
}

export function buildSearchIndex(jobs: Job[], hashBuckets = 32): SearchIndexEntry[] {
  return jobs.map((job) => ({
    job_id: job.job_id,
    company_id: job.company_id,
    company_name: job.company_name,
    company_slug: slugifyCompany(job.company_name),
    title: job.title,
    title_lower: job.title.toLowerCase(),
    career_level: job.career_level,
    discipline: job.disciplines.primary,
    location_text: job.locations
      .map((loc) => [loc.city, loc.region, loc.country, loc.raw].filter(Boolean).join(', '))
      .join(' | ')
      .toLowerCase(),
    workplace_type: job.workplace_type,
    posted_at: postedAt(job),
    first_seen_at: job.first_seen_at,
    mobility_flags: getMobilityFlags(job.mobility),
    bucket: bucketForJobId(job.job_id, hashBuckets),
  }));
}

export function tokenizeQuery(query: string): string[] {
  return query
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2);
}

export function matchesQuery(entry: SearchIndexEntry, tokens: string[]): boolean {
  if (tokens.length === 0) return true;
  const haystack = `${entry.title_lower} ${entry.company_name.toLowerCase()} ${entry.location_text} ${entry.discipline}`;
  return tokens.every((token) => haystack.includes(token));
}

/** Prefer radar first-seen for "new since visit"; posted_at for time windows. */
export function freshnessTimestamp(
  job: Pick<Job, 'source_posted_at' | 'first_seen_at'>,
  freshness: FilterState['freshness'],
): string {
  if (freshness === 'new_since_visit') {
    return job.first_seen_at || postedAt(job);
  }
  return postedAt(job);
}

export function matchesFreshness(
  dateIso: string,
  freshness: FilterState['freshness'],
  lastVisit: string | null,
): boolean {
  if (freshness === 'all') return true;
  const seen = Date.parse(dateIso);
  if (Number.isNaN(seen)) return true;

  if (freshness === 'new_since_visit') {
    // No prior visit yet — show last 24h so the feed still feels fresh.
    if (!lastVisit) {
      return Date.now() - seen <= FRESHNESS_MS['24h'];
    }
    const visit = Date.parse(lastVisit);
    return !Number.isNaN(visit) && seen > visit;
  }

  const windowMs = FRESHNESS_MS[freshness];
  if (!windowMs) return true;
  return Date.now() - seen <= windowMs;
}

export function matchesMobility(job: Job, flags: MobilityFlag[]): boolean {
  if (flags.length === 0) return true;
  return flags.every((flag) => {
    const claim = job.mobility[MOBILITY_CLAIM_KEYS[flag]];
    return claim.status === 'confirmed';
  });
}

/** Round-robin across companies so big boards don't flood the first pages. */
export function diversifyByCompany(jobs: Job[]): Job[] {
  const byCompany = new Map<string, Job[]>();
  for (const job of jobs) {
    const list = byCompany.get(job.company_id);
    if (list) list.push(job);
    else byCompany.set(job.company_id, [job]);
  }

  for (const list of byCompany.values()) {
    list.sort((a, b) => {
      const diff = Date.parse(postedAt(b)) - Date.parse(postedAt(a));
      if (diff !== 0) return diff;
      return a.job_id.localeCompare(b.job_id);
    });
  }

  // Prefer companies with freshest top role when starting the round-robin.
  const queues = [...byCompany.values()].sort((a, b) => {
    const diff = Date.parse(postedAt(b[0])) - Date.parse(postedAt(a[0]));
    if (diff !== 0) return diff;
    return a[0].company_name.localeCompare(b[0].company_name);
  });

  const result: Job[] = [];
  let progressed = true;
  while (progressed) {
    progressed = false;
    for (const queue of queues) {
      const next = queue.shift();
      if (next) {
        result.push(next);
        progressed = true;
      }
    }
  }
  return result;
}

export function sortJobs(jobs: Job[], sort: SortMode): Job[] {
  const copy = [...jobs];
  if (sort === 'company') {
    return copy.sort(
      (a, b) =>
        a.company_name.localeCompare(b.company_name) ||
        a.title.localeCompare(b.title),
    );
  }
  if (sort === 'title') {
    return copy.sort(
      (a, b) =>
        a.title.localeCompare(b.title) ||
        a.company_name.localeCompare(b.company_name),
    );
  }
  if (sort === 'newest') {
    return copy.sort((a, b) => {
      const diff = Date.parse(postedAt(b)) - Date.parse(postedAt(a));
      if (diff !== 0) return diff;
      return a.job_id.localeCompare(b.job_id);
    });
  }
  return diversifyByCompany(copy);
}

export function filterJobs(
  jobs: Job[],
  index: SearchIndexEntry[],
  filters: FilterState,
  options: {
    savedJobIds?: Set<string>;
    dismissedJobIds?: Set<string>;
    lastVisit?: string | null;
  } = {},
): Job[] {
  const indexById = new Map(index.map((entry) => [entry.job_id, entry]));
  const tokens = tokenizeQuery(filters.q);
  const saved = options.savedJobIds ?? new Set<string>();
  const dismissed = options.dismissedJobIds ?? new Set<string>();

  const filtered = jobs.filter((job) => {
    const entry = indexById.get(job.job_id);
    if (!entry) return false;

    if (filters.hideDismissed && dismissed.has(job.job_id)) return false;
    if (filters.showSavedOnly && !saved.has(job.job_id)) return false;
    if (!matchesQuery(entry, tokens)) return false;

    if (
      filters.careerLevels.length > 0 &&
      !filters.careerLevels.includes(job.career_level)
    ) {
      return false;
    }

    if (
      filters.disciplines.length > 0 &&
      !filters.disciplines.includes(job.disciplines.primary)
    ) {
      return false;
    }

    if (filters.regions.length > 0 && !jobMatchesRegions(job, filters.regions)) {
      return false;
    }

    if (
      filters.workplaceTypes.length > 0 &&
      !filters.workplaceTypes.includes(job.workplace_type)
    ) {
      return false;
    }

    if (
      filters.academicTerms.length > 0 &&
      !filters.academicTerms.includes(job.academic_term)
    ) {
      return false;
    }

    if (
      (filters.companyIds?.length ?? 0) > 0 &&
      !filters.companyIds.includes(job.company_id)
    ) {
      return false;
    }

    if (filters.startupsOnly && !isStartupCompany(job.company_name)) {
      return false;
    }

    const freshnessAt = freshnessTimestamp(
      { source_posted_at: job.source_posted_at, first_seen_at: entry.first_seen_at },
      filters.freshness,
    );
    if (!matchesFreshness(freshnessAt, filters.freshness, options.lastVisit ?? null)) {
      return false;
    }

    if (!matchesMobility(job, filters.mobility)) return false;

    if (filters.originCountry) {
      const country = normalizeCountryCode(filters.originCountry);
      const allowed = job.eligibility.explicit_allowed_countries.map(normalizeCountryCode);
      const excluded = job.eligibility.explicit_excluded_countries.map(normalizeCountryCode);
      if (excluded.includes(country)) return false;
      if (allowed.length > 0 && !allowed.includes(country)) return false;
    }

    return true;
  });

  return sortJobs(filtered, filters.sort);
}

export function paginateJobs<T>(items: T[], page: number, pageSize: number): T[] {
  const safePage = Math.max(1, page);
  const start = (safePage - 1) * pageSize;
  return items.slice(start, start + pageSize);
}

export function totalPages(count: number, pageSize: number): number {
  return Math.max(1, Math.ceil(count / pageSize));
}

export function computeStats(
  jobs: Job[],
  lastVisit: string | null,
  generatedAt: string,
): SiteStats {
  const companies = new Set(jobs.map((job) => job.company_id));
  const withVisa = jobs.filter(
    (job) => job.mobility.visa_sponsorship.status === 'confirmed',
  ).length;
  const withRelocation = jobs.filter(
    (job) => job.mobility.relocation_assistance.status === 'confirmed',
  ).length;
  const remoteCount = jobs.filter((job) => job.workplace_type === 'remote').length;
  const newSinceVisit = jobs.filter((job) =>
    matchesFreshness(
      freshnessTimestamp(job, 'new_since_visit'),
      'new_since_visit',
      lastVisit,
    ),
  ).length;

  return {
    totalJobs: jobs.length,
    totalCompanies: companies.size,
    withVisa,
    withRelocation,
    remoteCount,
    newSinceVisit,
    generatedAt,
  };
}

export function aggregateCompanies(jobs: Job[]): CompanySummary[] {
  const map = new Map<string, CompanySummary>();
  for (const job of jobs) {
    const existing = map.get(job.company_id);
    if (existing) {
      existing.job_count += 1;
    } else {
      map.set(job.company_id, {
        company_id: job.company_id,
        name: job.company_name,
        slug: slugifyCompany(job.company_name),
        job_count: 1,
      });
    }
  }
  return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export interface FacetCounts {
  disciplines: Partial<Record<string, number>>;
  careerLevels: Partial<Record<CareerLevel, number>>;
  regions: Partial<Record<RegionId, number>>;
  workplaceTypes: Partial<Record<WorkplaceType, number>>;
  academicTerms: Partial<Record<AcademicTerm, number>>;
  mobility: Partial<Record<MobilityFlag, number>>;
  startups: number;
  companies: CompanySummary[];
}

export function collectFacetValues(jobs: Job[]): FacetCounts {
  const disciplines: Partial<Record<string, number>> = {};
  const careerLevels: Partial<Record<CareerLevel, number>> = {};
  const regions: Partial<Record<RegionId, number>> = {};
  const workplaceTypes: Partial<Record<WorkplaceType, number>> = {};
  const academicTerms: Partial<Record<AcademicTerm, number>> = {};
  const mobility: Partial<Record<MobilityFlag, number>> = {};
  let startups = 0;

  for (const job of jobs) {
    if (job.disciplines.primary && job.disciplines.primary !== 'other') {
      const key = job.disciplines.primary;
      disciplines[key] = (disciplines[key] ?? 0) + 1;
    }
    careerLevels[job.career_level] = (careerLevels[job.career_level] ?? 0) + 1;
    workplaceTypes[job.workplace_type] = (workplaceTypes[job.workplace_type] ?? 0) + 1;
    if (job.academic_term && job.academic_term !== 'unknown') {
      academicTerms[job.academic_term] = (academicTerms[job.academic_term] ?? 0) + 1;
    }
    for (const region of regionsForJob(job)) {
      regions[region] = (regions[region] ?? 0) + 1;
    }
    for (const flag of getMobilityFlags(job.mobility)) {
      mobility[flag] = (mobility[flag] ?? 0) + 1;
    }
    if (isStartupCompany(job.company_name)) startups += 1;
  }

  return {
    disciplines,
    careerLevels,
    regions,
    workplaceTypes,
    academicTerms,
    mobility,
    startups,
    companies: aggregateCompanies(jobs),
  };
}

export function getMobilityClaims(
  mobility: MobilityBenefits,
  flag: MobilityFlag,
) {
  return mobility[MOBILITY_CLAIM_KEYS[flag]];
}
