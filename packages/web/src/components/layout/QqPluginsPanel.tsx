import { useEffect, useState } from 'react';
import {
  fetchQqGatewayPlugins,
  selectQqGatewayPlugin,
  setQqGatewayPluginAutostart,
  setQqGatewayPluginToken,
  startQqGatewayPlugin,
  stopQqGatewayPlugin,
  type QqGatewayPluginsResponse,
} from '@/services/api';

export function QqPluginsPanel() {
  const [data, setData] = useState<QqGatewayPluginsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');

  const refresh = async () => setData(await fetchQqGatewayPlugins());
  useEffect(() => {
    void refresh().catch((cause: unknown) => setError(String(cause)));
  }, []);

  const run = async (key: string, action: () => Promise<unknown>, requiresRestart = false) => {
    setBusy(key);
    setError(null);
    try {
      await action();
      await refresh();
      if (requiresRestart) setRestartNeeded(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">QQ plugins</h3>
        <p className="mt-1 text-[11px] text-text-secondary">
          Select a OneBot gateway, then start or stop the process owned by Pan. Auto start applies to the selected plugin when Pan starts.
        </p>
      </div>
      {error && <p role="alert" className="rounded border border-danger/30 bg-danger/10 p-2 text-xs text-danger">{error}</p>}
      {restartNeeded && <p className="rounded border border-border-default bg-bg-tertiary p-2 text-xs text-text-secondary">Selection saved. Restart Pan to reconnect the QQ bridge to the new gateway.</p>}
      {!data && !error && <p className="text-xs text-text-secondary">Loading…</p>}
      {data?.plugins.map((plugin) => {
        const selected = data.selected === plugin.id;
        return (
          <div key={plugin.id} className="min-w-0 rounded-md border border-border-default bg-bg-primary p-3 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-medium text-text-primary">{plugin.name} {selected && <span className="text-accent">· Selected</span>}</div>
                <div className="break-all font-mono text-[10px] text-text-tertiary">{plugin.wsUrl}</div>
              </div>
              <span className="text-[11px] text-text-secondary">
                {plugin.running ? 'Running (Pan owned)' : plugin.endpointReachable ? 'Endpoint in use' : plugin.installed ? 'Stopped' : 'Not installed'}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" disabled={!!busy || selected} onClick={() => void run(`select-${plugin.id}`, () => selectQqGatewayPlugin(plugin.id), true)} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50">Select</button>
              <button type="button" disabled={!!busy || !selected || !plugin.installed || plugin.running || plugin.endpointReachable} onClick={() => void run(`start-${plugin.id}`, () => startQqGatewayPlugin(plugin.id))} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50">Start</button>
              <button type="button" disabled={!!busy || !plugin.running} onClick={() => void run(`stop-${plugin.id}`, () => stopQqGatewayPlugin(plugin.id))} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50">Stop</button>
              <label className="ml-auto flex items-center gap-1.5 text-[11px] text-text-secondary">
                <input type="checkbox" checked={plugin.autoStart} disabled={!!busy} onChange={(event) => void run(`auto-${plugin.id}`, () => setQqGatewayPluginAutostart(plugin.id, event.target.checked))} />
                Start with Pan
              </label>
            </div>
            {selected && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border-muted pt-2">
                <label htmlFor={`qq-token-${plugin.id}`} className="text-[11px] text-text-secondary">OneBot token {plugin.tokenConfigured ? '(configured)' : '(missing)'}</label>
                <input id={`qq-token-${plugin.id}`} type="password" autoComplete="off" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder="Paste token from gateway WebUI" className="min-w-0 flex-1 rounded border border-border-default bg-bg-primary px-2 py-1 text-[11px] text-text-primary" />
                <button type="button" disabled={!!busy || !tokenDraft} onClick={() => void run(`token-${plugin.id}`, async () => { await setQqGatewayPluginToken(plugin.id, tokenDraft); setTokenDraft(''); }, true)} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50">Save token</button>
              </div>
            )}
            {plugin.id === 'napcat' && (
              <p className="text-[10px] text-text-tertiary">Stopping the NapCat launcher may leave its Hook in QQ. Exit NapCat/QQ through its original workflow before switching frameworks.</p>
            )}
          </div>
        );
      })}
      {data && <p className="break-all font-mono text-[10px] text-text-tertiary">Registration: {data.manifestPath}</p>}
    </section>
  );
}
