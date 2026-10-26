import { create } from 'zustand';
import { isReservedBinding, normalizeBinding } from '@/shortcuts/bindings';
import type { GroupMode } from '@/stores/uiStore';
import { useUIStore } from '@/stores/uiStore';
import { fetchUiSettings, updateUiSettings } from '@/services/api';

export interface CompletionBridgeDefaults {
  system: boolean;
  browser: boolean;
  qqReport: boolean;
  qqSubscribe: boolean;
  qqTargets: string[];
}

export const DEFAULT_COMPLETION_BRIDGE: CompletionBridgeDefaults = {
  system: true, browser: false, qqReport: false, qqSubscribe: false, qqTargets: [],
};

export function sanitizeCompletionBridge(value: unknown): CompletionBridgeDefaults {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  return {
    system: typeof raw.system === 'boolean' ? raw.system : true,
    browser: raw.browser === true,
    qqReport: raw.qqReport === true,
    qqSubscribe: raw.qqSubscribe === true,
    qqTargets: Array.isArray(raw.qqTargets) ? [...new Set(raw.qqTargets.filter((v): v is string => typeof v === 'string' && /^(user|group):[1-9][0-9]*(@[1-9][0-9]*)?$/.test(v)))] : [],
  };
}

/** Behavior → Message auto force send, three independent persistent switches. */
export interface AutoForceSendSettings {
  system: boolean;
  ma: boolean;
  report: boolean;
}

/** Default: every automatic Force source stays off (legacy queue behavior). */
export const DEFAULT_AUTO_FORCE_SEND: AutoForceSendSettings = {
  system: false,
  ma: false,
  report: false,
};

export function sanitizeAutoForceSend(value: unknown): AutoForceSendSettings {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  return {
    system: raw.system === true,
    ma: raw.ma === true,
    report: raw.report === true,
  };
}

/** Scope ids accepted by the backend unread-recording policy. */
export type UnreadRecordingScope = 'bell-on' | 'bell-off' | 'pinned' | 'all' | 'none';

/** Legacy default: every Session records done unread. */
export const DEFAULT_UNREAD_RECORDING: UnreadRecordingScope[] = ['all'];

const UNREAD_RECORDING_SCOPES: readonly UnreadRecordingScope[] = [
  'bell-on', 'bell-off', 'pinned', 'all', 'none',
];

/**
 * Mirror of the backend ``unread_policy.normalize_unread_recording`` rule:
 * missing/invalid values keep the legacy default (all sessions), all/none
 * absorb every other scope, bell-on + bell-off collapse to all (their union
 * covers every Session), and an empty set means none ("empty ⇒ no-recording",
 * shared with the backend).
 */
export function sanitizeUnreadRecording(value: unknown): UnreadRecordingScope[] {
  if (!Array.isArray(value)) return [...DEFAULT_UNREAD_RECORDING];
  const scopes: UnreadRecordingScope[] = [];
  for (const item of value) {
    if (
      typeof item === 'string'
      && (UNREAD_RECORDING_SCOPES as readonly string[]).includes(item)
      && !scopes.includes(item as UnreadRecordingScope)
    ) {
      scopes.push(item as UnreadRecordingScope);
    }
  }
  if (scopes.includes('all')) return ['all'];
  if (scopes.includes('none')) return ['none'];
  if (scopes.length === 0) return ['none'];
  if (scopes.includes('bell-on') && scopes.includes('bell-off')) return ['all'];
  // Canonical, order-stable output (mirrors the backend normalize rule).
  return UNREAD_RECORDING_SCOPES.filter((candidate) => scopes.includes(candidate));
}

/**
 * Apply one checkbox toggle over the canonical scope set
 * (App Settings → Notification → Unread recording).
 *
 * Interaction rules: All / No sessions are exclusive — enabling one clears
 * every other scope, disabling one falls back to No sessions (the shared
 * "empty set ⇒ none" rule). Bell on / Bell off are mutually exclusive.
 * Enabling any category clears All / No sessions; disabling the last category
 * falls back to No sessions.
 */
