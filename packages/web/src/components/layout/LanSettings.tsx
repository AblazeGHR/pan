import { settingsText } from '@/components/layout/settingsCatalog';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { beginForegroundRequest } from '@/services/foregroundActivity';
import {
  RemoteField,
  RemoteSettingsSection,
  RemoteStatus,
  RemoteSwitchRow,
  remoteControlClass,
} from './RemoteSettingsSection';

type Config = { startupEnabled: boolean; host: string; port: number };
type Status = { config: Config | null; activeConfig: Config | null; running: boolean;
  addresses: string[]; urls: string[]; error: string };

export function LanSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<'' | 'refresh' | 'save' | 'start' | 'stop'>('');
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const [error, setError] = useState('');
  async function action(name: 'refresh' | 'save' | 'start' | 'stop', value?: Config) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setPending(name); setError('');
    const finish = beginForegroundRequest();
    try {
      const response = await fetch(`/api/remote/lan${name === 'start' || name === 'stop' ? `/${name}` : ''}`, {
        method: name === 'refresh' ? 'GET' : name === 'save' ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: name === 'save' ? JSON.stringify(value) : undefined,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'LAN 请求失败');
      if (!Array.isArray(result.addresses) || !Array.isArray(result.urls) || typeof result.running !== 'boolean') {
        throw new Error('LAN 状态不可用，请刷新重试');
      }
      if (mounted.current) {
        setStatus(result);
        if ((name === 'save' && !result.error) || name === 'refresh') setConfig(result.config);
      }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      finish(); inFlight.current = false;
      if (mounted.current) { setBusy(false); setPending(''); }
    }
  }
  useEffect(() => {
    mounted.current = true;
    void action('refresh');
    return () => { mounted.current = false; };
  }, []);
  const changed = !!config && JSON.stringify(config) !== JSON.stringify(status?.config);
  const valid = !!config && !!config.host && Number.isInteger(config.port) && config.port >= 1 && config.port <= 65535;
  const active = status?.running ? status.activeConfig : null;
  return <RemoteSettingsSection
    settingId="lan"
    id="remote-lan"
    testId="remote-lan-section"
    title={settingsText("lan")}
    description={settingsText("lan-settings.1")}
    status={<RemoteStatus tone={status?.running ? 'ok' : 'idle'}>
      {busy && !status ? '正在读取状态…' : status
        ? status.running
          ? `已开启 ${active?.host ?? ''}:${active?.port ?? ''}`
          : '已关闭'
        : '状态不可用'}
    </RemoteStatus>}
  >
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void action('refresh')}>{busy && pending === 'refresh' ? '处理中…' : '刷新 LAN 状态'}</Button>
    </div>
    {config && status && <>
      <RemoteSwitchRow
        settingId="lan-settings.2"
        label={settingsText("lan-settings.2")}
        hint={settingsText("lan-settings.3")}
        checked={config.startupEnabled}
        disabled={busy}
        onChange={value => setConfig({ ...config, startupEnabled: value })}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_6rem_auto] sm:items-end" data-settings-id="lan-settings.4">
        <RemoteField id="lan-host" label={settingsText("lan-settings.4")}>
          <select id="lan-host" aria-label={settingsText("lan-settings.5")} disabled={busy} value={config.host}
            className={`${remoteControlClass} w-full max-w-full`}
            onChange={event => setConfig({ ...config, host: event.target.value })} data-settings-id="lan-settings.5">
            <option value="" data-settings-id="lan-settings.6">{settingsText("lan-settings.6")}</option>
            {config.host && config.host !== '0.0.0.0' && !status.addresses.includes(config.host) && <option value={config.host} data-settings-id="lan-settings.7">{config.host}{settingsText("lan-settings.7")}</option>}
            {status.addresses.map(address => <option key={address} value={address}>{address}</option>)}
            <option value="0.0.0.0" data-settings-id="lan-settings.8">{settingsText("lan-settings.8")}</option>
          </select>
        </RemoteField>
        <RemoteField id="lan-port" label={settingsText("lan-settings.9")}>
          <input id="lan-port" aria-label={settingsText("lan-settings.10")} type="number" min={1} max={65535} disabled={busy} value={config.port}
            className={`${remoteControlClass} w-full`}
            onChange={event => setConfig({ ...config, port: Number(event.target.value) })} data-settings-id="lan-settings.10" />
        </RemoteField>
        <Button size="sm" disabled={busy || !changed || !valid} onClick={() => void action('save', config)}>{busy && pending === 'save' ? '保存中…' : '保存 LAN 配置'}</Button>
      </div>
      <p className="text-[11px] leading-relaxed text-text-tertiary" data-settings-id="lan-settings.11">{settingsText("lan-settings.11")}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || changed || !valid || status.running} onClick={() => void action('start')}>{busy && pending === 'start' ? '启动中…' : '开启 LAN 访问'}</Button>
        <Button size="sm" disabled={busy || !status.running} onClick={() => void action('stop')}>{busy && pending === 'stop' ? '停止中…' : '关闭 LAN 访问'}</Button>
      </div>
      {status.urls.length > 0 && <div className="space-y-2">
        <p className="text-[11px] text-text-secondary" data-settings-id="lan-settings.12">{settingsText("lan-settings.12")}</p>
        {status.urls.map(url => <div key={url} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
          <code className="min-w-0 break-all text-xs">{url}</code>
          <Button size="sm" onClick={() => {
            void (async () => {
              try { await navigator.clipboard.writeText(url); }
              catch { setError('复制失败，请选择地址手动复制（HTTP 局域网页面可能不支持剪贴板）'); }
            })();
          }} data-settings-id="lan-settings.13">{settingsText("lan-settings.13")}</Button>
        </div>)}
      </div>}
    </>}
    <div className="space-y-1 text-[11px] leading-relaxed text-text-tertiary">
      <p data-settings-id="lan-settings.14">{settingsText("lan-settings.14")}</p>
      <p data-settings-id="lan-settings.15">{settingsText("lan-settings.15")}</p>
    </div>
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </RemoteSettingsSection>;
}
