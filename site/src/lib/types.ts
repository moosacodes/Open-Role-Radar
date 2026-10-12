export type CareerLevel =
  | 'internship'
  | 'co_op'
  | 'new_grad'
  | 'entry_level'
  | 'apprenticeship'
  | 'graduate_program'
  | 'rotational_program'
  | 'research_internship'
  | 'fellowship'
  | 'student_program'
  | 'unknown';

export type WorkplaceType = 'onsite' | 'hybrid' | 'remote' | 'unknown';

export type RemoteScope =
  | 'worldwide'
  | 'country_limited'
  | 'region_limited'
  | 'timezone_limited'
  | 'location_limited'
  | 'unknown';

export type AcademicTerm =
  | 'winter'
  | 'spring'
  | 'summer'
  | 'fall'
  | 'rolling'
  | 'off_cycle'
  | 'unknown';

export type EvidenceStatus = 'confirmed' | 'not_available' | 'unknown' | 'not_applicable';

export type EligibilityMatch =
  | 'explicit_match'
  | 'potential_match'
  | 'unknown'
  | 'explicitly_ineligible';

export type JobLifecycle = 'open' | 'suspected_closed' | 'closed' | 'reopened' | 'quarantined';

export interface EvidenceClaim {
  status: EvidenceStatus;
  confidence: number;
  extraction_rule?: string | null;
  evidence_excerpt?: string | null;
}

export interface MobilityBenefits {
  visa_sponsorship: EvidenceClaim;
  immigration_assistance: EvidenceClaim;
  international_candidates: EvidenceClaim;
  relocation_assistance: EvidenceClaim;
  relocation_stipend: EvidenceClaim;
  moving_expenses: EvidenceClaim;
  airfare: EvidenceClaim;
  travel_reimbursement: EvidenceClaim;
  housing_provided: EvidenceClaim;
  housing_stipend: EvidenceClaim;
  temporary_housing: EvidenceClaim;
  fully_funded_relocation: EvidenceClaim;
}

export interface Location {
  raw?: string | null;
  city?: string | null;
  region?: string | null;
  country?: string | null;
  country_code?: string | null;
  continent?: string | null;
}

export interface Eligibility {
  work_authorization?: string | null;
  explicit_allowed_countries: string[];
  explicit_excluded_countries: string[];
  citizenship_requirements: string[];
  residency_requirements: string[];
  security_clearance?: string | null;
  export_control_restrictions?: boolean | null;
  language_requirements: string[];
  origin_match: EligibilityMatch;
}

export interface DisciplineClassification {
  primary: string;
  secondary: string[];
  confidence: number;
}

export interface Provenance {
  adapter: string;
  source_id: string;
  source_job_id: string;
  fetched_at: string;
  source_posted_at?: string | null;
  first_seen_at?: string | null;
  parser_version: string;
  classification_version: string;
  content_hash: string;
  source_health_at_fetch: string;
  first_party_verified: boolean;
}

export interface Compensation {
  min_amount?: number | null;
  max_amount?: number | null;
  currency?: string | null;
  period?: string | null;
}

export interface Job {
  job_id: string;
  source_job_id: string;
  requisition_id?: string | null;
  company_id: string;
  company_name: string;
  title: string;
  job_url: string;
  apply_url: string;
  careers_url?: string | null;
  source_url?: string | null;
  summary?: string | null;
  career_level: CareerLevel;
  career_level_confidence: number;
  disciplines: DisciplineClassification;
  skills?: string[];
  locations: Location[];
  workplace_type: WorkplaceType;
  remote_scope: RemoteScope;
  remote_allowed_countries: string[];
  remote_allowed_regions: string[];
  source_posted_at?: string | null;
  first_seen_at: string;
  last_seen_at: string;
  last_changed_at?: string | null;
  closed_at?: string | null;
  academic_term: AcademicTerm;
  compensation?: Compensation | null;
  eligibility: Eligibility;
  mobility: MobilityBenefits;
  provenance: Provenance;
  lifecycle: JobLifecycle;
}

export interface ShardInfo {
  bucket: number;
  filename: string;
  job_count: number;
  byte_size: number;
}

export interface DataManifest {
  version: number;
  generated_at: string;
  total_jobs: number;
  hash_buckets: number;
  shards: ShardInfo[];
  feed_mode?: 'strict' | 'wide';
}

export type FeedMode = 'strict' | 'wide';

export interface CompanySummary {
  company_id: string;
  name: string;
  slug: string;
  job_count: number;
}