export function toggleUnreadRecordingScope(
  scopes: UnreadRecordingScope[],
  scope: UnreadRecordingScope,
): UnreadRecordingScope[] {
  const current = sanitizeUnreadRecording(scopes);
  if (scope === 'all' || scope === 'none') {
    if (current.includes(scope)) return ['none'];
    return [scope];
  }
  const next = new Set(current);
  next.delete('all');
  next.delete('none');
  if (next.has(scope)) {
    next.delete(scope);
  } else {
    next.add(scope);
    if (scope === 'bell-on') next.delete('bell-off');
    if (scope === 'bell-off') next.delete('bell-on');
  }
  const ordered = UNREAD_RECORDING_SCOPES.filter((candidate) => next.has(candidate));
  return ordered.length > 0 ? [...ordered] : ['none'];
}

export interface AutoReportSourceFlags {
  system: boolean;
  ma: boolean;
  user: boolean;
  plugins: Record<string, boolean>;
}

export interface AutoReportSettings extends AutoReportSourceFlags {
  /**
   * Independent policy: whether an automatic answer report may include the
   * first 100 characters of the input body that started the run. Every source
   * defaults to off and plugin switches never inherit `auto_report_default`.
   */
  summary: AutoReportSourceFlags;
}

export const DEFAULT_AUTO_REPORT_SUMMARY: AutoReportSourceFlags = {
  system: false, ma: false, user: false, plugins: {},
};

export const DEFAULT_AUTO_REPORT: AutoReportSettings = {
  system: false, ma: true, user: false, plugins: {},
  summary: DEFAULT_AUTO_REPORT_SUMMARY,
};

function sanitizeAutoReportSources(value: unknown, maDefault: boolean): AutoReportSourceFlags {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const plugins = raw.plugins && typeof raw.plugins === 'object' && !Array.isArray(raw.plugins)
    ? Object.fromEntries(Object.entries(raw.plugins).filter(([key, v]) => /^[a-z][a-z0-9_-]{0,47}$/.test(key) && typeof v === 'boolean')) as Record<string, boolean> : {};
  return { system: raw.system === true, ma: typeof raw.ma === 'boolean' ? raw.ma : maDefault,
    user: raw.user === true, plugins };
}

export function sanitizeAutoReport(value: unknown): AutoReportSettings {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  return {
    ...sanitizeAutoReportSources(raw, true),
    summary: sanitizeAutoReportSources(raw.summary, false),
  };
}

export interface AppSettings {
  /** Stable navigation entry ids in user-selected order. */
  viewEntryOrder: string[];
  autoReport: AutoReportSettings;
  /**
   * Chat message presentation. TUI is the default; `bubble` is the opt-in
   * shrink-to-fit view, and `raw` keeps the TUI rows while showing each body's
   * original text instead of rendered Markdown.
   */
  chatViewStyle: 'tui' | 'bubble' | 'raw';
  /** Default session-list grouping (mirrors uiStore GroupMode options). */
  defaultGroupBy: GroupMode;
  /** Show the sidebar's interactive Group by cycle button. */
  showGroupBy: boolean;
  /**
   * Show the Chat TopBar's TUI/Bubble/Raw view switcher. On by default; when
   * off only that switcher disappears — Terminal TUI / Upgrades, navigation
   * and routing stay available.
   */
  showChatViewSwitcher: boolean;
  /** Show the floating collapse/expand handle on the desktop sidebar edge. */
  showSidebarCollapseHandle: boolean;
  /** Put new Sessions in the active Workspace when the active scope is concrete. */
  defaultNewSessionToCurrentWorkspace: boolean;
  /** Show meta-agent info (e.g. messages with the `////by agent` prefix). */
  showMetaAgent: boolean;
  /** Show task-agent info (e.g. messages with the `@@@@by agent` prefix). */
  showTaskAgent: boolean;
  /** Show QQ-injected info (e.g. messages with the `@@@@by qq` prefix). */
  showQQ: boolean;
  /**
   * Report an error to the Manager when the user explicitly stops a
   * running or queued Worker (App Settings → Behavior). Off by default;
   * real crashes, abnormal exits, and watchdog reports are unaffected
   * either way.
   */
  reportWorkerKillErrors: boolean;
  /**
   * Automatically route newly enqueued structured messages through Force
   * send. `system` = Job terminal notices, `ma` = manager/meta-agent
   * messages, `report` = completion reports. All off by default.
   */
  autoForceSend: AutoForceSendSettings;
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
   * Mount the quick-location strip on the chat's right edge. On by default.
   * Turning it off also skips its full-history index pass, so sessions
   * open without that cost (one request per 200 messages of the session).
   */
  showMessageNavigationRail: boolean;
  /** Mount Session-scoped history search UI and keyboard shortcuts. */
  showHistorySearch: boolean;
  /**
   * Configurable app shortcuts keyed by stable action id. A missing key uses
   * the action default; an explicit null is an intentionally unbound action.
   * Dynamic plugin-panel ids are stored under `panel:plugin:<pluginId>/<panelId>`
   * so array order changes never move a binding to another panel.
   */
  shortcuts: Record<string, string | null>;
  /** Notification preferences for CLI adapter warnings. */
  notifications: {
    /** Show structured Codex warning events through a Toast. */
    codexWarningToast: boolean;
    /**
     * Send one system (desktop) notification when the user Locker's unread
     * total grows while the page is in the background. Off by default; the
     * browser permission is requested when the switch is turned on, and the
     * stored preference survives a denied permission.
     */
    lockerDesktop: boolean;
    completionBridge?: CompletionBridgeDefaults;
    /**
     * Which Sessions record done unread. Persisted globally in
     * config.json (``ui.notifications.unreadRecording``) and enforced by the
     * backend single predicate; excluded Sessions stay at an authoritative
     * count 0 on every surface.
     */
    unreadRecording: UnreadRecordingScope[];
    confirmCrossWorkspaceManagement: boolean;
    confirmAgentSystemQueueEdit: boolean;
  };
}

