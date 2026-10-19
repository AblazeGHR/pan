import { useEffect, useRef, useState } from 'react';

type Status = {
  enabled: boolean; registered: boolean; available: boolean; conflict: boolean;
  blocked: boolean; reason: string; targetRoot: string; dataDirectory: string;
  entry: string; registration: string | null; revision: string;
};

export function LoginStartupSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const generation = useRef(0);
  async function action(enabled?: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    const current = generation.current;
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/settings/login-startup', {
        method: enabled === undefined ? 'GET' : 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: enabled === undefined ? undefined : JSON.stringify({ enabled, revision: status?.revision }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Login startup request failed');
      if (typeof result.enabled !== 'boolean' || typeof result.revision !== 'string') throw new Error('Invalid login startup status');
      if (current === generation.current) setStatus(result);
    } catch (reason) {
      // A failed/uncertain PUT may have reached the OS. Re-read native truth.
      if (enabled === undefined && current === generation.current) setStatus(null);
      if (enabled !== undefined) {
        try {
          const response = await fetch('/api/settings/login-startup');
          const result = await response.json();
          if (response.ok && typeof result.enabled === 'boolean' && current === generation.current) setStatus(result);
          else if (current === generation.current) setStatus(null);
        } catch { if (current === generation.current) setStatus(null); }
      }
      if (current === generation.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (current === generation.current) { inFlight.current = false; setBusy(false); }
    }
  }
  useEffect(() => {
    void action();
    return () => { generation.current += 1; inFlight.current = false; };
    // Component is mounted only while the Settings section is visible.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <section className="space-y-2" aria-label="Login startup">
    <h3 className="text-xs font-semibold uppercase text-text-tertiary">Login startup</h3>
    <button type="button" role="switch" aria-label="Start Pan after login"
      aria-checked={status?.enabled ?? false}
      disabled={busy || !status || status.conflict || (!status.available && !status.registered)}
      onClick={() => void action(!(status?.registered ?? false))}
      className="w-full rounded-md border border-border-muted px-3 py-2 text-left text-xs disabled:opacity-50">
      Start Pan after login <span className="float-right">{busy ? 'Checking…' : !status ? 'Unknown' : status.enabled ? 'On' : status.registered ? 'Disabled by OS' : 'Off'}</span>
    </button>
    <p className="text-[11px] text-text-secondary">Starts Pan for the current user after login. This is not a system service and does not start before login. Worker recovery follows the existing Pan startup policy.</p>
    {status && <div className="break-all text-[11px] text-text-tertiary">
      <p>Installation: {status.targetRoot}</p><p>Data: {status.dataDirectory}</p><p>Entry: {status.entry}</p>
      {status.reason && <p role="status">{status.reason}</p>}
      {status.conflict && <pre className="whitespace-pre-wrap">{status.registration}</pre>}
    </div>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    <button type="button" disabled={busy} onClick={() => void action()} className="text-xs underline">Refresh login startup status</button>
  </section>;
}
