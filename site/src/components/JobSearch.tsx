import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { postedAt } from '../lib/dates';
import {
  applyDelta,
  hydrateFromCache,
  loadAllJobs,
  loadDelta,
  loadHotCompanies,
  loadManifest,
  peekNewestManifest,
  resetDataLoader,
  setFeedMode,
  writeCachedFeed,
  type HotCompany,
} from '../lib/data-loader';
import {
  countActiveFilters,
  DEFAULT_FILTERS,
  parseFiltersFromUrl,
  serializeFiltersToUrl,
  type FilterState,
} from '../lib/filters';
import {
  buildSearchIndex,
  collectFacetValues,
  computeStats,
  filterJobs,
  paginateJobs,
  totalPages,
} from '../lib/search';
import {
  beginVisitSession,
  endVisitSession,
  getSessionBaseline,
  loadPreferences,
  setOriginCountry,
  toggleSavedJob,
} from '../lib/storage';
import type { Job, SiteStats, SortMode } from '../lib/types';
import FilterPanel from './FilterPanel';
import JobRow from './JobRow';
import StatsBar from './StatsBar';

interface JobSearchProps {
  initialStats?: SiteStats;
  companySlug?: string;
  showStats?: boolean;
  showHero?: boolean;
}

const EMPTY_STATS: SiteStats = {
  totalJobs: 0,
  totalCompanies: 0,
  withVisa: 0,
  withRelocation: 0,
  remoteCount: 0,
  newSinceVisit: 0,
  generatedAt: new Date().toISOString(),
};

function HeroBanner({
  newSinceVisit,
  onShowNew,
  onShowLast2h,
}: {
  newSinceVisit: number;
  onShowNew: () => void;
  onShowLast2h: () => void;
}) {
  return (
    <section className="hero hero--compact">
      <div className="hero__copy">
        <h1 className="hero__brand">
          OpenRole<span>Radar</span>
        </h1>
        <p className="hero__lead">
          Be first. Live CS internships and new-grad roles from first-party ATS boards — refreshed
          continuously.
        </p>
        <div className="hero__actions">
          <button type="button" className="btn btn--primary" onClick={onShowNew}>
            {newSinceVisit > 0
              ? `Show ${newSinceVisit.toLocaleString()} new since last visit`
              : 'Show new since last visit'}
          </button>
          <button type="button" className="btn btn--ghost" onClick={onShowLast2h}>
            Last 2 hours
          </button>
          <a
            className="btn btn--ghost"
            href="https://github.com/moosacodes/Open-Role-Radar"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
        </div>
      </div>
      <aside className="hero__panel" aria-label="Why OpenRoleRadar">
        <div className="hero__radar" aria-hidden="true" />
        <h2>Fresh opportunities. No noise.</h2>
        <ul>
          <li>
            <span className="hero__check" aria-hidden="true" />
            Direct apply links from employer ATS boards
          </li>
          <li>
            <span className="hero__check" aria-hidden="true" />
            Hot companies polled every few minutes
          </li>
          <li>
            <span className="hero__check" aria-hidden="true" />
            Built for CS students hunting early-career roles
          </li>
        </ul>
      </aside>
    </section>
  );
}

