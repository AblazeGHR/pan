import { useEffect, useState, type ReactNode } from 'react';

type Status = { mode: string; ready: boolean; error?: { message: string; resource: string; reason: string } };
type RecordRow = { id: string; originalId: string; sourceFile: string; sha256: string; revision: number; reasonCodes: string[]; deleteBlocked: boolean; deleteReason: string };

/** Probe once before mounting the effectful shell; the server also fences APIs. */
export function JobMaintenanceGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/job-maintenance/status', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('Startup status unavailable');
        setStatus(await response.json() as Status);
      }).catch(() => { if (!controller.signal.aborted) setStatus({ mode: 'unverified', ready: false }); });
    return () => controller.abort();
  }, []);
  if (!status) return <p role="status" className="p-6">正在检查启动状态…</p>;
  if (status.mode === 'unverified') return <main className="p-6"><p role="alert">无法核实启动状态，请刷新后重试。</p></main>;
  return status.mode === 'maintenance' ? <Maintenance status={status} /> : children;
}

function Maintenance({ status }: { status: Status }) {
  const [key, setKey] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [items, setItems] = useState<RecordRow[]>([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function login() {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/job-maintenance/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
      if (!response.ok) throw new Error('登录失败，请检查本机 automation/user.key。');
      const data = await response.json() as { token: string };
      setKey(''); setToken(data.token);
      const records = await fetch('/api/job-maintenance/records', { headers: { Authorization: `Bearer ${data.token}` } });
      if (!records.ok) throw new Error('处置清单无法读取；原数据未改变。');
      const result = await records.json() as { items: RecordRow[]; message: string };
      setItems(result.items); setMessage(result.message);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取失败'); }
    finally { setBusy(false); }
  }
  return <main className="min-h-screen overflow-y-auto bg-bg-primary p-4 text-text-primary sm:p-8">
    <section className="mx-auto max-w-4xl space-y-4" aria-label="Job maintenance">
      <h1 className="text-2xl font-semibold">Job 维护模式</h1>
      <p role="alert">运行所有权尚未核实，Job 执行与写入已阻止。服务尚未就绪。</p>
      <p className="break-words">{status.error?.message}</p>
      <p className="break-words">{status.error?.resource} · {status.error?.reason}</p>
      <p>请按原身份核实旧 owner，完成离线交接后重新启动。不会自动接管、清空 lease 或重放未知效果。</p>
      {!token && <form onSubmit={(event) => { event.preventDefault(); void login(); }} className="flex flex-wrap gap-2">
        <label className="min-w-0">本机用户密钥<input aria-label="本机用户密钥" type="password" value={key}
          onChange={(event) => setKey(event.target.value)} className="mt-1 block w-full rounded border p-2 text-black" /></label>
        <button type="submit" disabled={busy} className="rounded border px-3 py-2">查看保留数据</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      {token && <><h2 className="text-xl">不兼容的保留数据</h2><p>{message}</p>
        {items.map((item) => <article key={item.id} className="min-w-0 space-y-2 rounded border border-border-default p-3">
          <h3 className="break-all font-semibold">{item.originalId || item.id}</h3>
          <p>未知数据 · 原数据保留，未迁移、未执行。建议手动创建新的 Job。</p>
          <p className="break-all">{item.sourceFile}</p>
          <p className="break-all">SHA-256: {item.sha256} · revision {item.revision}</p>
          <p className="break-words">{item.reasonCodes.join(', ')}</p>
          <button type="button" disabled={item.deleteBlocked} className="rounded border px-3 py-2 disabled:opacity-50">删除原记录</button>
          <p>{item.deleteReason}</p>
        </article>)}</>}
    </section>
  </main>;
}
