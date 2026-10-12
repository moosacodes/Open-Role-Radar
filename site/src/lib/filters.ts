import type { FilterState, FreshnessFilter, SortMode } from './types';

export type { FilterState };

const DEFAULT_PAGE_SIZE = 40;

export const DEFAULT_FILTERS: FilterState = {
  q: '',
  careerLevels: [],
  disciplines: [],
  regions: [],
  workplaceTypes: [],
  academicTerms: [],
  companyIds: [],
  startupsOnly: false,
  // Prefer "new since visit" once a session baseline exists; else last 24h.
  freshness: '24h',
  mobility: [],
  originCountry: '',
  showSavedOnly: false,
  hideDismissed: true,
  sort: 'newest',
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  view: 'table',
};

const ARRAY_KEYS = new Set([
  'careerLevels',
  'disciplines',
  'regions',
  'workplaceTypes',
  'academicTerms',
  'companyIds',
  'mobility',
]);

const BOOL_KEYS = new Set(['showSavedOnly', 'hideDismissed', 'startupsOnly']);

const NUMBER_KEYS = new Set(['page', 'pageSize']);

function parseList<T extends string>(value: string | null): T[] {
  if (!value) return [];
  return value.split(',').filter(Boolean) as T[];
}

function serializeList(values: string[]): string | undefined {
  if (values.length === 0) return undefined;
  return values.join(',');
}

function arraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const a = [...left].sort();
  const b = [...right].sort();
  return a.every((value, index) => value === b[index]);
}

export function parseFiltersFromUrl(search: string): FilterState {
  const params = new URLSearchParams(search);
  const next: FilterState = {
    ...DEFAULT_FILTERS,
    careerLevels: [...DEFAULT_FILTERS.careerLevels],
    disciplines: [...DEFAULT_FILTERS.disciplines],
    regions: [],
    workplaceTypes: [],
    academicTerms: [],
    companyIds: [],
    mobility: [],
  };

  for (const key of Object.keys(DEFAULT_FILTERS) as (keyof FilterState)[]) {
    const value = params.get(key);
    if (value === null) continue;

    if (ARRAY_KEYS.has(key)) {
      (next[key] as string[]) = parseList(value);
      continue;
    }

    if (BOOL_KEYS.has(key)) {
      (next[key] as boolean) = value === '1' || value === 'true';
      continue;
    }

    if (NUMBER_KEYS.has(key)) {
      const num = Number.parseInt(value, 10);
      if (!Number.isNaN(num) && num > 0) {
        (next[key] as number) = num;
      }
      continue;
    }

    if (key === 'freshness') {
      next.freshness = value as FreshnessFilter;
      continue;
    }

    if (key === 'view') {
      next.view = 'table';
      continue;
    }

    if (key === 'sort') {
      next.sort = value as SortMode;
      continue;
    }

    if (key === 'q' || key === 'originCountry') {
      next[key] = value;
    }
  }

  return next;
}

export function serializeFiltersToUrl(filters: FilterState): string {
  const params = new URLSearchParams();

  for (const key of Object.keys(DEFAULT_FILTERS) as (keyof FilterState)[]) {
    const value = filters[key];
    const defaultValue = DEFAULT_FILTERS[key];

    if (ARRAY_KEYS.has(key)) {
      const current = value as string[];
      const defaults = defaultValue as string[];
      if (!arraysEqual(current, defaults)) {
        params.set(key, serializeList(current) ?? '');
      }
      continue;
    }

    if (BOOL_KEYS.has(key)) {
      if (value !== defaultValue) {
        params.set(key, value ? '1' : '0');
      }
      continue;
    }

    if (NUMBER_KEYS.has(key)) {
      if (value !== defaultValue) {
        params.set(key, String(value));
      }
      continue;
    }

    if (key === 'q' || key === 'originCountry') {
      if (value && value !== defaultValue) {
        params.set(key, value as string);
      }
      continue;
    }

    if (value !== defaultValue) {
      params.set(key, String(value));
    }
  }

  const query = params.toString();
  return query ? `?${query}` : '';
}

export function filtersAreDefault(filters: FilterState): boolean {
  return serializeFiltersToUrl(filters) === '';
}

export function countActiveFilters(filters: FilterState): number {
  let count = 0;
  if (filters.q.trim()) count += 1;
  if (filters.careerLevels.length) count += 1;
  if (filters.disciplines.length) count += 1;
  if (filters.regions.length) count += 1;
  if (filters.workplaceTypes.length) count += 1;
  if (filters.academicTerms.length) count += 1;
  if (filters.companyIds.length) count += 1;
  if (filters.startupsOnly) count += 1;
  if (filters.freshness !== 'all') count += 1;
  if (filters.mobility.length) count += 1;
  if (filters.originCountry) count += 1;
  if (filters.showSavedOnly) count += 1;
  return count;
}

export function toggleArrayValue<T extends string>(values: T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}
