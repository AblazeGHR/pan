import { useSettingsAvailability } from './settingsAvailability';
import { settingsText, settingsVisible } from '@/components/layout/settingsCatalog';
import { UserCardReminderSettings } from './UserCardReminderSettings';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { McpRemoteSettings } from './McpRemoteSettings';
import { TailscaleSettings } from './TailscaleSettings';
import { LanSettings } from './LanSettings';
import { RemoteSettingsSection, RemoteStatus } from './RemoteSettingsSection';
import { Button } from '@/components/ui/Button';
import { LoginStartupSettings } from './LoginStartupSettings';
import { AutoReportSettingsGroup } from './AutoReportSettingsGroup';
import {
  Bell,
  Database,
  KeyRound,
  Eye,
  Globe,
  Keyboard,
  Power,
  Search,
  Puzzle,
  SlidersHorizontal,
  ToggleLeft,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { SETTINGS_CATEGORY_LABELS, SETTINGS_TABS } from './settingsCategories';
import type { SettingsTab } from './settingsCategories';
import { SettingsSearchField, SettingsSearchResults } from './SettingsSearchPanel';
import { useShortcutActions } from '@/shortcuts/registry';
import { createSettingsIndex, SETTINGS_SEARCH_ENTRIES, findSettingsAnchor, searchSettings } from './settingsSearch';
import type { SettingsSearchEntry } from './settingsSearch';
import { useAutomationAuth } from '@/stores/automationStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import type { AutoForceSendSettings } from '@/stores/appSettingsStore';
import { useCapability, usePlatformStore } from '@/stores/platformStore';
import { platformLabel } from '@/utils/platformCapabilities';
import { useUIStore } from '@/stores/uiStore';
import { JobRetentionSettings } from '@/components/jobs/JobRetentionSettings';
import { AuthorizationSettings } from './AuthorizationSettings';
import {
  reloadConfig,
  fetchRemoteStatus,
  restartRemoteTunnel,
  fetchMainRestartStatus,
  restartMainService,
  fetchMainExitStatus,
  exitMainService,
  fetchSessionLifecyclePreferences,
  updateSessionLifecyclePreferences,
  fetchHealth,
  updateWorkerSettings,
  fetchCodexModels,
  refreshCodexOfficialModels,
  fetchDataCatalog,
  fetchDataRetention,
  updateDataRetention,
} from '@/services/api';
import type {
  ApiConfigReloadResponse,
  ApiRemoteStatusResponse,
  ApiMainRestartStatusResponse,
  ApiMainExitStatusResponse,
  ApiSessionExitStrategy,
  ApiStartupPreference,
  ApiSessionLifecyclePreferences,
  ApiModelsResponse,
  ApiDataCatalogResponse,
  ApiDataRetentionResponse,
  DataRetentionPolicyId,
} from '@/types';
import type { GroupMode } from '@/stores/uiStore';
import { DataSettingsPanel } from './DataSettingsPanel';
import { SourceNotificationPanel } from './SourceNotificationPanel';
import { SessionErrorBridgeSetting } from './SessionErrorBridgeSetting';
import { CompletionBridgeDefaultsPanel } from './CompletionBridgeDefaultsPanel';
import { UnreadRecordingPanel } from './UnreadRecordingPanel';
import { QqPluginsPanel } from './QqPluginsPanel';
import { LocalPluginPanelsSettings } from './LocalPluginPanelsSettings';
import { ShortcutsSettings } from './ShortcutsSettings';
import { CodexNativePassthrough } from '@/components/session/CodexNativePassthrough';
import {
  lockerNotificationPermission,
  requestLockerNotificationPermission,
} from '@/services/lockerDesktopNotify';
import type { LockerNotificationPermission } from '@/services/lockerDesktopNotify';
import { useCurrentSession } from '@/stores/sessionStore';
import { reorderViewEntryIds, useViewEntries } from './viewEntries';
import { resolveDropZone } from '@/components/session/sessionDrag';

interface AppSettingsModalProps {
  open: boolean;
  onClose: () => void;
}

const GROUP_OPTIONS: { value: GroupMode; label: string; labelId: string }[] = [
  { value: 'none', labelId: "app-settings-modal.1", label: settingsText("app-settings-modal.1") },
  { value: 'workdir', labelId: "app-settings-modal.2", label: settingsText("app-settings-modal.2") },
  { value: 'manager', labelId: "app-settings-modal.3", label: settingsText("app-settings-modal.3") },
];

const WORKER_KEYS = ['timeout_sec', 'task_timeout_sec', 'idle_sec'] as const;

const DEFAULT_SESSION_LIFECYCLE: ApiSessionLifecyclePreferences = {
  exitStrategy: 'ask',
  startupPreference: 'ask',
  wakeMessage: '继续',
};

function normalizeSessionLifecyclePreferences(
  value: ApiSessionLifecyclePreferences,
): ApiSessionLifecyclePreferences {
  return {
    wakeMessage: value?.wakeMessage || '继续',
    exitStrategy: ['ask', 'offline', 'preserve-running'].includes(value?.exitStrategy)
      ? value.exitStrategy
      : 'ask',
    startupPreference: ['ask', 'wake-running', 'clear-illegal', 'preserve-running'].includes(value?.startupPreference)
      ? value.startupPreference
      : 'ask',
  };
}

function mainLifecycleOptions(
  strategy: ApiSessionExitStrategy,
  markRunningOffline: boolean | null,
): { markRunningSessionsOffline: boolean } | undefined | null {
  if (strategy !== 'ask') return undefined;
  return markRunningOffline === null
    ? null
    : { markRunningSessionsOffline: markRunningOffline };
}

function MainLifecycleRunningChoice({
  strategy,
  markRunningOffline,
  onChange,
  inputName,
}: {
  strategy: ApiSessionExitStrategy;
  markRunningOffline: boolean | null;
  onChange: (value: boolean) => void;
  inputName: string;
}) {
  if (strategy !== 'ask') {
    return (
      <p className="mt-3 text-[11px] text-text-secondary" data-settings-id="app-settings-modal.4">
        {settingsText("app-settings-modal.4")}{strategy === 'offline'
          ? 'safely retire stopped execution.'
          : 'preserve unfinished execution as Illegal.'}
      </p>
    );
  }

  return (
    <fieldset className="mt-3 space-y-2">
      <legend className="text-[11px] text-text-secondary" data-settings-id="app-settings-modal.5">
        {settingsText("app-settings-modal.5")}</legend>
      <label className="flex items-start gap-2 text-[11px] text-text-primary">
        <input
          type="radio"
          name={inputName}
          checked={markRunningOffline === true}
          onChange={() => onChange(true)}
        />
        <span data-settings-id="app-settings-modal.6">{settingsText("app-settings-modal.6")}</span>
      </label>
      <label className="flex items-start gap-2 text-[11px] text-text-primary">
        <input
          type="radio"
          name={inputName}
          checked={markRunningOffline === false}
          onChange={() => onChange(false)}
        />
        <span data-settings-id="app-settings-modal.7">{settingsText("app-settings-modal.7")}</span>
      </label>
    </fieldset>
  );
}

const SETTINGS_TAB_META: Record<SettingsTab, { label: string; labelId?: string; hintId?: string; descriptionId?: string; Icon: LucideIcon }> = {
  search: { label: settingsText('settings-search-nav'), Icon: Search },
  lifecycle: { label: SETTINGS_CATEGORY_LABELS.lifecycle, Icon: Power },
  behavior: { label: SETTINGS_CATEGORY_LABELS.behavior, Icon: ToggleLeft },
  remote: { label: SETTINGS_CATEGORY_LABELS.remote, Icon: Globe },
  appearance: { label: SETTINGS_CATEGORY_LABELS.appearance, Icon: Eye },
  shortcuts: { label: SETTINGS_CATEGORY_LABELS.shortcuts, Icon: Keyboard },
  notifications: { label: SETTINGS_CATEGORY_LABELS.notifications, Icon: Bell },
  adapters: { label: SETTINGS_CATEGORY_LABELS.adapters, Icon: SlidersHorizontal },
  plugins: { label: SETTINGS_CATEGORY_LABELS.plugins, Icon: Puzzle },
  data: { label: SETTINGS_CATEGORY_LABELS.data, Icon: Database },
  authorization: { label: SETTINGS_CATEGORY_LABELS.authorization, Icon: KeyRound },
};

type ReloadScope = 'adapters' | 'worker' | 'plugin' | 'memory';
type MainRestartState =
  'idle' | 'confirming' | 'restarting' | 'restored' | 'timeout' | 'cancelled' | 'error';
type MainExitState = 'idle' | 'confirming' | 'exiting' | 'exited' | 'error';
type BehaviorAnchor = 'exit-strategy' | 'startup-preference';

function SwitchRow({
  settingId,
  label,
  hint,
  checked,
  onChange,
  disabled = false,
}: {
  settingId?: string;
  label: string; labelId?: string; hintId?: string; descriptionId?: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      data-settings-id={settingId}
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        {hint && (
          <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">{hint}</span>
        )}
      </span>
      <span
        className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-hover'
        }`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[14px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  );
}

const CHAT_VIEW_OPTIONS = [
  { value: 'tui', labelId: "app-settings-modal.9", label: settingsText("app-settings-modal.9"), hintId: "app-settings-modal.10", hint: settingsText("app-settings-modal.10") },
  { value: 'bubble', labelId: "app-settings-modal.11", label: settingsText("app-settings-modal.11"), hintId: "app-settings-modal.12", hint: settingsText("app-settings-modal.12") },
  { value: 'raw', labelId: "app-settings-modal.13", label: settingsText("app-settings-modal.13"), hintId: "app-settings-modal.14", hint: settingsText("app-settings-modal.14") },
] as const;

/**
 * Explicit three-mode picker for the chat presentation. Persisted through the
 * same `setChatViewStyle`/config.json path as the TopBar selector, so the two
 * surfaces always agree and Raw is never confused with Bubble.
 */
function ChatViewSelect({
  value,
  onChange,
}: {
  value: 'tui' | 'bubble' | 'raw';
  onChange: (v: 'tui' | 'bubble' | 'raw') => void;
}) {
  const activeHint = CHAT_VIEW_OPTIONS.find((option) => option.value === value)?.hint ?? '';
  return (
    <div className="px-3 py-3">
      <div
        role="radiogroup"
        aria-label={settingsText("chat-view")}
        className="flex gap-1 rounded border border-border-default bg-bg-tertiary p-1" data-settings-id="chat-view"
      >
        {CHAT_VIEW_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            onClick={() => onChange(option.value)}
            className={`flex-1 rounded px-2 py-1.5 text-xs transition-colors ${
              value === option.value
                ? 'bg-bg-primary text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`} data-settings-id={option.labelId}
          >
            {option.label}
          </button>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">{activeHint}</p>
    </div>
  );
}

function ReloadRow({
  settingId,
  label,
  hint,
  busy,
  action = 'Reload',
  onClick,
}: {
  settingId?: string;
  label: string; labelId?: string; hintId?: string; descriptionId?: string;
  hint: string;
  busy: boolean;
  action?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-settings-id={settingId}
      disabled={busy}
      onClick={onClick}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">{hint}</span>
      </span>
      <span className="shrink-0 text-[11px] text-text-tertiary">
        {busy ? 'Reloading…' : action}
      </span>
    </button>
  );
}

/**
 * Edit dialog for the worker lifecycle timeouts (config.json worker
 * section). Rendered through its own portal to <body> so it stacks above
 * the App Settings card. Overlay click / X / Escape (handled by the parent,
 * which owns the state) close it.
 */
function WorkerEditModal({
  values,
  loading,
  saving,
  error,
  onValueChange,
  onSave,
  onClose,
}: {
  values: Record<string, string>;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onValueChange: (key: string, value: string) => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const FIELD_HINTS: Record<string, string> = {
    timeout_sec: settingsText("appsettingsmodal.extra.1"),
    task_timeout_sec: settingsText("appsettingsmodal.extra.2"),
    idle_sec: settingsText("appsettingsmodal.extra.3"),
  };
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={settingsText("app-settings-modal.15")}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }} data-settings-id="app-settings-modal.15"
    >
      <div className="w-[min(420px,92vw)] bg-bg-primary border border-border-default rounded-lg shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-border-default px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary" data-settings-id="app-settings-modal.16">{settingsText("app-settings-modal.16")}</h3>
            <p className="mt-0.5 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.17">
              {settingsText("app-settings-modal.17")}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={settingsText("app-settings-modal.18")}
            className="text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary p-1.5 rounded transition-colors shrink-0" data-settings-id="app-settings-modal.18"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-4 py-4 space-y-3">
          {WORKER_KEYS.map((k) => (
            <div key={k}>
              <label
                htmlFor={`worker-${k}`}
                className="block text-xs text-text-secondary font-mono"
              >
                {k}
              </label>
              <input
                id={`worker-${k}`}
                type="number"
                min="0"
                step="any"
                disabled={loading || saving}
                value={values[k] ?? ''}
                onChange={(e) => onValueChange(k, e.target.value)}
                className="mt-1 w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary font-mono outline-none focus:border-accent disabled:opacity-50"
              />
              <p className="mt-1 text-[10px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.19">
                {FIELD_HINTS[k]} {settingsText("app-settings-modal.19")}</p>
            </div>
          ))}
          {loading && <p className="text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.20">{settingsText("app-settings-modal.20")}</p>}
          {error && (
            <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-border-default px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={loading || saving}
            className="rounded bg-accent px-3 py-1.5 text-xs text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Render the plugin half of a config-reload result: path-list diff
 * (added/removed manifests) + the freshly loaded template counts.
 */
function PluginResult({ plugin }: { plugin: NonNullable<ApiConfigReloadResponse['plugin']> }) {
  const beforeSet = new Set(plugin.before);
  const afterSet = new Set(plugin.after);
  const added = plugin.after.filter((p) => !beforeSet.has(p));
  const removed = plugin.before.filter((p) => !afterSet.has(p));
  const changed = added.length > 0 || removed.length > 0;
  return (
    <>
      <div data-settings-id="app-settings-modal.21">
        {settingsText("app-settings-modal.21")}{plugin.before.length} → {plugin.after.length}
        {changed ? ' (changed)' : ''}
      </div>
      {added.map((p, i) => (
        <div key={`added-${i}`} className="text-text-primary break-all">
          + {p}
        </div>
      ))}
      {removed.map((p, i) => (
        <div key={`removed-${i}`} className="text-text-tertiary break-all">
          − {p}
        </div>
      ))}
      <div data-settings-id="app-settings-modal.22">
        {settingsText("app-settings-modal.22")}{plugin.sessionTemplates ?? '?'} {settingsText("app-settings-modal.23")}{plugin.mcpServers ?? '?'} {settingsText("app-settings-modal.24")}{plugin.characters ?? '?'} {settingsText("app-settings-modal.25")}{plugin.commandRoutes ?? '?'}
      </div>
    </>
  );
}

/**
 * Global app-settings modal. Desktop: centered card covering ~75% of the
 * viewport. Mobile: full-screen, edge-to-edge and scrollable. Reads/writes
 * appSettingsStore directly so changes take effect immediately.
 *
 * Rendered through a portal to <body> — the sidebar's mobile container uses
 * `transform`, which would otherwise become the containing block for
 * `position: fixed` descendants and clamp the overlay to the sidebar width.
 */
export function AppSettingsModal({ open, onClose }: AppSettingsModalProps) {
  const currentSession = useCurrentSession();
  const {
    defaultGroupBy,
    showGroupBy,
    showChatViewSwitcher,
    showSidebarCollapseHandle,
    defaultNewSessionToCurrentWorkspace,
    showMetaAgent,
    showTaskAgent,
    showQQ,
    showCodexTerminalInput,
    reportWorkerKillErrors,
    autoForceSend,
    behavior,
    mergeConsecutiveNonBodyBlocks,
    keepScrollOnSessionSwitch,
    showMessageNavigationRail,
    showHistorySearch,
    chatViewStyle,
    notifications,
    setDefaultGroupBy,
    setShowGroupBy,
    setShowChatViewSwitcher,
    setShowSidebarCollapseHandle,
    setDefaultNewSessionToCurrentWorkspace,
    setShowMetaAgent,
    setShowTaskAgent,
    setShowQQ,
    setShowCodexTerminalInput,
    setMergeConsecutiveNonBodyBlocks,
    setKeepScrollOnSessionSwitch,
    setShowMessageNavigationRail,
    setShowHistorySearch,
    setChatViewStyle,
    setCodexWarningToast,
    setLockerDesktop,
    setConfirmCrossWorkspaceManagement,
    setConfirmAgentSystemQueueEdit,
    saveReportWorkerKillErrors,
    saveAutoForceSend,
    saveBehavior,
    resetSettings,
    setViewEntryOrder,
  } = useAppSettingsStore();
  const viewEntries = useViewEntries();
  const [dragEntry, setDragEntry] = useState<string | null>(null);
  const [dropEntry, setDropEntry] = useState<{ id: string; zone: 'before' | 'after' } | null>(null);
  const orderedEntries = viewEntries;
  const moveEntry = (id: string, delta: number) => {
    const ids = orderedEntries.map((entry) => entry.id);
    const index = ids.indexOf(id); const target = index + delta;
    if (index < 0 || target < 0 || target >= ids.length) return;
    const current = ids[index]!;
    ids[index] = ids[target]!;
    ids[target] = current;
    setViewEntryOrder(ids);
  };
  const dropOrder = (targetId: string, zone: 'before' | 'after') => {
    if (!dragEntry || dragEntry === targetId) return;
    const ids = reorderViewEntryIds(orderedEntries.map((entry) => entry.id), dragEntry, targetId, zone);
    setViewEntryOrder(ids); setDragEntry(null); setDropEntry(null);
  };

  const [activeTab, setActiveTab] = useState<SettingsTab>('lifecycle');
  const [behaviorAnchor, setBehaviorAnchor] = useState<BehaviorAnchor | null>(null);
  // Search owns its own tab panel, just like every settings category.
  const [settingsQuery, setSettingsQuery] = useState('');
  const [settingsJump, setSettingsJump] = useState<{ anchor: string; nonce: number } | null>(null);
  const settingsJumpNonce = useRef(0);
  const tabPanelRef = useRef<HTMLDivElement>(null);
  const tabListRef = useRef<HTMLDivElement>(null);
  const behaviorSessionRef = useRef<HTMLElement>(null);

  // Reopening the modal always starts at Lifecycle; the two Lifecycle policy
  // shortcuts below switch to Behavior only for that interaction.
  useEffect(() => {
    if (!open) return;
    setActiveTab('lifecycle');
    setBehaviorAnchor(null);
    setSettingsQuery('');
    setSettingsJump(null);
  }, [open]);

  const openBehaviorSessionPolicy = (anchor: BehaviorAnchor) => {
    setBehaviorAnchor(anchor);
    setActiveTab('behavior');
  };

  useEffect(() => {
    const tabList = tabListRef.current;
    const activeTabElement = document.getElementById(`app-settings-tab-${activeTab}`);
    if (!tabList || !activeTabElement) return;

    const listRect = tabList.getBoundingClientRect();
    const tabRect = activeTabElement.getBoundingClientRect();
    if (tabRect.left < listRect.left) {
      tabList.scrollLeft -= listRect.left - tabRect.left;
    } else if (tabRect.right > listRect.right) {
      tabList.scrollLeft += tabRect.right - listRect.right;
    }
  }, [activeTab]);

  const [reloadScope, setReloadScope] = useState<ReloadScope | null>(null);
  // Keep each reload outcome with the page and controls that own it.
  const [reloadSection, setReloadSection] = useState<ReloadScope | null>(null);
  const [reloadResult, setReloadResult] = useState<ApiConfigReloadResponse | null>(null);
  const [reloadError, setReloadError] = useState<string | null>(null);

  const [codexModels, setCodexModels] = useState<ApiModelsResponse | null>(null);
  const [codexModelsLoading, setCodexModelsLoading] = useState(false);
  const [codexRefreshBusy, setCodexRefreshBusy] = useState(false);
  const [codexRefreshResult, setCodexRefreshResult] = useState<{
    before: string[];
    after: string[];
  } | null>(null);
  const [codexRefreshError, setCodexRefreshError] = useState<string | null>(null);
  const [dataCatalog, setDataCatalog] = useState<ApiDataCatalogResponse | null>(null);
  const [dataCatalogLoading, setDataCatalogLoading] = useState(false);
  const [dataCatalogError, setDataCatalogError] = useState<string | null>(null);
  const [dataRetention, setDataRetention] = useState<ApiDataRetentionResponse | null>(null);
  const [dataRetentionDraft, setDataRetentionDraft] = useState<ApiDataRetentionResponse['policies'] | null>(null);
  const [dataRetentionLoading, setDataRetentionLoading] = useState(false);
  const [dataRetentionError, setDataRetentionError] = useState<string | null>(null);
  const [dataRetentionSaving, setDataRetentionSaving] = useState(false);
  const [dataRetentionSaveError, setDataRetentionSaveError] = useState<string | null>(null);

  // Behavior → user-kill error reports. Persisted through the global ui
  // settings; a failed PUT rejects and leaves the store on the last saved
  // value, which the switch then honestly keeps showing.
  const [killReportSaving, setKillReportSaving] = useState(false);
  const [killReportError, setKillReportError] = useState<string | null>(null);

  // Locker desktop notifications: the switch stores a preference, the browser
  // owns the permission. Re-read it on every open, because the user can change
  // it in the browser's site settings while Pan is running.
  const [lockerPermission, setLockerPermission] = useState<LockerNotificationPermission>(
    lockerNotificationPermission,
  );
  useEffect(() => {
    if (open) setLockerPermission(lockerNotificationPermission());
  }, [open]);

  const toggleLockerDesktopNotify = async (next: boolean) => {
    setLockerDesktop(next);
    if (!next) return;
    // Permission must be requested inside the switch's user gesture. A denied
    // answer keeps the stored preference: allowing the site later starts the
    // alerts without another visit to this panel.
    setLockerPermission(await requestLockerNotificationPermission());
  };

  const toggleReportWorkerKillErrors = useCallback(async (value: boolean) => {
    if (killReportSaving) return;
    setKillReportSaving(true);
    setKillReportError(null);
    try {
      await saveReportWorkerKillErrors(value);
    } catch (error) {
      setKillReportError(error instanceof Error ? error.message : String(error));
    } finally {
      setKillReportSaving(false);
    }
  }, [killReportSaving, saveReportWorkerKillErrors]);

  // Behavior → message auto force send. The store only changes after the
  // server confirms the write, so a failed save keeps the previous switch
  // position and shows an inline error instead of pretending success.
  const [autoForceSaving, setAutoForceSaving] = useState<keyof AutoForceSendSettings | null>(null);
  const [autoForceError, setAutoForceError] = useState<string | null>(null);

  const toggleAutoForceSend = useCallback(async (
    key: keyof AutoForceSendSettings,
    value: boolean,
  ) => {
    if (autoForceSaving) return;
    setAutoForceSaving(key);
    setAutoForceError(null);
    try {
      await saveAutoForceSend({ ...autoForceSend, [key]: value });
    } catch (error) {
      setAutoForceError(error instanceof Error ? error.message : String(error));
    } finally {
      setAutoForceSaving(null);
    }
  }, [autoForceSend, autoForceSaving, saveAutoForceSend]);
  const [behaviorSaving, setBehaviorSaving] = useState(false);
  const toggleIllegalAutoLock = useCallback(async (value: boolean) => {
    if (behaviorSaving) return;
    setBehaviorSaving(true);
    try { await saveBehavior({ autoLockIllegalQueue: value }); }
    catch (error) { useUIStore.getState().showToast(error instanceof Error ? error.message : 'Behavior save failed', 'error'); }
    finally { setBehaviorSaving(false); }
  }, [behaviorSaving, saveBehavior]);

  // Worker config edit dialog — opened from the "Edit worker config" row.
  // Prefills current values (reloadConfig('worker').before — idempotent),
  // saves via PUT /api/settings/worker (persist + hot-apply), and reuses
  // the config-reload result block to show the returned before→after.
  const [workerEditOpen, setWorkerEditOpen] = useState(false);
  const [workerValues, setWorkerValues] = useState<Record<string, string>>({
    timeout_sec: '',
    task_timeout_sec: '',
    idle_sec: '',
  });
  const [workerLoading, setWorkerLoading] = useState(false);
  const [workerSaving, setWorkerSaving] = useState(false);
  const [workerEditError, setWorkerEditError] = useState<string | null>(null);

  // Remote tunnel state — fetched when the modal opens. The whole
  // "Remote / Tunnel" section only renders when config.json has a remote
  // section AND remote.enabled is true (backend /api/remote/status).
  const [remoteStatus, setRemoteStatus] = useState<ApiRemoteStatusResponse | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [mainRestartStatus, setMainRestartStatus] = useState<ApiMainRestartStatusResponse | null>(
    null,
  );
  // Restart/exit need a supervisor that can stop and respawn this process.
  // The platform probe states whether the server implements that at all; an
  // unanswered probe leaves the existing status-driven behavior untouched.
  const serviceLifecycle = useCapability('service.lifecycle');
  const platform = usePlatformStore((state) => state.platform);
  const platformCapabilities = usePlatformStore((state) => state.capabilities);
  const lifecycleUnavailableLabel = `Not available on this platform (${platformLabel(platform)})`;
  const [mainRestartState, setMainRestartState] = useState<MainRestartState>('idle');
  const [mainRestartHealthCheckActive, setMainRestartHealthCheckActive] = useState(false);
  const [mainRestartError, setMainRestartError] = useState<string | null>(null);
  const [mainExitStatus, setMainExitStatus] = useState<ApiMainExitStatusResponse | null>(null);
  const [mainExitState, setMainExitState] = useState<MainExitState>('idle');
  const [mainExitError, setMainExitError] = useState<string | null>(null);
  const [mainLifecycleMarkRunningOffline, setMainLifecycleMarkRunningOffline] =
    useState<boolean | null>(null);
  const [mainLifecycleStatusLoading, setMainLifecycleStatusLoading] = useState(false);
  const [mainLifecycleStatusError, setMainLifecycleStatusError] = useState<string | null>(null);
  const [sessionLifecycle, setSessionLifecycle] = useState(DEFAULT_SESSION_LIFECYCLE);
  const [sessionLifecycleLoaded, setSessionLifecycleLoaded] = useState(false);
  const [sessionLifecycleLoading, setSessionLifecycleLoading] = useState(false);
  const [sessionLifecycleSaving, setSessionLifecycleSaving] = useState(false);
  const [sessionLifecycleError, setSessionLifecycleError] = useState<string | null>(null);
  const [sessionLifecycleReloadSeq, setSessionLifecycleReloadSeq] = useState(0);

  // Settings search covers every group the tabs render. The availability gates
  // mirror the conditions the panels themselves use, so a hit never points at a
  // control the current permission or config keeps hidden.
  const authorizationLoggedIn = useAutomationAuth((state) =>
    Boolean(state.token || state.authenticated),
  );
  const settingsSearchActive = settingsQuery.trim().length > 0;
  const availability = useSettingsAvailability();
  const shortcutActions = useShortcutActions();
  const settingsSearchEntries = useMemo(() => [...SETTINGS_SEARCH_ENTRIES,
    ...createSettingsIndex(availability.pluginSources.flatMap(source => ['auto-report', 'prompt-summary'].map(scope => ({
      id: scope + '.plugin.' + source.id, tab: 'behavior' as const, label: source.label,
      hint: settingsText(scope), anchor: scope + '.plugin.' + source.id,
    })))),
    ...createSettingsIndex(shortcutActions.map(action => ({id: 'shortcut.' + action.id,
      tab: 'shortcuts' as const, label: action.label, hint: action.description, anchor: 'shortcut.' + action.id}))),
  ], [shortcutActions, availability.pluginSources]);
  const settingsSearchResults = useMemo(
    () =>
      searchSettings(settingsSearchEntries, settingsQuery, {
        remoteTunnel: Boolean(remoteStatus?.available && remoteStatus.enabled),
        authorizationLoggedIn,
        ...availability,
        completionContacts: Boolean(notifications.completionBridge?.qqReport || notifications.completionBridge?.qqSubscribe),
        notificationContacts: Boolean(notifications.lockerUnread?.qqReport || notifications.newError?.qqReport),
        capabilities: platformCapabilities,
        codexSession: currentSession?.adapter === 'codex' && !currentSession.readonlySession,
      }),
    [settingsQuery, remoteStatus, authorizationLoggedIn, platformCapabilities, currentSession, settingsSearchEntries, availability, notifications],
  );

  const openSettingsSearchHit = useCallback((entry: SettingsSearchEntry) => {
    settingsJumpNonce.current += 1;
    setSettingsQuery('');
    setActiveTab(entry.tab);
    setSettingsJump({ anchor: entry.anchor, nonce: settingsJumpNonce.current });
  }, []);

  // Choosing a tab by hand leaves the search results, so the tab shows its own panel.
  const activateSettingsTab = useCallback((tab: SettingsTab) => {
    setSettingsQuery('');
    setActiveTab(tab);
  }, []);

  // Scroll the hit group into view once its tab is on screen, then flash it so
  // the control is easy to find in an otherwise unchanged panel.
  useEffect(() => {
    if (!settingsJump) return;
    const panel = tabPanelRef.current;
    if (!panel) return;
    const heading = findSettingsAnchor(panel, settingsJump);
    const target = heading ?? panel;
    if (typeof target.scrollIntoView === 'function') target.scrollIntoView({ block: 'center' });
    panel.focus({ preventScroll: true });
    if (!heading) return;
    const ring = ['ring-2', 'ring-accent/40', 'rounded-md'];
    heading.classList.add(...ring);
    const timer = window.setTimeout(() => heading.classList.remove(...ring), 1600);
    return () => {
      window.clearTimeout(timer);
      heading.classList.remove(...ring);
    };
  }, [settingsJump]);

  useEffect(() => {
    if (!open || activeTab !== 'behavior' || behaviorAnchor === null) return;
    const selectId =
      behaviorAnchor === 'exit-strategy' ? 'session-exit-strategy' : 'session-startup-preference';
    const control = document.getElementById(selectId);
    if (control) {
      control.scrollIntoView?.({ block: 'center' });
      control.focus({ preventScroll: true });
      setBehaviorAnchor(null);
      return;
    }
    // Preferences load asynchronously. Once loading settles without the
    // control (load error), land keyboard users on the section and its Retry.
    if (sessionLifecycleLoaded || sessionLifecycleError) {
      behaviorSessionRef.current?.scrollIntoView?.({ block: 'center' });
      behaviorSessionRef.current?.focus({ preventScroll: true });
      setBehaviorAnchor(null);
    }
  }, [open, activeTab, behaviorAnchor, sessionLifecycleLoaded, sessionLifecycleError]);

  const recoveryAbortRef = useRef<AbortController | null>(null);
  const recoveryCancelledRef = useRef(false);
  const mainLifecycleGenerationRef = useRef(0);
  const mainLifecycleActionInFlightRef = useRef(false);
  const modalOpenRef = useRef(open);
  modalOpenRef.current = open;
  const showToast = useUIStore((s) => s.showToast);

  const refreshMainLifecycleStatus = useCallback(async (
    requestedGeneration?: number,
    preserveActionError?: { operation: 'restart' | 'exit'; message: string },
  ) => {
    const generation = requestedGeneration ?? ++mainLifecycleGenerationRef.current;
    setMainLifecycleStatusLoading(true);
    setMainLifecycleStatusError(null);
    try {
      const [restartStatus, exitStatus] = await Promise.all([
        fetchMainRestartStatus(),
        fetchMainExitStatus(),
      ]);
      if (mainLifecycleGenerationRef.current !== generation) return;
      setMainRestartStatus(restartStatus);
      setMainExitStatus(exitStatus);
      const restartPending = restartStatus.pending && restartStatus.operation !== 'exit';
      const restartFailed = restartStatus.phase === 'failed' ||
        restartStatus.phase === 'timed_out';
      const exitFailed = exitStatus.phase === 'failed' || exitStatus.phase === 'timed_out';
      const restartErrorMessage = restartStatus.error ||
        (preserveActionError?.operation === 'restart' ? preserveActionError.message : null) ||
        (restartFailed
          ? restartStatus.phase === 'timed_out'
            ? 'Pan restart supervisor timed out.'
            : 'Pan restart failed in the supervisor.'
          : null);
      const exitErrorMessage = exitStatus.error ||
        (preserveActionError?.operation === 'exit' ? preserveActionError.message : null) ||
        (exitFailed ? 'Pan Exit failed in the lifecycle supervisor.' : null);
      setMainRestartState(restartPending
        ? 'restarting'
        : restartStatus.phase === 'timed_out'
          ? 'timeout'
          : restartFailed || preserveActionError?.operation === 'restart'
            ? 'error'
            : 'idle');
      setMainRestartError(restartPending
        ? null
        : restartErrorMessage);
      setMainExitState(exitStatus.pending
        ? 'exiting'
        : exitFailed || preserveActionError?.operation === 'exit'
          ? 'error'
          : 'idle');
      setMainExitError(exitStatus.pending
        ? null
        : exitErrorMessage);
      setMainLifecycleMarkRunningOffline(null);
    } catch (error) {
      if (mainLifecycleGenerationRef.current !== generation) return;
      const reason = error instanceof Error ? error.message : String(error);
      setMainRestartStatus({
        available: false,
        pending: false,
        platform: 'unknown',
        reason,
      });
      setMainExitStatus({
        available: false,
        pending: false,
        platform: 'unknown',
        stage: 'error',
        reason,
      });
      setMainRestartState('idle');
      setMainRestartHealthCheckActive(false);
      setMainExitState('idle');
      setMainLifecycleStatusError(
        'Could not refresh Pan lifecycle status: ' + reason +
          '. Refresh status before scheduling Restart or Exit.',
      );
    } finally {
      if (mainLifecycleGenerationRef.current === generation) {
        setMainLifecycleStatusLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchRemoteStatus()
      .then((s) => {
        if (!cancelled) setRemoteStatus(s);
      })
      .catch(() => {
        /* modal still usable without the remote section */
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSessionLifecycleLoading(true);
    setSessionLifecycleLoaded(false);
    setSessionLifecycleError(null);
    fetchSessionLifecyclePreferences()
      .then((preferences) => {
        if (cancelled) return;
        setSessionLifecycle(normalizeSessionLifecyclePreferences(preferences));
        setSessionLifecycleLoaded(true);
      })
      .catch((error) => {
        if (!cancelled) {
          setSessionLifecycleError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setSessionLifecycleLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, sessionLifecycleReloadSeq]);

  useEffect(() => {
    const generation = ++mainLifecycleGenerationRef.current;
    recoveryCancelledRef.current = true;
    recoveryAbortRef.current?.abort();
    recoveryAbortRef.current = null;
    if (open) {
      setMainRestartStatus(null);
      setMainRestartState('idle');
      setMainRestartHealthCheckActive(false);
      setMainRestartError(null);
      setMainExitStatus(null);
      setMainExitState('idle');
      setMainExitError(null);
      setMainLifecycleMarkRunningOffline(null);
      setMainLifecycleStatusError(null);
      void refreshMainLifecycleStatus(generation);
    }
    return () => {
      if (mainLifecycleGenerationRef.current === generation) {
        mainLifecycleGenerationRef.current += 1;
      }
      recoveryCancelledRef.current = true;
      recoveryAbortRef.current?.abort();
      recoveryAbortRef.current = null;
    };
  }, [open, refreshMainLifecycleStatus]);

  useEffect(() => {
    if (!open || activeTab !== 'adapters') return;
    let cancelled = false;
    setCodexModelsLoading(true);
    fetchCodexModels()
      .then((models) => {
        if (!cancelled) setCodexModels(models);
      })
      .catch((e) => {
        if (!cancelled) setCodexRefreshError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setCodexModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, activeTab]);

  useEffect(() => {
    if (!open || activeTab !== 'data') return;
    let cancelled = false;
    setDataCatalogLoading(true);
    setDataCatalogError(null);
    fetchDataCatalog()
      .then((catalog) => {
        if (!cancelled) setDataCatalog(catalog);
      })
      .catch((e) => {
        if (!cancelled) {
          setDataCatalogError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setDataCatalogLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, activeTab]);

  useEffect(() => {
    if (!open) {
      setDataRetention(null);
      setDataRetentionDraft(null);
      setDataRetentionError(null);
      setDataRetentionSaveError(null);
      setDataRetentionLoading(false);
      return;
    }
    if (activeTab !== 'data' || dataRetention !== null) return;
    let cancelled = false;
    setDataRetentionLoading(true);
    setDataRetentionError(null);
    fetchDataRetention()
      .then((settings) => {
        if (cancelled) return;
        setDataRetention(settings);
        setDataRetentionDraft(settings.policies);
      })
      .catch((e) => {
        if (!cancelled) setDataRetentionError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setDataRetentionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, activeTab, dataRetention]);

  const dataRetentionDirty = Boolean(
    dataRetention && dataRetentionDraft
    && JSON.stringify(dataRetention.policies) !== JSON.stringify(dataRetentionDraft),
  );

  const updateDataRetentionDraft = (
    id: DataRetentionPolicyId,
    field: 'enabled' | 'days',
    value: boolean | number | null,
  ) => {
    setDataRetentionDraft((current) => current && ({
      ...current,
      [id]: { ...current[id], [field]: value },
    }));
  };

  const saveDataRetentionDraft = async () => {
    if (!dataRetentionDraft) return;
    setDataRetentionSaving(true);
    setDataRetentionSaveError(null);
    try {
      const result = await updateDataRetention({ policies: dataRetentionDraft });
      setDataRetention(result);
      setDataRetentionDraft(result.policies);
    } catch (e) {
      setDataRetentionSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setDataRetentionSaving(false);
    }
  };

  const saveSessionLifecycle = async (patch: Partial<ApiSessionLifecyclePreferences>) => {
    if (!sessionLifecycleLoaded || sessionLifecycleSaving) return;
    setSessionLifecycleSaving(true);
    setSessionLifecycleError(null);
    try {
      const saved = await updateSessionLifecyclePreferences({ ...sessionLifecycle, ...patch });
      setSessionLifecycle(normalizeSessionLifecyclePreferences(saved));
    } catch (error) {
      setSessionLifecycleError(error instanceof Error ? error.message : String(error));
    } finally {
      setSessionLifecycleSaving(false);
    }
  };

  const handleCodexRefresh = async () => {
    setCodexRefreshBusy(true);
    setCodexRefreshResult(null);
    setCodexRefreshError(null);
    try {
      const result = await refreshCodexOfficialModels();
      setCodexRefreshResult({ before: result.before, after: result.after });
      setCodexModels({ models: result.after, default: codexModels?.default ?? '' });
    } catch (e) {
      setCodexRefreshError(e instanceof Error ? e.message : String(e));
    } finally {
      setCodexRefreshBusy(false);
    }
  };

  const cancelMainRestartCheck = () => {
    if (!mainRestartHealthCheckActive) return;
    recoveryCancelledRef.current = true;
    recoveryAbortRef.current?.abort();
    recoveryAbortRef.current = null;
    setMainRestartState('cancelled');
    setMainRestartError(null);
  };

  const waitForMainRestartRecovery = async (generation: number) => {
    const controller = new AbortController();
    recoveryAbortRef.current = controller;
    setMainRestartHealthCheckActive(true);
    const isCurrent = () =>
      mainLifecycleGenerationRef.current === generation && modalOpenRef.current;
    const wait = (milliseconds: number) =>
      new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(resolve, milliseconds);
        const abort = () => {
          window.clearTimeout(timer);
          reject(new DOMException('Health check cancelled', 'AbortError'));
        };
        controller.signal.addEventListener('abort', abort, { once: true });
      });

    try {
      // Avoid treating the still-running old process as the recovered one.
      await wait(1000);
      for (let attempt = 0; attempt < 20; attempt += 1) {
        if (controller.signal.aborted || !isCurrent())
          throw new DOMException('Health check cancelled', 'AbortError');
        const probe = new AbortController();
        const forwardAbort = () => probe.abort();
        controller.signal.addEventListener('abort', forwardAbort, { once: true });
        const timeout = window.setTimeout(() => probe.abort(), 1000);
        let healthy = false;
        try {
          await fetchHealth(probe.signal);
          healthy = true;
        } catch {
          // The expected connection refusal during stop/start is retried.
        } finally {
          window.clearTimeout(timeout);
        }
        if (healthy && !controller.signal.aborted && isCurrent()) {
          let persisted: ApiMainRestartStatusResponse;
          try {
            persisted = await fetchMainRestartStatus();
          } catch (error) {
            if (isCurrent()) {
              const reason = error instanceof Error ? error.message : String(error);
              const message = 'Could not refresh Pan lifecycle status: ' + reason +
                '. Refresh status before scheduling Restart or Exit.';
              setMainLifecycleStatusError(
                message,
              );
              setMainRestartStatus({
                available: false, pending: false, platform: 'unknown', reason,
              });
              setMainExitStatus({
                available: false, pending: false, platform: 'unknown',
                stage: 'error', reason,
              });
              setMainRestartState('error');
              setMainRestartError(message);
              setMainExitState('idle');
            }
            return;
          }
          if (!isCurrent() || controller.signal.aborted) return;
          setMainRestartStatus(persisted);
          if (persisted.phase === 'failed' || persisted.phase === 'timed_out') {
            const message = persisted.error || 'Pan restart failed in the supervisor.';
            setMainRestartState(persisted.phase === 'timed_out' ? 'timeout' : 'error');
            setMainRestartError(message);
            showToast(message, 'error');
            return;
          }
          if (persisted.pending) {
            setMainRestartState('restarting');
            await wait(750);
            continue;
          }
          setMainRestartState('restored');
          setMainRestartError(null);
          showToast('Pan main service is back online', 'info');
          return;
        }
        await wait(750);
      }
      let persisted: ApiMainRestartStatusResponse | null;
      try {
        persisted = await fetchMainRestartStatus();
      } catch (error) {
        if (isCurrent()) {
          const reason = error instanceof Error ? error.message : String(error);
          const message = 'Could not refresh Pan lifecycle status: ' + reason +
            '. Refresh status before scheduling Restart or Exit.';
          setMainRestartStatus({
            available: false, pending: false, platform: 'unknown', reason,
          });
          setMainExitStatus({
            available: false, pending: false, platform: 'unknown', stage: 'error', reason,
          });
          setMainLifecycleStatusError(message);
          setMainRestartState('error');
          setMainRestartError(message);
          setMainExitState('idle');
        }
        return;
      }
      if (!isCurrent()) return;
      if (persisted) setMainRestartStatus(persisted);
      if (persisted?.phase === 'failed' || persisted?.phase === 'timed_out') {
        setMainRestartStatus(persisted);
        setMainRestartState(persisted.phase === 'timed_out' ? 'timeout' : 'error');
        setMainRestartError(persisted.error || 'Pan restart failed in the supervisor.');
        showToast(persisted.error || 'Pan restart failed', 'error');
      } else if (persisted?.pending) {
        setMainRestartState('restarting');
        setMainRestartError('Pan restart is still pending. Refresh lifecycle status to check again.');
      } else {
        setMainRestartState('timeout');
        setMainRestartError('Pan was restarted, but health check timed out after 16 seconds.');
        showToast('Pan restart health check timed out', 'error');
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (isCurrent()) {
        setMainRestartState('error');
        setMainRestartError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (recoveryAbortRef.current === controller) recoveryAbortRef.current = null;
      if (isCurrent()) setMainRestartHealthCheckActive(false);
    }
  };

  const handleMainRestart = async () => {
    if (
      !serviceLifecycle ||
      mainLifecycleActionInFlightRef.current ||
      mainLifecycleStatusLoading ||
      mainLifecycleStatusError ||
      !mainRestartStatus?.available ||
      !mainExitStatus?.available ||
      mainRestartStatus.pending ||
      mainExitStatus.pending ||
      !sessionLifecycleLoaded ||
      sessionLifecycleSaving
    ) return;
    const options = mainLifecycleOptions(
      sessionLifecycle.exitStrategy,
      mainLifecycleMarkRunningOffline,
    );
    if (options === null) return;
    mainLifecycleActionInFlightRef.current = true;
    const generation = ++mainLifecycleGenerationRef.current;
    recoveryCancelledRef.current = false;
    setMainRestartState('restarting');
    setMainRestartError(null);
    setMainRestartStatus((previous) => (previous ? { ...previous, pending: true } : previous));
    try {
      await restartMainService(options);
      if (mainLifecycleGenerationRef.current !== generation || recoveryCancelledRef.current) return;
      showToast('Pan restart scheduled; waiting for service recovery', 'info');
      void waitForMainRestartRecovery(generation);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (mainLifecycleGenerationRef.current === generation) {
        setMainRestartState('error');
        setMainRestartError(message);
        await refreshMainLifecycleStatus(generation, { operation: 'restart', message });
      }
    } finally {
      mainLifecycleActionInFlightRef.current = false;
      if (mainLifecycleGenerationRef.current !== generation && modalOpenRef.current) {
        void refreshMainLifecycleStatus();
      }
    }
  };

  const handleMainExit = async () => {
    if (
      !serviceLifecycle ||
      mainLifecycleActionInFlightRef.current ||
      mainLifecycleStatusLoading ||
      mainLifecycleStatusError ||
      !mainRestartStatus?.available ||
      !mainExitStatus?.available ||
      mainRestartStatus.pending ||
      mainExitStatus.pending ||
      !sessionLifecycleLoaded ||
      sessionLifecycleSaving
    ) return;
    const options = mainLifecycleOptions(
      sessionLifecycle.exitStrategy,
      mainLifecycleMarkRunningOffline,
    );
    if (options === null) return;
    mainLifecycleActionInFlightRef.current = true;
    const generation = ++mainLifecycleGenerationRef.current;
    setMainExitState('exiting');
    setMainExitError(null);
    setMainExitStatus((previous) => (previous ? { ...previous, pending: true } : previous));
    try {
      await exitMainService(options);
      if (mainLifecycleGenerationRef.current !== generation) return;
      setMainExitState('exited');
      showToast('Pan exit scheduled; this service will stop', 'info');
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (mainLifecycleGenerationRef.current === generation) {
        setMainExitState('error');
        setMainExitError(message);
        await refreshMainLifecycleStatus(generation, { operation: 'exit', message });
      }
    } finally {
      mainLifecycleActionInFlightRef.current = false;
      if (mainLifecycleGenerationRef.current !== generation && modalOpenRef.current) {
        void refreshMainLifecycleStatus();
      }
    }
  };

  const handleRemoteRestart = async () => {
    setRemoteBusy(true);
    try {
      const r = await restartRemoteTunnel();
      const killed = r.killed?.length ?? 0;
      showToast(
        r.restarted
          ? `Tunnel restarted${killed ? ` (stopped ${killed} old process${killed > 1 ? 'es' : ''})` : ''}`
          : 'Tunnel stop/start issued, but process not detected yet',
        r.restarted ? 'info' : 'error',
      );
      const s = await fetchRemoteStatus();
      setRemoteStatus(s);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Tunnel restart failed', 'error');
    } finally {
      setRemoteBusy(false);
    }
  };

  const handleReload = async (scope: ReloadScope) => {
    setReloadScope(scope);
    setReloadSection(scope);
    setReloadResult(null);
    setReloadError(null);
    try {
      const r = await reloadConfig(scope);
      setReloadResult(r);
      if (!r.reloaded) {
        setReloadError(r.errors?.join('; ') || 'Reload failed');
      }
    } catch (e) {
      setReloadError(e instanceof Error ? e.message : String(e));
    } finally {
      setReloadScope(null);
    }
  };

  const openWorkerEdit = async () => {
    setWorkerEditOpen(true);
    setWorkerEditError(null);
    setWorkerLoading(true);
    try {
      const r = await reloadConfig('worker');
      const b = r.worker?.before ?? {};
      setWorkerValues({
        timeout_sec: b.timeout_sec !== undefined ? String(b.timeout_sec) : '',
        task_timeout_sec: b.task_timeout_sec !== undefined ? String(b.task_timeout_sec) : '',
        idle_sec: b.idle_sec !== undefined ? String(b.idle_sec) : '',
      });
    } catch (e) {
      setWorkerEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkerLoading(false);
    }
  };

  const handleWorkerSave = async () => {
    // All three fields are required and must be positive finite numbers.
    const patch: Record<string, number> = {};
    for (const k of WORKER_KEYS) {
      const raw = (workerValues[k] ?? '').trim();
      const v = Number(raw);
      if (!raw || !Number.isFinite(v) || v <= 0) {
        setWorkerEditError(`${k} must be a positive number (seconds)`);
        return;
      }
      patch[k] = v;
    }
    setWorkerSaving(true);
    setWorkerEditError(null);
    try {
      const r = await updateWorkerSettings(patch);
      // Reuse the config-reload result block to render before→after.
      setReloadResult({ reloaded: true, worker: r });
      setReloadSection('worker');
      setReloadError(null);
      setWorkerEditOpen(false);
      showToast('Worker config saved and applied', 'info');
    } catch (e) {
      setWorkerEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkerSaving(false);
    }
  };

  // Close on Escape — the worker edit dialog (when open) takes priority
  // over closing the whole settings modal.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (workerEditOpen) setWorkerEditOpen(false);
      else onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose, workerEditOpen]);

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={settingsText("app-settings-modal.26")}
      className="app-settings-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }} data-settings-id="app-settings-modal.26"
    >
      <div className="app-settings-card flex flex-col w-full h-full bg-bg-primary overflow-hidden md:w-[75vw] md:h-[75vh] md:rounded-lg md:border md:border-border-default md:shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-border-default px-4 py-3 md:px-6 md:py-4 shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-text-primary" data-settings-id="app-settings-modal.27">{settingsText("app-settings-modal.27")}</h2>
            <p className="mt-0.5 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.28">
              {settingsText("app-settings-modal.28")}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={settingsText("app-settings-modal.29")}
            className="text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary p-1.5 rounded transition-colors shrink-0" data-settings-id="app-settings-modal.29"
          >
            <X size={16} />
          </button>
        </div>

        <div className="shrink-0 min-w-0 border-b border-border-default px-4 md:px-6">
          <div
            ref={tabListRef}
            role="tablist"
            aria-label={settingsText("app-settings-modal.30")}
            className="flex min-w-0 overflow-x-auto overflow-y-hidden overscroll-x-contain touch-pan-x [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            onKeyDown={(event) => {
              const currentIndex = SETTINGS_TABS.indexOf(activeTab);
              const nextIndex =
                event.key === 'ArrowRight'
                  ? (currentIndex + 1) % SETTINGS_TABS.length
                  : event.key === 'ArrowLeft'
                    ? (currentIndex - 1 + SETTINGS_TABS.length) % SETTINGS_TABS.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? SETTINGS_TABS.length - 1
                        : null;
              if (nextIndex === null) return;
              event.preventDefault();
              const nextTab = SETTINGS_TABS[nextIndex]!;
              document.getElementById(`app-settings-tab-${nextTab}`)?.focus();
              activateSettingsTab(nextTab);
            }} data-settings-id="app-settings-modal.30"
          >
            {SETTINGS_TABS.map((tab) => {
              const { label, Icon } = SETTINGS_TAB_META[tab];
              const selected = activeTab === tab;
              return (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  id={`app-settings-tab-${tab}`}
                  aria-controls="app-settings-tabpanel"
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => activateSettingsTab(tab)}
                  className={`inline-flex min-w-[70px] shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs transition-colors ${
                    selected
                      ? 'border-accent text-text-primary'
                      : 'border-transparent text-text-tertiary hover:text-text-primary'
                  }`}
                >
                  <Icon size={14} />
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Body */}
        <div
          ref={tabPanelRef}
          role="tabpanel"
          id="app-settings-tabpanel"
          aria-labelledby={`app-settings-tab-${activeTab}`}
          tabIndex={0}
          className="flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5 space-y-6"
        >
          {activeTab === 'search' ? (
            <section className="mx-auto w-full max-w-2xl space-y-4" aria-label={settingsText('settings-search-panel')}>
              <div>
                <h3 className="text-sm font-semibold text-text-primary">{settingsText('settings-search-heading')}</h3>
                <p className="mt-1 mb-3 text-xs text-text-tertiary">Find a setting across all categories.</p>
                <SettingsSearchField value={settingsQuery} onChange={setSettingsQuery} />
              </div>
              {settingsSearchActive ? <SettingsSearchResults
              query={settingsQuery}
              results={settingsSearchResults}
              onSelect={openSettingsSearchHit}
              /> : <p className="text-xs text-text-tertiary">Enter a setting name or keyword to get started.</p>}
            </section>
          ) : activeTab === 'authorization' ? (
            <AuthorizationSettings />
          ) : activeTab === 'adapters' ? (
            <>
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="codex-adapter">
                {settingsText("codex-adapter")}</h3>
              <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                <SwitchRow
                  label={settingsText("app-settings-modal.31")}
                  hint={settingsText("app-settings-modal.32")}
                  checked={showCodexTerminalInput}
                  onChange={setShowCodexTerminalInput} settingId="app-settings-modal.31"
                />
                <CodexNativePassthrough
                  sessionId={currentSession?.adapter === 'codex' && !currentSession.readonlySession ? currentSession.id : null}
                />
                <div className="px-3 py-2">
                  <div className="text-xs text-text-primary" data-settings-id="app-settings-modal.33">{settingsText("app-settings-modal.33")}</div>
                  <div className="mt-1 text-[10px] text-text-tertiary font-mono break-words">
                    {codexModelsLoading
                      ? 'Loading…'
                      : codexModels?.models.length
                        ? codexModels.models.join(', ')
                        : 'No models configured'}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={codexRefreshBusy}
                  onClick={handleCodexRefresh}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="min-w-0">
                    <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.34">{settingsText("app-settings-modal.34")}</span>
                    <span className="block text-[10px] text-text-tertiary font-mono mt-0.5" data-settings-id="app-settings-modal.35">
                      {settingsText("app-settings-modal.35")}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-text-tertiary">
                    {codexRefreshBusy ? 'Refreshing…' : 'Replace'}
                  </span>
                </button>
              </div>
              {codexRefreshError && (
                <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                  {codexRefreshError}
                </div>
              )}
              {!codexRefreshError && codexRefreshResult && (
                <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                  <div data-settings-id="app-settings-modal.36">{settingsText("app-settings-modal.36")}{codexRefreshResult.before.join(', ') || '(empty)'}</div>
                  <div data-settings-id="app-settings-modal.37">{settingsText("app-settings-modal.37")}{codexRefreshResult.after.join(', ') || '(empty)'}</div>
                </div>
              )}
            </section>
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="adapter-reload">
                {settingsText("adapter-reload")}</h3>
              <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                <ReloadRow
                  label={settingsText("app-settings-modal.38")}
                  hint={settingsText("app-settings-modal.39")}
                  busy={reloadScope === 'adapters'}
                  onClick={() => handleReload('adapters')} settingId="app-settings-modal.38"
                />
              </div>
              {reloadError && reloadSection === 'adapters' && (
                <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                  {reloadError}
                </div>
              )}
              {!reloadError && reloadResult && reloadSection === 'adapters' && (
                <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                  {reloadResult.adapters?.map((adapter) => (
                    <div key={adapter.name} data-settings-id="app-settings-modal.40">
                      {adapter.name}: {adapter.modelsBefore ?? '?'} → {adapter.modelsAfter ?? '?'} {settingsText("app-settings-modal.40")}</div>
                  ))}
                </div>
              )}
              <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.41">
                {settingsText("app-settings-modal.41")}</p>
            </section>
            </>
          ) : activeTab === 'notifications' ? (
            <section>
              <CompletionBridgeDefaultsPanel />
              <SourceNotificationPanel source="lockerUnread" />
              <SourceNotificationPanel source="newError" />
              <UnreadRecordingPanel />
              <div className="mb-5" data-testid="locker-desktop-notify-section">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="locker-desktop">
                  {settingsText("locker-desktop")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.42")}
                    hint={
                      lockerPermission === 'unsupported'
                        ? 'This browser does not support system notifications.'
                        : 'System notification only while this page is in the background.'
                    }
                    checked={notifications.lockerDesktop}
                    disabled={lockerPermission === 'unsupported'}
                    onChange={(v) => void toggleLockerDesktopNotify(v)} settingId="app-settings-modal.42"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.43">
                  {settingsText("app-settings-modal.43")}</p>
                {notifications.lockerDesktop && lockerPermission === 'denied' && (
                  <div
                    role="alert"
                    data-testid="locker-notification-denied"
                    className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger" data-settings-id="app-settings-modal.44"
                  >
                    {settingsText("app-settings-modal.44")}</div>
                )}
              </div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="cli-adapter-warnings">
                {settingsText("cli-adapter-warnings")}</h3>
              <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                <SwitchRow
                  label={settingsText("app-settings-modal.45")}
                  hint={settingsText("app-settings-modal.46")}
                  checked={notifications.codexWarningToast}
                  onChange={setCodexWarningToast} settingId="app-settings-modal.45"
                />
                <div className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left opacity-60">
                  <span className="min-w-0">
                    <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.47">{settingsText("app-settings-modal.47")}</span>
                    <span className="block text-[10px] text-text-tertiary font-mono mt-0.5" data-settings-id="app-settings-modal.48">
                      {settingsText("app-settings-modal.48")}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.49">{settingsText("app-settings-modal.49")}</span>
                </div>
              </div>
              <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.50">
                {settingsText("app-settings-modal.50")}</p>
            </section>
          ) : activeTab === 'appearance' ? (
            <>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="panel-order">{settingsText("panel-order")}</h3>
                <div data-testid="panel-order-list" className="rounded-md border border-border-muted bg-bg-primary divide-y divide-border-muted">
                  {orderedEntries.map((entry, index) => <div key={entry.id}
                    className={`relative flex items-center gap-2 px-3 py-2 text-xs ${dropEntry?.id === entry.id ? (dropEntry.zone === 'before' ? 'border-t-2' : 'border-b-2') + ' border-accent' : ''}`}
                    draggable onDragStart={() => setDragEntry(entry.id)}
                    onDragOver={(event) => { event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); const zone = resolveDropZone(rect.top, rect.height, event.clientY); setDropEntry(zone === 'center' ? null : { id: entry.id, zone }); }}
                    onDragEnd={() => { setDragEntry(null); setDropEntry(null); }}
                    onDrop={(event) => { event.preventDefault(); if (dropEntry?.id === entry.id) dropOrder(entry.id, dropEntry.zone); }}>
                    <entry.icon size={14} className="shrink-0 text-text-tertiary" /><span className="min-w-0 flex-1 truncate">{entry.label}</span>
                    <button type="button" aria-label={`Move ${entry.label} up`} disabled={index === 0} onClick={() => moveEntry(entry.id, -1)} className="px-1 disabled:opacity-30">↑</button>
                    <button type="button" aria-label={`Move ${entry.label} down`} disabled={index === orderedEntries.length - 1} onClick={() => moveEntry(entry.id, 1)} className="px-1 disabled:opacity-30">↓</button>
                  </div>)}
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.51">{settingsText("app-settings-modal.51")}</p>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="app-settings-modal.52">
                  {settingsText("app-settings-modal.52")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <ChatViewSelect value={chatViewStyle} onChange={setChatViewStyle} />
                  <SwitchRow
                    label={settingsText("app-settings-modal.53")}
                    hint={settingsText("app-settings-modal.54")}
                    checked={showChatViewSwitcher}
                    onChange={setShowChatViewSwitcher} settingId="app-settings-modal.53"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.55">
                  {settingsText("app-settings-modal.55")}</p>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="sidebar">
                  {settingsText("sidebar")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.56")}
                    hint={settingsText("app-settings-modal.57")}
                    checked={showSidebarCollapseHandle}
                    onChange={setShowSidebarCollapseHandle} settingId="app-settings-modal.56"
                  />
                </div>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="session-list">
                  {settingsText("session-list")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.58")}
                    hint={settingsText("app-settings-modal.59")}
                    checked={showGroupBy}
                    onChange={setShowGroupBy} settingId="app-settings-modal.58"
                  />
                  <div className="px-3 py-2">
                    <label
                      htmlFor="app-settings-default-group-by"
                      className="block text-xs text-text-secondary mb-1" data-settings-id="app-settings-modal.60"
                    >
                      {settingsText("app-settings-modal.60")}</label>
                    <select
                      data-settings-id="app-settings-modal.60" id="app-settings-default-group-by"
                      value={defaultGroupBy}
                      onChange={(e) => setDefaultGroupBy(e.target.value as GroupMode)}
                      className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent"
                    >
                      {GROUP_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value} data-settings-id={o.labelId}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.61">
                      {settingsText("app-settings-modal.61")}</p>
                  </div>
                </div>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="message-visibility">
                  {settingsText("message-visibility")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.62")}
                    hint={settingsText("app-settings-modal.63")}
                    checked={showMetaAgent}
                    onChange={setShowMetaAgent} settingId="app-settings-modal.62"
                  />
                  <SwitchRow
                    label={settingsText("app-settings-modal.64")}
                    hint={settingsText("app-settings-modal.65")}
                    checked={showTaskAgent}
                    onChange={setShowTaskAgent} settingId="app-settings-modal.64"
                  />
                  <SwitchRow
                    label={settingsText("app-settings-modal.66")}
                    hint={settingsText("app-settings-modal.67")}
                    checked={showQQ}
                    onChange={setShowQQ} settingId="app-settings-modal.66"
                  />
                </div>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="message-grouping">
                  {settingsText("message-grouping")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.68")}
                    hint={settingsText("app-settings-modal.69")}
                    checked={mergeConsecutiveNonBodyBlocks}
                    onChange={setMergeConsecutiveNonBodyBlocks} settingId="app-settings-modal.68"
                  />
                </div>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="scroll-position">
                  {settingsText("scroll-position")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.70")}
                    hint={settingsText("app-settings-modal.71")}
                    checked={keepScrollOnSessionSwitch}
                    onChange={setKeepScrollOnSessionSwitch} settingId="app-settings-modal.70"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.72">
                  {settingsText("app-settings-modal.72")}</p>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="quick-location">
                  {settingsText("quick-location")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.73")}
                    hint={settingsText("app-settings-modal.74")}
                    checked={showMessageNavigationRail}
                    onChange={setShowMessageNavigationRail} settingId="app-settings-modal.73"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.75">
                  {settingsText("app-settings-modal.75")}</p>
              </section>
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="history-search">
                  {settingsText("history-search")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                  label={settingsText("app-settings-modal.76")}
                  hint={settingsText("app-settings-modal.77")}
                    checked={showHistorySearch}
                    onChange={setShowHistorySearch} settingId="app-settings-modal.76"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.78">
                  {settingsText("app-settings-modal.78")}</p>
              </section>
              <section className="border-t border-border-muted pt-4">
                <h3 data-settings-id="reset-ui-preferences" className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
                  {settingsText('reset-ui-preferences')}
                </h3>
                <p className="text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.79">
                  {settingsText("app-settings-modal.79")}</p>
                <button
                  type="button"
                  onClick={resetSettings}
                  className="mt-2 text-[11px] text-text-tertiary hover:text-text-primary underline underline-offset-2" data-settings-id="app-settings-modal.80"
                >
                  {settingsText("app-settings-modal.80")}</button>
              </section>
            </>
          ) : activeTab === 'shortcuts' ? (
            <ShortcutsSettings />
          ) : activeTab === 'behavior' ? (
            <>
              <SessionErrorBridgeSetting />
              <UserCardReminderSettings />
              <AutoReportSettingsGroup />
              <section
                ref={behaviorSessionRef}
                data-testid="behavior-session-section"
                tabIndex={-1}
                className="focus:outline-none"
              >
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="session-lifecycle">
                  {settingsText("session-lifecycle")}</h3>
                {sessionLifecycleLoading && !sessionLifecycleLoaded && (
                  <p className="mb-2 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.81">{settingsText("app-settings-modal.81")}</p>
                )}
                {sessionLifecycleLoaded && (
                  <div className="space-y-3">
                    <div
                      data-settings-anchor="exit-strategy"
                      tabIndex={-1}
                      className="rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                    >
                      <label htmlFor="session-exit-strategy" className="mb-1 block text-xs text-text-secondary" data-settings-id="app-settings-modal.82">
                        {settingsText("app-settings-modal.82")}</label>
                      <select
                        data-settings-id="app-settings-modal.82" id="session-exit-strategy"
                        value={sessionLifecycle.exitStrategy}
                        disabled={
                          sessionLifecycleSaving ||
                          mainExitState === 'confirming' ||
                          mainExitState === 'exiting' ||
                          mainRestartState === 'confirming' ||
                          mainRestartState === 'restarting'
                        }
                        onChange={(event) => void saveSessionLifecycle({
                          exitStrategy: event.target.value as ApiSessionExitStrategy,
                        })}
                        className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent disabled:opacity-60"
                      >
                        <option value="ask" data-settings-id="app-settings-modal.83">{settingsText("app-settings-modal.83")}</option>
                        <option value="offline" data-settings-id="app-settings-modal.84">{settingsText("app-settings-modal.84")}</option>
                        <option value="preserve-running" data-settings-id="app-settings-modal.85">{settingsText("app-settings-modal.85")}</option>
                      </select>
                      <p className="mt-1 text-[10px] text-text-tertiary" data-settings-id="app-settings-modal.86">
                        {settingsText("app-settings-modal.86")}</p>
                    </div>
                    <div>
                      <label htmlFor="session-wake-message" className="mb-1 block text-xs text-text-secondary" data-settings-id="app-settings-modal.87">{settingsText("app-settings-modal.87")}</label>
                      <textarea data-settings-id="app-settings-modal.87" id="session-wake-message" key={sessionLifecycle.wakeMessage}
                        defaultValue={sessionLifecycle.wakeMessage || '继续'} maxLength={32000}
                        disabled={sessionLifecycleSaving}
                        onBlur={(event) => {
                          const wakeMessage = event.target.value;
                          if (wakeMessage.trim() && wakeMessage !== sessionLifecycle.wakeMessage)
                            void saveSessionLifecycle({ wakeMessage });
                          else if (!wakeMessage.trim()) event.target.value = sessionLifecycle.wakeMessage || '继续';
                        }}
                        className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary"
                      />
                      <p className="mt-1 text-[10px] text-text-tertiary" data-settings-id="app-settings-modal.88">{settingsText("app-settings-modal.88")}</p>
                    </div>
                    <div
                      data-settings-anchor="startup-preference"
                      tabIndex={-1}
                      className="rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                    >
                      <label htmlFor="session-startup-preference" className="mb-1 block text-xs text-text-secondary" data-settings-id="app-settings-modal.89">
                        {settingsText("app-settings-modal.89")}</label>
                      <select
                        data-settings-id="app-settings-modal.89" id="session-startup-preference"
                        value={sessionLifecycle.startupPreference}
                        disabled={sessionLifecycleSaving}
                        onChange={(event) => void saveSessionLifecycle({
                          startupPreference: event.target.value as ApiStartupPreference,
                        })}
                        className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent disabled:opacity-60"
                      >
                        <option value="ask" data-settings-id="app-settings-modal.90">{settingsText("app-settings-modal.90")}</option>
                        <option value="wake-running" data-settings-id="app-settings-modal.91">{settingsText("app-settings-modal.91")}</option>
                        <option value="clear-illegal" data-settings-id="app-settings-modal.92">{settingsText("app-settings-modal.92")}</option>
                        <option value="preserve-running" data-settings-id="app-settings-modal.93">{settingsText("app-settings-modal.93")}</option>
                      </select>
                      <p className="mt-1 text-[10px] text-text-tertiary" data-settings-id="app-settings-modal.94">
                        {settingsText("app-settings-modal.94")}</p>
                    </div>
                  </div>
                )}
                {sessionLifecycleError && (
                  <div role="alert" className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {sessionLifecycleError}
                    <button
                      type="button"
                      disabled={sessionLifecycleLoading}
                      onClick={() => setSessionLifecycleReloadSeq((value) => value + 1)}
                      className="ml-2 underline underline-offset-2 disabled:opacity-60" data-settings-id="app-settings-modal.95"
                    >
                      {settingsText("app-settings-modal.95")}</button>
                  </div>
                )}
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="worker-config">
                  {settingsText("worker-config")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <ReloadRow
                    label={settingsText("app-settings-modal.96")}
                    hint={settingsText("app-settings-modal.97")}
                    busy={false}
                    action="Edit"
                    onClick={openWorkerEdit} settingId="app-settings-modal.96"
                  />
                </div>
                {reloadError && reloadSection === 'worker' && (
                  <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {reloadError}
                  </div>
                )}
                {!reloadError && reloadResult && reloadSection === 'worker' && (
                  <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                    {reloadResult.worker &&
                      WORKER_KEYS.map((k) => {
                        const before = reloadResult.worker?.before[k];
                        const after = reloadResult.worker?.after[k];
                        return (
                          <div key={k} data-settings-id="app-settings-modal.98">
                            {settingsText("app-settings-modal.98")}{k}: {before ?? '?'} → {after ?? '?'}
                            {before !== undefined && before !== after ? ' (changed)' : ''}
                          </div>
                        );
                      })}
                  </div>
                )}
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.99">
                  {settingsText("app-settings-modal.99")}</p>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="worker-stop-reports">
                  {settingsText("worker-stop-reports")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.100")}
                    hint={settingsText("app-settings-modal.101")}
                    checked={reportWorkerKillErrors}
                    onChange={(v) => void toggleReportWorkerKillErrors(v)} settingId="app-settings-modal.100"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.102">
                  {settingsText("app-settings-modal.102")}</p>
                {killReportError && (
                  <div role="alert" className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {killReportError}
                  </div>
                )}
              </section>

              <section data-testid="auto-force-send-section">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="auto-force-send">
                  {settingsText("auto-force-send")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.103")}
                    hint={settingsText("app-settings-modal.104")}
                    checked={autoForceSend.system}
                    disabled={autoForceSaving !== null}
                    onChange={(v) => void toggleAutoForceSend('system', v)} settingId="app-settings-modal.103"
                  />
                  <SwitchRow
                    label={settingsText("app-settings-modal.105")}
                    hint={settingsText("app-settings-modal.106")}
                    checked={autoForceSend.ma}
                    disabled={autoForceSaving !== null}
                    onChange={(v) => void toggleAutoForceSend('ma', v)} settingId="app-settings-modal.105"
                  />
                  <SwitchRow
                    label={settingsText("app-settings-modal.107")}
                    hint={settingsText("app-settings-modal.108")}
                    checked={autoForceSend.report}
                    disabled={autoForceSaving !== null}
                    onChange={(v) => void toggleAutoForceSend('report', v)} settingId="app-settings-modal.107"
                  />
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.109">
                  {settingsText("app-settings-modal.109")}</p>
                {autoForceError && (
                  <div role="alert" className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {autoForceError}
                  </div>
                )}
              </section>

              <section data-testid="illegal-queue-behavior-section">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="illegal-queue-safety">
                  {settingsText("illegal-queue-safety")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.110")}
                    hint={settingsText("app-settings-modal.111")}
                    checked={behavior.autoLockIllegalQueue}
                    disabled={behaviorSaving}
                    onChange={(v) => void toggleIllegalAutoLock(v)} settingId="app-settings-modal.110"
                  />
                </div>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="new-sessions">
                  {settingsText("new-sessions")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.112")}
                    hint={settingsText("app-settings-modal.113")}
                    checked={defaultNewSessionToCurrentWorkspace}
                    onChange={setDefaultNewSessionToCurrentWorkspace} settingId="app-settings-modal.112"
                  />
                </div>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="confirmations">
                  {settingsText("confirmations")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <SwitchRow
                    label={settingsText("app-settings-modal.114")}
                    hint={settingsText("app-settings-modal.115")}
                    checked={notifications.confirmAgentSystemQueueEdit}
                    onChange={setConfirmAgentSystemQueueEdit} settingId="app-settings-modal.114"
                  />
                  <SwitchRow
                    label={settingsText("app-settings-modal.116")}
                    hint={settingsText("app-settings-modal.117")}
                    checked={notifications.confirmCrossWorkspaceManagement}
                    onChange={setConfirmCrossWorkspaceManagement} settingId="app-settings-modal.116"
                  />
                </div>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="memory-advanced">
                  {settingsText("memory-advanced")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <ReloadRow
                    label={settingsText("app-settings-modal.118")}
                    hint={settingsText("app-settings-modal.119")}
                    busy={reloadScope === 'memory'}
                    onClick={() => handleReload('memory')} settingId="app-settings-modal.118"
                  />
                </div>
                {reloadError && reloadSection === 'memory' && (
                  <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {reloadError}
                  </div>
                )}
                {!reloadError && reloadResult && reloadSection === 'memory' && (
                  <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                    {reloadResult.memory && (
                      <div data-settings-id="app-settings-modal.120">
                        {settingsText("app-settings-modal.120")}{String(reloadResult.memory.before.enabled ?? '?')} →{' '}
                        {String(reloadResult.memory.after.enabled ?? '?')}
                        {reloadResult.memory.before.enabled !== undefined &&
                        reloadResult.memory.before.enabled !== reloadResult.memory.after.enabled
                          ? ' (changed)'
                          : ''}
                      </div>
                    )}
                  </div>
                )}
              </section>
            </>
          ) : activeTab === 'remote' ? (
            <>
              {/* Remote / Tunnel — cloudflared tunnel managed by the internal
              Python launcher. Only rendered when config.json has a remote
              section with enabled=true. Restart kills only Pan's own
              launcher-recorded tunnel process and picks up port +
              remote.protocol. */}
              {remoteStatus && settingsVisible('remoteTunnel', { remoteTunnel: Boolean(remoteStatus.available && remoteStatus.enabled) }) && (
                <RemoteSettingsSection
                  id="remote-tunnel"
                  settingId="remote-tunnel"
                  testId="remote-tunnel-section"
                  title={settingsText("remote-tunnel")}
                  description={settingsText("app-settings-modal.121")}
                  status={<RemoteStatus tone={remoteStatus.running ? 'ok' : 'off'}>
                    {settingsText("app-settings-modal.122")}{remoteStatus.running ? 'running' : 'stopped'}
                    {remoteStatus.protocol ? ` · ${remoteStatus.protocol}` : ''}
                    {remoteStatus.port ? ` · :${remoteStatus.port}` : ''}
                  </RemoteStatus>}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" disabled={remoteBusy} onClick={handleRemoteRestart}>
                      {remoteBusy ? 'Restarting…' : 'Restart tunnel'}
                    </Button>
                  </div>
                  <p className="text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.123">
                    {settingsText("app-settings-modal.123")}</p>
                </RemoteSettingsSection>
              )}
              <McpRemoteSettings />
              <TailscaleSettings />
              <LanSettings />
            </>
          ) : activeTab === 'plugins' ? (
            <>
              <LocalPluginPanelsSettings />
              <QqPluginsPanel />
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="plugin-manifests">
                  {settingsText("plugin-manifests")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <ReloadRow
                    label={settingsText("app-settings-modal.124")}
                    hint={settingsText("app-settings-modal.125")}
                    busy={reloadScope === 'plugin'}
                    onClick={() => handleReload('plugin')} settingId="app-settings-modal.124"
                  />
                </div>
                {reloadError && reloadSection === 'plugin' && (
                  <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {reloadError}
                  </div>
                )}
                {!reloadError && reloadResult && reloadSection === 'plugin' && (
                  <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                    {reloadResult.plugin && <PluginResult plugin={reloadResult.plugin} />}
                  </div>
                )}
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.126">
                  {settingsText("app-settings-modal.126")}</p>
              </section>
            </>
          ) : activeTab === 'data' ? (
            <DataSettingsPanel
              catalog={dataCatalog}
              loading={dataCatalogLoading}
              error={dataCatalogError}
              retention={dataRetention}
              retentionDraft={dataRetentionDraft}
              retentionLoading={dataRetentionLoading}
              retentionError={dataRetentionError}
              retentionSaving={dataRetentionSaving}
              retentionSaveError={dataRetentionSaveError}
              retentionDirty={dataRetentionDirty}
              onRetentionChange={updateDataRetentionDraft}
              onSaveRetention={() => void saveDataRetentionDraft()}
              jobsRetentionSlot={<JobRetentionSettings />}
            />
          ) : (
            <>
              {/* Main Pan service — deliberately separate from worker and tunnel
              restarts. The API only schedules a detached supervisor; the
              browser expects a short disconnect while Pan is replaced. */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="pan-service">
                  {settingsText("pan-service")}</h3>
                {mainLifecycleStatusLoading && (
                  <p className="mb-2 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.127">
                    {settingsText("app-settings-modal.127")}</p>
                )}
                {mainLifecycleStatusError && (
                  <div role="alert" className="mb-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {mainLifecycleStatusError}
                    <button
                      type="button"
                      disabled={mainLifecycleStatusLoading}
                      onClick={() => void refreshMainLifecycleStatus()}
                      className="ml-2 underline underline-offset-2 disabled:opacity-60" data-settings-id="app-settings-modal.128"
                    >
                      {settingsText("app-settings-modal.128")}</button>
                  </div>
                )}
                {mainRestartState === 'confirming' ? (
                  <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-3">
                    <p className="text-xs text-text-primary" data-settings-id="app-settings-modal.129">
                      {settingsText("app-settings-modal.129")}</p>
                    <MainLifecycleRunningChoice
                      strategy={sessionLifecycle.exitStrategy}
                      markRunningOffline={mainLifecycleMarkRunningOffline}
                      onChange={setMainLifecycleMarkRunningOffline}
                      inputName="main-restart-running-session-state"
                    />
                    <div className="mt-3 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setMainLifecycleMarkRunningOffline(null);
                          setMainRestartState('idle');
                        }}
                        className="rounded border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={handleMainRestart}
                        disabled={
                          !serviceLifecycle ||
                          (sessionLifecycle.exitStrategy === 'ask' &&
                            mainLifecycleMarkRunningOffline === null) ||
                          mainLifecycleStatusLoading ||
                          Boolean(mainLifecycleStatusError) ||
                          !mainRestartStatus?.available ||
                          !mainExitStatus?.available ||
                          Boolean(mainRestartStatus?.pending) ||
                          Boolean(mainExitStatus?.pending) ||
                          mainExitState === 'confirming' ||
                          mainExitState === 'exiting' ||
                          mainLifecycleActionInFlightRef.current
                        }
                        className="rounded bg-warning px-3 py-1.5 text-xs text-black hover:opacity-90" data-settings-id="app-settings-modal.130"
                      >
                        {settingsText("app-settings-modal.130")}</button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    disabled={
                      !serviceLifecycle ||
                      mainLifecycleStatusLoading ||
                      Boolean(mainLifecycleStatusError) ||
                      !mainRestartStatus?.available ||
                      !mainExitStatus?.available ||
                      !sessionLifecycleLoaded ||
                      sessionLifecycleLoading ||
                      sessionLifecycleSaving ||
                      mainRestartState === 'restarting' ||
                      Boolean(mainRestartStatus?.pending) ||
                      Boolean(mainExitStatus?.pending) ||
                      mainExitState === 'confirming' ||
                      mainExitState === 'exiting' ||
                      mainLifecycleActionInFlightRef.current
                    }
                    onClick={() => {
                      setMainLifecycleMarkRunningOffline(null);
                      setMainRestartState('confirming');
                    }}
                    className="w-full flex items-center justify-between gap-3 rounded-md border border-border-muted bg-bg-primary px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <span className="min-w-0">
                      <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.131">
                        {settingsText("app-settings-modal.131")}</span>
                      <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
                        {mainRestartState === 'restarting'
                          ? mainRestartHealthCheckActive
                            ? 'Waiting for /api/health (max 16 seconds)'
                            : mainRestartStatus?.pending
                              ? 'Pan restart is in progress'
                              : 'Scheduling Pan restart…'
                          : !serviceLifecycle
                            ? lifecycleUnavailableLabel
                            : mainRestartStatus?.available
                              ? 'Applies startup-only config changes'
                              : mainRestartStatus?.reason || 'Checking restart support…'}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-text-tertiary">
                      {mainRestartState === 'restarting' ? 'Restarting…' : 'Restart'}
                    </span>
                  </button>
                )}
                {mainRestartState === 'restarting' && mainRestartHealthCheckActive && (
                  <button
                    type="button"
                    onClick={cancelMainRestartCheck}
                    className="mt-2 text-[11px] text-text-tertiary hover:text-text-primary underline underline-offset-2" data-settings-id="app-settings-modal.132"
                  >
                    {settingsText("app-settings-modal.132")}</button>
                )}
                {mainRestartState === 'restored' && (
                  <p className="mt-1.5 text-[11px] text-success" data-settings-id="app-settings-modal.133">
                    {settingsText("app-settings-modal.133")}</p>
                )}
                {mainRestartError && (
                  <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {mainRestartError}
                  </div>
                )}
                {mainRestartState === 'cancelled' && (
                  <p className="mt-1.5 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.134">
                    {settingsText("app-settings-modal.134")}</p>
                )}
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.135">
                  {settingsText("app-settings-modal.135")}</p>
              </section>

              {/* Both buttons only navigate to the real Behavior settings.
              They never schedule Restart/Exit and never write a value. */}
              <section aria-label={settingsText("app-settings-modal.136")} data-settings-id="app-settings-modal.136">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="session-policy">
                  {settingsText("session-policy")}</h3>
                <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                  <button
                    type="button"
                    data-testid="open-exit-session-policy"
                    onClick={() => openBehaviorSessionPolicy('exit-strategy')}
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors"
                  >
                    <span className="min-w-0">
                      <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.137">
                        {settingsText("app-settings-modal.137")}</span>
                      <span className="block text-[10px] text-text-tertiary font-mono mt-0.5" data-settings-id="app-settings-modal.138">
                        {settingsText("app-settings-modal.138")}</span>
                    </span>
                    <span className="shrink-0 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.139">{settingsText("app-settings-modal.139")}</span>
                  </button>
                  <button
                    type="button"
                    data-testid="open-startup-session-policy"
                    onClick={() => openBehaviorSessionPolicy('startup-preference')}
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors"
                  >
                    <span className="min-w-0">
                      <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.140">
                        {settingsText("app-settings-modal.140")}</span>
                      <span className="block text-[10px] text-text-tertiary font-mono mt-0.5" data-settings-id="app-settings-modal.141">
                        {settingsText("app-settings-modal.141")}</span>
                    </span>
                    <span className="shrink-0 text-[11px] text-text-tertiary" data-settings-id="app-settings-modal.142">{settingsText("app-settings-modal.142")}</span>
                  </button>
                </div>
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.143">
                  {settingsText("app-settings-modal.143")}</p>
              </section>

              <LoginStartupSettings />

              {/* Stop-only Pan exit — intentionally has no health-recovery
              polling because this action makes the current service unavailable. */}
              <section className="mt-4">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="exit-pan-service">
                  {settingsText("exit-pan-service")}</h3>
                {mainExitState === 'confirming' ? (
                  <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-3">
                    <p className="text-xs text-text-primary" data-settings-id="app-settings-modal.144">
                      {settingsText("app-settings-modal.144")}</p>
                    <MainLifecycleRunningChoice
                      strategy={sessionLifecycle.exitStrategy}
                      markRunningOffline={mainLifecycleMarkRunningOffline}
                      onChange={setMainLifecycleMarkRunningOffline}
                      inputName="main-exit-running-session-state"
                    />
                    <div className="mt-3 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setMainLifecycleMarkRunningOffline(null);
                          setMainExitState('idle');
                        }}
                        className="rounded border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-hover"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={handleMainExit}
                        disabled={
                          !serviceLifecycle ||
                          (sessionLifecycle.exitStrategy === 'ask' &&
                            mainLifecycleMarkRunningOffline === null) ||
                          mainLifecycleStatusLoading ||
                          Boolean(mainLifecycleStatusError) ||
                          !mainRestartStatus?.available ||
                          !mainExitStatus?.available ||
                          Boolean(mainRestartStatus?.pending) ||
                          Boolean(mainExitStatus?.pending) ||
                          mainRestartState === 'confirming' ||
                          mainRestartState === 'restarting' ||
                          mainLifecycleActionInFlightRef.current
                        }
                        className="rounded bg-danger px-3 py-1.5 text-xs text-white hover:opacity-90" data-settings-id="app-settings-modal.145"
                      >
                        {settingsText("app-settings-modal.145")}</button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    disabled={
                      !serviceLifecycle ||
                      !mainExitStatus?.available ||
                      mainLifecycleStatusLoading ||
                      Boolean(mainLifecycleStatusError) ||
                      !sessionLifecycleLoaded ||
                      sessionLifecycleLoading ||
                      sessionLifecycleSaving ||
                      mainExitState === 'exiting' ||
                      mainExitState === 'exited' ||
                      Boolean(mainExitStatus?.pending) ||
                      mainRestartState === 'restarting' ||
                      mainRestartState === 'confirming' ||
                      Boolean(mainRestartStatus?.pending) ||
                      mainLifecycleActionInFlightRef.current
                    }
                    onClick={() => {
                      setMainLifecycleMarkRunningOffline(null);
                      setMainExitState('confirming');
                    }}
                    className="w-full flex items-center justify-between gap-3 rounded-md border border-danger/30 bg-bg-primary px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <span className="min-w-0">
                      <span className="block text-xs text-text-primary" data-settings-id="app-settings-modal.146">{settingsText("app-settings-modal.146")}</span>
                      <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
                        {mainExitState === 'exiting'
                          ? 'Stopping Workers and Pan…'
                          : mainExitState === 'exited'
                            ? 'Exit scheduled; this service will go offline'
                            : !serviceLifecycle
                              ? lifecycleUnavailableLabel
                              : mainExitStatus?.available
                                ? 'Stops this Pan instance without restarting it'
                                : mainExitStatus?.reason || 'Checking exit support…'}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-text-tertiary">
                      {mainExitState === 'exiting' ? 'Exiting…' : 'Exit'}
                    </span>
                  </button>
                )}
                {mainExitState === 'exited' && (
                  <p className="mt-1.5 text-[11px] text-success" data-settings-id="app-settings-modal.147">
                    {settingsText("app-settings-modal.147")}</p>
                )}
                {mainExitError && (
                  <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                    {mainExitError}
                  </div>
                )}
                <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed" data-settings-id="app-settings-modal.148">
                  {settingsText("app-settings-modal.148")}</p>
              </section>

            </>
          )}
        </div>
      </div>

      {/* Worker config edit dialog — portal stacks above the settings card. */}
      {workerEditOpen && (
        <WorkerEditModal
          values={workerValues}
          loading={workerLoading}
          saving={workerSaving}
          error={workerEditError}
          onValueChange={(k, v) => setWorkerValues((prev) => ({ ...prev, [k]: v }))}
          onSave={handleWorkerSave}
          onClose={() => setWorkerEditOpen(false)}
        />
      )}
    </div>,
    document.body,
  );
}
