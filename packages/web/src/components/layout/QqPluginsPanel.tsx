import { useSettingsAvailability } from './settingsAvailability';
import { settingsText, settingsVisible } from '@/components/layout/settingsCatalog';
import { useEffect, useState } from 'react';
import {
  fetchQqGatewayPlugins,
  selectQqGatewayPlugin,
  setQqGatewayPluginAutostart,
  setQqGatewayPluginToken,
  setSnowLumaAccounts,
  startQqGatewayPlugin,
  stopQqGatewayPlugin,
  type QqGatewayPluginsResponse,
  type SnowLumaAccount,
} from '@/services/api';

export function QqPluginsPanel() {
  const [data, setData] = useState<QqGatewayPluginsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');
  const [accounts, setAccounts] = useState<SnowLumaAccount[]>([]);

  const refresh = async () => {
    const next = await fetchQqGatewayPlugins();
    setData(next);
    useSettingsAvailability.setState({qqPlugins: next.plugins.length > 0, qqSelected: next.plugins.some(plugin => plugin.id === next.selected), snowLuma: next.selected === 'snowluma'});
    setAccounts(next.snowlumaAccounts ?? []);
  };
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
        <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary" data-settings-id="qq-plugins">{settingsText("qq-plugins")}</h3>
        <p className="mt-1 text-[11px] text-text-secondary" data-settings-id="qq-plugins-panel.1">
          {settingsText("qq-plugins-panel.1")}</p>
      </div>
      {error && <p role="alert" className="rounded border border-danger/30 bg-danger/10 p-2 text-xs text-danger">{error}</p>}
      {data?.legacyMultiChannel && (
        <p className="rounded border border-border-default bg-bg-tertiary p-2 text-xs text-text-secondary" data-settings-id="qq-plugins-panel.2">
          {settingsText("qq-plugins-panel.2")}{data.legacyChannelCount} {settingsText("qq-plugins-panel.3")}</p>
      )}
      {restartNeeded && <p className="rounded border border-border-default bg-bg-tertiary p-2 text-xs text-text-secondary" data-settings-id="qq-plugins-panel.4">{settingsText("qq-plugins-panel.4")}</p>}
      {!data && !error && <p className="text-xs text-text-secondary">Loading…</p>}
      {data?.plugins.map((plugin) => {
        const selected = data.selected === plugin.id;
        return (
          <div key={plugin.id} className="min-w-0 rounded-md border border-border-default bg-bg-primary p-3 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-medium text-text-primary">{plugin.name} {selected && <span className="text-accent">· {data.legacyMultiChannel ? 'Default in legacy mode' : 'Selected'}</span>}</div>
                <div className="break-all font-mono text-[10px] text-text-tertiary">{plugin.wsUrl}</div>
              </div>
              <span className="text-[11px] text-text-secondary">
                {plugin.running ? 'Running (Pan owned)' : plugin.endpointReachable ? 'Endpoint in use' : plugin.installed ? 'Stopped' : 'Not installed'}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" disabled={!!busy || selected} onClick={() => void run(`select-${plugin.id}`, () => selectQqGatewayPlugin(plugin.id), true)} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.5">{settingsText("qq-plugins-panel.5")}</button>
              <button type="button" disabled={!!busy || !selected || !plugin.installed || plugin.running || plugin.endpointReachable} onClick={() => void run(`start-${plugin.id}`, () => startQqGatewayPlugin(plugin.id))} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.6">{settingsText("qq-plugins-panel.6")}</button>
              <button type="button" disabled={!!busy || !plugin.running} onClick={() => void run(`stop-${plugin.id}`, () => stopQqGatewayPlugin(plugin.id))} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.7">{settingsText("qq-plugins-panel.7")}</button>
              <label className="ml-auto flex items-center gap-1.5 text-[11px] text-text-secondary" data-settings-id="qq-plugins-panel.8">
                <input type="checkbox" checked={plugin.autoStart} disabled={!!busy} onChange={(event) => void run(`auto-${plugin.id}`, () => setQqGatewayPluginAutostart(plugin.id, event.target.checked))} />
                {settingsText("qq-plugins-panel.8")}</label>
            </div>
            {selected && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border-muted pt-2">
                <label htmlFor={`qq-token-${plugin.id}`} className="text-[11px] text-text-secondary" data-settings-id="qq-plugins-panel.9">{settingsText("qq-plugins-panel.9")}{plugin.tokenConfigured ? '(configured)' : '(missing)'}</label>
                <input id={`qq-token-${plugin.id}`} type="password" autoComplete="off" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder={settingsText("qq-plugins-panel.10")} className="min-w-0 flex-1 rounded border border-border-default bg-bg-primary px-2 py-1 text-[11px] text-text-primary" data-settings-id="qq-plugins-panel.10" />
                <button type="button" disabled={!!busy || !tokenDraft} onClick={() => void run(`token-${plugin.id}`, async () => { await setQqGatewayPluginToken(plugin.id, tokenDraft); setTokenDraft(''); }, true)} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.11">{settingsText("qq-plugins-panel.11")}</button>
              </div>
            )}
            {settingsVisible('snowLuma', {snowLuma: selected && plugin.id === 'snowluma'}) && (
              <div className="space-y-2 border-t border-border-muted pt-2">
                <p className="text-[11px] text-text-secondary" data-settings-id="qq-plugins-panel.12">{settingsText("qq-plugins-panel.12")}</p>
                {accounts.map((account, index) => (
                  <div key={index} className="flex flex-wrap items-center gap-2">
                    <input aria-label={`QQ number ${index + 1}`} value={account.bot_uin} onChange={(event) => setAccounts((current) => current.map((item, i) => i === index ? { ...item, bot_uin: event.target.value } : item))} placeholder={settingsText("qq-plugins-panel.13")} inputMode="numeric" className="min-w-0 w-32 rounded border border-border-default bg-bg-primary px-2 py-1 text-[11px] text-text-primary" data-settings-id="qq-plugins-panel.13" />
                    <input aria-label={`WebSocket URL ${index + 1}`} value={account.ws_url} onChange={(event) => setAccounts((current) => current.map((item, i) => i === index ? { ...item, ws_url: event.target.value } : item))} placeholder={settingsText("qq-plugins-panel.14")} className="min-w-0 flex-1 rounded border border-border-default bg-bg-primary px-2 py-1 font-mono text-[11px] text-text-primary" data-settings-id="qq-plugins-panel.14" />
                    <button type="button" disabled={!!busy} onClick={() => setAccounts((current) => current.filter((_, i) => i !== index))} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.15">{settingsText("qq-plugins-panel.15")}</button>
                  </div>
                ))}
                <div className="flex flex-wrap gap-2">
                  <button type="button" disabled={!!busy} onClick={() => setAccounts((current) => [...current, { bot_uin: '', ws_url: `ws://127.0.0.1:${3003 + current.length}` }])} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.16">{settingsText("qq-plugins-panel.16")}</button>
                  <button type="button" disabled={!!busy} onClick={() => void run('snowluma-accounts', () => setSnowLumaAccounts(accounts), true)} className="rounded border border-border-default px-2 py-1 text-[11px] text-text-primary disabled:opacity-50" data-settings-id="qq-plugins-panel.17">{settingsText("qq-plugins-panel.17")}</button>
                </div>
                <p className="text-[10px] text-text-tertiary" data-settings-id="qq-plugins-panel.18">{settingsText("qq-plugins-panel.18")}</p>
              </div>
            )}
            {plugin.id === 'napcat' && (
              <p className="text-[10px] text-text-tertiary" data-settings-id="qq-plugins-panel.19">{settingsText("qq-plugins-panel.19")}</p>
            )}
          </div>
        );
      })}
      {data && <p className="break-all font-mono text-[10px] text-text-tertiary" data-settings-id="qq-plugins-panel.20">{settingsText("qq-plugins-panel.20")}{data.manifestPath}</p>}
    </section>
  );
}
