import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { automationMutation, useAutomationAuth } from '@/stores/automationStore';
import type { AutomationSnapshot } from '@/types/automation';
import { cardClass, fieldClass, JsonDetail } from './fields';

export function RunList({ snapshot }: { snapshot: AutomationSnapshot }) {
  const token = useAutomationAuth((s) => s.token);
  const [filter, setFilter] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const runs = Object.values(snapshot.runs).filter((r) => (!filter || r.triggerId === filter) && (!status || r.status === status)).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return <section aria-label="Runs" className="min-w-0 space-y-4"><h2 className="text-lg font-semibold">Runs</h2>
    <p className="text-sm text-text-secondary">Snapshot only. Use Refresh Runs above for current status. Failed and uncertain Runs are not automatically retried; there is no resend button. Uncertain effects require reconciliation.</p>
    <div className="grid min-w-0 gap-3 sm:grid-cols-2"><label className="text-sm">Trigger filter<select className={fieldClass} value={filter} onChange={(e) => setFilter(e.target.value)}><option value="">All Triggers</option>{[...new Set([...Object.keys(snapshot.triggers), ...Object.values(snapshot.runs).map((r) => r.triggerId)])].map((id) => <option key={id} value={id}>{snapshot.triggers[id]?.name ?? id}</option>)}</select></label>
      <label className="text-sm">Run status<select className={fieldClass} value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{['queued', 'running', 'succeeded', 'failed', 'cancelled', 'uncertain', 'skipped'].map((s) => <option key={s}>{s}</option>)}</select></label></div>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {!runs.length && <p className={cardClass}>No matching Runs in this snapshot.</p>}
    {runs.map((run) => <article key={run.id} className={`${cardClass} space-y-3 break-words`}>
      <h3 className="break-all font-semibold">{run.status} · {run.id}</h3>
      <dl className="space-y-1 text-xs text-text-secondary"><div>Trigger: {snapshot.triggers[run.triggerId]?.name ?? 'Deleted Trigger'} · {run.triggerId} · revision {run.triggerRevision}</div><div>Event: {run.event.type} · {run.event.id}</div><div>Action: {run.action?.name ?? run.actionId} · {run.actionId}</div><div>Created: {run.createdAt} · Started: {run.startedAt ?? 'Not started'} · Finished: {run.finishedAt ?? 'Not finished'}</div></dl>
      {run.error && <p className="whitespace-pre-wrap break-all text-sm text-danger">{run.error}</p>}
      {run.status === 'uncertain' && <p className="text-sm text-danger">Effects may have occurred. Reconcile each target before taking any further action.</p>}
      <JsonDetail label="Event and payload" value={run.event} /><JsonDetail label="Resolved parameters and State" value={{ arguments: run.arguments, state: run.state }} />
      <JsonDetail label="Actual Action at execution start" value={run.action ? { action: run.action, digest: run.actionDigest } : 'No execution snapshot recorded yet'} />
      <JsonDetail label="Fixed target IDs" value={run.targetIds ?? []} />
      {run.selection && <div className="space-y-2 text-xs"><p>Query selection: {run.selection.matched} matched from {run.selection.candidateCount} candidates · frozen {run.selection.matchedAt}</p><p>Targets were fixed once. Later Session changes do not recompute this Run.</p><JsonDetail label="Selection diagnostics" value={run.selection} /></div>}
      <JsonDetail label="Per-target results" value={run.targetResults ?? []} />
      <JsonDetail label="Action result / aggregate counts" value={run.result} />
      {run.status === 'queued' && <Button disabled={!token || !!busy} onClick={() => { setBusy(run.id); setError(''); void automationMutation(`automation/runs/${encodeURIComponent(run.id)}/cancel`, 'POST', { expectedRevision: run.revision }).catch((e: unknown) => setError(e instanceof Error ? e.message : 'Cancel failed')).finally(() => setBusy(null)); }}>Cancel queued Run</Button>}
    </article>)}
  </section>;
}
