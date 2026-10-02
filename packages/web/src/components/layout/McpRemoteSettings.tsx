import { useEffect, useState } from 'react';
type Config = { enabled: boolean; port: number; public_hostname: string; access_issuer: string;
  access_audience: string; config_path: string; binary_path: string };
type Status = { config: Config; publicUrl: string; gateway: { listening: boolean }; tunnel: { running: boolean } };
async function call(path = '', method = 'GET', body?: Config) {
  const r = await fetch(`/api/remote/mcp${path}`, { method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  const s = await r.json();
  if (!r.ok || s.ok === false) throw new Error(s.error || s.detail || 'Request failed');
  return s;
}
export function McpRemoteSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { let active = true;
    call().then(s => { if (active) { setStatus(s); setConfig(s.config); } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  async function action(name: string) {
    if (!config) return;
    setBusy(true); setError('');
    try { await call(name === 'save' ? '' : `/${name}`, name === 'save' ? 'PUT' : 'POST', name === 'save' ? config : undefined);
      const s = await call(); setStatus(s); setConfig(s.config);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const fields: [keyof Pick<Config, 'public_hostname' | 'access_issuer' | 'access_audience' | 'config_path' | 'binary_path'>, string][] = [
    ['public_hostname', 'MCP 域名'], ['access_issuer', 'Access 团队 HTTPS 地址'], ['access_audience', 'Access 应用 AUD'],
    ['config_path', 'MCP 隧道配置文件'], ['binary_path', 'cloudflared 路径（可留空）']];
  return <section className="space-y-2">
    <h3 className="text-xs font-semibold">MCP 远程连接</h3>
    <p className="text-xs text-text-secondary">独立管理 MCP 网关和隧道，不影响前端隧道。</p>
    {config && <>
      <label className="flex gap-2 text-xs"><input type="checkbox" checked={config.enabled} disabled={busy}
        onChange={e => setConfig({ ...config, enabled: e.target.checked })} />随 Pan 启动</label>
      <p className="text-xs">网关：{status?.gateway.listening ? '就绪' : '未就绪'} · 隧道进程：{status?.tunnel.running ? '运行中' : '已停止'}</p>
      <p className="text-xs break-all">{status?.publicUrl}</p>
      <details><summary className="text-xs cursor-pointer">连接配置</summary><div className="space-y-2 mt-2">
        {fields.map(([key, label]) => <label key={key} className="block text-xs">{label}<input disabled={busy}
          className="block w-full border rounded px-2 py-1 bg-bg-primary" value={config[key]}
          onChange={e => setConfig({ ...config, [key]: e.target.value })} /></label>)}
        <label className="block text-xs">网关端口<input type="number" min="1024" max="65535" value={config.port} disabled={busy}
          onChange={e => setConfig({ ...config, port: Number(e.target.value) })} /></label>
      </div></details>
      <div className="flex flex-wrap gap-2">{([['save', '保存配置'], ['start', '启动'], ['restart', '重启 MCP'], ['stop', '停止 MCP']] as const).map(([name, label]) =>
        <button key={name} type="button" disabled={busy} onClick={() => void action(name)}
          className="text-xs border rounded px-2 py-1 disabled:opacity-50">{label}</button>)}</div>
      <p className="text-xs text-text-tertiary">保存后启动或重启使配置生效。停止会中断 ChatGPT 连接。隧道进程运行不代表公网已连接。</p>
    </>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </section>;
}
