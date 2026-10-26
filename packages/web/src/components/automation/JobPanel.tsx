import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { automationRequest } from '@/stores/automationStore';
import type { Action } from '@/types/actions';
import type { Trigger } from '@/types/automation';
import { scheduleSummary, type ActionJob, type JobEdit, type SchedulerSource } from '@/types/automationJobs';
import { cardClass, fieldClass } from './fields';
import { JobEditor } from './JobEditor';

export function JobPanel({ jobs, sources, triggers, actions, operations, focusId, refresh, jump }: { jobs: ActionJob[]; sources: SchedulerSource[]; triggers: Trigger[]; actions: Action[]; operations: JobEdit[]; focusId?: string; refresh: () => Promise<void>; jump: (tab: 'Job' | 'Trigger' | 'Event Register', id: string) => void }) {
  const [editor, setEditor] = useState<{ sequence: number; job: ActionJob | null } | null>(null);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function mutate(path: string, method: string, body?: unknown) { setBusy(true); setError(''); try { await automationRequest(path, method, body); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : 'Operation failed'); } finally { setBusy(false); } }
  return <div className="min-w-0 space-y-5"><header className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-lg font-semibold">Jobs</h2><p className="mt-1 text-sm text-text-secondary">Reusable Action chains, with manual runs or shared time events.</p></div><Button disabled={!!editor} variant="primary" onClick={() => setEditor({ sequence: Date.now(), job: null })}>New Job</Button></header>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {operations.length > 0 && !editor && <section aria-label="Incomplete Job setup" className={`${cardClass} space-y-3`}><h3 className="font-medium">Incomplete setup</h3><p className="text-xs text-text-secondary">Created drafts stay disabled. Resume the saved operation to finish without creating duplicates.</p>{operations.map((op) => <div key={op.key} className="flex flex-wrap items-center gap-2"><span className="min-w-0 break-all text-xs">{jobs.find((j) => j.jobId === op.jobId)?.name ?? 'New Job'} · {op.error ?? op.status}</span><Button disabled={busy} size="sm" onClick={() => void mutate(`automation/job-edits/${op.key}/resume`, 'POST')}>Resume setup</Button><Button disabled={busy} size="sm" onClick={() => void mutate(`automation/job-edits/${op.key}/cancel`, 'POST')}>Keep draft</Button></div>)}</section>}
    {editor && <JobEditor key={editor.sequence} initial={editor.job} actions={actions} sources={sources} triggers={triggers} onClose={() => setEditor(null)} onSaved={refresh} />}
    <label className="block text-sm">Search Jobs<input type="search" className={fieldClass} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Job name or ID" /></label>
    {!jobs.length && !editor && <section className={`${cardClass} py-10 text-center`}><h3 className="font-medium">Start with an Action chain</h3><p className="mx-auto mt-2 max-w-md text-sm text-text-secondary">Build ordered steps, then add an existing time source or create a new Scheduler. Management is available in Canonical Jobs.</p></section>}
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">{jobs.filter((j) => `${j.name} ${j.jobId}`.toLowerCase().includes(search.toLowerCase())).map((job) => {
      const owned = triggers.filter((t) => t.ownerJobId === job.jobId);
      return <article key={job.jobId} id={job.jobId} aria-label={`Job ${job.name}`} className={`${cardClass} space-y-3 ${focusId === job.jobId ? 'ring-2 ring-accent' : ''}`}><header className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><h3 className="break-words font-semibold">{job.name}</h3><p className="mt-1 text-xs text-text-secondary">{job.enabled ? 'Enabled' : 'Disabled'} · Revision {job.configRevision} · {job.runCount ?? 0} runs</p></div><span className="rounded-full bg-bg-tertiary px-2 py-1 text-xs text-text-secondary">{job.lastStatus ?? job.status}</span></header>
        <p className="break-words text-sm text-text-secondary">{job.config.description || 'No description'}</p><p className="break-words text-sm">{(job.config.steps ?? [{ actionId: job.config.actionId }]).map((s) => actions.find((a) => a.id === s.actionId)?.name ?? s.actionId).join(' → ')}</p>
        <p className="text-xs text-text-secondary">Overlap: {job.config.overlap.mode} · Recovery {job.config.recovery.enabled ? 'enabled' : 'off'}</p>
        {job.integrity && (job.integrity.status !== 'complete' || job.integrity.approvalRequired) && <p className="text-xs text-danger">Action references need repair before this Job can run.</p>}
        <section className="space-y-2 border-t border-border-default pt-3" aria-label={`Schedulers for ${job.name}`}>{!owned.length ? <p className="text-xs text-text-secondary">{job.config.trigger.kind === 'manual' ? 'Manual run · No linked Scheduler' : `Existing ${job.config.trigger.kind} trigger · Timing kept in existing Job controls`}</p> : owned.map((t) => { const source = sources.find((s) => s.id === t.eventFilter?.['payload.scheduleId']); return <div key={t.id} className="min-w-0 space-y-1"><p className="break-words text-xs">{source ? `${source.name} · ${scheduleSummary(source)} · ${source.enabled ? 'Enabled' : 'Disabled'}` : 'Scheduler source missing'}</p><div className="flex flex-wrap gap-3 text-xs"><button type="button" className="text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => jump('Trigger', t.id)}>Open Trigger</button>{source && <button type="button" className="text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => jump('Event Register', source.id)}>Open Scheduler</button>}</div></div>; })}</section>
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={!!editor || busy} onClick={() => setEditor({ sequence: Date.now(), job })}>Edit {job.name}</Button><Button size="sm" disabled={!job.enabled || busy || job.status === 'running'} onClick={() => void mutate(`jobs/action-jobs/${job.jobId}/run-now`, 'POST', { expectedRevision: job.configRevision })}>Run now</Button><Button size="sm" variant="ghost" disabled={busy || !!editor || job.status === 'running' || job.status === 'cleaning'} onClick={() => { if (window.confirm(`Delete ${job.name}? Managed Triggers will be removed. Shared Schedulers are kept.`)) void mutate(`jobs/action-jobs/${job.jobId}?expectedRevision=${job.configRevision}`, 'DELETE'); }}>Delete Job</Button></div>
      </article>;
    })}</div>
  </div>;
}

