import type { ReactNode } from 'react';
import type { FilterState } from '../lib/filters';
import { toggleArrayValue } from '../lib/filters';
import {
  formatDiscipline,
  REGION_LABELS,
  REGION_OPTIONS,
  SEASON_TERMS,
  type RegionId,
} from '../lib/labels';
import type { FacetCounts } from '../lib/search';
import type { AcademicTerm, CareerLevel, MobilityFlag, WorkplaceType } from '../lib/types';
import {
  ACADEMIC_TERM_LABELS,
  CAREER_LEVEL_LABELS,
  MOBILITY_LABELS,
  WORKPLACE_LABELS,
} from '../lib/types';

interface FilterPanelProps {
  filters: FilterState;
  facets: FacetCounts;
  onChange: (next: FilterState) => void;
  onReset: () => void;
  activeCount: number;
}

const PRIMARY_CAREER_LEVELS: CareerLevel[] = [
  'internship',
  'co_op',
  'new_grad',
  'entry_level',
];

const WORKPLACE_TYPES = Object.keys(WORKPLACE_LABELS) as WorkplaceType[];
const MOBILITY_FLAGS = Object.keys(MOBILITY_LABELS) as MobilityFlag[];
const ALL_CAREER_LEVELS = Object.keys(CAREER_LEVEL_LABELS) as CareerLevel[];

function FilterSection({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="filter-section" open={defaultOpen}>
      <summary className="filter-section__summary">
        <span>{title}</span>
        <svg className="filter-section__chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </summary>
      <div className="filter-section__body">{children}</div>
    </details>
  );
}

function CheckboxRow({
  id,
  label,
  checked,
  count,
  onChange,
}: {
  id: string;
  label: string;
  checked: boolean;
  count?: number;
  onChange: () => void;
}) {
  return (
    <label htmlFor={id} className="filter-checkbox">
      <span className="filter-checkbox__main">
        <input id={id} type="checkbox" checked={checked} onChange={onChange} />
        <span>{label}</span>
      </span>
      {typeof count === 'number' ? (
        <span className="filter-checkbox__count">{count.toLocaleString()}</span>
      ) : null}
    </label>
  );
}

function QuickChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={active ? 'filter-chip filter-chip--active' : 'filter-chip'}
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function visibleOptions<T extends string>(
  options: readonly T[],
  counts: Partial<Record<T, number>> | undefined,
  selected: readonly T[],
  skip: ReadonlySet<T> = new Set(),
): T[] {
  return options.filter((option) => {
    if (skip.has(option)) return false;
    if (selected.includes(option)) return true;
    return (counts?.[option] ?? 0) > 0;
  });
}

