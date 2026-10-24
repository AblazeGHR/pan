import { useEffect, useState } from 'react';
import type { Job } from '@/types/jobs';
import { fetchLegacyJob, retryLegacyJob } from '@/services/api';
import { useJobStore } from '@/stores/jobStore';

/** Inspect the original record. Never render its compatibility Action/Run projection. */
export function LegacyJobDetails({ job }: { job: Job }) {
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setRecord(null); setError('');
    void fetchLegacyJob(job.jobId).then((value) => { if (active) setRecord(value); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : 'Legacy detail unavailable'); });
    return () => { active = false; };
  }, [job.jobId, job.updatedAt, refresh]);
  const retry = async () => {
    if (!window.confirm('Retry this legacy background process as a new legacy Job?')) return;
    setBusy(true); setError('');
    try { await retryLegacyJob(job.jobId); await useJobStore.getState().loadJobs(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Legacy retry failed'); }
    finally { setBusy(false); }
  };
  return <section className="flex flex-col gap-2 text-xs" aria-label="Legacy execution details">
    <h3>Legacy execution / 旧执行器</h3>
    <p>background_jobs 原始记录与旧 runner / scheduler 状态；不使用新 Job 的 Action、Run 或 Attempt 控制。</p>
    <p>删除使用现有安全取消/清理路径。进程树退出未确认时保留 cleanup_pending，等待显式再次核对；不会强删未知身份。</p>
    <div className="flex gap-2">
      <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh legacy details</button>
      {job.kind === 'background-process' && ['completed', 'failed', 'cancelled'].includes(job.status) &&
        <button type="button" disabled={busy} onClick={() => void retry()}>Retry legacy Job</button>}
    </div>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {record && <details><summary>Original record / log path / delivery details</summary>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-bg-tertiary p-2">{JSON.stringify(record, null, 2)}</pre>
    </details>}
  </section>;
}
