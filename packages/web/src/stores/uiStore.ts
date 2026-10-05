import { create } from 'zustand';
import type {
  ApprovalRequest,
  ElicitationRequest,
  TerminalInteraction,
  ToastMessage,
  UserInputRequest,
} from '@/types';
import type { SpecialFilterId } from '@/utils/sessionFilters';
import { updateUiSettings } from '@/services/api';

// ── localStorage helpers ──

function loadSidebarWidth(): number {
  try {
    const v = localStorage.getItem('pan:sidebarWidth');
    const n = v ? parseInt(v, 10) : 280;
    return Math.max(280, Math.min(480, n));
  } catch {
    return 280;
  }
}

function persistSidebarWidth(w: number) {
  try {
    localStorage.setItem('pan:sidebarWidth', String(w));
  } catch {
    // no-op
  }
}

function loadSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem('pan:sidebarCollapsed') === '1';
  } catch {
    return false;
  }
}

function persistSidebarCollapsed(c: boolean) {
  try {
    localStorage.setItem('pan:sidebarCollapsed', c ? '1' : '0');
  } catch {
    // no-op
  }
}

function loadGroupBy(): GroupMode {
  try {
    const v = localStorage.getItem('pan:groupBy');
    if (v === 'workdir' || v === 'manager') return v;
    return 'none';
  } catch {
    return 'none';
  }
}

function persistGroupBy(mode: GroupMode) {
  try {
    localStorage.setItem('pan:groupBy', mode);
  } catch {
    // no-op
  }
}

function loadSortBy(): SortMode {
  try {
    const v = localStorage.getItem('pan:sortBy');
    return v === 'name' || v === 'custom' ? v : 'recent';
  } catch {
    return 'recent';
  }
}

function persistSortBy(mode: SortMode) {
  try {
    localStorage.setItem('pan:sortBy', mode);
  } catch {
    // no-op
  }
}

function loadDragEnabled(): boolean {
  try {
    const v = localStorage.getItem('pan:dragEnabled');
    return v === null ? true : v === 'true';
  } catch {
    return true;
  }
}

function persistDragEnabled(enabled: boolean) {
  try {
    localStorage.setItem('pan:dragEnabled', String(enabled));
  } catch {
    // no-op
  }
}

/** Manual session order for the "custom" sort mode (drag-reorder result). */
function loadCustomOrder(): string[] {
  try {
    const v = localStorage.getItem('pan:customOrder');
    if (!v) return [];
    const arr: unknown = JSON.parse(v);
    if (!Array.isArray(arr)) return [];
    return arr.filter((x): x is string => typeof x === 'string');
  } catch {
    return [];
  }
}

function persistCustomOrder(order: string[]) {
  try {
    localStorage.setItem('pan:customOrder', JSON.stringify(order));
  } catch {
    // no-op
  }
}

const HIDDEN_SESSIONS_KEY = 'pan:hiddenSessions';

/**
 * Active workspace scope for the session list ('all' | 'ungrouped' | ws id).
 * Persisted so a reload lands back in the same workspace; the rail falls back
 * to 'all' when the remembered workspace no longer exists.
 */
function loadActiveWorkspaceId(): string {
  try {
    return localStorage.getItem('pan:activeWorkspaceId') || 'all';
  } catch {
    return 'all';
  }
}

function persistActiveWorkspaceId(id: string) {
  try {
    localStorage.setItem('pan:activeWorkspaceId', id);
  } catch {
    // no-op
  }
}

/** Workspace rail: collapsed = floating handle only; expanded = full panel. */
function loadRailExpanded(): boolean {
  try {
    return localStorage.getItem('pan:railExpanded') === '1';
  } catch {
    return false;
  }
}

function persistRailExpanded(expanded: boolean) {
  try {
    localStorage.setItem('pan:railExpanded', expanded ? '1' : '0');
  } catch {
    // no-op
  }
}

/** Session ids hidden via Select mode, persisted per session id across reloads. */
function loadHiddenSessions(): Set<string> {
  try {
    const v = localStorage.getItem(HIDDEN_SESSIONS_KEY);
    if (!v) return new Set();
    const arr: unknown = JSON.parse(v);
    if (!Array.isArray(arr)) return new Set();
    return new Set(arr.filter((x): x is string => typeof x === 'string'));
  } catch {
    return new Set();
  }
}

