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
 *  - Every async window (lookup, lazy router import, router navigation, the
 *    workspace-list settle) is re-validated against the latest click intent,
 *    the selection generation, the workspace-selection generation and the
 *    user's own routing. A later user action always wins; comparing
 *    `currentSessionId` alone is not enough (the user can roam A → B → A).
 *  - The jump switches the workspace scope AND selects the session together:
 *    the scope is the session's EFFECTIVE membership (a managed child inherits
 *    its root's workspace), resolved from the latest state after the waits. A
 *    session with no (provable) membership lands in the virtual '无工作区'
 *    scope — a missing root or an unavailable workspace list must never pick an
 *    arbitrary workspace.
 *  - Router failures are surfaced (toast + false), never swallowed.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { UNGROUPED_WORKSPACES, effectiveWorkspaceIds } from '@/utils/sessionFilters';

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

/**
 * Does the browser sit on the chat root for this build? Used only AFTER our
 * own `router.navigate('/')`: the router moves `window.location` itself, so a
 * pathname that is not the chat root means the user navigated away during the
 * await — their later action wins over the pending notification jump.
 */
function isAtChatRoot(): boolean {
  const path = window.location.pathname.replace(/\/+$/, '');
  const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  return path === base;
}

/**
 * Selection generation: `selectSession` is the single writer of
 * `currentSessionId` and bumps the target's `_selectionSeq` entry every time.
 * The summed generation therefore changes for ANY user selection, including
 * re-selecting a session that was already current (A → B → A).
 */
function selectionEpoch(): number {
  const seq = useSessionStore.getState()._selectionSeq;
  let total = 0;
  for (const key of Object.keys(seq)) total += seq[key] ?? 0;
  return total;
}

/**
 * Workspace-selection generation: `setActiveWorkspace` bumps `_workspaceSeq`
 * on every actual switch, so an async jump can tell a user workspace take-over
 * (including A → B → A) apart from "nothing happened", which a value comparison
 * would miss. This is a race guard, not a source of workspace ownership.
 */
function workspaceEpoch(): number {
  return useUIStore.getState()._workspaceSeq ?? 0;
}

/**
 * Resolve the workspace scope a jump must switch to for a session, from the
 * LATEST store state (call only after every async wait):
 *  - ownership is the session's EFFECTIVE membership (`effectiveWorkspaceIds`),
 *    so a managed child inherits its root's workspace and a corrupt/missing-root
 *    chain fails closed to no membership — never the child's own stale ids;
 *  - no membership → the virtual '无工作区' scope;
 *  - a membership id counts only while the workspace list is loaded AND still
 *    contains it (a deleted workspace's leftover id is not a real workspace);
 *  - an unavailable workspace list therefore falls back to '无工作区' — it must
 *    never pick an arbitrary workspace.
 * Returns null only when the target session no longer exists.
 */
function resolveWorkspaceScopeFor(sessionId: string): string | null {
  const sessions = useSessionStore.getState().sessions;
  const session = sessions.find((item) => item.id === sessionId);
  if (!session) return null;
  const [candidate] = effectiveWorkspaceIds(session, sessions);
  if (!candidate) return UNGROUPED_WORKSPACES;
  const workspaceStore = useWorkspaceStore.getState();
  if (workspaceStore.loaded && workspaceStore.workspaces.some((w) => w.id === candidate)) {
    return candidate;
  }
  return UNGROUPED_WORKSPACES;
}

/**
 * Wait for the workspace list to settle before resolving ownership, so a cold
 * start does not mistake a valid membership for a stale one just because the
 * list has not arrived yet. Bounded: a failed load settles (error set) and the
 * caller falls back to a safe scope.
 */
async function settleWorkspaceLoad(): Promise<void> {
  const store = useWorkspaceStore;
  const start = store.getState();
  if (start.loaded) return;
  if (!start.loading) void start.loadWorkspaces();
  await new Promise<void>((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      unsubscribe();
      clearTimeout(timer);
      resolve();
    };
    const unsubscribe = store.subscribe((s) => {
      if (s.loaded || (!s.loading && !!s.error)) finish();
    });
    const timer = setTimeout(finish, 15000);
    const now = store.getState();
    if (now.loaded || (!now.loading && !!now.error)) finish();
  });
}

