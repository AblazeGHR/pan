import { useEffect, useState, type ReactNode } from 'react';

type Status = { mode: string; ready: boolean; error?: { message: string; resource: string; reason: string } };
type RecordRow = { id: string; originalId: string; sourceFile: string; sha256: string; revision: number; reasonCodes: string[]; deleteBlocked: boolean; deleteReason: string; deleted?: boolean };
type Pending = { id: string; key: string };

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
  if (!status) return <p role="status" className="p-6">Checking startup state...</p>;
  if (status.mode === 'unverified') return <main className="p-6"><p role="alert">Startup state is unverified. Refresh to check again.</p></main>;
  return status.mode === 'maintenance' ? <main className="min-h-screen overflow-y-auto bg-bg-primary p-4 text-text-primary sm:p-8">
    <section className="mx-auto max-w-4xl space-y-4" aria-label="Job maintenance">
      <h1 className="text-2xl font-semibold">Job Maintenance mode</h1>
      <p role="alert">Runtime ownership is unverified. Job execution and writes are blocked until the service is ready.</p>
      <p className="break-words">{status.error?.message}</p>
      <p className="break-words">{status.error?.resource} · {status.error?.reason}</p>
      <p>Verify the original owner and complete the offline handoff before restarting. Leases and unknown effects are preserved.</p>
      <PreservedDataPanel />
    </section>
  </main> : children;
}

/** Same nonexecuting dispositions in maintenance and normal-owner management. */
export function PreservedDataPanel({ authorization }: { authorization?: string | null }) {
  const [key, setKey] = useState('');
  const [token, setToken] = useState<string | null>(authorization ?? null);
  const [items, setItems] = useState<RecordRow[]>([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  async function load(auth: string) {
    const response = await fetch('/api/job-maintenance/records', { headers: { ...(auth === 'cookie' ? {} : {Authorization: `Bearer ${auth}`}) } });
    if (!response.ok) throw new Error('The maintenance inventory is unavailable. Original data is preserved.');
    const result = await response.json() as { items: RecordRow[]; message: string };
    setItems(result.items); setMessage(result.message);
  }
  useEffect(() => {
    if (authorization) { setToken(authorization); void load(authorization).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : 'Read failed')); }
  }, [authorization]);
  async function login() {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/job-maintenance/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
      if (!response.ok) throw new Error('Login failed. Check the local automation/user.key.');
      const data = await response.json() as { token: string };
      setKey(''); setToken(data.token); await load(data.token);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Read failed'); }
    finally { setBusy(false); }
  }
  async function remove(item: RecordRow) {
    if (!token || busy || pending || !window.confirm(`Delete source record ${item.originalId || item.id}？Original bytes are retained in an audit backup. Resources and unknown outcomes are preserved.`)) return;
    const requestKey = `preserved-delete:${crypto.randomUUID()}`;
    setPending({ id: item.id, key: requestKey }); setBusy(true); setError('');
    try {
      const response = await fetch(`/api/job-maintenance/records/${encodeURIComponent(item.id)}`, {
        method: 'DELETE', headers: { ...(token === 'cookie' ? {} : { Authorization: `Bearer ${token}` }), 'Content-Type': 'application/json' },
        body: JSON.stringify({ expectedRevision: item.revision, expectedSha256: item.sha256,
          expectedSourceFile: item.sourceFile, requestKey }),
      });
      if (!response.ok) {
        if (response.status < 500) setPending(null);
        const failure = await response.json().catch(() => ({})) as { error?: { message?: string } };
        throw new Error(failure.error?.message ?? 'Deletion is unconfirmed. Query the original identity.');
      }
      setPending(null);
      setItems((rows) => rows.map((row) => row.id === item.id ? { ...row, deleted: true, deleteBlocked: true } : row));
      await load(token);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unknown result. Query the original request.'); }
    finally { setBusy(false); }
  }
  async function queryOriginal() {
    if (!token || !pending || busy) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/operations?operation=retire&requestKey=${encodeURIComponent(pending.key)}`, {
        headers: { ...(token === 'cookie' ? {} : { Authorization: `Bearer ${token}` }) },
      });
      if (!response.ok) throw new Error('No confirmed result yet. Preserve the original request identity and query again.');
      const receipt = await response.json() as { result?: { deleted?: boolean } };
      if (!receipt.result?.deleted) throw new Error('Deletion of the original request is unconfirmed.');
      setPending(null); setError(''); await load(token);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Query failed'); }
    finally { setBusy(false); }
  }
  return <section className="min-w-0 space-y-3" aria-label="Preserved data history">
    {!token && <form onSubmit={(event) => { event.preventDefault(); void login(); }} className="flex flex-wrap gap-2">
      <label className="min-w-0">Local user key<input aria-label="Local user key" type="password" value={key}
        onChange={(event) => setKey(event.target.value)} className="mt-1 block w-full rounded border p-2 text-black" /></label>
      <button type="submit" disabled={busy} className="rounded border px-3 py-2">Inspect preserved data</button>
    </form>}
    {error && <p role="alert">{error}</p>}
    {pending && <button type="button" disabled={busy} onClick={() => void queryOriginal()}>Query original deletion result</button>}
    {token && <><h2 className="text-xl">Invalid / Retired history</h2><p>{message}</p>
      {items.map((item) => <article key={item.id} className="min-w-0 space-y-2 rounded border border-border-default p-3">
        <h3 className="break-all font-semibold">{item.originalId || item.id}</h3>
        <p>{item.deleted ? 'The source record was deleted. The audit backup is retained.' : 'Invalid data. Original records are preserved and cannot execute. Create a new Job.'}</p>
        <p className="break-all">{item.sourceFile}</p>
        <p className="break-all">SHA-256: {item.sha256} · revision {item.revision}</p>
        <p className="break-words">{item.reasonCodes.join(', ')}</p>
        <button type="button" disabled={item.deleteBlocked || busy || !!pending} onClick={() => void remove(item)}
          className="rounded border px-3 py-2 disabled:opacity-50">Delete source record</button>
        <p>{item.deleteReason}</p>
      </article>)}</>}
  </section>;
}