export interface SearchIndexEntry {
  job_id: string;
  company_id: string;
  company_name: string;
  company_slug: string;
  title: string;
  title_lower: string;
  career_level: CareerLevel;
  discipline: string;
  location_text: string;
  workplace_type: WorkplaceType;
  posted_at: string;
  first_seen_at: string;
  mobility_flags: MobilityFlag[];
  bucket: number;
}

export type MobilityFlag =
  | 'visa'
  | 'relocation'
  | 'housing'
  | 'airfare';

export type FreshnessFilter = 'all' | '2h' | '24h' | '7d' | '30d' | 'new_since_visit';

export type SortMode = 'diverse' | 'newest' | 'company' | 'title';

export type ViewMode = 'cards' | 'table';

export interface FilterState {
  q: string;
  careerLevels: CareerLevel[];
  disciplines: string[];
  regions: string[];
  workplaceTypes: WorkplaceType[];
  academicTerms: AcademicTerm[];
  companyIds: string[];
  startupsOnly: boolean;
  freshness: FreshnessFilter;
  feedMode: FeedMode;
  mobility: MobilityFlag[];
  originCountry: string;
  showSavedOnly: boolean;
  hideDismissed: boolean;
  sort: SortMode;
  page: number;
  pageSize: number;
  view: ViewMode;
}

export interface SiteStats {
  totalJobs: number;
  totalCompanies: number;
  withVisa: number;
  withRelocation: number;
  remoteCount: number;
  newSinceVisit: number;
  generatedAt: string;
}

export const MOBILITY_CLAIM_KEYS: Record<
  MobilityFlag,
  keyof MobilityBenefits
> = {
  visa: 'visa_sponsorship',
  relocation: 'relocation_assistance',
  housing: 'housing_provided',
  airfare: 'airfare',
};

export const MOBILITY_LABELS: Record<MobilityFlag, string> = {
  visa: 'Visa sponsorship',
  relocation: 'Relocation assistance',
  housing: 'Housing provided',
  airfare: 'Airfare / travel',
};

export const CAREER_LEVEL_LABELS: Record<CareerLevel, string> = {
  internship: 'Internship',
  co_op: 'Co-op',
  new_grad: 'New grad',
  entry_level: 'Entry level',
  apprenticeship: 'Apprenticeship',
  graduate_program: 'Graduate program',
  rotational_program: 'Rotational program',
  research_internship: 'Research internship',
  fellowship: 'Fellowship',
  student_program: 'Student program',
  unknown: 'Unknown',
};

export const WORKPLACE_LABELS: Record<WorkplaceType, string> = {
  onsite: 'On-site',
  hybrid: 'Hybrid',
  remote: 'Remote',
  unknown: 'Unknown',
};

export const REMOTE_SCOPE_LABELS: Record<RemoteScope, string> = {
  worldwide: 'Worldwide',
  country_limited: 'Country limited',
  region_limited: 'Region limited',
  timezone_limited: 'Timezone limited',
  location_limited: 'Location limited',
  unknown: 'Unknown',
};

export const ACADEMIC_TERM_LABELS: Record<AcademicTerm, string> = {
  winter: 'Winter',
  spring: 'Spring',
  summer: 'Summer',
  fall: 'Fall',
  rolling: 'Rolling',
  off_cycle: 'Off-cycle',
  unknown: 'Unknown',
};

export const ELIGIBILITY_MATCH_LABELS: Record<EligibilityMatch, string> = {
  explicit_match: 'Explicit match',
  potential_match: 'Potential match',
  unknown: 'Unknown',
  explicitly_ineligible: 'Ineligible',
};

export function slugifyCompany(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function formatLocation(locations: Location[]): string {
  if (locations.length === 0) return 'Location not specified';
  const first = locations[0];
  const parts = [first.city, first.region, first.country].filter(Boolean);
  if (parts.length > 0) {
    const suffix = locations.length > 1 ? ` +${locations.length - 1}` : '';
    return parts.join(', ') + suffix;
  }
  return first.raw ?? 'Location not specified';
}

export function getMobilityFlags(mobility: MobilityBenefits): MobilityFlag[] {
  const flags: MobilityFlag[] = [];
  for (const [flag, key] of Object.entries(MOBILITY_CLAIM_KEYS) as [
    MobilityFlag,
    keyof MobilityBenefits,
  ][]) {
    if (mobility[key].status === 'confirmed') {
      flags.push(flag);
    }
  }
  return flags;
}

export function isFirstPartyVerified(job: Job): boolean {
  return job.provenance.first_party_verified;
}
