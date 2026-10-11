/**
 * Deep link between a completion notification (or URL) and a Session.
 *
 * Phase 1 scope: the Pan page is alive (foreground or background tab). The
 * browser side already shows a Web Notification on `worker.result` when the
 * session's `browser` notification setting is on; the event carries the
 * session id at the top level. Clicking the notification must focus Pan and
 * jump to that session — and the same `?session=<id>` URL contract doubles as
 * the cold start entry (bookmark / external helper), so a session can be
 * addressed from outside the SPA.
 *
 * Reliability rules (review-hardened):
 *  - The cached session list cannot prove deletion: a miss may just mean the
 *    list is unloaded or stale. Only a completed authoritative list fetch
 *    that does not contain the id counts as "missing".
 *  - A failed list load is NOT deletion: the deep link keeps waiting and
 *    recovers when a later refresh succeeds.
 *  - Async work carries an intent guard: a click started earlier never wins
 *    over a newer click, a newer selection, or a user navigation that
 *    happened while it was resolving.
 *  - Router failures are surfaced (toast + false), never swallowed.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

export const SESSION_DEEPLINK_PARAM = 'session';

/**
 * Conservative allowlist for a session reference: real ids are `ses_<hex>`,
 * demo ids are slugs. Anything else is rejected before it can reach the store
 * or the URL, so a hand-crafted link can never smuggle arbitrary strings in.
 */
const SESSION_REF_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export function isValidSessionRef(value: string | null | undefined): value is string {
  return typeof value === 'string' && SESSION_REF_PATTERN.test(value);
}

const MISSING_SESSION_MESSAGE = '通知对应的会话不存在或已被删除，无法跳转';
const LIST_UNAVAILABLE_MESSAGE = '会话列表暂时无法加载，未能打开该会话';
const ROUTE_FAILED_MESSAGE = '无法返回聊天视图，请手动打开 Pan 聊天';

/** Monotonic intent id: every notification click supersedes earlier ones. */
let clickIntent = 0;

function locationKey(): string {
  return `${window.location.pathname}${window.location.search}`;
}

type SessionLookup = 'found' | 'missing' | 'unknown';

/** Resolve once no session-list refresh is in flight (bounded wait). */
function settleLoads(): Promise<void> {
  const store = useSessionStore;
  if (!store.getState().sessionsLoading) return Promise.resolve();
  return new Promise<void>((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      unsubscribe();
      clearTimeout(timer);
      resolve();
    };
    const unsubscribe = store.subscribe((s) => {
      if (!s.sessionsLoading) finish();
    });
    const timer = setTimeout(finish, 15000);
    if (!store.getState().sessionsLoading) finish();
  });
}

/**
 * Authoritative lookup. Returns:
 *  - 'found'   the session is in the (cached or freshly fetched) list;
 *  - 'missing' a completed list fetch — ours, latest, same server epoch —
 *              did not contain it (real deletion);
 *  - 'unknown' the list could not be refreshed (network failure, superseded
 *              refresh, server restart) — callers must NOT claim deletion.
 *
 * The app refreshes the list on its own (initial load, WS events, focus
 * recovery); a concurrent refresh can supersede ours (loadSessions resolves
 * without applying the response). That is not a verdict, so the lookup waits
 * for in-flight loads, and when superseded retries once with a clean
 * sequence before giving up.
 */
async function lookupSessionRef(sessionId: string): Promise<SessionLookup> {
  const store = useSessionStore;
  const has = () => store.getState().sessions.some((session) => session.id === sessionId);
  if (has()) return 'found';
  await settleLoads();
  if (has()) return 'found';
  for (let round = 0; round < 2; round += 1) {
    const before = store.getState();
    const expectedSeq = before._loadSeq + 1;
    const epochBefore = before.serverEpoch;
    try {
      await store.getState().loadSessions({ throwOnError: true });
    } catch {
      return 'unknown';
    }
    if (has()) return 'found';
    const after = store.getState();
    const epochStable = !(epochBefore !== null && after.serverEpoch !== epochBefore);
    if (after._loadSeq === expectedSeq && !after.sessionsLoading && epochStable) {
      return 'missing';
    }
    // Superseded by another refresh: let it settle and try once more.
    await settleLoads();
    if (has()) return 'found';
  }
  return 'unknown';
}

/** Router-singleton navigation, lazily imported to stay out of its cycle. */
async function navigateToChat(): Promise<boolean> {
  try {
    const { router } = await import('@/router');
    await router.navigate('/');
    return true;
  } catch {
    return false;
  }
}

