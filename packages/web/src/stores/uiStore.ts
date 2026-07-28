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
    return v === 'workdir' ? 'workdir' : 'none';
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

export type GroupMode = 'none' | 'workdir';
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
  settingsOpen: boolean;
  toastQueue: ToastMessage[];
  bubbleViewEnabled: boolean;
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  groupBy: GroupMode;
  searchQuery: string;
  sortBy: SortMode;
  collapsedGroups: Set<string>;
  filesCollapsed: boolean;
  theme: Theme;

  toggleSettings: () => void;
  closeSettings: () => void;
  showToast: (message: string, type?: ToastMessage['type']) => void;
  dismissToast: (id: string) => void;
  toggleBubbleView: () => void;
  setSidebarWidth: (w: number) => void;
  toggleSidebar: () => void;
  setGroupBy: (mode: GroupMode) => void;
  setSearchQuery: (q: string) => void;
  setSortBy: (mode: SortMode) => void;
  toggleGroupCollapse: (key: string) => void;
  collapseAllGroups: () => void;
  expandAllGroups: () => void;
  toggleFilesCollapsed: () => void;
  toggleTheme: () => void;
}

let toastCounter = 0;

export const useUIStore = create<UIStore>((set, get) => ({
  settingsOpen: false,
  toastQueue: [],
  bubbleViewEnabled: true,
  sidebarWidth: loadSidebarWidth(),
  sidebarCollapsed: loadSidebarCollapsed(),
  groupBy: loadGroupBy(),
  searchQuery: '',
  sortBy: loadSortBy(),
  collapsedGroups: new Set<string>(),
  filesCollapsed: false,
  theme: loadTheme(),

  toggleSettings: () => {
    set((s) => ({ settingsOpen: !s.settingsOpen }));
  },

  closeSettings: () => {
    set({ settingsOpen: false });
  },

  showToast: (message, type = 'info') => {
    const id = `toast-${++toastCounter}`;
    set((s) => ({
      toastQueue: [...s.toastQueue, { id, message, type }],
    }));
    setTimeout(() => {
      get().dismissToast(id);
    }, 3000);
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

  setGroupBy: (mode) => {
    set({ groupBy: mode });
    persistGroupBy(mode);
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

  collapseAllGroups: () => {
    set((s) => {
      // Get all current group keys from store side effects are OK
      return { collapsedGroups: new Set(s.collapsedGroups) };
    });
  },

  expandAllGroups: () => {
    set({ collapsedGroups: new Set() });
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