export default function FilterPanel({
  filters,
  facets,
  onChange,
  onReset,
  activeCount,
}: FilterPanelProps) {
  const update = (partial: Partial<FilterState>) => {
    onChange({ ...filters, ...partial, page: 1 });
  };

  const primaryRoles = visibleOptions(PRIMARY_CAREER_LEVELS, facets.careerLevels, filters.careerLevels);
  const moreRoles = visibleOptions(
    ALL_CAREER_LEVELS,
    facets.careerLevels,
    filters.careerLevels,
    new Set<CareerLevel>([...PRIMARY_CAREER_LEVELS, 'unknown']),
  );
  const seasons = visibleOptions(
    [...SEASON_TERMS],
    facets.academicTerms,
    filters.academicTerms as AcademicTerm[],
  );
  const regions = visibleOptions(
    [...REGION_OPTIONS],
    facets.regions,
    filters.regions as RegionId[],
  );
  const workplaces = visibleOptions(
    WORKPLACE_TYPES.filter((option) => option !== 'unknown'),
    facets.workplaceTypes,
    filters.workplaceTypes,
  );
  const mobility = visibleOptions(MOBILITY_FLAGS, facets.mobility, filters.mobility);

  const disciplineOptions = Object.entries(facets.disciplines)
    .map(([id, count]) => ({ id, count: count ?? 0 }))
    .filter(({ id, count }) => count > 0 || filters.disciplines.includes(id))
    .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));

  const internshipsActive = filters.careerLevels.includes('internship');
  const newGradActive = filters.careerLevels.includes('new_grad');
  const remoteActive = filters.workplaceTypes.includes('remote');
  const twoHourActive = filters.freshness === '2h';
  const dayActive = filters.freshness === '24h';
  const sinceVisitActive = filters.freshness === 'new_since_visit';

  return (
    <aside className="filter-panel" aria-label="Job filters">
      <div className="filter-panel__header">
        <h2>Filters</h2>
        <button type="button" className="btn-text" onClick={onReset}>
          Reset all
        </button>
      </div>

      <div className="filter-quick" role="group" aria-label="Quick filters">
        <QuickChip
          label="Since last visit"
          active={sinceVisitActive}
          onClick={() =>
            update({ freshness: sinceVisitActive ? 'all' : 'new_since_visit' })
          }
        />
        <QuickChip
          label="Last 2h"
          active={twoHourActive}
          onClick={() => update({ freshness: twoHourActive ? 'all' : '2h' })}
        />
        <QuickChip
          label="Last 24h"
          active={dayActive}
          onClick={() => update({ freshness: dayActive ? 'all' : '24h' })}
        />
        <QuickChip
          label="Internships"
          active={internshipsActive}
          onClick={() =>
            update({ careerLevels: toggleArrayValue(filters.careerLevels, 'internship') })
          }
        />
        <QuickChip
          label="New grad"
          active={newGradActive}
          onClick={() =>
            update({ careerLevels: toggleArrayValue(filters.careerLevels, 'new_grad') })
          }
        />
        <QuickChip
          label="Remote"
          active={remoteActive}
          onClick={() =>
            update({ workplaceTypes: toggleArrayValue(filters.workplaceTypes, 'remote') })
          }
        />
        <QuickChip
          label="Startups"
          active={filters.startupsOnly}
          onClick={() => update({ startupsOnly: !filters.startupsOnly })}
        />
      </div>

      <FilterSection title="Role type">
        <div className="filter-group__options">
          {primaryRoles.map((option) => (
            <CheckboxRow
              key={option}
              id={`role-${option}`}
              label={CAREER_LEVEL_LABELS[option]}
              checked={filters.careerLevels.includes(option)}
              count={facets.careerLevels[option] ?? 0}
              onChange={() =>
                update({ careerLevels: toggleArrayValue(filters.careerLevels, option) })
              }
            />
          ))}
        </div>
        {moreRoles.length > 0 ? (
          <details className="filter-more">
            <summary>More role types</summary>
            <div className="filter-group__options">
              {moreRoles.map((option) => (
                <CheckboxRow
                  key={option}
                  id={`role-more-${option}`}
                  label={CAREER_LEVEL_LABELS[option]}
                  checked={filters.careerLevels.includes(option)}
                  count={facets.careerLevels[option] ?? 0}
                  onChange={() =>
                    update({ careerLevels: toggleArrayValue(filters.careerLevels, option) })
                  }
                />
              ))}
            </div>
          </details>
        ) : null}
      </FilterSection>

      {seasons.length > 0 ? (
        <FilterSection title="Season">
          <div className="filter-group__options">
            {seasons.map((term) => (
              <CheckboxRow
                key={term}
                id={`season-${term}`}
                label={ACADEMIC_TERM_LABELS[term as AcademicTerm]}
                checked={filters.academicTerms.includes(term)}
                count={facets.academicTerms[term] ?? 0}
                onChange={() =>
                  update({
                    academicTerms: toggleArrayValue(filters.academicTerms, term as AcademicTerm),
                  })
                }
              />
            ))}
          </div>
        </FilterSection>
      ) : null}

      {regions.length > 0 ? (
        <FilterSection title="Location">
          <div className="filter-group__options">
            {regions.map((region) => (
              <CheckboxRow
                key={region}
                id={`region-${region}`}
                label={REGION_LABELS[region]}
                checked={filters.regions.includes(region)}
                count={facets.regions[region] ?? 0}
                onChange={() =>
                  update({
                    regions: toggleArrayValue(filters.regions, region as RegionId),
                  })
                }
              />
            ))}
          </div>
        </FilterSection>
      ) : null}

      {disciplineOptions.length > 0 ? (
        <FilterSection title="Discipline" defaultOpen={false}>
          <div className="filter-group__options filter-group__options--scroll">
            {disciplineOptions.map(({ id, count }) => (
              <CheckboxRow
                key={id}
                id={`discipline-${id}`}
                label={formatDiscipline(id)}
                checked={filters.disciplines.includes(id)}
                count={count}
                onChange={() =>
                  update({
                    disciplines: toggleArrayValue(filters.disciplines, id),
                  })
                }
              />
            ))}
          </div>
        </FilterSection>
      ) : null}

      {workplaces.length > 0 ? (
        <FilterSection title="Workplace" defaultOpen={false}>
          <div className="filter-group__options">
            {workplaces.map((option) => (
              <CheckboxRow
                key={option}
                id={`workplace-${option}`}
                label={WORKPLACE_LABELS[option]}
                checked={filters.workplaceTypes.includes(option)}
                count={facets.workplaceTypes[option] ?? 0}
                onChange={() =>
                  update({
                    workplaceTypes: toggleArrayValue(filters.workplaceTypes, option),
                  })
                }
              />
            ))}
          </div>
        </FilterSection>
      ) : null}

      <FilterSection title="Posted" defaultOpen={false}>
        <select
          id="freshness"
          value={filters.freshness}
          onChange={(event) =>
            update({ freshness: event.target.value as FilterState['freshness'] })
          }
        >
          <option value="all">Any time</option>
          <option value="new_since_visit">New since last visit</option>
          <option value="2h">Last 2 hours</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </select>
      </FilterSection>

      {mobility.length > 0 ? (
        <FilterSection title="Mobility" defaultOpen={false}>
          <div className="filter-group__options">
            {mobility.map((option) => (
              <CheckboxRow
                key={option}
                id={`mobility-${option}`}
                label={MOBILITY_LABELS[option]}
                checked={filters.mobility.includes(option)}
                count={facets.mobility[option] ?? 0}
                onChange={() =>
                  update({ mobility: toggleArrayValue(filters.mobility, option) })
                }
              />
            ))}
          </div>
        </FilterSection>
      ) : null}

      {activeCount > 0 ? (
        <p className="filter-active-hint">{activeCount} active filter{activeCount === 1 ? '' : 's'}</p>
      ) : null}
    </aside>
  );
}