export const DEFAULT_SETTINGS: AppSettings = {
  viewEntryOrder: [],
  autoReport: DEFAULT_AUTO_REPORT,
  chatViewStyle: 'tui',
  defaultGroupBy: 'none',
  showGroupBy: false,
  showChatViewSwitcher: true,
  showSidebarCollapseHandle: true,
  defaultNewSessionToCurrentWorkspace: true,
  showMetaAgent: true,
  showTaskAgent: true,
  showQQ: true,
  reportWorkerKillErrors: false,
  autoForceSend: DEFAULT_AUTO_FORCE_SEND,
  showCodexTerminalInput: false,
  mergeConsecutiveNonBodyBlocks: false,
  keepScrollOnSessionSwitch: false,
  showMessageNavigationRail: true,
  showHistorySearch: true,
  shortcuts: {},
  notifications: {
    codexWarningToast: true,
    lockerDesktop: false,
    completionBridge: DEFAULT_COMPLETION_BRIDGE,
    unreadRecording: DEFAULT_UNREAD_RECORDING,
    confirmCrossWorkspaceManagement: true,
    confirmAgentSystemQueueEdit: true,
  },
};

/**
 * Validate a raw (possibly partial / malformed) settings object, falling back
 * to defaults for missing or wrong-typed fields. Used both when the backend
 * load lands and defensively against any garbage in config.json.
 */