/**
 * Notification click handler: focus Pan, return to the chat view and select
 * the session. Resolves to true only when the routing actually completed and
 * the selection was applied; false means "not applied" (invalid or deleted
 * session, unavailable list, user took over, or router failure). Every
 * failure mode except an overtaken intent is surfaced with a toast; an
 * overtaken intent is silently dropped because a newer action already won.
 */
export async function openSessionFromNotification(sessionId: string): Promise<boolean> {
  if (!isValidSessionRef(sessionId)) return false;
  const intent = ++clickIntent;
  const sessionAtStart = useSessionStore.getState().currentSessionId;
  const startLocation = locationKey();

  const lookup = await lookupSessionRef(sessionId);
  if (intent !== clickIntent) return false; // a newer click superseded us
  if (lookup === 'missing') {
    useUIStore.getState().showToast(MISSING_SESSION_MESSAGE, 'warning');
    return false;
  }
  if (lookup === 'unknown') {
    // The refresh failed: deletion is NOT proven. Stay honest and let the
    // user retry (the notification is still in the system tray).
    useUIStore.getState().showToast(LIST_UNAVAILABLE_MESSAGE, 'warning');
    return false;
  }
  // While we were resolving, the user may have taken over: another session
  // selected or another view opened. Respect the later intent.
  if (useSessionStore.getState().currentSessionId !== sessionAtStart) return false;
  if (locationKey() !== startLocation) return false;

  try {
    // Same-origin windows may be focused from the notification click listener
    // (the spec encourages focus() from within the click event). Best effort:
    // a blocked focus must not break the jump.
    window.focus();
  } catch {
    // ignore
  }

  if (!(await navigateToChat())) {
    useUIStore.getState().showToast(ROUTE_FAILED_MESSAGE, 'error');
    return false;
  }
  // navigateToChat settled means the router applied the navigation; the
  // remaining selectSession state update below is synchronous (its history
  // fetch keeps its own _selectionSeq guards).
  void useSessionStore.getState().selectSession(sessionId);
  return true;
}

/**
 * Cold start: `?session=<id>` selects the session once the session list has
 * loaded, then removes only that parameter. Distinctions that matter:
 *  - a cache miss triggers ONE authoritative list fetch;
 *  - when that fetch fails the parameter is kept (no false "deleted") and the
 *    selection recovers as soon as a later refresh brings the session in;
 *  - only a completed list fetch without the id reports deletion.
 */
export function useSessionDeepLinkParam(): void {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get(SESSION_DEEPLINK_PARAM);
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const retry = useRef({ raw: '', attempted: false, warned: false });

  const clearParam = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete(SESSION_DEEPLINK_PARAM);
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    if (!raw) return;
    if (!isValidSessionRef(raw)) {
      useUIStore.getState().showToast('会话链接无效', 'warning');
      clearParam();
      return;
    }
    if (sessions.some((session) => session.id === raw)) {
      // selectSession holds the session it was given; its internal selection
      // generation guards make a later user pick win over its late history
      // fetch, so no extra guard is needed here.
      void useSessionStore.getState().selectSession(raw);
      clearParam();
      return;
    }
    // Cache miss. Do not conclude from `sessionsLoading`/`_loadSeq` alone: a
    // failed refresh settles both exactly like a successful one. Run at most
    // one authoritative lookup per parameter value; if it fails, stay patient
    // — a later successful refresh populates the cache and resolves this.
    const state = retry.current;
    if (state.raw !== raw) {
      state.raw = raw;
      state.attempted = false;
      state.warned = false;
    }
    if (state.attempted || sessionsLoading) return;
    state.attempted = true;
    void (async () => {
      const lookup = await lookupSessionRef(raw);
      if (retry.current.raw !== raw) return; // parameter changed meanwhile
      if (lookup === 'found') {
        void useSessionStore.getState().selectSession(raw);
        clearParam();
        return;
      }
      if (lookup === 'missing') {
        useUIStore.getState().showToast(MISSING_SESSION_MESSAGE, 'warning');
        clearParam();
        return;
      }
      if (!retry.current.warned) {
        retry.current.warned = true;
        useUIStore.getState().showToast(LIST_UNAVAILABLE_MESSAGE, 'warning');
      }
    })();
  }, [raw, sessions, sessionsLoading, clearParam]);
}