function persistHiddenSessions(ids: Set<string>) {
  try {
    localStorage.setItem(HIDDEN_SESSIONS_KEY, JSON.stringify([...ids]));
  } catch {
    // no-op
  }
}

const COLLAPSED_GROUPS_KEY = 'pan:collapsedGroups';

/**
 * Collapsed group keys (manager mode: session ids; workdir mode: normalized
 * workdir paths). localStorage is the synchronous first-paint cache so a
 * reload renders the remembered state with zero delay; the server copy lives
 * in config.json's `ui.collapsedGroups` (via the existing GET/PUT
 * /api/settings/ui endpoints) so the state also survives restarts and is
 * shared across browsers.
 */
function loadCollapsedGroups(): Set<string> {
  try {
    const v = localStorage.getItem(COLLAPSED_GROUPS_KEY);
    if (!v) return new Set();
    const arr: unknown = JSON.parse(v);
    if (!Array.isArray(arr)) return new Set();
    return new Set(arr.filter((x): x is string => typeof x === 'string'));
  } catch {
    return new Set();
  }
}

function persistCollapsedGroupsCache(ids: Set<string>) {
  try {
    localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify([...ids]));
  } catch {
    // no-op
  }
}

// ── Collapsed-groups server writeback (merged, single in-flight) ──
// Every mutation writes the localStorage cache synchronously and schedules ONE
// merged PUT of the whole set. State machine:
//   - Coalescing / latest-wins: at most one write per delay window and never
//     more than one request in flight; changes that arrive during a flight
//     collapse into a single trailing write of the latest state.
//   - Bounded failure retry: a failed PUT retries (same window) until
//     MAX_WRITE_FAILURES consecutive failures within the current burst, then
//     the burst gives up silently until the next user action starts a new
//     one. A user action ALWAYS resets the failure counter (new burst), so
//     sustained user activity keeps retrying with a fresh budget each time.
//   - No spurious requests: a successful write with nothing pending schedules
//     nothing.
// The in-memory and localStorage values stay authoritative throughout; no
// per-node requests, no polling.
const COLLAPSED_GROUPS_WRITE_DELAY_MS = 600;
const COLLAPSED_GROUPS_MAX_WRITE_FAILURES = 3;

let collapsedWriteInFlight = false;
let collapsedWritePending = false;
let collapsedWriteTimer: ReturnType<typeof setTimeout> | null = null;
/** Consecutive failed PUTs in the current burst (reset by user actions and by
 *  any success). */
let collapsedWriteFailures = 0;
/** Set by user-driven mutations so a late startup GET can never clobber them. */
let collapsedUserDirty = false;
/**
 * Latch: true once the startup settings GET outcome is known — hydrated from
 * the server, key absent, or load failed. Automatic maintenance writes
 * (prune) are blocked until then, because before that the local set may be
 * older than the server's and writing it back could clobber the not-yet-
 * hydrated authoritative state. User actions are exempt: they carry user
 * intent and stay protected against late GETs by collapsedUserDirty.
 */
let collapsedServerStateResolved = false;

function scheduleCollapsedGroupsWrite() {
  if (collapsedWriteTimer !== null) return; // already scheduled; latest state wins
  collapsedWriteTimer = setTimeout(flushCollapsedGroupsWrite, COLLAPSED_GROUPS_WRITE_DELAY_MS);
}

function flushCollapsedGroupsWrite() {
  collapsedWriteTimer = null;
  if (collapsedWriteInFlight) {
    collapsedWritePending = true;
    return;
  }
  collapsedWriteInFlight = true;
  const payload = { collapsedGroups: [...useUIStore.getState().collapsedGroups] };
  void updateUiSettings(payload)
    .then(() => {
      collapsedWriteFailures = 0; // success closes the burst
    })
    .catch(() => {
      collapsedWriteFailures += 1;
    })
    .finally(() => {
      collapsedWriteInFlight = false;
      if (collapsedWritePending) {
        collapsedWritePending = false;
        // Changes arrived during the flight: one trailing write of the latest
        // state. Bounded by the same budget — an exhausted burst is not
        // extended; the next user action re-arms it.
        if (collapsedWriteFailures < COLLAPSED_GROUPS_MAX_WRITE_FAILURES) {
          scheduleCollapsedGroupsWrite();
        }
      } else if (
        collapsedWriteFailures > 0 &&
        collapsedWriteFailures < COLLAPSED_GROUPS_MAX_WRITE_FAILURES
      ) {
        // Failed with nothing new pending: bounded retry of the same state.
        scheduleCollapsedGroupsWrite();
      }
      // Else: success with nothing pending (no request), or the burst
      // exhausted its failure budget (next user action starts a new one).
    });
}

