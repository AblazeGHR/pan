import type { SessionUsageView } from '@/types';

export interface SessionUsageCacheEntry {
  usage: SessionUsageView;
  cachedAt: number;
}

// This is a render fast path only. Every Usage expansion starts a fresh
// request, so the cache cannot become the sole source of truth indefinitely.
const sessionUsageCache = new Map<string, SessionUsageCacheEntry>();

export function getSessionUsageCache(sessionId: string): SessionUsageCacheEntry | undefined {
  return sessionUsageCache.get(sessionId);
}

export function setSessionUsageCache(sessionId: string, usage: SessionUsageView): void {
  sessionUsageCache.set(sessionId, { usage, cachedAt: Date.now() });
}

export function clearSessionUsageCache(): void {
  sessionUsageCache.clear();
}
