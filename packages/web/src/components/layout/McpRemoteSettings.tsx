import { useEffect, useState } from 'react';
import { beginForegroundRequest } from '@/services/foregroundActivity';
import { Button } from '@/components/ui/Button';
import {
  RemoteField,
  RemoteSettingsSection,
  RemoteStatus,
  RemoteSwitchRow,
  remoteControlClass,
} from './RemoteSettingsSection';
type Config = { enabled: boolean; port: number; public_hostname: string; access_issuer: string;
  access_audience: string; config_path: string; binary_path: string };
type Status = { config: Config; publicUrl: string; gateway: { listening: boolean }; tunnel: { running: boolean } };
async function call(path = '', method = 'GET', body?: Config) {
  const finishRequest = beginForegroundRequest();
  try {
  const r = await fetch(`/api/remote/mcp${path}`, { method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  const s = await r.json();
  if (!r.ok || s.ok === false) throw new Error(s.error || s.detail || 'Request failed');
  return s;
  } finally { finishRequest(); }
}
export function McpRemoteSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { let active = true;
    call().then(s => { if (active) { setStatus(s); setConfig(s.config); } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  async function action(name: string) {
    if (!config) return;
    setBusy(true); setPending(name); setError('');
    try { await call(name === 'save' ? '' : `/${name}`, name === 'save' ? 'PUT' : 'POST', name === 'save' ? config : undefined);
      const s = await call(); setStatus(s); setConfig(s.config);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); setPending(''); }
  }
  const busyLabels: Record<string, string> = {
    save: '保存中…', start: '启动中…', restart: '重启中…', stop: '停止中…',
  };
  const fields: [keyof Pick<Config, 'public_hostname' | 'access_issuer' | 'access_audience' | 'config_path' | 'binary_path'>, string][] = [
    ['public_hostname', 'MCP 域名'], ['access_issuer', 'Access 团队 HTTPS 地址'], ['access_audience', 'Access 应用 AUD'],
    ['config_path', 'MCP 隧道配置文件'], ['binary_path', 'cloudflared 路径（可留空）']];
  const ready = Boolean(status?.gateway.listening && status?.tunnel.running);
  return <RemoteSettingsSection
    id="remote-mcp"
    testId="remote-mcp-section"
    title="MCP 远程连接"
    description="独立管理 MCP 网关和隧道，不影响前端隧道。"
    status={<RemoteStatus tone={ready ? 'ok' : 'idle'}>
      {status
        ? `网关：${status.gateway.listening ? '就绪' : '未就绪'} · 隧道进程：${status.tunnel.running ? '运行中' : '已停止'}`
        : error ? '状态不可用' : '正在读取状态…'}
    </RemoteStatus>}
  >
    {config && <>
      <RemoteSwitchRow
        label="随 Pan 启动"
        hint="保存配置并启动或重启 MCP 后生效。"
        checked={config.enabled}
        disabled={busy}
        onChange={value => setConfig({ ...config, enabled: value })}
      />
      {status?.publicUrl && <div className="min-w-0 space-y-2">
        <p className="text-[11px] text-text-secondary">公网地址</p>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
          <code className="min-w-0 break-all text-xs">{status.publicUrl}</code>
          <Button size="sm" disabled={busy} onClick={() => {
            void navigator.clipboard.writeText(status.publicUrl).catch(() => setError('复制失败，请选择地址手动复制'));
          }}>复制公网地址</Button>
        </div>
      </div>}
      <div className="space-y-2">
        <p className="text-xs font-medium text-text-primary">连接配置</p>
        {fields.map(([key, label]) => <RemoteField key={key} id={`mcp-${key}`} label={label}>
          <input id={`mcp-${key}`} disabled={busy} className={`${remoteControlClass} w-full`} value={config[key]}
            onChange={e => setConfig({ ...config, [key]: e.target.value })} />
        </RemoteField>)}
        <RemoteField id="mcp-port" label="网关端口">
          <input id="mcp-port" type="number" min="1024" max="65535" value={config.port} disabled={busy}
            className={`${remoteControlClass} w-full sm:w-40`}
            onChange={e => setConfig({ ...config, port: Number(e.target.value) })} />
        </RemoteField>
      </div>
      <div className="flex flex-wrap gap-2">{([['save', '保存配置'], ['start', '启动'], ['restart', '重启 MCP'], ['stop', '停止 MCP']] as const).map(([name, label]) =>
        <Button key={name} size="sm" disabled={busy} onClick={() => void action(name)}>{busy && pending === name ? busyLabels[name] : label}</Button>)}
      </div>
      <p className="text-[11px] leading-relaxed text-text-tertiary">保存后启动或重启使配置生效。停止会中断 ChatGPT 连接。隧道进程运行不代表公网已连接。</p>
    </>}
    {!config && !error && <p role="status" className="text-xs text-text-secondary">正在读取 MCP 状态…</p>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </RemoteSettingsSection>;
}
