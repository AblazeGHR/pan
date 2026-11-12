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

type Config = { startupEnabled: boolean; protocol: 'http' | 'https'; port: number };
type Status = { config: Config; activeConfig?: Config; running: boolean; online?: boolean;
  url: string; ip?: string; dns?: string; target: string; error: string; pendingCleanup: boolean };

async function call(method = 'GET', action = '', body?: Config): Promise<Status> {
  const finish = beginForegroundRequest();
  try {
    const response = await fetch(`/api/remote/tailscale${action}`, {
      method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '请求失败');
    return result;
  } finally { finish(); }
}

export function TailscaleSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<'' | 'save' | 'start' | 'stop' | 'refresh'>('');
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    inFlight.current = true; setBusy(true);
    call().then(value => { if (active) { setStatus(value); setConfig(value.config); } })
      .catch(reason => { if (active) setError(String(reason)); })
      .finally(() => { if (active) { inFlight.current = false; setBusy(false); } });
    return () => { active = false; };
  }, []);
  async function action(name: 'save' | 'start' | 'stop' | 'refresh') {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setPending(name); setError('');
    try {
      const value = await call(name === 'refresh' ? 'GET' : name === 'save' ? 'PUT' : 'POST',
        name === 'start' || name === 'stop' ? `/${name}` : '', name === 'save' ? config ?? undefined : undefined);
      setStatus(value);
      if (name === 'save' || !config) setConfig(value.config);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { inFlight.current = false; setBusy(false); setPending(''); }
  }
  const changed = !!config && !!status && JSON.stringify(config) !== JSON.stringify(status.config);
  const runState = status
    ? `${busy ? '处理中…' : status.running ? '已开启' : status.pendingCleanup ? '待协调' : '已关闭'} · Tailscale ${status.online ? '在线' : '未就绪'}`
    : busy ? '正在读取状态…' : '状态不可用';
  return <RemoteSettingsSection
    settingId="tailscale"
    id="remote-tailscale"
    testId="remote-tailscale-section"
    title={settingsText("tailscale")}
    description={settingsText("tailscale-settings.1")}
    status={<RemoteStatus tone={status?.running ? 'ok' : 'idle'}>{runState}</RemoteStatus>}
  >
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void action('refresh')}>
        {busy && pending === 'refresh' ? '处理中…' : status ? '刷新状态' : '重试加载状态'}
      </Button>
    </div>
    {config && status && <>
      <RemoteSwitchRow
        settingId="tailscale-settings.2"
        label={settingsText("tailscale-settings.2")}
        hint={settingsText("tailscale-settings.3")}
        checked={config.startupEnabled}
        disabled={busy}
        onChange={value => setConfig({ ...config, startupEnabled: value })}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_6rem_auto] sm:items-end" data-settings-id="tailscale-settings.4">
        <RemoteField id="tailscale-protocol" label={settingsText("tailscale-settings.4")}>
          <select id="tailscale-protocol" disabled={busy} value={config.protocol}
            className={`${remoteControlClass} w-full max-w-full`}
            onChange={event => setConfig({ ...config, protocol: event.target.value as Config['protocol'] })}>
            <option value="http" data-settings-id="tailscale-settings.5">{settingsText("tailscale-settings.5")}</option><option value="https" data-settings-id="tailscale-settings.6">{settingsText("tailscale-settings.6")}</option>
          </select>
        </RemoteField>
        <RemoteField id="tailscale-port" label={settingsText("tailscale-settings.7")}>
          <input id="tailscale-port" type="number" min={1} max={65535} disabled={busy} value={config.port}
            className={`${remoteControlClass} w-full`}
            onChange={event => setConfig({ ...config, port: Number(event.target.value) })} />
        </RemoteField>
        <Button size="sm" disabled={busy || !changed} onClick={() => void action('save')}>{busy && pending === 'save' ? '保存中…' : '保存配置'}</Button>
      </div>
      <div className="space-y-1 text-[11px] leading-relaxed text-text-tertiary">
        <p className="break-all" data-settings-id="tailscale-settings.8">{settingsText("tailscale-settings.8")}{status.ip || '无 IP'} · {status.dns || '无域名'}</p>
        <p className="break-all" data-settings-id="tailscale-settings.9">{settingsText("tailscale-settings.9")}{status.target}</p>
      </div>
      {status.url && <div className="min-w-0 space-y-2">
        <p className="text-[11px] text-text-secondary" data-settings-id="tailscale-settings.10">{settingsText("tailscale-settings.10")}<span className="text-text-tertiary">{status.running ? '· 当前入口' : '· 预览地址（尚未确认开启）'}</span></p>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
          <code className="min-w-0 break-all text-xs">{status.url}</code>
          <Button size="sm" disabled={busy} onClick={() => {
            void navigator.clipboard.writeText(status.url).catch(() => setError('复制失败，请选择地址手动复制'));
          }} data-settings-id="tailscale-settings.11">{settingsText("tailscale-settings.11")}</Button>
        </div>
      </div>}
      <p className="text-[11px] leading-relaxed text-text-tertiary" data-settings-id="tailscale-settings.12">{settingsText("tailscale-settings.12")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || changed} onClick={() => void action('start')}>{busy && pending === 'start' ? '启动中…' : status.running ? '应用已保存入口' : '开启当前访问'}</Button>
        <Button size="sm" disabled={busy} onClick={() => void action('stop')}>{busy && pending === 'stop' ? '停止中…' : '关闭当前访问'}</Button>
      </div>
      {changed && <p className="text-[11px] text-text-secondary" data-settings-id="tailscale-settings.13">{settingsText("tailscale-settings.13")}</p>}
      <p className="text-[11px] leading-relaxed text-text-tertiary" data-settings-id="tailscale-settings.14">{settingsText("tailscale-settings.14")}</p>
    </>}
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </RemoteSettingsSection>;
}
