import type { AccountQuota, AccountSnapshot } from '@/types/pluginPanels';

/** A quota is only considered fresh for five minutes. The boundary is strict:
 *  exactly 300000 ms old is still fresh, 300001 ms is stale. */
export const QUOTA_STALE_AFTER_MS = 5 * 60_000;
/** Cap on simultaneous quota reads so entering a panel with many stale configs
 *  does not fire the whole batch at once. */
export const QUOTA_REFRESH_CONCURRENCY = 2;

export interface QuotaResult {
  id: string;
  app: string;
  quota: AccountQuota;
}

export interface PanelScope {
  pluginId: string;
  panelId: string;
}

/** Unknown timestamps need a query: an absent timestamp cannot prove
 *  freshness. A timestamp from the future (clock skew) yields a negative age,
 *  which is treated as fresh rather than inventing an age; it never needs a
 *  query just because the clock disagrees. */
export function needsQuotaRefresh(quota: AccountQuota | null | undefined, now: number): boolean {
  if (!quota) return true;
  const queriedAt = quota.queriedAt;
  if (queriedAt == null || !Number.isFinite(queriedAt)) return true;
  return now - queriedAt > QUOTA_STALE_AFTER_MS;
}

/** Merge a server snapshot into the last known one without losing quota reads
 *  that completed after the request started. The snapshot is otherwise
 *  authoritative: a provider without quota stays without quota. `keepQuota`
 *  marks providers whose locally applied value is newer than this request. */
export function mergeSnapshots(
  previous: AccountSnapshot | null,
  incoming: AccountSnapshot,
  keepQuota?: (providerId: string) => boolean,
): AccountSnapshot {
  if (!previous || previous.app !== incoming.app) return incoming;
  return {
    ...incoming,
    providers: incoming.providers.map((provider) => {
      const earlier = previous.providers.find((item) => item.id === provider.id);
      if (!earlier) return provider;
      return keepQuota?.(provider.id) ? { ...provider, quota: earlier.quota } : provider;
    }),
  };
}

/** In-memory cache only: snapshots contain the same names/emails the panel
 *  already renders and never credentials. Keys are isolated per plugin, panel
 *  and app, so remounting or switching apps cannot leak another view's data. */
const snapshots = new Map<string, AccountSnapshot>();
const listRequests = new Map<string, ListEntry>();
const quotaRequests = new Map<string, Promise<QuotaResult>>();
/** Generation of the shared snapshot. A pending list started before a write
 *  must not be applied after that write, even when a different mount reuses it. */
const snapshotVersions = new Map<string, number>();
/** Generation of each provider's quota patch, shared across mounts. */
const quotaVersions = new Map<string, number>();
let quotaVersionSeq = 0;

function keyFor(scope: PanelScope, app: string): string {
  return `${scope.pluginId}\u0000${scope.panelId}\u0000${app}`;
}

function quotaKeyFor(scope: PanelScope, app: string, providerId: string): string {
  return keyFor(scope, app) + '\u0000' + providerId;
}

export function readCachedSnapshot(scope: PanelScope, app: string): AccountSnapshot | null {
  return snapshots.get(keyFor(scope, app)) ?? null;
}

export function writeCachedSnapshot(scope: PanelScope, snapshot: AccountSnapshot): void {
  snapshots.set(keyFor(scope, snapshot.app), snapshot);
}

export function currentSnapshotVersion(scope: PanelScope, app: string): number {
  return snapshotVersions.get(keyFor(scope, app)) ?? 0;
}

export function bumpSnapshotVersion(scope: PanelScope, app: string): number {
  const next = currentSnapshotVersion(scope, app) + 1;
  snapshotVersions.set(keyFor(scope, app), next);
  return next;
}

export function markQuotaVersion(scope: PanelScope, app: string, providerId: string): number {
  quotaVersionSeq += 1;
  quotaVersions.set(quotaKeyFor(scope, app, providerId), quotaVersionSeq);
  return quotaVersionSeq;
}

export function currentQuotaVersion(scope: PanelScope, app: string, providerId: string): number {
  return quotaVersions.get(quotaKeyFor(scope, app, providerId)) ?? 0;
}

export function captureQuotaVersions(scope: PanelScope, app: string): Record<string, number> {
  const prefix = keyFor(scope, app) + '\u0000';
  const captured: Record<string, number> = {};
  for (const [key, version] of quotaVersions) {
    if (key.startsWith(prefix)) captured[key.slice(prefix.length)] = version;
  }
  return captured;
}

interface ListEntry {
  promise: Promise<AccountSnapshot>;
  quotaVersions: Record<string, number>;
  snapshotVersion: number;
}

export interface ListTicket extends ListEntry {
  reused: boolean;
}

/** Deduplicates a list read across mounts (including StrictMode replay), while
 *  carrying the runtime generations captured when the request actually began.
 *  A ticket started before a switch is not reused afterwards and its response
 *  is discarded by the caller. */
export function startListRequest(scope: PanelScope, app: string,
  factory: () => Promise<AccountSnapshot>): ListTicket {
  const key = keyFor(scope, app);
  const snapshotVersion = currentSnapshotVersion(scope, app);
  const existing = listRequests.get(key);
  if (existing && existing.snapshotVersion === snapshotVersion) {
    return { ...existing, reused: true };
  }
  const quotaVersionsAtStart = captureQuotaVersions(scope, app);
  const created = factory();
  const entry: ListEntry = { promise: created, quotaVersions: quotaVersionsAtStart, snapshotVersion };
  entry.promise = created.finally(() => {
    if (listRequests.get(key) === entry) listRequests.delete(key);
  });
  listRequests.set(key, entry);
  return { ...entry, reused: false };
}

function dedupe<T>(requests: Map<string, Promise<T>>, key: string, factory: () => Promise<T>):
  { promise: Promise<T>; reused: boolean } {
  const existing = requests.get(key);
  if (existing) return { promise: existing, reused: true };
  const tracked = factory().finally(() => { if (requests.get(key) === tracked) requests.delete(key); });
  requests.set(key, tracked);
  return { promise: tracked, reused: false };
}

/** A second mount or StrictMode replay shares the quota read already in flight. */
export function dedupeQuotaRequest(scope: PanelScope, app: string, providerId: string,
  factory: () => Promise<QuotaResult>) {
  return dedupe(quotaRequests, quotaKeyFor(scope, app, providerId), factory);
}

/** Run at most `limit` tasks at once; a rejected task never blocks the rest. */
export async function runLimited(tasks: (() => Promise<unknown>)[], limit: number): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < tasks.length) {
      const task = tasks[next];
      next += 1;
      if (!task) return;
      try { await task(); } catch { /* each caller records its own failure */ }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, () => worker()));
}

/** Tests only: the cache and the in-flight registries are process-wide. */
export function resetPluginPanelRuntime(): void {
  snapshots.clear();
  listRequests.clear();
  quotaRequests.clear();
  snapshotVersions.clear();
  quotaVersions.clear();
  quotaVersionSeq = 0;
}
