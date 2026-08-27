import { create } from 'zustand';
import type { ToastMessage } from '@/types';

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
    return v === 'name' ? 'name' : 'recent';
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

// ── Store ──

export type GroupMode = 'none' | 'workdir' | 'manager';
export type SortMode = 'recent' | 'name';
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
  bubbleViewEnabled: boolean;
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  /** Mobile drawer (hamburger) open state — store-backed so route changes /
   *  other components (e.g. the session Manage page) can close it. */
  mobileSidebarOpen: boolean;
  groupBy: GroupMode;
  searchQuery: string;
  sortBy: SortMode;
  collapsedGroups: Set<string>;
  filesCollapsed: boolean;
  theme: Theme;

  showToast: (message: string, type?: ToastMessage['type']) => void;
  dismissToast: (id: string) => void;
  toggleBubbleView: () => void;
  setSidebarWidth: (w: number) => void;
  toggleSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setGroupBy: (mode: GroupMode) => void;
  cycleGroupBy: () => void;
  setSearchQuery: (q: string) => void;
  setSortBy: (mode: SortMode) => void;
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
  bubbleViewEnabled: true,
  sidebarWidth: loadSidebarWidth(),
  sidebarCollapsed: loadSidebarCollapsed(),
  mobileSidebarOpen: false,
  groupBy: loadGroupBy(),
  searchQuery: '',
  sortBy: loadSortBy(),
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

  toggleBubbleView: () => {
    set((s) => ({ bubbleViewEnabled: !s.bubbleViewEnabled }));
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