function persistCollapsedGroups(ids: Set<string>) {
  persistCollapsedGroupsCache(ids);
  scheduleCollapsedGroupsWrite();
}

/** User-driven mutation: starts a fresh burst (resets the failure budget) in
 *  addition to the cache write and the coalesced server write. */
function persistCollapsedGroupsAfterUserAction(ids: Set<string>) {
  collapsedWriteFailures = 0;
  collapsedUserDirty = true;
  persistCollapsedGroups(ids);
}

// ── Store ──

export type GroupMode = 'none' | 'workdir' | 'manager';
/** 'custom' = manual drag order (see customOrder); UI label 自定义排序 / Custom. */
export type SortMode = 'recent' | 'name' | 'custom';
export type Theme = 'dark' | 'light';

export interface ChatAttachmentRequest {
  sessionId: string;
  path: string;
}

function loadTheme(): Theme {
  try {
    const v = localStorage.getItem('pan:theme');
    return v === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

function persistTheme(t: Theme) {
  try {
    localStorage.setItem('pan:theme', t);
  } catch {
    // no-op
  }
}

interface UIStore {
  toastQueue: ToastMessage[];
  approvalRequests: ApprovalRequest[];
  userInputRequests: UserInputRequest[];
  elicitationRequests: ElicitationRequest[];
  terminalInteractions: TerminalInteraction[];
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  /** Mobile drawer (hamburger) open state — store-backed so route changes /
   *  other components (e.g. the session Manage page) can close it. */
  mobileSidebarOpen: boolean;
  groupBy: GroupMode;
  searchQuery: string;
  sortBy: SortMode;
  /** Whether session drag/reorder affordances are enabled. Persisted. */
  dragEnabled: boolean;
  /** Manual session-id order backing the 'custom' sort mode (drag reorder).
   *  Ids not present keep their current relative order after the mapped ones. */
  customOrder: string[];
  /** Active special filters (see utils/sessionFilters); composable with the
   *  text search and cleared individually. Not persisted (like searchQuery). */
  specialFilters: Set<SpecialFilterId>;
  /** Sessions hidden via Select mode's eye button, keyed by session id.
   *  Persisted to localStorage so hiding survives refreshes/reloads. */
  hiddenSessionIds: Set<string>;
  /** Collapsed group keys (manager: session ids; workdir: normalized paths).
   *  Persisted to the localStorage cache and to config.json's
   *  `ui.collapsedGroups` so collapse state survives refreshes/restarts. */
  collapsedGroups: Set<string>;
  theme: Theme;
  /** Active workspace scope for the session list ('all' | 'ungrouped' | ws id). */
  activeWorkspaceId: string;
  /** Workspace rail panel expanded (false = the floating handle only). */
  railExpanded: boolean;
  /** One-shot requests from the editor to the mounted chat composer. */
  chatAttachmentRequests: ChatAttachmentRequest[];
  /** Monotonic token bumped to ask the mounted chat composer for focus. */
  composerFocusToken: number;

  showToast: (message: string, type?: ToastMessage['type']) => void;
  dismissToast: (id: string) => void;
  addApprovalRequest: (request: ApprovalRequest) => void;
  removeApprovalRequest: (sessionId: string, requestId: string | number) => void;
  clearApprovalRequests: (sessionId: string) => void;
  addUserInputRequest: (request: UserInputRequest) => void;
  removeUserInputRequest: (sessionId: string, requestId: string | number) => void;
  clearUserInputRequests: (sessionId: string) => void;
  addElicitationRequest: (request: ElicitationRequest) => void;
  removeElicitationRequest: (sessionId: string, requestId: string | number) => void;
  clearElicitationRequests: (sessionId: string) => void;
  addTerminalInteraction: (interaction: TerminalInteraction) => void;
  removeTerminalInteraction: (sessionId: string, itemId: string) => void;
  clearTerminalInteractions: (sessionId: string) => void;
  setSidebarWidth: (w: number) => void;
  toggleSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setGroupBy: (mode: GroupMode) => void;
  cycleGroupBy: () => void;
  setSearchQuery: (q: string) => void;
  setSortBy: (mode: SortMode) => void;
  /** Cycle recent → name → custom → recent (sidebar Sort button). */
  cycleSortBy: () => void;
  setDragEnabled: (enabled: boolean) => void;
  /** Replace the manual custom order (persisted). */
  setCustomOrder: (order: string[]) => void;
  toggleSpecialFilter: (id: SpecialFilterId) => void;
  clearSpecialFilters: () => void;
  /** Mark a session hidden (Select mode eye button) or shown again. */
  setSessionHidden: (id: string, hidden: boolean) => void;  /** Drop hidden ids that no longer correspond to a live session. */
  pruneHiddenSessions: (validIds: Set<string>) => void;
  toggleGroupCollapse: (key: string) => void;
  /** Collapse every key in the operation scope (union): scope nodes all end up
   *  collapsed in one store update while keys outside the scope (sessions
   *  filtered out by search/filters/workspace or hidden) keep their state. */
  collapseAllGroups: (keys: string[]) => void;
  /** Expand every key in the operation scope (removal): out-of-scope collapse
   *  preferences are preserved. */
  expandAllGroups: (keys: string[]) => void;
  /** Drop collapsed keys that no longer correspond to a live group/session
   *  (e.g. stale `__pending_*` placeholders or deleted sessions), keeping the
   *  set consistent with the current tree. */
  pruneCollapsedGroups: (validKeys: Set<string>) => void;
  /** Apply the server-persisted collapse set from the startup settings GET.
   *  Always called once the GET settles: an explicit array — including empty —
   *  is authoritative (applied unless the user already toggled something); an
   *  absent/invalid key or a failed load passes `undefined`, which keeps the
   *  local state. Either outcome marks the server state as resolved, lifting
   *  the automatic-maintenance write-back gate. */
  hydrateCollapsedGroupsFromServer: (ids: string[] | undefined) => void;
  toggleTheme: () => void;
  /** Switch the session-list scope (callers clear the multi-select selection). */
  setActiveWorkspace: (id: string) => void;
  setRailExpanded: (expanded: boolean) => void;
  requestChatAttachment: (sessionId: string, path: string) => void;
  consumeChatAttachmentRequests: (sessionId: string, paths: string[]) => void;
  requestComposerFocus: () => void;
}

let toastCounter = 0;

export const useUIStore = create<UIStore>((set, get) => ({
  toastQueue: [],
  approvalRequests: [],
  userInputRequests: [],
  elicitationRequests: [],
  terminalInteractions: [],
  sidebarWidth: loadSidebarWidth(),
  sidebarCollapsed: loadSidebarCollapsed(),
  mobileSidebarOpen: false,
  groupBy: loadGroupBy(),
  searchQuery: '',
  sortBy: loadSortBy(),
  dragEnabled: loadDragEnabled(),
  customOrder: loadCustomOrder(),
  specialFilters: new Set<SpecialFilterId>(),
  hiddenSessionIds: loadHiddenSessions(),
  collapsedGroups: loadCollapsedGroups(),
  theme: loadTheme(),
  activeWorkspaceId: loadActiveWorkspaceId(),
  railExpanded: loadRailExpanded(),
  chatAttachmentRequests: [],
  composerFocusToken: 0,

  showToast: (message, type = 'info') => {
    const id = `toast-${++toastCounter}`;
    set((s) => ({
      toastQueue: [...s.toastQueue, { id, message, type }],
    }));
    // Auto-dismiss is scheduled by ToastContainer so the exit animation
    // can play before removal.
  },

  dismissToast: (id) => {
    set((s) => ({
      toastQueue: s.toastQueue.filter((t) => t.id !== id),
    }));
  },

  addApprovalRequest: (request) => {
    set((s) => ({
      approvalRequests: [
        ...s.approvalRequests.filter(
          (item) => !(item.sessionId === request.sessionId && item.requestId === request.requestId),
        ),
        request,
      ],
    }));
  },

  removeApprovalRequest: (sessionId, requestId) => {
    set((s) => ({
      approvalRequests: s.approvalRequests.filter(
        (item) => !(item.sessionId === sessionId && item.requestId === requestId),
      ),
    }));
  },

  clearApprovalRequests: (sessionId) => {
    set((s) => ({
      approvalRequests: s.approvalRequests.filter((item) => item.sessionId !== sessionId),
    }));
  },

  addUserInputRequest: (request) => {
    set((s) => ({
      userInputRequests: [
        ...s.userInputRequests.filter(
          (item) => !(item.sessionId === request.sessionId && item.requestId === request.requestId),
        ),
        request,
      ],
    }));
  },

  removeUserInputRequest: (sessionId, requestId) => {
    set((s) => ({
      userInputRequests: s.userInputRequests.filter(
        (item) => !(item.sessionId === sessionId && item.requestId === requestId),
      ),
    }));
  },

  clearUserInputRequests: (sessionId) => {
    set((s) => ({
      userInputRequests: s.userInputRequests.filter((item) => item.sessionId !== sessionId),
    }));
  },

  addElicitationRequest: (request) => {
    set((s) => ({
      elicitationRequests: [
        ...s.elicitationRequests.filter(
          (item) => !(item.sessionId === request.sessionId && item.requestId === request.requestId),
        ),
        request,
      ],
    }));
  },

  removeElicitationRequest: (sessionId, requestId) => {
    set((s) => ({
      elicitationRequests: s.elicitationRequests.filter(
        (item) => !(item.sessionId === sessionId && item.requestId === requestId),
      ),
    }));
  },

  clearElicitationRequests: (sessionId) => {
    set((s) => ({
      elicitationRequests: s.elicitationRequests.filter((item) => item.sessionId !== sessionId),
    }));
  },

  addTerminalInteraction: (interaction) => {
    set((s) => ({
      terminalInteractions: [
        ...s.terminalInteractions.filter(
          (item) => !(item.sessionId === interaction.sessionId && item.itemId === interaction.itemId),
        ),
        interaction,
      ],
    }));
  },

  removeTerminalInteraction: (sessionId, itemId) => {
    set((s) => ({
      terminalInteractions: s.terminalInteractions.filter(
        (item) => !(item.sessionId === sessionId && item.itemId === itemId),
      ),
    }));
  },

  clearTerminalInteractions: (sessionId) => {
    set((s) => ({
      terminalInteractions: s.terminalInteractions.filter((item) => item.sessionId !== sessionId),
    }));
  },

  setSidebarWidth: (w) => {
    const clamped = Math.max(280, Math.min(480, Math.round(w)));
    set({ sidebarWidth: clamped });
    persistSidebarWidth(clamped);
  },

  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    set({ sidebarCollapsed: next });
    persistSidebarCollapsed(next);
  },

  setMobileSidebarOpen: (open) => {
    set({ mobileSidebarOpen: open });
  },

  setGroupBy: (mode) => {
    set({ groupBy: mode });
    persistGroupBy(mode);
  },

  // Cycle grouping mode: workdir → manager → none → workdir ...
  cycleGroupBy: () => {
    const order: GroupMode[] = ['workdir', 'manager', 'none'];
    const next = order[(order.indexOf(get().groupBy) + 1) % order.length]!;
    set({ groupBy: next });
    persistGroupBy(next);
  },

  setSearchQuery: (q) => {
    set({ searchQuery: q });
  },

  setSortBy: (mode) => {
    set({ sortBy: mode });
    persistSortBy(mode);
  },

  cycleSortBy: () => {
    const order: SortMode[] = ['recent', 'name', 'custom'];
    const next = order[(order.indexOf(get().sortBy) + 1) % order.length]!;
    set({ sortBy: next });
    persistSortBy(next);
  },

  setDragEnabled: (enabled) => {
    set({ dragEnabled: enabled });
    persistDragEnabled(enabled);
  },

  setCustomOrder: (order) => {
    set({ customOrder: [...order] });
    persistCustomOrder(order);
  },

  toggleSpecialFilter: (id) => {
    set((s) => {
      const next = new Set(s.specialFilters);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return { specialFilters: next };
    });
  },

  clearSpecialFilters: () => {
    set({ specialFilters: new Set() });
  },

  setSessionHidden: (id, hidden) => {
    set((s) => {
      const next = new Set(s.hiddenSessionIds);
      if (hidden) {
        next.add(id);
      } else {
        next.delete(id);
      }
      persistHiddenSessions(next);
      return { hiddenSessionIds: next };
    });
  },

  pruneHiddenSessions: (validIds) => {
    set((s) => {
      let changed = false;
      const next = new Set<string>();
      for (const id of s.hiddenSessionIds) {
        if (validIds.has(id)) {
          next.add(id);
        } else {
          changed = true;
        }
      }
      if (!changed) return s;
      persistHiddenSessions(next);
      return { hiddenSessionIds: next };
    });
  },

  toggleGroupCollapse: (key) => {
    set((s) => {
      const next = new Set(s.collapsedGroups);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      persistCollapsedGroupsAfterUserAction(next);
      return { collapsedGroups: next };
    });
  },

  collapseAllGroups: (keys) => {
    set((s) => {
      const next = new Set(s.collapsedGroups);
      for (const k of keys) next.add(k);
      if (next.size === s.collapsedGroups.size) return s; // nothing new to collapse
      persistCollapsedGroupsAfterUserAction(next);
      return { collapsedGroups: next };
    });
  },

  expandAllGroups: (keys) => {
    set((s) => {
      const next = new Set(s.collapsedGroups);
      let changed = false;
      for (const k of keys) {
        if (next.delete(k)) changed = true;
      }
      if (!changed) return s;
      persistCollapsedGroupsAfterUserAction(next);
      return { collapsedGroups: next };
    });
  },

  pruneCollapsedGroups: (validKeys) => {
    set((s) => {
      let changed = false;
      const next = new Set<string>();
      for (const k of s.collapsedGroups) {
        if (validKeys.has(k)) {
          next.add(k);
        } else {
          changed = true;
        }
      }
      // Returning the same state skips Zustand's merge and subscriber fan-out.
      // Session summaries can trigger this maintenance on every durable update.
      if (!changed) return s;
      // Automatic maintenance, not a user action: it must not raise
      // collapsedUserDirty (that would wrongly skip a concurrent startup GET),
      // and its write-back is blocked until the startup GET outcome is known —
      // before that the local set may be older than the server's and writing
      // it back could clobber the not-yet-hydrated authoritative state. The
      // in-memory prune still applies so stale placeholders disappear at once.
      if (collapsedServerStateResolved) {
        persistCollapsedGroups(next);
      }
      return { collapsedGroups: next };
    });
  },

  hydrateCollapsedGroupsFromServer: (ids) => {
    // The startup GET outcome is now determined either way; automatic
    // maintenance write-back may proceed from here on.
    collapsedServerStateResolved = true;
    // `undefined` / non-array = no usable server state (the key was never
    // saved, or the load failed) → the local state stays authoritative.
    // An explicit array — including empty — is authoritative: the user
    // expanded all groups in some browser.
    if (!Array.isArray(ids)) return;
    if (collapsedUserDirty) return; // user acted while the GET was in flight
    const next = new Set(ids);
    const current = get().collapsedGroups;
    if (current.size === next.size && [...current].every((k) => next.has(k))) return;
    persistCollapsedGroupsCache(next);
    set({ collapsedGroups: next });
  },

  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark';
    set({ theme: next });
    persistTheme(next);
  },

  setActiveWorkspace: (id) => {
    if (get().activeWorkspaceId === id) return;
    set({ activeWorkspaceId: id });
    persistActiveWorkspaceId(id);
  },

  setRailExpanded: (expanded) => {
    set({ railExpanded: expanded });
    persistRailExpanded(expanded);
  },

  requestChatAttachment: (sessionId, path) => {
    if (!sessionId || !path) return;
    set((s) => s.chatAttachmentRequests.some((request) =>
      request.sessionId === sessionId && request.path === path,
    ) ? s : {
      chatAttachmentRequests: [...s.chatAttachmentRequests, { sessionId, path }],
    });
  },

  consumeChatAttachmentRequests: (sessionId, paths) => {
    if (paths.length === 0) return;
    const pathSet = new Set(paths);
    set((s) => ({
      chatAttachmentRequests: s.chatAttachmentRequests.filter((request) =>
        request.sessionId !== sessionId || !pathSet.has(request.path),
      ),
    }));
  },

  requestComposerFocus: () => {
    set((s) => ({ composerFocusToken: s.composerFocusToken + 1 }));
  },
}));
