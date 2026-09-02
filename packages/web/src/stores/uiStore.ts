import { create } from 'zustand';
import type {
  ApprovalRequest,
  ElicitationRequest,
  TerminalInteraction,
  ToastMessage,
  UserInputRequest,
} from '@/types';
import type { SpecialFilterId } from '@/utils/sessionFilters';

// ── localStorage helpers ──

function loadSidebarWidth(): number {
  try {
    const v = localStorage.getItem('pan:sidebarWidth');
    const n = v ? parseInt(v, 10) : 260;
    return Math.max(200, Math.min(480, n));
  } catch {
    return 260;
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

// ── Store ──

export type GroupMode = 'none' | 'workdir' | 'manager';
/** 'custom' = manual drag order (see customOrder); UI label 自定义排序 / Custom. */
export type SortMode = 'recent' | 'name' | 'custom';
export type Theme = 'dark' | 'light';

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
  tuiViewEnabled: boolean;
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  /** Mobile drawer (hamburger) open state — store-backed so route changes /
   *  other components (e.g. the session Manage page) can close it. */
  mobileSidebarOpen: boolean;
  groupBy: GroupMode;
  searchQuery: string;
  sortBy: SortMode;
  /** Manual session-id order backing the 'custom' sort mode (drag reorder).
   *  Ids not present keep their current relative order after the mapped ones. */
  customOrder: string[];
  /** Active special filters (see utils/sessionFilters); composable with the
   *  text search and cleared individually. Not persisted (like searchQuery). */
  specialFilters: Set<SpecialFilterId>;
  /** Sessions hidden via Select mode's eye button, keyed by session id.
   *  Persisted to localStorage so hiding survives refreshes/reloads. */
  hiddenSessionIds: Set<string>;
  collapsedGroups: Set<string>;
  filesCollapsed: boolean;
  theme: Theme;

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
  toggleTuiView: () => void;
  setSidebarWidth: (w: number) => void;
  toggleSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setGroupBy: (mode: GroupMode) => void;
  cycleGroupBy: () => void;
  setSearchQuery: (q: string) => void;
  setSortBy: (mode: SortMode) => void;
  /** Cycle recent → name → custom → recent (sidebar Sort button). */
  cycleSortBy: () => void;
  /** Replace the manual custom order (persisted). */
  setCustomOrder: (order: string[]) => void;
  toggleSpecialFilter: (id: SpecialFilterId) => void;
  clearSpecialFilters: () => void;
  /** Mark a session hidden (Select mode eye button) or shown again. */
  setSessionHidden: (id: string, hidden: boolean) => void;
  /** Drop hidden ids that no longer correspond to a live session. */
  pruneHiddenSessions: (validIds: Set<string>) => void;
  toggleGroupCollapse: (key: string) => void;
  collapseAllGroups: (keys: string[]) => void;
  expandAllGroups: () => void;
  addCollapsedGroups: (ids: string[]) => void;
  removeCollapsedGroups: (ids: string[]) => void;
  /** Drop collapsed keys that no longer correspond to a live group/session
   *  (e.g. stale `__pending_*` placeholders or deleted sessions), keeping the
   *  set consistent with the current tree. */
  pruneCollapsedGroups: (validKeys: Set<string>) => void;
  toggleFilesCollapsed: () => void;
  toggleTheme: () => void;
}

let toastCounter = 0;

export const useUIStore = create<UIStore>((set, get) => ({
  toastQueue: [],
  approvalRequests: [],
  userInputRequests: [],
  elicitationRequests: [],
  terminalInteractions: [],
  // The old names were reversed: this flag now describes the retained TUI
  // branch. The Bubble branch remains available for a future re-enable.
  tuiViewEnabled: true,
  sidebarWidth: loadSidebarWidth(),
  sidebarCollapsed: loadSidebarCollapsed(),
  mobileSidebarOpen: false,
  groupBy: loadGroupBy(),
  searchQuery: '',
  sortBy: loadSortBy(),
  customOrder: loadCustomOrder(),
  specialFilters: new Set<SpecialFilterId>(),
  hiddenSessionIds: loadHiddenSessions(),
  collapsedGroups: new Set<string>(),
  filesCollapsed: false,
  theme: loadTheme(),

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

  toggleTuiView: () => {
    set((s) => ({ tuiViewEnabled: !s.tuiViewEnabled }));
  },

  setSidebarWidth: (w) => {
    const clamped = Math.max(200, Math.min(480, Math.round(w)));
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
      if (!changed) return {};
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
      return { collapsedGroups: next };
    });
  },

  collapseAllGroups: (keys) => {
    set({ collapsedGroups: new Set(keys) });
  },

  expandAllGroups: () => {
    set({ collapsedGroups: new Set() });
  },

  // Additive/removal collapse for recursive manager-group toggling.
  addCollapsedGroups: (ids) => {
    set((s) => {
      const next = new Set(s.collapsedGroups);
      for (const id of ids) next.add(id);
      return { collapsedGroups: next };
    });
  },

  removeCollapsedGroups: (ids) => {
    set((s) => {
      const next = new Set(s.collapsedGroups);
      for (const id of ids) next.delete(id);
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
      if (!changed) return {};
      return { collapsedGroups: next };
    });
  },

  toggleFilesCollapsed: () => {
    set((s) => ({ filesCollapsed: !s.filesCollapsed }));
  },

  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark';
    set({ theme: next });
    persistTheme(next);
  },
}));
