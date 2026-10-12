const STORAGE_PREFIX = 'orr:';
const SESSION_BASELINE_KEY = `${STORAGE_PREFIX}sessionBaseline`;
const SESSION_STARTED_KEY = `${STORAGE_PREFIX}sessionStarted`;

export interface UserPreferences {
  savedJobIds: string[];
  dismissedJobIds: string[];
  lastVisit: string | null;
  originCountry: string;
  theme: 'light' | 'dark' | 'system';
}

const DEFAULT_PREFERENCES: UserPreferences = {
  savedJobIds: [],
  dismissedJobIds: [],
  lastVisit: null,
  originCountry: '',
  theme: 'dark',
};

function storageKey(key: keyof UserPreferences): string {
  return `${STORAGE_PREFIX}${key}`;
}

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function canUseSession(): boolean {
  return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
}

function readJson<T>(key: string, fallback: T): T {
  if (!canUseStorage()) return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson<T>(key: string, value: T): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota or privacy mode — ignore
  }
}

export function loadPreferences(): UserPreferences {
  if (!canUseStorage()) return { ...DEFAULT_PREFERENCES };
  return {
    savedJobIds: readJson<string[]>(storageKey('savedJobIds'), []),
    dismissedJobIds: readJson<string[]>(storageKey('dismissedJobIds'), []),
    lastVisit: readJson<string | null>(storageKey('lastVisit'), null),
    originCountry: readJson<string>(storageKey('originCountry'), ''),
    theme: readJson<UserPreferences['theme']>(storageKey('theme'), 'system'),
  };
}

export function savePreferences(prefs: Partial<UserPreferences>): UserPreferences {
  const current = loadPreferences();
  const next = { ...current, ...prefs };
  writeJson(storageKey('savedJobIds'), next.savedJobIds);
  writeJson(storageKey('dismissedJobIds'), next.dismissedJobIds);
  writeJson(storageKey('lastVisit'), next.lastVisit);
  writeJson(storageKey('originCountry'), next.originCountry);
  writeJson(storageKey('theme'), next.theme);
  return next;
}

export function toggleSavedJob(jobId: string): UserPreferences {
  const prefs = loadPreferences();
  const saved = new Set(prefs.savedJobIds);
  if (saved.has(jobId)) saved.delete(jobId);
  else saved.add(jobId);
  return savePreferences({ savedJobIds: [...saved] });
}

export function toggleDismissedJob(jobId: string): UserPreferences {
  const prefs = loadPreferences();
  const dismissed = new Set(prefs.dismissedJobIds);
  if (dismissed.has(jobId)) dismissed.delete(jobId);
  else dismissed.add(jobId);
  return savePreferences({ dismissedJobIds: [...dismissed] });
}

/**
 * Start a browsing session. Captures the previous visit as the baseline for
 * "new since last visit" without overwriting it until the user leaves.
 */
export function beginVisitSession(): string | null {
  if (!canUseSession()) {
    return loadPreferences().lastVisit;
  }
  const existing = window.sessionStorage.getItem(SESSION_STARTED_KEY);
  if (existing) {
    const baseline = window.sessionStorage.getItem(SESSION_BASELINE_KEY);
    return baseline || null;
  }
  const baseline = loadPreferences().lastVisit;
  window.sessionStorage.setItem(SESSION_STARTED_KEY, new Date().toISOString());
  window.sessionStorage.setItem(SESSION_BASELINE_KEY, baseline ?? '');
  return baseline;
}

/** Baseline timestamp for this tab session (previous visit), or null. */
export function getSessionBaseline(): string | null {
  if (!canUseSession()) {
    return loadPreferences().lastVisit;
  }
  if (!window.sessionStorage.getItem(SESSION_STARTED_KEY)) {
    return beginVisitSession();
  }
  const baseline = window.sessionStorage.getItem(SESSION_BASELINE_KEY);
  return baseline || null;
}

/** Persist this session as the next visit marker (call on page leave only). */
export function endVisitSession(): string {
  const now = new Date().toISOString();
  savePreferences({ lastVisit: now });
  return now;
}

/** @deprecated use endVisitSession — kept for call-site compatibility */
export function recordVisit(): string {
  return endVisitSession();
}

export function setOriginCountry(country: string): UserPreferences {
  return savePreferences({ originCountry: country });
}

export function setTheme(theme: UserPreferences['theme']): UserPreferences {
  return savePreferences({ theme });
}

export function applyTheme(theme: UserPreferences['theme']): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.theme = theme;
  if (theme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.resolvedTheme = prefersDark ? 'dark' : 'light';
  } else {
    root.dataset.resolvedTheme = theme;
  }
}

export function initTheme(): void {
  applyTheme(loadPreferences().theme);
  if (typeof window === 'undefined') return;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const handler = () => {
    if (loadPreferences().theme === 'system') {
      applyTheme('system');
    }
  };
  media.addEventListener('change', handler);
}
