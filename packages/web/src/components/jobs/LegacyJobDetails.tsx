import { useEffect, useState } from 'react';
import type { Job } from '@/types/jobs';
import { fetchLegacyJob } from '@/services/api';

/** Inspect the original record. Never render its compatibility Action/Run projection. */
export function LegacyJobDetails({ job }: { job: Job }) {
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setRecord(null); setError('');
    void fetchLegacyJob(job.jobId).then((value) => { if (active) setRecord(value); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : 'Legacy detail unavailable'); });
    return () => { active = false; };
  }, [job.jobId, job.updatedAt, refresh]);
  return <section className="flex flex-col gap-2 text-xs" aria-label="Legacy execution details">
    <h3>Retired execution record</h3>
    <p>This record came from the legacy runner or scheduler. It is preserved for history and does not expose a legacy writer or recovery path.</p>
    <p className="text-warning">Read-only · Invalid/Retired records cannot be restored into a new Job.</p>
    <div className="flex gap-2">
      <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh record</button>
    </div>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {record && <dl className="grid grid-cols-1 gap-2 rounded border border-border-muted p-3 sm:grid-cols-[9rem_minmax(0,1fr)]">
      {Object.entries({
        argv: record.argv,
        'Working directory': record.cwd,
        Environment: record.env ?? record.environment,
        Window: record.hideWindow ?? record.hide_window ?? record.window,
        Timeout: record.timeout ?? record.timeoutSec ?? record.timeout_sec,
        'Log path': record.logPath ?? record.log_path,
      }).map(([label, value]) => <div key={label} className="contents"><dt className="text-text-tertiary">{label}</dt><dd className="min-w-0 whitespace-pre-wrap break-all font-mono">{value == null ? 'Not provided' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</dd></div>)}
    </dl>}
    {record && <details><summary>Original record / log path / delivery details</summary>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-bg-tertiary p-2">{JSON.stringify(record, null, 2)}</pre>
    </details>}
  </section>;
}
