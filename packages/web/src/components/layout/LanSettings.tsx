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
    id="remote-lan"
    testId="remote-lan-section"
    title="普通局域网访问（LAN）"
    description="默认关闭，无需 Tailscale。开启后，能连接此接口的设备可操作 Pan 的聊天与终端；无登录保护，请只在可信网络使用。HTTP 内容未加密。"
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
        label="下次启动 Pan 默认开启 LAN"
        hint="只改保存的默认值；当前入口仍由下方开关控制。"
        checked={config.startupEnabled}
        disabled={busy}
        onChange={value => setConfig({ ...config, startupEnabled: value })}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_6rem_auto] sm:items-end">
        <RemoteField id="lan-host" label="本机监听地址">
          <select id="lan-host" aria-label="LAN 本机监听地址" disabled={busy} value={config.host}
            className={`${remoteControlClass} w-full max-w-full`}
            onChange={event => setConfig({ ...config, host: event.target.value })}>
            <option value="">请选择本机 IPv4 地址</option>
            {config.host && config.host !== '0.0.0.0' && !status.addresses.includes(config.host) && <option value={config.host}>{config.host}（当前不可用）</option>}
            {status.addresses.map(address => <option key={address} value={address}>{address}</option>)}
            <option value="0.0.0.0">0.0.0.0（所有接口，包含 VPN）</option>
          </select>
        </RemoteField>
        <RemoteField id="lan-port" label="入口端口">
          <input id="lan-port" aria-label="LAN 入口端口" type="number" min={1} max={65535} disabled={busy} value={config.port}
            className={`${remoteControlClass} w-full`}
            onChange={event => setConfig({ ...config, port: Number(event.target.value) })} />
        </RemoteField>
        <Button size="sm" disabled={busy || !changed || !valid} onClick={() => void action('save', config)}>{busy && pending === 'save' ? '保存中…' : '保存 LAN 配置'}</Button>
      </div>
      <p className="text-[11px] leading-relaxed text-text-tertiary">保存不会改变当前入口；开启、关闭即时生效，无需重启 Pan。更换已开启的地址或端口，请先关闭再开启。启动失败不会影响主入口。</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || changed || !valid || status.running} onClick={() => void action('start')}>{busy && pending === 'start' ? '启动中…' : '开启 LAN 访问'}</Button>
        <Button size="sm" disabled={busy || !status.running} onClick={() => void action('stop')}>{busy && pending === 'stop' ? '停止中…' : '关闭 LAN 访问'}</Button>
      </div>
      {status.urls.length > 0 && <div className="space-y-2">
        <p className="text-[11px] text-text-secondary">入口地址</p>
        {status.urls.map(url => <div key={url} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
          <code className="min-w-0 break-all text-xs">{url}</code>
          <Button size="sm" onClick={() => {
            void (async () => {
              try { await navigator.clipboard.writeText(url); }
              catch { setError('复制失败，请选择地址手动复制（HTTP 局域网页面可能不支持剪贴板）'); }
            })();
          }}>复制 LAN 地址</Button>
        </div>)}
      </div>}
    </>}
    <div className="space-y-1 text-[11px] leading-relaxed text-text-tertiary">
      <p>请选择连接同一局域网的网卡地址，VPN 地址不代表普通 LAN。0.0.0.0 开放所有接口，不限于局域网。此功能不配置公网 Funnel。</p>
      <p>请在本机回环地址管理入口。正常关闭会断开此入口连接；异常退出后由操作系统释放端口，下次启动按保存配置恢复。监听成功不证明手机可达；若访问失败，请检查同网段、无线隔离和 Windows 防火墙，本功能不会修改它们。</p>
    </div>
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </RemoteSettingsSection>;
}
