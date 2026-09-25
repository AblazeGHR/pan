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
  /** Combine adjacent tool and thinking display blocks under one disclosure. */
  mergeConsecutiveNonBodyBlocks: boolean;
  /**
   * Remember where the reader was when switching back to a Session inside the
   * Chat view. Off by default: selecting a Session always shows its newest
   * message. Leaving and re-entering the Chat route (Editor / Manage / any
   * route) always restores the position, independently of this switch.
   */
  keepScrollOnSessionSwitch: boolean;
  /**
   * Mount the quick-location strip on the chat's right edge. Off by default.
   * Leaving it unmounted also skips its full-history index pass, so sessions
   * open without that cost (one request per 200 messages of the session).
   */
  showMessageNavigationRail: boolean;
  /** Notification preferences for CLI adapter warnings. */
  notifications: {
    /** Show structured Codex warning events through a Toast. */
    codexWarningToast: boolean;
    confirmCrossWorkspaceManagement: boolean;
  };
}

export const DEFAULT_SETTINGS: AppSettings = {
  defaultGroupBy: 'none',
  showMetaAgent: true,
  showTaskAgent: true,
  showQQ: true,
  showCodexTerminalInput: false,
  mergeConsecutiveNonBodyBlocks: false,
  keepScrollOnSessionSwitch: false,
  showMessageNavigationRail: false,
  notifications: {
    codexWarningToast: true,
    confirmCrossWorkspaceManagement: true,
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
    mergeConsecutiveNonBodyBlocks:
      typeof parsed.mergeConsecutiveNonBodyBlocks === 'boolean'
        ? parsed.mergeConsecutiveNonBodyBlocks
        : DEFAULT_SETTINGS.mergeConsecutiveNonBodyBlocks,
    keepScrollOnSessionSwitch:
      typeof parsed.keepScrollOnSessionSwitch === 'boolean'
        ? parsed.keepScrollOnSessionSwitch
        : DEFAULT_SETTINGS.keepScrollOnSessionSwitch,
    showMessageNavigationRail:
      typeof parsed.showMessageNavigationRail === 'boolean'
        ? parsed.showMessageNavigationRail
        : DEFAULT_SETTINGS.showMessageNavigationRail,
    notifications: {
      codexWarningToast:
        typeof notifications.codexWarningToast === 'boolean'
          ? notifications.codexWarningToast
          : DEFAULT_SETTINGS.notifications.codexWarningToast,
      confirmCrossWorkspaceManagement:
        typeof notifications.confirmCrossWorkspaceManagement === 'boolean'
          ? notifications.confirmCrossWorkspaceManagement
          : DEFAULT_SETTINGS.notifications.confirmCrossWorkspaceManagement,
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
  setMergeConsecutiveNonBodyBlocks: (v: boolean) => void;
  setKeepScrollOnSessionSwitch: (v: boolean) => void;
  setShowMessageNavigationRail: (v: boolean) => void;
  setCodexWarningToast: (v: boolean) => void;
  setConfirmCrossWorkspaceManagement: (v: boolean) => void;
  /** Reset every field to its default and persist. */
  resetSettings: () => void;
  /** Fetch the persisted ui object from config.json into the store. */
  loadSettings: () => Promise<void>;
}

type AppSettingsPatch = Omit<Partial<AppSettings>, 'notifications'> & {
  notifications?: Partial<AppSettings['notifications']>;
};

export const useAppSettingsStore = create<AppSettingsStore>((set) => {
  // Race guard: if the user changes a setting while the initial GET is still
  // in flight, the (possibly stale) server response must not clobber it.
  // Re-armed at the start of every load, so a later load still applies.
  let dirty = false;

  const persist = (patch: AppSettingsPatch) => {
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

    setMergeConsecutiveNonBodyBlocks: (v) => {
      set({ mergeConsecutiveNonBodyBlocks: v });
      persist({ mergeConsecutiveNonBodyBlocks: v });
    },

    setKeepScrollOnSessionSwitch: (v) => {
      set({ keepScrollOnSessionSwitch: v });
      persist({ keepScrollOnSessionSwitch: v });
    },

    setShowMessageNavigationRail: (v) => {
      set({ showMessageNavigationRail: v });
      persist({ showMessageNavigationRail: v });
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

    setConfirmCrossWorkspaceManagement: (v) => {
      set((s) => ({ notifications: { ...s.notifications, confirmCrossWorkspaceManagement: v } }));
      persist({ notifications: { confirmCrossWorkspaceManagement: v } });
    },

    resetSettings: () => {
      set({ ...DEFAULT_SETTINGS });
      persist({ ...DEFAULT_SETTINGS });
    },
  };
});
