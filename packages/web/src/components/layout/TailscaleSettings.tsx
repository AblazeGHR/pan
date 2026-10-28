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
    id="remote-tailscale"
    testId="remote-tailscale-section"
    title="Tailscale 备用访问"
    description="Cloudflare 隧道不可用时，同一 tailnet 的设备可访问 Pan。自动绑定 Tailscale 地址，Pan 继续监听回环；不开放 Funnel 公网。"
    status={<RemoteStatus tone={status?.running ? 'ok' : 'idle'}>{runState}</RemoteStatus>}
  >
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void action('refresh')}>
        {busy && pending === 'refresh' ? '处理中…' : status ? '刷新状态' : '重试加载状态'}
      </Button>
    </div>
    {config && status && <>
      <RemoteSwitchRow
        label="下次启动 Pan 默认开启"
        hint="保存默认配置不会启停当前入口；当前开关即时生效，无需重启 Pan。"
        checked={config.startupEnabled}
        disabled={busy}
        onChange={value => setConfig({ ...config, startupEnabled: value })}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_6rem_auto] sm:items-end">
        <RemoteField id="tailscale-protocol" label="协议">
          <select id="tailscale-protocol" disabled={busy} value={config.protocol}
            className={`${remoteControlClass} w-full max-w-full`}
            onChange={event => setConfig({ ...config, protocol: event.target.value as Config['protocol'] })}>
            <option value="http">HTTP + Tailscale IP（推荐）</option><option value="https">HTTPS + Serve 域名</option>
          </select>
        </RemoteField>
        <RemoteField id="tailscale-port" label="访问端口">
          <input id="tailscale-port" type="number" min={1} max={65535} disabled={busy} value={config.port}
            className={`${remoteControlClass} w-full`}
            onChange={event => setConfig({ ...config, port: Number(event.target.value) })} />
        </RemoteField>
        <Button size="sm" disabled={busy || !changed} onClick={() => void action('save')}>{busy && pending === 'save' ? '保存中…' : '保存配置'}</Button>
      </div>
      <div className="space-y-1 text-[11px] leading-relaxed text-text-tertiary">
        <p className="break-all">自动检测：{status.ip || '无 IP'} · {status.dns || '无域名'}</p>
        <p className="break-all">目标：{status.target}</p>
      </div>
      {status.url && <div className="min-w-0 space-y-2">
        <p className="text-[11px] text-text-secondary">访问地址 <span className="text-text-tertiary">{status.running ? '· 当前入口' : '· 预览地址（尚未确认开启）'}</span></p>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
          <code className="min-w-0 break-all text-xs">{status.url}</code>
          <Button size="sm" disabled={busy} onClick={() => {
            void navigator.clipboard.writeText(status.url).catch(() => setError('复制失败，请选择地址手动复制'));
          }}>复制地址</Button>
        </div>
      </div>}
      <p className="text-[11px] leading-relaxed text-text-tertiary">HTTP 默认端口 8768，地址易记；HTTPS 使用 Tailscale 原生证书，首次使用可能需要在管理界面启用 HTTPS。地址由 Tailscale 分配，不能自定义绑定其它网卡。</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || changed} onClick={() => void action('start')}>{busy && pending === 'start' ? '启动中…' : status.running ? '应用已保存入口' : '开启当前访问'}</Button>
        <Button size="sm" disabled={busy} onClick={() => void action('stop')}>{busy && pending === 'stop' ? '停止中…' : '关闭当前访问'}</Button>
      </div>
      {changed && <p className="text-[11px] text-text-secondary">请先保存配置，再开启或应用入口；关闭始终可用。</p>}
      <p className="text-[11px] leading-relaxed text-text-tertiary">正常退出会关闭本功能入口。异常退出时 Serve 可能持续保留映射，Pan 下次启动会先清理，再按默认配置恢复；清理失败会就地报错。修改中的外部 Serve 入口不会被接管。</p>
    </>}
    {(error || status?.error) && <p role="alert" className="text-xs text-danger whitespace-pre-wrap">{error || status?.error}</p>}
  </RemoteSettingsSection>;
}
