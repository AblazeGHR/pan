import { create } from 'zustand';
import type { GroupMode } from '@/stores/uiStore';
import { fetchUiSettings, updateUiSettings } from '@/services/api';

export interface AppSettings {
  /** Default session-list grouping (mirrors uiStore GroupMode options). */
  defaultGroupBy: GroupMode;
  /** Show meta-agent info (e.g. messages with the `////by agent` prefix). */
  showMetaAgent: boolean;
  /** Show task-agent info (e.g. messages with the `@@@@by agent` prefix). */
  showTaskAgent: boolean;
  /** Show QQ-injected info (e.g. messages with the `@@@@by qq` prefix). */
  showQQ: boolean;
  /** Show the Codex terminal input popup when a process is waiting for stdin. */
  showCodexTerminalInput: boolean;
  /** Notification preferences for CLI adapter warnings. */
  notifications: {
    /** Show structured Codex warning events through a Toast. */
    codexWarningToast: boolean;
  };
}

export const DEFAULT_SETTINGS: AppSettings = {
  defaultGroupBy: 'none',
  showMetaAgent: true,
  showTaskAgent: true,
  showQQ: true,
  showCodexTerminalInput: false,
  notifications: {
    codexWarningToast: true,
  },
};

/**
 * Validate a raw (possibly partial / malformed) settings object, falling back
 * to defaults for missing or wrong-typed fields. Used both when the backend
 * load lands and defensively against any garbage in config.json.
 */
export function sanitizeSettings(
  raw: Record<string, unknown> | null | undefined,
): AppSettings {
  const parsed = raw && typeof raw === 'object' ? raw : {};
  const rawNotifications = parsed.notifications;
  const notifications =
    rawNotifications && typeof rawNotifications === 'object'
      ? rawNotifications as Record<string, unknown>
      : {};
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
    showCodexTerminalInput:
      typeof parsed.showCodexTerminalInput === 'boolean'
        ? parsed.showCodexTerminalInput
        : DEFAULT_SETTINGS.showCodexTerminalInput,
    notifications: {
      codexWarningToast:
        typeof notifications.codexWarningToast === 'boolean'
          ? notifications.codexWarningToast
          : DEFAULT_SETTINGS.notifications.codexWarningToast,
    },
  };
}

interface AppSettingsStore extends AppSettings {
  /** True once the initial GET finished (success or failure). */
  loaded: boolean;
  setDefaultGroupBy: (mode: GroupMode) => void;
  setShowMetaAgent: (v: boolean) => void;
  setShowTaskAgent: (v: boolean) => void;
  setShowQQ: (v: boolean) => void;
  setShowCodexTerminalInput: (v: boolean) => void;
  setCodexWarningToast: (v: boolean) => void;
  /** Reset every field to its default and persist. */
  resetSettings: () => void;
  /** Fetch the persisted ui object from config.json into the store. */
  loadSettings: () => Promise<void>;
}

export const useAppSettingsStore = create<AppSettingsStore>((set) => {
  // Race guard: if the user changes a setting while the initial GET is still
  // in flight, the (possibly stale) server response must not clobber it.
  // Re-armed at the start of every load, so a later load still applies.
  let dirty = false;

  const persist = (patch: Partial<AppSettings>) => {
    dirty = true;
    void updateUiSettings(patch).catch(() => {
      // Best-effort writeback: a backend failure is non-fatal, the in-memory
      // value stays for the current session and is retried next change.
    });
  };

  return {
    ...DEFAULT_SETTINGS,
    loaded: false,

    loadSettings: async () => {
      dirty = false;
      try {
        const ui = await fetchUiSettings();
        if (!dirty) set(sanitizeSettings(ui));
      } catch {
        // Backend unreachable → keep defaults for this session.
      } finally {
        set({ loaded: true });
      }
    },

    setDefaultGroupBy: (mode) => {
      set({ defaultGroupBy: mode });
      persist({ defaultGroupBy: mode });
    },

    setShowMetaAgent: (v) => {
      set({ showMetaAgent: v });
      persist({ showMetaAgent: v });
    },

    setShowTaskAgent: (v) => {
      set({ showTaskAgent: v });
      persist({ showTaskAgent: v });
    },

    setShowQQ: (v) => {
      set({ showQQ: v });
      persist({ showQQ: v });
    },

    setShowCodexTerminalInput: (v) => {
      set({ showCodexTerminalInput: v });
      persist({ showCodexTerminalInput: v });
    },

    setCodexWarningToast: (v) => {
      set((s) => ({
        notifications: {
          ...s.notifications,
          codexWarningToast: v,
        },
      }));
      persist({ notifications: { codexWarningToast: v } });
    },

    resetSettings: () => {
      set({ ...DEFAULT_SETTINGS });
      persist({ ...DEFAULT_SETTINGS });
    },
  };
});
