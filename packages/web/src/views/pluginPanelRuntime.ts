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

/** Unknown and future timestamps both need a query: an absent timestamp cannot
 *  prove freshness, and a future one (clock skew) must not produce a negative
 *  age that would otherwise look fresh. */
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
const listRequests = new Map<string, Promise<AccountSnapshot>>();
const quotaRequests = new Map<string, Promise<QuotaResult>>();

function keyFor(scope: PanelScope, app: string): string {
  return `${scope.pluginId}\u0000${scope.panelId}\u0000${app}`;
}

export function readCachedSnapshot(scope: PanelScope, app: string): AccountSnapshot | null {
  return snapshots.get(keyFor(scope, app)) ?? null;
}

export function writeCachedSnapshot(scope: PanelScope, snapshot: AccountSnapshot): void {
  snapshots.set(keyFor(scope, snapshot.app), snapshot);
}

function dedupe<T>(requests: Map<string, Promise<T>>, key: string, factory: () => Promise<T>):
  { promise: Promise<T>; reused: boolean } {
  const existing = requests.get(key);
  if (existing) return { promise: existing, reused: true };
  const tracked = factory().finally(() => { if (requests.get(key) === tracked) requests.delete(key); });
  requests.set(key, tracked);
  return { promise: tracked, reused: false };
}

/** A second mount, StrictMode replay, or quick away-and-back trip shares the
 *  request already in flight instead of sending another CLI call. */
export function dedupeListRequest(scope: PanelScope, app: string, factory: () => Promise<AccountSnapshot>) {
  return dedupe(listRequests, keyFor(scope, app), factory);
}

export function dedupeQuotaRequest(scope: PanelScope, app: string, providerId: string,
  factory: () => Promise<QuotaResult>) {
  return dedupe(quotaRequests, keyFor(scope, app) + '\u0000' + providerId, factory);
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
}
