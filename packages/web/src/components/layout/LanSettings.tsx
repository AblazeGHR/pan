import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { beginForegroundRequest } from '@/services/foregroundActivity';

type Config = { startupEnabled: boolean; host: string; port: number };
type Status = { config: Config | null; activeConfig: Config | null; running: boolean;
  addresses: string[]; urls: string[]; error: string };

export function LanSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const [error, setError] = useState('');
  async function action(name: 'refresh' | 'save' | 'start' | 'stop', value?: Config) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
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
      if (mounted.current) setBusy(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    void action('refresh');
    return () => { mounted.current = false; };
  }, []);
  const changed = !!config && JSON.stringify(config) !== JSON.stringify(status?.config);
  const valid = !!config && !!config.host && Number.isInteger(config.port) && config.port >= 1 && config.port <= 65535;
  return <section className="space-y-3" aria-label="LAN 访问设置">
    <h3 className="text-xs font-semibold">普通局域网访问（LAN）</h3>
    <p className="text-xs text-text-secondary">默认关闭，无需 Tailscale。开启后，能连接此接口的设备可操作 Pan 的聊天与终端；无登录保护，请只在可信网络使用。HTTP 内容未加密。</p>
    <Button size="sm" disabled={busy} onClick={() => void action('refresh')}>{busy ? '处理中…' : '刷新 LAN 状态'}</Button>
    {status && <p className="text-xs">当前：{status.running ? `已开启 ${status.activeConfig?.host}:${status.activeConfig?.port}` : '已关闭'}</p>}
    {config && status && <>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={busy}
        checked={config.startupEnabled} onChange={event => setConfig({ ...config, startupEnabled: event.target.checked })} />下次启动 Pan 默认开启 LAN</label>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs min-w-0">本机监听地址<select aria-label="LAN 本机监听地址" disabled={busy} value={config.host}
          className="block max-w-full border border-border-default rounded px-2 py-1 bg-bg-primary"
          onChange={event => setConfig({ ...config, host: event.target.value })}>
          <option value="">请选择本机 IPv4 地址</option>
          {config.host && config.host !== '0.0.0.0' && !status.addresses.includes(config.host) && <option value={config.host}>{config.host}（当前不可用）</option>}
          {status.addresses.map(address => <option key={address} value={address}>{address}</option>)}
          <option value="0.0.0.0">0.0.0.0（所有接口，包含 VPN）</option>
        </select></label>
        <label className="text-xs">入口端口<input aria-label="LAN 入口端口" type="number" min={1} max={65535} disabled={busy} value={config.port}
          className="block w-24 border border-border-default rounded px-2 py-1 bg-bg-primary"
          onChange={event => setConfig({ ...config, port: Number(event.target.value) })} /></label>
        <Button size="sm" disabled={busy || !changed || !valid} onClick={() => void action('save', config)}>保存 LAN 配置</Button>
      </div>
      <p className="text-xs text-text-secondary">保存不会改变当前入口；开启、关闭即时生效，无需重启 Pan。更换已开启的地址或端口，请先关闭再开启。启动失败不会影响主入口。</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || changed || !valid || status.running} onClick={() => void action('start')}>开启 LAN 访问</Button>
        <Button size="sm" disabled={busy || !status.running} onClick={() => void action('stop')}>关闭 LAN 访问</Button>
      </div>
    </>}
    {status?.urls.map(url => <div key={url} className="flex flex-wrap items-center gap-2">
      <code className="text-xs break-all">{url}</code>
      <Button size="sm" onClick={() => {
        void (async () => {
          try { await navigator.clipboard.writeText(url); }
          catch { setError('复制失败，请选择地址手动复制（HTTP 局域网页面可能不支持剪贴板）'); }
        })();
      }}>复制 LAN 地址</Button>
    </div>)}
    <p className="text-xs text-text-secondary">请选择连接同一局域网的网卡地址，VPN 地址不代表普通 LAN。0.0.0.0 开放所有接口，不限于局域网。此功能不配置公网 Funnel。</p>
    <p className="text-xs text-text-secondary">请在本机回环地址管理入口。正常关闭会断开此入口连接；异常退出后由操作系统释放端口，下次启动按保存配置恢复。监听成功不证明手机可达；若访问失败，请检查同网段、无线隔离和 Windows 防火墙，本功能不会修改它们。</p>
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </section>;
}