export function sanitizeShortcuts(raw: unknown): Record<string, string | null> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const result: Record<string, string | null> = {};
  for (const [actionId, value] of Object.entries(raw)) {
    if (!actionId || actionId.length > 200) continue;
    if (value === null) {
      result[actionId] = null;
      continue;
    }
    const normalized = normalizeBinding(value);
    if (normalized && !isReservedBinding(normalized, actionId)) result[actionId] = normalized;
  }
  return result;
}

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
    viewEntryOrder: Array.isArray(parsed.viewEntryOrder)
      ? [...new Set(parsed.viewEntryOrder.filter((id): id is string => typeof id === 'string' && id.length > 0))]
      : [],
    autoReport: sanitizeAutoReport(parsed.autoReport),
    chatViewStyle:
      parsed.chatViewStyle === 'bubble' ||
      parsed.chatViewStyle === 'tui' ||
      parsed.chatViewStyle === 'raw'
        ? parsed.chatViewStyle
        : DEFAULT_SETTINGS.chatViewStyle,
    defaultGroupBy:
      parsed.defaultGroupBy === 'workdir' || parsed.defaultGroupBy === 'manager'
        ? parsed.defaultGroupBy
        : DEFAULT_SETTINGS.defaultGroupBy,
    showGroupBy:
      typeof parsed.showGroupBy === 'boolean'
        ? parsed.showGroupBy
        : DEFAULT_SETTINGS.showGroupBy,
    showChatViewSwitcher:
      typeof parsed.showChatViewSwitcher === 'boolean'
        ? parsed.showChatViewSwitcher
        : DEFAULT_SETTINGS.showChatViewSwitcher,
    showSidebarCollapseHandle:
      typeof parsed.showSidebarCollapseHandle === 'boolean'
        ? parsed.showSidebarCollapseHandle
        : DEFAULT_SETTINGS.showSidebarCollapseHandle,
    defaultNewSessionToCurrentWorkspace:
      typeof parsed.defaultNewSessionToCurrentWorkspace === 'boolean'
        ? parsed.defaultNewSessionToCurrentWorkspace
        : DEFAULT_SETTINGS.defaultNewSessionToCurrentWorkspace,
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
    reportWorkerKillErrors:
      typeof parsed.reportWorkerKillErrors === 'boolean'
        ? parsed.reportWorkerKillErrors
        : DEFAULT_SETTINGS.reportWorkerKillErrors,
    autoForceSend: sanitizeAutoForceSend(parsed.autoForceSend),
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
    showHistorySearch:
      typeof parsed.showHistorySearch === 'boolean'
        ? parsed.showHistorySearch
        : DEFAULT_SETTINGS.showHistorySearch,
    shortcuts: sanitizeShortcuts(parsed.shortcuts),
    notifications: {
      completionBridge: sanitizeCompletionBridge(notifications.completionBridge),
      unreadRecording: sanitizeUnreadRecording(notifications.unreadRecording),
      confirmAgentSystemQueueEdit: typeof notifications.confirmAgentSystemQueueEdit === 'boolean' ? notifications.confirmAgentSystemQueueEdit : true,
      codexWarningToast:
        typeof notifications.codexWarningToast === 'boolean'
          ? notifications.codexWarningToast
          : DEFAULT_SETTINGS.notifications.codexWarningToast,
      lockerDesktop:
        typeof notifications.lockerDesktop === 'boolean'
          ? notifications.lockerDesktop
          : DEFAULT_SETTINGS.notifications.lockerDesktop,
      confirmCrossWorkspaceManagement:
        typeof notifications.confirmCrossWorkspaceManagement === 'boolean'
          ? notifications.confirmCrossWorkspaceManagement
          : DEFAULT_SETTINGS.notifications.confirmCrossWorkspaceManagement,
    },
  };
}

interface AppSettingsStore extends AppSettings {
  setViewEntryOrder: (value: string[]) => void;
  /** True once the initial GET finished (success or failure). */
  loaded: boolean;
  setDefaultGroupBy: (mode: GroupMode) => void;
  setShowGroupBy: (v: boolean) => void;
  setShowChatViewSwitcher: (v: boolean) => void;
  setShowSidebarCollapseHandle: (v: boolean) => void;
  setChatViewStyle: (style: AppSettings['chatViewStyle']) => void;
  setDefaultNewSessionToCurrentWorkspace: (v: boolean) => void;
  setShowMetaAgent: (v: boolean) => void;
  setShowTaskAgent: (v: boolean) => void;
  setShowQQ: (v: boolean) => void;
  setShowCodexTerminalInput: (v: boolean) => void;
  setMergeConsecutiveNonBodyBlocks: (v: boolean) => void;
  setKeepScrollOnSessionSwitch: (v: boolean) => void;
  setShowMessageNavigationRail: (v: boolean) => void;
  setShowHistorySearch: (v: boolean) => void;
  /** Persist one action binding; `null` explicitly unbinds it. */
  setShortcutBinding: (actionId: string, binding: string | null) => Promise<void>;
  /** Remove the override so the action returns to its built-in default. */
  resetShortcutBinding: (actionId: string) => Promise<void>;
  /** Restore every action to its built-in default (new actions stay unbound). */
  resetAllShortcuts: () => Promise<void>;
  setCodexWarningToast: (v: boolean) => void;
  /** Persist the Locker desktop-notification switch. */
  setLockerDesktop: (v: boolean) => void;
  saveCompletionBridge: (v: CompletionBridgeDefaults) => Promise<void>;
  /**
   * Persist the unread-recording scope set. Resolves only after the server
   * wrote the settings; a failure rejects and leaves the store unchanged.
   */
  saveUnreadRecording: (v: UnreadRecordingScope[]) => Promise<void>;
  /**
   * Persist the user-kill error report switch. Resolves only after the
   * server wrote the settings; a failure rejects and leaves the store
   * unchanged.
   */
  saveAutoReport: (value: AutoReportSettings) => Promise<void>;
  saveReportWorkerKillErrors: (v: boolean) => Promise<void>;
  /**
   * Persist the three automatic Force switches. Resolves only after the
   * server wrote the settings; a failure rejects and leaves the store on the
   * last saved value.
   */
  saveAutoForceSend: (v: AutoForceSendSettings) => Promise<void>;
  setConfirmCrossWorkspaceManagement: (v: boolean) => void;
  setConfirmAgentSystemQueueEdit: (v: boolean) => void;
  /** Reset every field to its default and persist. */
  resetSettings: () => void;
  /** Fetch the persisted ui object from config.json into the store. */
  loadSettings: () => Promise<void>;
  /** Wait for the initial settings GET, using defaults if it fails. */
  ensureSettingsLoaded: () => Promise<void>;
}