export default function JobSearch({
  initialStats,
  companySlug,
  showStats = true,
  showHero = false,
}: JobSearchProps) {
  const [sessionBaseline] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return beginVisitSession();
  });
  const [filters, setFilters] = useState<FilterState>(() => {
    if (typeof window === 'undefined') return DEFAULT_FILTERS;
    const parsed = parseFiltersFromUrl(window.location.search);
    // Keep DEFAULT_FILTERS.freshness (24h) — "new since visit" is opt-in so
    // returning users never land on an empty table.
    return { ...parsed, view: 'table' };
  });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState(() => loadPreferences());
  const [stats, setStats] = useState<SiteStats>(initialStats ?? EMPTY_STATS);
  const [hotCompanies, setHotCompanies] = useState<HotCompany[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [arrivedIds, setArrivedIds] = useState<Set<string>>(() => new Set());
  const [liveNotice, setLiveNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const silentRef = useRef(false);
  const lastStampRef = useRef<string | null>(null);
  const knownIdsRef = useRef<Set<string>>(new Set());
  const pollInFlightRef = useRef(false);
  const jobsRef = useRef<Job[]>([]);

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  const reload = useCallback((silent = false) => {
    silentRef.current = silent;
    setReloadToken((token) => token + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const silent = silentRef.current;
      const mode = filters.feedMode;
      setFeedMode(mode);

      if (!silent) {
        const cached = await hydrateFromCache(mode);
        if (!cancelled && cached) {
          const scoped = companySlug
            ? cached.jobs.filter(
                (job) =>
                  job.company_name
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, '-')
                    .replace(/^-+|-+$/g, '') === companySlug,
              )
            : cached.jobs;
          const baseline = getSessionBaseline() ?? sessionBaseline;
          startTransition(() => {
            setJobs(scoped);
            setStats(computeStats(scoped, baseline, cached.generatedAt));
            setLoading(false);
          });
          lastStampRef.current = cached.generatedAt;
          knownIdsRef.current = new Set(scoped.map((job) => job.job_id));
        }
      }

      if (silent && lastStampRef.current) {
        try {
          const peek = await peekNewestManifest(mode);
          if (cancelled) return;
          if (peek.generated_at === lastStampRef.current) {
            silentRef.current = false;
            pollInFlightRef.current = false;
            return;
          }
          setSyncing(true);

          // Fast path: patch via delta when the change set is small.
          resetDataLoader();
          setFeedMode(mode);
          const manifest = await loadManifest(mode);
          const delta = await loadDelta();
          const currentJobs = jobsRef.current;
          if (
            delta &&
            currentJobs.length > 0 &&
            delta.generated_at === manifest.generated_at
          ) {
            const patched = applyDelta(currentJobs, delta);
            if (patched) {
              const scopedJobs = companySlug
                ? patched.filter(
                    (job) =>
                      job.company_name
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, '-')
                        .replace(/^-+|-+$/g, '') === companySlug,
                  )
                : patched;
              const nextIds = new Set(scopedJobs.map((job) => job.job_id));
              const fresh = scopedJobs
                .filter((job) => !knownIdsRef.current.has(job.job_id))
                .map((job) => job.job_id);
              if (fresh.length > 0) {
                setArrivedIds(new Set(fresh));
                setLiveNotice(
                  `${fresh.length.toLocaleString()} new role${fresh.length === 1 ? '' : 's'} just landed`,
                );
                window.setTimeout(() => setLiveNotice(null), 4500);
                window.setTimeout(() => setArrivedIds(new Set()), 1600);
              }
              knownIdsRef.current = nextIds;
              lastStampRef.current = manifest.generated_at;
              void writeCachedFeed(mode, manifest, patched);
              const baseline = getSessionBaseline() ?? sessionBaseline;
              startTransition(() => {
                setJobs(scopedJobs);
                setStats(computeStats(scopedJobs, baseline, manifest.generated_at));
              });
              silentRef.current = false;
              pollInFlightRef.current = false;
              setSyncing(false);
              return;
            }
          }
        } catch {
          if (cancelled) return;
          setSyncing(true);
        }
      }

      if (!silent && jobsRef.current.length === 0) {
        setLoading(true);
      }
      setError(null);
      try {
        resetDataLoader();
        setFeedMode(mode);
        const manifest = await loadManifest(mode);
        const allJobs = await loadAllJobs(manifest);
        if (cancelled) return;

        const scopedJobs = companySlug
          ? allJobs.filter(
              (job) =>
                job.company_name
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, '-')
                  .replace(/^-+|-+$/g, '') === companySlug,
            )
          : allJobs;

        const nextIds = new Set(scopedJobs.map((job) => job.job_id));
        if (silent && knownIdsRef.current.size > 0) {
          const fresh = scopedJobs
            .filter((job) => !knownIdsRef.current.has(job.job_id))
            .map((job) => job.job_id);
          if (fresh.length > 0) {
            setArrivedIds(new Set(fresh));
            setLiveNotice(
              `${fresh.length.toLocaleString()} new role${fresh.length === 1 ? '' : 's'} just landed`,
            );
            window.setTimeout(() => setLiveNotice(null), 4500);
            window.setTimeout(() => setArrivedIds(new Set()), 1600);
          }
        }
        knownIdsRef.current = nextIds;
        lastStampRef.current = manifest.generated_at;
        void loadHotCompanies().then((companies) => {
          if (!cancelled) setHotCompanies(companies);
        });

        const stored = loadPreferences();
        const baseline = getSessionBaseline() ?? sessionBaseline;
        startTransition(() => {
          setJobs(scopedJobs);
          setPrefs(stored);
          setStats(computeStats(scopedJobs, baseline, manifest.generated_at));
        });

        if (stored.originCountry && !filters.originCountry) {
          setFilters((current) => ({
            ...current,
            originCountry: stored.originCountry,
            view: 'table',
          }));
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load jobs');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setSyncing(false);
          silentRef.current = false;
          pollInFlightRef.current = false;
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companySlug, reloadToken, filters.feedMode]);

  useEffect(() => {
    let armed = false;
    const armTimer = window.setTimeout(() => {
      armed = true;
    }, 2500);

    const peek = () => {
      if (!armed || pollInFlightRef.current) return;
      pollInFlightRef.current = true;
      reload(true);
    };

    const onFocus = () => peek();
    const onVisible = () => {
      if (document.visibilityState === 'visible') peek();
    };
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) peek();
    };

    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', onPageShow);

    let timer = window.setInterval(peek, 15_000);
    const onVisibilityForPoll = () => {
      window.clearInterval(timer);
      const ms = document.visibilityState === 'visible' ? 15_000 : 60_000;
      timer = window.setInterval(peek, ms);
    };
    document.addEventListener('visibilitychange', onVisibilityForPoll);

    return () => {
      window.clearTimeout(armTimer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
      document.removeEventListener('visibilitychange', onVisibilityForPoll);
      window.removeEventListener('pageshow', onPageShow);
      window.clearInterval(timer);
    };
  }, [reload]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const query = serializeFiltersToUrl({ ...filters, view: 'table' });
    const base = window.location.pathname;
    window.history.replaceState(null, '', query ? `${base}${query}` : base);
  }, [filters]);

  useEffect(() => {
    if (filters.originCountry !== prefs.originCountry) {
      const next = setOriginCountry(filters.originCountry);
      setPrefs(next);
    }
  }, [filters.originCountry, prefs.originCountry]);

  useEffect(() => {
    const handleLeave = () => endVisitSession();
    window.addEventListener('pagehide', handleLeave);
    return () => {
      window.removeEventListener('pagehide', handleLeave);
      // Do not end the session on React remount — only on real page leave.
    };
  }, []);

  const index = useMemo(() => buildSearchIndex(jobs), [jobs]);
  const facets = useMemo(() => collectFacetValues(jobs), [jobs]);
  const visitBaseline = sessionBaseline ?? getSessionBaseline();

  const filteredJobs = useMemo(
    () =>
      filterJobs(jobs, index, { ...filters, view: 'table' }, {
        savedJobIds: new Set(prefs.savedJobIds),
        dismissedJobIds: new Set(prefs.dismissedJobIds),
        lastVisit: visitBaseline,
      }),
    [jobs, index, filters, prefs, visitBaseline],
  );

  const showNewSinceVisit = useCallback(() => {
    setFilters((current) => ({
      ...current,
      freshness: 'new_since_visit',
      page: 1,
      view: 'table',
    }));
    document.getElementById('roles')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const showLast2h = useCallback(() => {
    setFilters((current) => ({
      ...current,
      freshness: '2h',
      page: 1,
      view: 'table',
    }));
    document.getElementById('roles')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const pageJobs = useMemo(
    () => paginateJobs(filteredJobs, filters.page, filters.pageSize),
    [filteredJobs, filters.page, filters.pageSize],
  );

  const pages = totalPages(filteredJobs.length, filters.pageSize);
  const activeFilterCount = countActiveFilters(filters);

  const handleSave = useCallback((jobId: string) => {
    setPrefs(toggleSavedJob(jobId));
  }, []);

  const isNewJob = useCallback(
    (job: Job) => {
      const posted = Date.parse(postedAt(job));
      const firstSeen = Date.parse(job.first_seen_at);
      const now = Date.now();
      const hotWindow = 24 * 60 * 60 * 1000;
      if (!Number.isNaN(firstSeen) && now - firstSeen < hotWindow) return true;
      if (!Number.isNaN(posted) && now - posted < hotWindow) return true;
      if (!visitBaseline) return false;
      const visit = Date.parse(visitBaseline);
      return !Number.isNaN(firstSeen) && !Number.isNaN(visit) && firstSeen > visit;
    },
    [visitBaseline],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (pageJobs.length === 0) return;

      const currentIndex = Math.max(
        0,
        pageJobs.findIndex((job) => job.job_id === selectedId),
      );

      if (event.key === 'j' || event.key === 'ArrowDown') {
        event.preventDefault();
        const next = pageJobs[Math.min(pageJobs.length - 1, currentIndex + 1)];
        setSelectedId(next.job_id);
        document
          .querySelector<HTMLElement>(`[data-job-id="${next.job_id}"]`)
          ?.scrollIntoView({ block: 'nearest' });
      }

      if (event.key === 'k' || event.key === 'ArrowUp') {
        event.preventDefault();
        const next = pageJobs[Math.max(0, currentIndex - 1)];
        setSelectedId(next.job_id);
        document
          .querySelector<HTMLElement>(`[data-job-id="${next.job_id}"]`)
          ?.scrollIntoView({ block: 'nearest' });
      }

      if (event.key === 'Enter' && selectedId) {
        event.preventDefault();
        setExpandedId((current) => (current === selectedId ? null : selectedId));
      }

      if (event.key === 's' && selectedId) {
        event.preventDefault();
        handleSave(selectedId);
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pageJobs, selectedId, handleSave]);

  useEffect(() => {
    if (pageJobs.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !pageJobs.some((job) => job.job_id === selectedId)) {
      setSelectedId(pageJobs[0].job_id);
    }
  }, [pageJobs, selectedId]);

  return (
    <div className="job-search">
      <div className="job-search__layout">
        <div className={filtersOpen ? 'filter-shell filter-shell--open' : 'filter-shell'}>
          <FilterPanel
            filters={filters}
            facets={facets}
            activeCount={activeFilterCount}
            onChange={(next) => {
              const apply = () => setFilters({ ...next, view: 'table' });
              if (next.q !== filters.q) {
                apply();
                return;
              }
              startTransition(apply);
            }}
            onReset={() =>
              setFilters({
                ...DEFAULT_FILTERS,
                careerLevels: [...DEFAULT_FILTERS.careerLevels],
                disciplines: [...DEFAULT_FILTERS.disciplines],
                academicTerms: [],
                companyIds: [],
                startupsOnly: false,
                originCountry: prefs.originCountry,
                view: 'table',
              })
            }
          />
        </div>

        <div className="job-search__main">
          {showHero ? (
            <HeroBanner
              newSinceVisit={stats.newSinceVisit}
              onShowNew={showNewSinceVisit}
              onShowLast2h={showLast2h}
            />
          ) : null}
          {showStats ? (
            <StatsBar
              stats={stats}
              loading={loading}
              syncing={syncing}
              onNewSinceVisit={showNewSinceVisit}
            />
          ) : null}

          {!loading && hotCompanies.length > 0 ? (
            <div className="hot-strip" aria-label="Companies hiring in the last 2 hours">
              <span className="hot-strip__label">Hot now</span>
              <div className="hot-strip__row">
                {hotCompanies.slice(0, 12).map((company) => (
                  <button
                    key={company.company_id}
                    type="button"
                    className="hot-strip__chip"
                    onClick={() =>
                      setFilters((current) => ({
                        ...current,
                        q: company.company_name,
                        freshness: '2h',
                        page: 1,
                        view: 'table',
                      }))
                    }
                    title={`${company.new_roles} new in last 2h`}
                  >
                    {company.company_name}
                    <span className="hot-strip__count">{company.new_roles}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <section id="roles" className="job-search__results" aria-label="Job results">
            <div className="job-search__toolbar">
              <div className="job-search__toolbar-left">
                <button
                  type="button"
                  className="btn btn--secondary btn--sm filter-toggle"
                  onClick={() => setFiltersOpen((open) => !open)}
                  aria-expanded={filtersOpen}
                >
                  Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
                </button>
                <label htmlFor="toolbar-search" className="sr-only">
                  Search roles
                </label>
                <div className="filter-search-field job-search__toolbar-search">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
                    <path
                      d="M20 20l-3.5-3.5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                  <input
                    id="toolbar-search"
                    type="search"
                    value={filters.q}
                    onChange={(event) =>
                      setFilters((current) => ({
                        ...current,
                        q: event.target.value,
                        page: 1,
                        view: 'table',
                      }))
                    }
                    placeholder="Search roles, companies…"
                    autoComplete="off"
                  />
                </div>
                <p className="job-search__count" aria-live="polite">
                  {loading
                    ? 'Pulling live feed…'
                    : `${filteredJobs.length.toLocaleString()} role${filteredJobs.length === 1 ? '' : 's'}`}
                </p>
              </div>
              <div className="job-search__controls">
                <label className="job-search__sort">
                  <span className="sr-only">Feed scope</span>
                  <select
                    value={filters.feedMode}
                    onChange={(event) => {
                      const feedMode = event.target.value === 'wide' ? 'wide' : 'strict';
                      setFilters((current) => ({
                        ...current,
                        feedMode,
                        page: 1,
                        view: 'table',
                      }));
                    }}
                    title="CS-focused vs all early-career disciplines"
                  >
                    <option value="strict">CS / software</option>
                    <option value="wide">All early-career</option>
                  </select>
                </label>
                <button
                  type="button"
                  className={
                    filters.showSavedOnly
                      ? 'btn btn--secondary btn--sm btn--pressed'
                      : 'btn btn--ghost btn--sm'
                  }
                  aria-pressed={filters.showSavedOnly}
                  onClick={() =>
                    setFilters((current) => ({
                      ...current,
                      showSavedOnly: !current.showSavedOnly,
                      page: 1,
                      view: 'table',
                    }))
                  }
                >
                  Saved only
                </button>
                <label className="job-search__sort">
                  <span className="sr-only">Sort by</span>
                  <select
                    value={filters.sort}
                    onChange={(event) =>
                      setFilters((current) => ({
                        ...current,
                        sort: event.target.value as SortMode,
                        page: 1,
                        view: 'table',
                      }))
                    }
                  >
                    <option value="newest">Newest first</option>
                    <option value="diverse">Mixed companies</option>
                    <option value="company">Company A–Z</option>
                    <option value="title">Title A–Z</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => reload(false)}
                  title="Force-reload the newest published feed"
                >
                  Refresh
                </button>
              </div>
            </div>

            {liveNotice ? (
              <p className="live-notice" role="status">
                {liveNotice}
              </p>
            ) : null}

            {error ? (
              <div className="alert alert--error" role="alert">
                {error}
              </div>
            ) : null}

            {!loading && filteredJobs.length === 0 ? (
              <div className="empty-state">
                <h3>
                  {filters.freshness === 'new_since_visit'
                    ? 'No new roles since your last visit'
                    : 'No roles match'}
                </h3>
                <p>
                  {filters.freshness === 'new_since_visit'
                    ? 'Widen the window — recent openings are still live.'
                    : 'Loosen filters or check back after the next sync.'}
                </p>
                <div className="empty-state__actions">
                  {filters.freshness !== '24h' ? (
                    <button
                      type="button"
                      className="btn btn--primary btn--sm"
                      onClick={() =>
                        setFilters((current) => ({
                          ...current,
                          freshness: '24h',
                          page: 1,
                          view: 'table',
                        }))
                      }
                    >
                      Show last 24 hours
                    </button>
                  ) : null}
                  {filters.freshness !== 'all' ? (
                    <button
                      type="button"
                      className="btn btn--secondary btn--sm"
                      onClick={() =>
                        setFilters((current) => ({
                          ...current,
                          freshness: 'all',
                          page: 1,
                          view: 'table',
                        }))
                      }
                    >
                      Show all open roles
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}

            {pageJobs.length > 0 ? (
              <div className="job-list" role="list">
                {pageJobs.map((job, index) => (
                  <div key={job.job_id} role="listitem">
                    <JobRow
                      job={job}
                      index={index}
                      saved={prefs.savedJobIds.includes(job.job_id)}
                      isNew={isNewJob(job)}
                      justArrived={arrivedIds.has(job.job_id)}
                      expanded={expandedId === job.job_id}
                      selected={selectedId === job.job_id}
                      onToggle={(jobId) =>
                        setExpandedId((current) => (current === jobId ? null : jobId))
                      }
                      onSelect={setSelectedId}
                      onSave={handleSave}
                    />
                  </div>
                ))}
              </div>
            ) : null}

            {filteredJobs.length > 0 ? (
              <nav className="pagination" aria-label="Results pagination">
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={filters.page <= 1}
                  onClick={() =>
                    setFilters((current) => ({
                      ...current,
                      page: current.page - 1,
                      view: 'table',
                    }))
                  }
                >
                  Previous
                </button>
                <span className="pagination__status">
                  Page {filters.page} of {pages}
                </span>
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={filters.page >= pages}
                  onClick={() =>
                    setFilters((current) => ({
                      ...current,
                      page: current.page + 1,
                      view: 'table',
                    }))
                  }
                >
                  Next
                </button>
              </nav>
            ) : null}
          </section>
        </div>
      </div>

      {filtersOpen ? (
        <button
          type="button"
          className="filter-backdrop"
          aria-label="Close filters"
          onClick={() => setFiltersOpen(false)}
        />
      ) : null}
    </div>
  );
}
