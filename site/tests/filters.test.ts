import type { CareerLevel, MobilityFlag } from '../src/lib/types';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FILTERS,
  parseFiltersFromUrl,
  serializeFiltersToUrl,
  countActiveFilters,
} from '../src/lib/filters';

describe('filters URL serialization', () => {
  it('returns empty string for default filters', () => {
    expect(serializeFiltersToUrl(DEFAULT_FILTERS)).toBe('');
    expect(DEFAULT_FILTERS.careerLevels).toEqual([]);
    expect(DEFAULT_FILTERS.disciplines).toEqual([]);
  });

  it('round-trips search query and arrays', () => {
    const filters = {
      ...DEFAULT_FILTERS,
      q: 'software intern',
      careerLevels: ['internship', 'co_op'] as CareerLevel[],
      mobility: ['visa', 'relocation'] as MobilityFlag[],
      freshness: '7d' as const,
      page: 2,
      view: 'table' as const,
    };

    const serialized = serializeFiltersToUrl(filters);
    expect(serialized).toContain('q=software+intern');
    expect(serialized).toContain('careerLevels=internship%2Cco_op');

    const parsed = parseFiltersFromUrl(serialized);
    expect(parsed.q).toBe('software intern');
    expect(parsed.careerLevels).toEqual(['internship', 'co_op']);
    expect(parsed.mobility).toEqual(['visa', 'relocation']);
    expect(parsed.freshness).toBe('7d');
    expect(parsed.page).toBe(2);
    expect(parsed.view).toBe('table');
  });

  it('counts active filters', () => {
    const count = countActiveFilters({
      ...DEFAULT_FILTERS,
      freshness: 'all',
      q: 'data',
      careerLevels: ['internship'],
      academicTerms: ['summer'],
      startupsOnly: true,
      originCountry: 'Canada',
    });
    // q + career + academic + startups + origin
    expect(count).toBe(5);
    expect(
      countActiveFilters({
        ...DEFAULT_FILTERS,
        feedMode: 'wide',
        freshness: 'all',
      }),
    ).toBe(1);
  });

  it('serializes saved-only toggle', () => {
    const serialized = serializeFiltersToUrl({
      ...DEFAULT_FILTERS,
      showSavedOnly: true,
    });
    expect(serialized).toContain('showSavedOnly=1');
    expect(parseFiltersFromUrl(serialized).showSavedOnly).toBe(true);
  });
});