/**
 * Apply a resolved target atomically: switch the workspace scope to the
 * session's effective workspace AND select the session, so the pair can never
 * come from two different user actions (no cross-session crosstalk). The
 * selection generation guards in `selectSession` keep a later user pick safe.
 */
function commitTarget(sessionId: string): void {
  const scope = resolveWorkspaceScopeFor(sessionId);
  if (scope) useUIStore.getState().setActiveWorkspace(scope);
  void useSessionStore.getState().selectSession(sessionId);
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

/**
 * Notification click handler: focus Pan, return to the chat view, switch to
 * the session's effective workspace and select the session. Resolves to true
 * only when the routing completed and the workspace + selection were applied;
 * false means "not applied" (invalid or deleted session, unavailable list,
 * user took over, or router failure). Every failure mode except a user
 * take-over is surfaced with a toast; a take-over is silently dropped because
 * a newer user action already won.
 *
 * The click flow has four async windows — the authoritative lookup, the lazy
 * router import, the navigation itself, and the workspace-list settle. Each
 * window is re-validated against the click intent, the selection generation,
 * the workspace-selection generation and the location, and the target scope is
 * re-resolved from the latest state right before the final apply, so a click
 * can never overwrite what the user did while it was settling:
 *   1. after the lookup: intent + selection generation + location;
 *   2. after the import, right before navigating: same three;
 *   3. after the navigation: intent + both generations + the router's own
 *      landing point (chat root = our own route change; anywhere else = the
 *      user navigated away and wins);
 *   4. after the workspace-list settle: the same guards again, then the
 *      workspace scope and the session are applied together.
 */
export async function openSessionFromNotification(sessionId: string): Promise<boolean> {
  if (!isValidSessionRef(sessionId)) return false;
  const intent = ++clickIntent;
  const startEpoch = selectionEpoch();
  const startLocation = locationKey();
  const startWorkspaceEpoch = workspaceEpoch();
  const userTookOver = (): boolean =>
    selectionEpoch() !== startEpoch ||
    workspaceEpoch() !== startWorkspaceEpoch ||
    locationKey() !== startLocation;

  const lookup = await lookupSessionRef(sessionId);
  if (intent !== clickIntent) return false; // a newer click superseded us
  if (userTookOver()) return false; // the user acted while we resolved
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

  try {
    // Same-origin windows may be focused from the notification click listener
    // (the spec encourages focus() from within the click event). Best effort:
    // a blocked focus must not break the jump.
    window.focus();
  } catch {
    // ignore
  }

  let router: { navigate: (to: string) => Promise<unknown> };
  try {
    // Lazy import keeps this util out of the router's static cycle; it can
    // still take time (first load/cold cache), so re-validate afterwards.
    ({ router } = await import('@/router'));
  } catch {
    useUIStore.getState().showToast(ROUTE_FAILED_MESSAGE, 'error');
    return false;
  }
  if (intent !== clickIntent) return false;
  if (userTookOver()) return false;

  try {
    await router.navigate('/');
  } catch {
    useUIStore.getState().showToast(ROUTE_FAILED_MESSAGE, 'error');
    return false;
  }
  // Post-navigation: any selection or workspace change (even A → B → A) or a
  // newer click during the await wins. Our own navigation lands on the chat
  // root — if we are anywhere else, the user routed away and wins too.
  if (intent !== clickIntent) return false;
  if (selectionEpoch() !== startEpoch) return false;
  if (workspaceEpoch() !== startWorkspaceEpoch) return false;
  if (!isAtChatRoot()) return false;
  // The workspace list may still be arriving (cold tab): wait for it, then
  // re-validate every guard, so a valid membership is not mistaken for a stale
  // one and a user action during the wait still wins.
  await settleWorkspaceLoad();
  if (intent !== clickIntent) return false;
  if (selectionEpoch() !== startEpoch) return false;
  if (workspaceEpoch() !== startWorkspaceEpoch) return false;
  if (!isAtChatRoot()) return false;
  // Atomic from here: the workspace scope and the session are applied together
  // (selectSession keeps its own _selectionSeq guards for its late history).
  commitTarget(sessionId);
  return true;
}

/**
 * Cold start: `?session=<id>` selects the session once the session list has
 * loaded, then removes only that parameter. Distinctions that matter:
 *  - a cache miss triggers ONE authoritative list fetch;
 *  - when that fetch fails the parameter is kept (no false "deleted") and the
 *    selection recovers as soon as a later refresh brings the session in;
 *  - only a completed list fetch without the id reports deletion;
 *  - every async result is tied to the parameter generation (raw value change
 *    or unmount invalidates it), the selection generation (a user pick during
 *    the lookup consumes the link without overriding the user) and the
 *    workspace-selection generation (a user workspace switch — including
 *    A → B → A — wins the same way);
 *  - the target workspace (the session's effective membership, or the virtual
 *    '无工作区' scope) is applied together with the selection, after the
 *    workspace list settles, so the pair always comes from one resolution.
 */
export function useSessionDeepLinkParam(): void {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get(SESSION_DEEPLINK_PARAM);
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const generation = useRef(0);
  const link = useRef({
    raw: null as string | null,
    baselineEpoch: 0,
    baselineWorkspaceEpoch: 0,
    attempted: false,
    warned: false,
    committed: false,
  });

  // Unmount invalidates every pending continuation for this hook instance.
  useEffect(() => () => { generation.current += 1; }, []);

  const clearParam = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete(SESSION_DEEPLINK_PARAM);
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    if (link.current.raw !== raw) {
      // Parameter generation changed (including → null): everything from the
      // previous raw value must not run any more, and the stale-link budget
      // resets. The baseline epoch marks the user's state when the link appeared.
      generation.current += 1;
      link.current = {
        raw,
        baselineEpoch: selectionEpoch(),
        baselineWorkspaceEpoch: workspaceEpoch(),
        attempted: false,
        warned: false,
        committed: false,
      };
    }
    const myGeneration = generation.current;
    if (!raw) return;
    if (!isValidSessionRef(raw)) {
      useUIStore.getState().showToast('会话链接无效', 'warning');
      clearParam();
      return;
    }
    const userTookOver = (): boolean =>
      selectionEpoch() !== link.current.baselineEpoch ||
      workspaceEpoch() !== link.current.baselineWorkspaceEpoch;
    if (userTookOver()) {
      // The user picked their own session since the link appeared: the link
      // is consumed silently and must never override their choice.
      clearParam();
      return;
    }
    // Resolve the target workspace AND the session together, once the workspace
    // list has settled, re-validating every guard afterwards: a user action
    // during the wait (including a workspace A → B → A) still wins.
    const applyLink = (sessionId: string) => {
      const expectedGeneration = generation.current;
      const expectedRaw = raw;
      void (async () => {
        await settleWorkspaceLoad();
        if (generation.current !== expectedGeneration || link.current.raw !== expectedRaw) return;
        if (userTookOver()) {
          clearParam();
          return;
        }
        commitTarget(sessionId);
        clearParam();
      })();
    };
    if (sessions.some((session) => session.id === raw)) {
      // selectSession holds the session it was given; its internal selection
      // generation guards make a later user pick win over its late history
      // fetch, so no extra guard is needed here. `committed` keeps a later
      // re-run of this effect from applying the same link twice.
      if (!link.current.committed) {
        link.current.committed = true;
        applyLink(raw);
      }
      return;
    }
    // Cache miss. Do not conclude from `sessionsLoading`/`_loadSeq` alone: a
    // failed refresh settles both exactly like a successful one. Run at most
    // one authoritative lookup per parameter value; if it fails, stay patient
    // — a later successful refresh populates the cache and resolves this.
    const state = link.current;
    if (state.attempted || sessionsLoading) return;
    state.attempted = true;
    void (async () => {
      const lookup = await lookupSessionRef(raw);
      // Lifecycle + parameter generation: the parameter was removed/changed,
      // or the component unmounted, while we were looking up.
      if (generation.current !== myGeneration || link.current.raw !== raw) return;
      if (userTookOver()) {
        clearParam();
        return;
      }
      if (lookup === 'found') {
        if (!link.current.committed) {
          link.current.committed = true;
          applyLink(raw);
        }
        return;
      }
      if (lookup === 'missing') {
        useUIStore.getState().showToast(MISSING_SESSION_MESSAGE, 'warning');
        clearParam();
        return;
      }
      if (!link.current.warned) {
        link.current.warned = true;
        useUIStore.getState().showToast(LIST_UNAVAILABLE_MESSAGE, 'warning');
      }
    })();
  }, [raw, sessions, sessionsLoading, clearParam]);
}
