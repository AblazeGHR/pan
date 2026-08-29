import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useUIStore } from '@/stores/uiStore';
import { reloadConfig, fetchRemoteStatus, restartRemoteTunnel } from '@/services/api';
import type {
  ApiConfigReloadResponse,
  ApiRemoteStatusResponse,
} from '@/types';
import type { GroupMode } from '@/stores/uiStore';

interface AppSettingsModalProps {
  open: boolean;
  onClose: () => void;
}

const GROUP_OPTIONS: { value: GroupMode; label: string }[] = [
  { value: 'none', label: 'Off' },
  { value: 'workdir', label: 'Working directory' },
  { value: 'manager', label: 'Manager' },
];

const WORKER_KEYS = ['timeout_sec', 'task_timeout_sec', 'idle_sec'] as const;

type ReloadScope = 'adapters' | 'worker' | 'plugin' | 'memory';

function SwitchRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        {hint && (
          <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
            {hint}
          </span>
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

function ReloadRow({
  label,
  hint,
  busy,
  onClick,
}: {
  label: string;
  hint: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
          {hint}
        </span>
      </span>
      <span className="shrink-0 text-[11px] text-text-tertiary">
        {busy ? 'Reloading…' : 'Reload'}
      </span>
    </button>
  );
}

/**
 * Render the plugin half of a config-reload result: path-list diff
 * (added/removed manifests) + the freshly loaded template counts.
 */
function PluginResult({
  plugin,
}: {
  plugin: NonNullable<ApiConfigReloadResponse['plugin']>;
}) {
  const beforeSet = new Set(plugin.before);
  const afterSet = new Set(plugin.after);
  const added = plugin.after.filter((p) => !beforeSet.has(p));
  const removed = plugin.before.filter((p) => !afterSet.has(p));
  const changed = added.length > 0 || removed.length > 0;
  return (
    <>
      <div>
        plugin manifests: {plugin.before.length} → {plugin.after.length}
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
      <div>
        templates: {plugin.sessionTemplates ?? '?'} · servers:{' '}
        {plugin.mcpServers ?? '?'} · characters: {plugin.characters ?? '?'} ·
        routes: {plugin.commandRoutes ?? '?'}
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
  const {
    defaultGroupBy,
    showMetaAgent,
    showTaskAgent,
    showQQ,
    setDefaultGroupBy,
    setShowMetaAgent,
    setShowTaskAgent,
    setShowQQ,
    resetSettings,
  } = useAppSettingsStore();

  const [reloadScope, setReloadScope] = useState<ReloadScope | null>(null);
  // Which section owns the current reloadResult/reloadError — each reload
  // section renders the outcome under its own rows instead of cross-fading
  // results between sections.
  const [reloadSection, setReloadSection] = useState<'config' | 'other' | null>(
    null,
  );
  const [reloadResult, setReloadResult] =
    useState<ApiConfigReloadResponse | null>(null);
  const [reloadError, setReloadError] = useState<string | null>(null);

  // Remote tunnel state — fetched when the modal opens. The whole
  // "Remote / Tunnel" section only renders when config.json has a remote
  // section AND remote.enabled is true (backend /api/remote/status).
  const [remoteStatus, setRemoteStatus] =
    useState<ApiRemoteStatusResponse | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const showToast = useUIStore((s) => s.showToast);

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
    setReloadSection(scope === 'plugin' || scope === 'memory' ? 'other' : 'config');
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

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="App Settings"
      className="app-settings-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="app-settings-card flex flex-col w-full h-full bg-bg-primary overflow-hidden md:w-[75vw] md:h-[75vh] md:rounded-lg md:border md:border-border-default md:shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-border-default px-4 py-3 md:px-6 md:py-4 shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">App Settings</h2>
            <p className="mt-0.5 text-[11px] text-text-tertiary">
              Global preferences for the Pan app.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary p-1.5 rounded transition-colors shrink-0"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5 space-y-6">
          {/* Session list grouping */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Session list
            </h3>
            <label className="block text-xs text-text-secondary mb-1">
              Default group by
            </label>
            <select
              value={defaultGroupBy}
              onChange={(e) => setDefaultGroupBy(e.target.value as GroupMode)}
              className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent"
            >
              {GROUP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">
              Applies to the session list as the default grouping. You can still
              cycle grouping per view with the group button.
            </p>
          </section>

          {/* Message visibility */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Message visibility
            </h3>
            <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
              <SwitchRow
                label="Show meta-agent info"
                hint="////by agent"
                checked={showMetaAgent}
                onChange={setShowMetaAgent}
              />
              <SwitchRow
                label="Show task-agent info"
                hint="@@@@by agent"
                checked={showTaskAgent}
                onChange={setShowTaskAgent}
              />
              <SwitchRow
                label="Show QQ messages"
                hint="@@@@by qq"
                checked={showQQ}
                onChange={setShowQQ}
              />
            </div>
          </section>

          {/* Configuration reload — POST /api/config/reload, original
              scopes. plugin/memory live in the "Other hot-reload" section
              below; ui settings are read live per request and need no
              reload; frontend/port/logging/remote are startup-frozen. */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Configuration reload
            </h3>
            <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
              <ReloadRow
                label="Reload adapters"
                hint="Adapter model lists (config.json per-adapter models)"
                busy={reloadScope === 'adapters'}
                onClick={() => handleReload('adapters')}
              />
              <ReloadRow
                label="Reload worker config"
                hint="Worker timeout_sec / task_timeout_sec / idle_sec"
                busy={reloadScope === 'worker'}
                onClick={() => handleReload('worker')}
              />
            </div>
            {reloadError && reloadSection === 'config' && (
              <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                {reloadError}
              </div>
            )}
            {!reloadError && reloadResult && reloadSection === 'config' && (
              <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                {reloadResult.adapters?.map((a) => (
                  <div key={a.name}>
                    {a.name}: {a.modelsBefore ?? '?'} → {a.modelsAfter ?? '?'}{' '}
                    models
                  </div>
                ))}
                {reloadResult.worker &&
                  WORKER_KEYS.map((k) => {
                    const before = reloadResult.worker?.before[k];
                    const after = reloadResult.worker?.after[k];
                    return (
                      <div key={k}>
                        worker.{k}: {before ?? '?'} → {after ?? '?'}
                        {before !== undefined && before !== after
                          ? ' (changed)'
                          : ''}
                      </div>
                    );
                  })}
              </div>
            )}
            <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">
              Applies config.json changes without restarting the server.
            </p>
          </section>

          {/* Other hot-reload — newer POST /api/config/reload scopes.
              plugin re-applies the plugin_manifests LIST from config.json,
              so manifest files added to / removed from the list take effect
              (/api/manifest/reload only re-reads the already-registered
              files). memory re-reads the memory.enabled injection switch.
              frontend / port / logging / remote stay startup-frozen. */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Other hot-reload
            </h3>
            <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
              <ReloadRow
                label="Reload plugin manifests"
                hint="plugin_manifests list in config.json (add/remove manifests)"
                busy={reloadScope === 'plugin'}
                onClick={() => handleReload('plugin')}
              />
              <ReloadRow
                label="Reload memory config"
                hint="memory.enabled injection switch"
                busy={reloadScope === 'memory'}
                onClick={() => handleReload('memory')}
              />
            </div>
            {reloadError && reloadSection === 'other' && (
              <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">
                {reloadError}
              </div>
            )}
            {!reloadError && reloadResult && reloadSection === 'other' && (
              <div className="mt-2 rounded-md border border-border-muted bg-bg-tertiary px-3 py-2 text-[11px] font-mono text-text-secondary space-y-0.5">
                {reloadResult.plugin && <PluginResult plugin={reloadResult.plugin} />}
                {reloadResult.memory && (
                  <div>
                    memory.enabled:{' '}
                    {String(reloadResult.memory.before.enabled ?? '?')} →{' '}
                    {String(reloadResult.memory.after.enabled ?? '?')}
                    {reloadResult.memory.before.enabled !== undefined &&
                    reloadResult.memory.before.enabled !==
                      reloadResult.memory.after.enabled
                      ? ' (changed)'
                      : ''}
                  </div>
                )}
              </div>
            )}
            <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">
              frontend / port / logging / remote are startup-frozen and need a
              server restart to apply.
            </p>
          </section>

          {/* Remote / Tunnel — cloudflared tunnel managed by
              scripts/start_cf.ps1. Only rendered when config.json has a
              remote section with enabled=true (the tunnel itself is optional;
              without it the section would be dead UI). Restart kills only
              Pan's own tunnel process (temp-yml command-line match) and
              re-runs start_cf.ps1, picking up port + remote.protocol. */}
          {remoteStatus?.available && remoteStatus.enabled && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
                Remote / Tunnel
              </h3>
              <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
                <button
                  type="button"
                  disabled={remoteBusy}
                  onClick={handleRemoteRestart}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="min-w-0">
                    <span className="block text-xs text-text-primary">
                      Restart tunnel
                      <span
                        className={`ml-2 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                          remoteStatus.running ? 'bg-success' : 'bg-danger'
                        }`}
                        aria-hidden
                      />
                    </span>
                    <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
                      cloudflared · {remoteStatus.running ? 'running' : 'stopped'}
                      {remoteStatus.protocol ? ` · ${remoteStatus.protocol}` : ''}
                      {remoteStatus.port ? ` · :${remoteStatus.port}` : ''}
                    </span>
                  </span>
                  <span className="shrink-0 text-[11px] text-text-tertiary">
                    {remoteBusy ? 'Restarting…' : 'Restart'}
                  </span>
                </button>
              </div>
              <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">
                Kills Pan's own cloudflared (temp-yml match only — the
                cloudflared-ssh service is untouched) and re-runs
                scripts/start_cf.ps1 with the current config.json.
              </p>
            </section>
          )}

          {/* Reset */}
          <div className="border-t border-border-muted pt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <p className="text-[11px] text-text-tertiary leading-relaxed sm:max-w-md">
              Hiding affects frontend display only — original messages stay in
              session history and reappear when toggled back on.
            </p>
            <button
              type="button"
              onClick={resetSettings}
              className="shrink-0 text-[11px] text-text-tertiary hover:text-text-primary underline underline-offset-2"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