type AppSettingsPatch = Omit<Partial<AppSettings>, 'notifications'> & {
  notifications?: Partial<AppSettings['notifications']>;
};

export const useAppSettingsStore = create<AppSettingsStore>((set, get) => {
  // Race guard: if the user changes a setting while the initial GET is still
  // in flight, the (possibly stale) server response must not clobber it.
  // Re-armed at the start of every load, so a later load still applies.
  let dirty = false;
  let loadingPromise: Promise<void> | null = null;
  let shortcutWrites: Promise<void> = Promise.resolve();
  const saveShortcuts = (shortcuts: Record<string, string | null>) => {
    const previous = get().shortcuts;
    dirty = true;
    set({ shortcuts });
    // Serialize whole-map writes so a slow earlier response cannot overwrite a newer edit.
    const write = shortcutWrites.catch(() => undefined).then(() => updateUiSettings({ shortcuts }));
    shortcutWrites = write.then(() => undefined);
    return shortcutWrites.catch((error: unknown) => {
      if (get().shortcuts === shortcuts) set({ shortcuts: previous });
      throw error;
    });
  };

  const persist = (patch: AppSettingsPatch) => {
    dirty = true;
    // Backend merges individual notification fields, preserving defaults.
    const payload = patch;
    void updateUiSettings(payload).catch(() => {
      // Best-effort writeback: a backend failure is non-fatal, the in-memory
      // value stays for the current session and is retried next change.
    });
  };

  return {
    ...DEFAULT_SETTINGS,
    loaded: false,

    setViewEntryOrder: (value) => {
      const order = [...new Set(value.filter((id): id is string => typeof id === 'string' && id.length > 0))];
      set({ viewEntryOrder: order });
      persist({ viewEntryOrder: order });
    },

    loadSettings: () => {
      if (loadingPromise) return loadingPromise;
      dirty = false;
      loadingPromise = Promise.resolve()
        .then(fetchUiSettings)
        .then((ui) => {
          if (!dirty) set(sanitizeSettings(ui));
          // Forward the persisted session-list collapse state to uiStore over
          // this already-running GET (no extra request). Always called, so
          // uiStore knows the server outcome either way (it gates automatic
          // maintenance write-back on that): an explicit array — even empty —
          // is authoritative and applied through uiStore's own user-action
          // race guard; an absent/invalid key passes undefined and keeps the
          // local state.
          const collapsed = ui.collapsedGroups;
          useUIStore.getState().hydrateCollapsedGroupsFromServer(
            Array.isArray(collapsed)
              ? collapsed.filter((x): x is string => typeof x === 'string')
              : undefined,
          );
        })
        .catch(() => {
          // Backend unreachable → keep defaults for this session. The collapse
          // state's server outcome also counts as resolved (load failed): the
          // local state stays authoritative and maintenance write-back is
          // unblocked.
          useUIStore.getState().hydrateCollapsedGroupsFromServer(undefined);
        })
        .finally(() => {
          set({ loaded: true });
          loadingPromise = null;
        });
      return loadingPromise;
    },

    ensureSettingsLoaded: async () => {
      if (get().loaded) return;
      await (loadingPromise ?? get().loadSettings());
    },

    setDefaultGroupBy: (mode) => {
      set({ defaultGroupBy: mode });
      persist({ defaultGroupBy: mode });
    },

    setShowGroupBy: (v) => {
      set({ showGroupBy: v });
      persist({ showGroupBy: v });
    },

    setShowChatViewSwitcher: (v) => {
      set({ showChatViewSwitcher: v });
      persist({ showChatViewSwitcher: v });
    },

    setShowSidebarCollapseHandle: (v) => {
      set({ showSidebarCollapseHandle: v });
      persist({ showSidebarCollapseHandle: v });
    },

    setChatViewStyle: (style) => {
      set({ chatViewStyle: style });
      persist({ chatViewStyle: style });
    },

    setDefaultNewSessionToCurrentWorkspace: (v) => {
      set({ defaultNewSessionToCurrentWorkspace: v });
      persist({ defaultNewSessionToCurrentWorkspace: v });
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

    setShowHistorySearch: (v) => {
      set({ showHistorySearch: v });
      persist({ showHistorySearch: v });
    },

    setShortcutBinding: async (actionId, binding) => {
      const normalized = binding === null ? null : normalizeBinding(binding);
      if (binding !== null && (!normalized || isReservedBinding(normalized, actionId))) return;
      const shortcuts = { ...get().shortcuts, [actionId]: normalized };
      await saveShortcuts(shortcuts);
    },

    resetShortcutBinding: (actionId) => {
      const shortcuts = { ...get().shortcuts };
      delete shortcuts[actionId];
      return saveShortcuts(shortcuts);
    },

    resetAllShortcuts: () => {
      return saveShortcuts({});
    },

    saveCompletionBridge: async (value) => {
      if ((value.qqReport || value.qqSubscribe) && value.qqTargets.length === 0) {
        throw new Error('Select at least one QQ contact before enabling QQ Report or Subscribe by default.');
      }
      dirty = true;
      const response = await updateUiSettings({ notifications: { completionBridge: value } });
      const next = sanitizeSettings(response).notifications.completionBridge;
      set((state) => ({ notifications: { ...state.notifications, completionBridge: next } }));
    },

    saveUnreadRecording: async (value) => {
      dirty = true;
      const response = await updateUiSettings({
        notifications: { unreadRecording: sanitizeUnreadRecording(value) },
      });
      const next = sanitizeSettings(response).notifications.unreadRecording;
      // The settings are saved (the values above are the persisted truth);
      // the follow-up alignment may still have failed for some Sessions.
      // Surface that honestly instead of showing an unconditional success.
      const applyReport = (
        response as { unreadRecordingApply?: { ok?: boolean; failedSessions?: string[] } }
      ).unreadRecordingApply;
      const failed = applyReport?.failedSessions?.length ?? 0;
      set((state) => ({ notifications: { ...state.notifications, unreadRecording: next } }));
      if (failed > 0) {
        throw new Error(
          `Settings saved, but ${failed} Session(s) could not be aligned yet — use Reapply settings to retry.`,
        );
      }
    },

    saveAutoReport: async (value) => {
      dirty = true;
      const response = await updateUiSettings({ autoReport: value });
      set({ autoReport: sanitizeAutoReport(response.autoReport) });
    },

    saveReportWorkerKillErrors: async (value) => {
      dirty = true;
      const response = await updateUiSettings({ reportWorkerKillErrors: value });
      set({ reportWorkerKillErrors: sanitizeSettings(response).reportWorkerKillErrors });
    },

    saveAutoForceSend: async (value) => {
      dirty = true;
      const response = await updateUiSettings({
        autoForceSend: sanitizeAutoForceSend(value),
      });
      set({ autoForceSend: sanitizeSettings(response).autoForceSend });
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

    setLockerDesktop: (v) => {
      set((s) => ({ notifications: { ...s.notifications, lockerDesktop: v } }));
      persist({ notifications: { lockerDesktop: v } });
    },

    setConfirmCrossWorkspaceManagement: (v) => {
      set((s) => ({ notifications: { ...s.notifications, confirmCrossWorkspaceManagement: v } }));
      persist({ notifications: { confirmCrossWorkspaceManagement: v } });
    },

    setConfirmAgentSystemQueueEdit: (v) => {
      set((s) => ({ notifications: { ...s.notifications, confirmAgentSystemQueueEdit: v } }));
      persist({ notifications: { confirmAgentSystemQueueEdit: v } });
    },

    resetSettings: () => {
      set({ ...DEFAULT_SETTINGS });
      persist({ ...DEFAULT_SETTINGS });
    },
  };
});
