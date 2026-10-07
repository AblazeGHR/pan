import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { beginForegroundRequest } from '@/services/foregroundActivity';

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
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    call().then(value => { if (active) { setStatus(value); setConfig(value.config); } })
      .catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, []);
  async function action(name: 'save' | 'start' | 'stop' | 'refresh') {
    setBusy(true); setError('');
    try {
      const value = await call(name === 'refresh' ? 'GET' : name === 'save' ? 'PUT' : 'POST',
        name === 'start' || name === 'stop' ? `/${name}` : '', name === 'save' ? config ?? undefined : undefined);
      setStatus(value);
      if (name === 'save' || !config) setConfig(value.config);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  const changed = !!config && !!status && JSON.stringify(config) !== JSON.stringify(status.config);
  return <section className="space-y-3">
    <h3 className="text-xs font-semibold">Tailscale 备用访问</h3>
    <p className="text-xs text-text-secondary">Cloudflare 隧道不可用时，同一 tailnet 的设备可访问 Pan。自动绑定 Tailscale 地址，Pan 继续监听回环；不开放 Funnel 公网。</p>
    {config && status && <>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={busy}
        checked={config.startupEnabled} onChange={event => setConfig({ ...config, startupEnabled: event.target.checked })} />
        下次启动 Pan 默认开启</label>
      <p className="text-xs text-text-secondary">保存默认配置不会启停当前入口；当前开关即时生效，无需重启 Pan。</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs">协议<select disabled={busy} value={config.protocol}
          className="block border border-border-default rounded px-2 py-1 bg-bg-primary"
          onChange={event => setConfig({ ...config, protocol: event.target.value as Config['protocol'] })}>
          <option value="http">HTTP + Tailscale IP（推荐）</option><option value="https">HTTPS + Serve 域名</option>
        </select></label>
        <label className="text-xs">访问端口<input type="number" min={1} max={65535} disabled={busy} value={config.port}
          className="block w-24 border border-border-default rounded px-2 py-1 bg-bg-primary"
          onChange={event => setConfig({ ...config, port: Number(event.target.value) })} /></label>
        <Button size="sm" disabled={busy || !changed} onClick={() => void action('save')}>保存配置</Button>
      </div>
      <p className="text-xs break-all">自动检测：{status.ip || '无 IP'} · {status.dns || '无域名'}</p>
      <p className="text-xs text-text-secondary">HTTP 默认端口 8768，地址易记；HTTPS 使用 Tailscale 原生证书，首次使用可能需要在管理界面启用 HTTPS。地址由 Tailscale 分配，不能自定义绑定其它网卡。目标：{status.target}</p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs">当前：{busy ? '处理中…' : status.running ? '已开启' : status.pendingCleanup ? '待协调' : '已关闭'} · Tailscale {status.online ? '在线' : '未就绪'}</span>
        <Button size="sm" disabled={busy || changed} onClick={() => void action('start')}>{status.running ? '应用已保存入口' : '开启当前访问'}</Button>
        <Button size="sm" disabled={busy} onClick={() => void action('stop')}>关闭当前访问</Button>
        <Button size="sm" disabled={busy} onClick={() => void action('refresh')}>刷新状态</Button>
      </div>
      {changed && <p className="text-xs text-text-secondary">请先保存配置，再开启或应用入口；关闭始终可用。</p>}
      {status.url && <div className="flex flex-wrap items-center gap-2">
        <code className="text-xs break-all">{status.url}</code>
        <Button size="sm" disabled={busy} onClick={() => {
          void navigator.clipboard.writeText(status.url).catch(() => setError('复制失败，请选择地址手动复制'));
        }}>复制地址</Button>
        <span className="text-xs text-text-secondary">{status.running ? '当前入口' : '预览地址（尚未确认开启）'}</span>
      </div>}
      <p className="text-xs text-text-secondary">正常退出会关闭本功能入口。异常退出时 Serve 可能持续保留映射，Pan 下次启动会先清理，再按默认配置恢复；清理失败会就地报错。修改中的外部 Serve 入口不会被接管。</p>
    </>}
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </section>;
}
