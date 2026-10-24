import { useCallback, useEffect, useRef, useState } from 'react';
import {
  buildLocalPluginPanel, fetchLocalPluginPanels, setLocalPluginPanelEnabled, unbuildLocalPluginPanel,
} from '@/services/api';
import type { LocalPluginPanel } from '@/types/pluginPanels';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';

const button = 'rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50';
const dangerButton = 'rounded border border-danger/40 px-2 py-1 text-[11px] text-danger disabled:opacity-50';

function statusText(panel: LocalPluginPanel): string {
  if (panel.status === 'building') return 'Building…';
  if (panel.status === 'failed') return panel.available ? 'Build failed · previous build kept' : 'Build failed';
  if (panel.available) return panel.enabled ? 'Enabled' : 'Built';
  return 'Not built';
}

function statusTone(panel: LocalPluginPanel): string {
  if (panel.status === 'building') return 'text-accent';
  if (panel.status === 'failed') return panel.available ? 'text-warning' : 'text-danger';
  return panel.available ? 'text-success' : 'text-text-tertiary';
}

/** Build/enable lifecycle for local `web-app/v1` panels. The command always
 *  comes from the plugin manifest; this panel only names the target. */
export function LocalPluginPanelsSettings() {
  const [panels, setPanels] = useState<LocalPluginPanel[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Record<string, string>>({});
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchLocalPluginPanels();
      if (mounted.current) {
        setPanels(next);
        setLoaded(true);
        setError(null);
      }
      return next;
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; };
  }, [refresh]);

  const building = panels.some((panel) => panel.status === 'building');
  useEffect(() => {
    if (!building) return;
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => window.clearInterval(timer);
  }, [building, refresh]);

  const run = async (panel: LocalPluginPanel, key: string, action: () => Promise<unknown>, refreshPanels = false) => {
    const id = `${panel.pluginId}/${panel.panelId}`;
    setBusy(key);
    setActionError((current) => {
      if (!(id in current)) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
    try {
      await action();
      await refresh();
      if (refreshPanels) await usePluginPanelStore.getState().load(true);
    } catch (cause) {
      if (mounted.current) {
        setActionError((current) => ({ ...current,
          [id]: cause instanceof Error ? cause.message : String(cause) }));
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  return <section className="space-y-3" aria-label="Local plugin panels">
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">Local plugin panels</h3>
      <p className="mt-1 text-[11px] text-text-secondary">
        Build a plugin-declared web UI, enable it, then open it from the view entries. A panel can only be
        enabled after a successful build; a failed rebuild keeps the previous build.
      </p>
    </div>
    {error && <p role="alert" className="rounded border border-danger/30 bg-danger/10 p-2 text-xs text-danger">{error}</p>}
    {!loaded && !error && <p className="text-xs text-text-secondary">Loading…</p>}
    {loaded && panels.length === 0 && (
      <p className="rounded border border-border-default bg-bg-primary p-2 text-xs text-text-secondary">
        No panels declare <span className="font-mono">web-app/v1</span>.
      </p>
    )}
    {panels.map((panel) => {
      const id = `${panel.pluginId}/${panel.panelId}`;
      const key = (name: string) => `${id}/${name}`;
      const pending = busy !== null;
      return <div key={id} className="min-w-0 rounded-md border border-border-default bg-bg-primary p-3 space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs font-medium text-text-primary">{panel.title}</div>
            <div className="break-all font-mono text-[10px] text-text-tertiary">
              {panel.pluginId}/{panel.panelId} · {panel.entry}
            </div>
          </div>
          <span className={`text-[11px] ${statusTone(panel)}`}>{statusText(panel)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={button} disabled={pending || panel.status === 'building'}
            onClick={() => void run(panel, key('build'), () => buildLocalPluginPanel(panel.pluginId, panel.panelId))}>
            {panel.available ? 'Rebuild' : 'Build'}
          </button>
          <button type="button" className={button}
            disabled={pending || panel.status === 'building' || !panel.available || panel.enabled}
            title={!panel.available ? 'Build the panel before enabling it' : undefined}
            onClick={() => void run(panel, key('enable'),
              () => setLocalPluginPanelEnabled(panel.pluginId, panel.panelId, true), true)}>Enable</button>
          <button type="button" className={button} disabled={pending || panel.status === 'building' || !panel.enabled}
            onClick={() => void run(panel, key('disable'),
              () => setLocalPluginPanelEnabled(panel.pluginId, panel.panelId, false), true)}>Disable</button>
          <button type="button" className={dangerButton}
            disabled={pending || panel.status === 'building' || (!panel.available && panel.status === 'unbuilt')}
            onClick={() => void run(panel, key('unbuild'),
              () => unbuildLocalPluginPanel(panel.pluginId, panel.panelId), true)}>Unbuild</button>
          {panel.available && panel.builtAt != null && (
            <span className="ml-auto text-[10px] text-text-tertiary">
              Built {new Date(panel.builtAt).toLocaleString()}
            </span>
          )}
        </div>
        {actionError[id] && <p role="alert" className="text-[11px] text-danger break-words">{actionError[id]}</p>}
        {panel.status === 'building' && (
          <p role="status" className="text-[11px] text-accent">Build in progress…</p>
        )}
        {panel.lastError && panel.status !== 'building' && (
          <p role="status" className="text-[11px] text-warning break-words">{panel.lastError}</p>
        )}
        {panel.log && (
          <details open>
            <summary className="cursor-pointer text-[10px] text-text-tertiary">Build log</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded border border-border-muted bg-bg-tertiary p-2 font-mono text-[10px] text-text-secondary">
              {panel.log}
            </pre>
          </details>
        )}
      </div>;
    })}
  </section>;
}
