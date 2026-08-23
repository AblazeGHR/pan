import { create } from 'zustand';
import type { GroupMode } from '@/stores/uiStore';

// ── localStorage helpers ──

const STORAGE_KEY = 'pan:appSettings';

export interface AppSettings {
  /** Default session-list grouping (mirrors uiStore GroupMode options). */
  defaultGroupBy: GroupMode;
  /** Show meta-agent info (e.g. messages with the `////by agent` prefix). */
  showMetaAgent: boolean;
  /** Show task-agent info (e.g. messages with the `@@@@by agent` prefix). */
  showTaskAgent: boolean;
  /** Show QQ-injected info (e.g. messages with the `@@@@by qq` prefix). */
  showQQ: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  defaultGroupBy: 'none',
  showMetaAgent: true,
  showTaskAgent: true,
  showQQ: true,
};

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    return {
      defaultGroupBy:
        parsed.defaultGroupBy === 'workdir' || parsed.defaultGroupBy === 'manager'
          ? parsed.defaultGroupBy
          : DEFAULT_SETTINGS.defaultGroupBy,
      showMetaAgent:
        typeof parsed.showMetaAgent === 'boolean'
          ? parsed.showMetaAgent
          : DEFAULT_SETTINGS.showMetaAgent,
      showTaskAgent:
        typeof parsed.showTaskAgent === 'boolean'
          ? parsed.showTaskAgent
          : DEFAULT_SETTINGS.showTaskAgent,
      showQQ:
        typeof parsed.showQQ === 'boolean'
          ? parsed.showQQ
          : DEFAULT_SETTINGS.showQQ,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function persistSettings(s: AppSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // no-op
  }
}

interface AppSettingsStore extends AppSettings {
  setDefaultGroupBy: (mode: GroupMode) => void;
  setShowMetaAgent: (v: boolean) => void;
  setShowTaskAgent: (v: boolean) => void;
  setShowQQ: (v: boolean) => void;
  /** Reset every field to its default and persist. */
  resetSettings: () => void;
}

export const useAppSettingsStore = create<AppSettingsStore>((set, get) => ({
  ...loadSettings(),

  setDefaultGroupBy: (mode) => {
    set({ defaultGroupBy: mode });
    persistSettings(get());
  },

  setShowMetaAgent: (v) => {
    set({ showMetaAgent: v });
    persistSettings(get());
  },

  setShowTaskAgent: (v) => {
    set({ showTaskAgent: v });
    persistSettings(get());
  },

  setShowQQ: (v) => {
    set({ showQQ: v });
    persistSettings(get());
  },

  resetSettings: () => {
    set({ ...DEFAULT_SETTINGS });
    persistSettings(get());
  },
}));
