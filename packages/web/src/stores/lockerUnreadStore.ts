import { useMemo, useSyncExternalStore } from 'react';
import { useAutomationAuth } from '@/stores/automationStore';
import { lockerRequest } from '@/views/lockerModel';

/**
 * Shared locker unread counts for the navigation badge and the per-Session
 * jump button. One module-level 30s poll is shared by every subscriber; the
 * poll pauses while the page is hidden, backs off on failure (keeping the last
 * known counts), and never blocks rendering because the fetch is off-render.
 *
 * The server returns only counts: `{unread, sessions: {sessionId: count}}`.
 * Keys are Session ids already public in the Session list; no content, titles,
 * card ids, or owner names are available here.
 */

const REFRESH_MS = 30_000;
const MAX_BACKOFF_MS = 120_000;

let unread = 0;
let sessions: Readonly<Record<string, number>> = Object.freeze({});
let timer: ReturnType<typeof setTimeout> | null = null;
let loading = false;
let backoff = REFRESH_MS;
const listeners = new Set<() => void>();
let bound = false;
let unsubscribeAuth: (() => void) | null = null;

const emit = () => listeners.forEach((listener) => listener());

function normalizedCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

async function refreshLockerUnread() {
  if (loading || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) return;
  loading = true;
  try {
    const result = await lockerRequest<{ unread?: number; sessions?: Record<string, number> }>(
      '/api/locker-unread-summary',
    );
    const next: Record<string, number> = {};
    for (const [sessionId, count] of Object.entries(result.sessions ?? {})) {
      const value = normalizedCount(count);
      if (value > 0) next[sessionId] = value;
    }
    unread = normalizedCount(result.unread);
    sessions = Object.freeze(next);
    emit();
    backoff = REFRESH_MS;
  } catch {
    /* keep the last known counts and retry with bounded backoff */
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
  } finally {
    loading = false;
    if (timer) clearTimeout(timer);
    if (listeners.size > 0) timer = setTimeout(() => void refreshLockerUnread(), backoff);
  }
}

function onVisibility() {
  if (document.visibilityState === 'visible') void refreshLockerUnread();
}

function start() {
  if (bound) return;
  bound = true;
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
  unsubscribeAuth = useAutomationAuth.subscribe((state, previous) => {
    if (state.token === previous.token) return;
    if (timer) clearTimeout(timer);
    timer = null;
    backoff = REFRESH_MS;
    void refreshLockerUnread();
  });
  void refreshLockerUnread();
}

function stop() {
  if (!bound) return;
  bound = false;
  if (typeof document !== 'undefined')
    document.removeEventListener('visibilitychange', onVisibility);
  unsubscribeAuth?.();
  unsubscribeAuth = null;
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) start();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) stop();
  };
}

const getUnreadSnapshot = () => unread;
const getServerSnapshot = () => 0;

/** Aggregate unread count for the Locker navigation badge. */
export function useLockerUnread(): number {
  return useSyncExternalStore(subscribe, getUnreadSnapshot, getServerSnapshot);
}

/** Unread count for one Session; a primitive keeps unrelated rows from re-rendering. */
export function useLockerSessionUnread(sessionId: string): number {
  const getSnapshot = useMemo(() => () => sessions[sessionId] ?? 0, [sessionId]);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
