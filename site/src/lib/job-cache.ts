import type { DataManifest, Job } from './types';

const DB_NAME = 'openroleradar-feed';
const DB_VERSION = 1;
const STORE = 'feeds';

export type FeedMode = 'strict' | 'wide';

interface CachedFeed {
  mode: FeedMode;
  generatedAt: string;
  jobs: Job[];
  savedAt: string;
}

function canUseIndexedDb(): boolean {
  return typeof window !== 'undefined' && typeof window.indexedDB !== 'undefined';
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB open failed'));
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'mode' });
      }
    };
    request.onsuccess = () => resolve(request.result);
  });
}

export async function readCachedFeed(mode: FeedMode): Promise<CachedFeed | null> {
  if (!canUseIndexedDb()) return null;
  try {
    const db = await openDb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const store = tx.objectStore(STORE);
      const req = store.get(mode);
      req.onerror = () => reject(req.error ?? new Error('IndexedDB read failed'));
      req.onsuccess = () => resolve((req.result as CachedFeed | undefined) ?? null);
    });
  } catch {
    return null;
  }
}

export async function writeCachedFeed(
  mode: FeedMode,
  manifest: DataManifest,
  jobs: Job[],
): Promise<void> {
  if (!canUseIndexedDb()) return;
  try {
    const db = await openDb();
    const payload: CachedFeed = {
      mode,
      generatedAt: manifest.generated_at,
      jobs,
      savedAt: new Date().toISOString(),
    };
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error('IndexedDB write failed'));
      tx.objectStore(STORE).put(payload);
    });
  } catch {
    // Quota / private mode — ignore
  }
}
