import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { automationRequest } from '@/stores/automationStore';
import type { AutomationSnapshot } from '@/types/automation';
import { defaultSource, newId, scheduleSummary, type ActionJob, type SchedulerConfig, type SchedulerSource } from '@/types/automationJobs';
import { cardClass, fieldClass, JsonDetail } from './fields';
import { SchedulerFields } from './SchedulerFields';
import { useAutomationDraft } from './DraftBoundary';

export function EventRegister({ sources, definitions, schema, jobs, focusId, refresh, jump }: { sources: SchedulerSource[]; definitions: AutomationSnapshot['definitions']; schema: unknown; jobs: ActionJob[]; focusId?: string; refresh: () => Promise<void>; jump: (tab: 'Job' | 'Trigger' | 'Event Register', id: string) => void }) {
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<{ key: string; source: SchedulerSource | null } | null>(null);
  return <div className="min-w-0 space-y-5"><header className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-lg font-semibold">Event Register</h2><p className="mt-1 text-sm text-text-secondary">Reusable event sources. Scheduler is the only source you can create.</p></div><Button disabled={!!editor} variant="primary" onClick={() => setEditor({ key: newId('source'), source: null })}>New Scheduler</Button></header>
    {editor && <SourceEditor key={editor.key} creationKey={editor.key} initial={editor.source} latest={sources.find((s) => s.id === editor.source?.id)} jobs={jobs} onClose={() => setEditor(null)} onSaved={refresh} />}
    <label className="block text-sm">Search event sources<input type="search" className={fieldClass} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name, rule or timezone" /></label>
    {!sources.length && <p className={cardClass}>No Scheduler sources yet. Create one here or while adding a time trigger to a Job.</p>}
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">{sources.filter((s) => `${s.name} ${scheduleSummary(s)}`.toLowerCase().includes(search.toLowerCase())).map((source) => <article key={source.id} id={source.id} aria-label={`Scheduler ${source.name}`} className={`${cardClass} space-y-3 ${focusId === source.id ? 'ring-2 ring-accent' : ''}`}>
      <header className="flex flex-wrap items-center justify-between gap-2"><h3 className="min-w-0 break-words font-semibold">{source.name}</h3><span className={`rounded-full px-2 py-1 text-xs ${source.enabled ? 'bg-accent/15 text-accent' : 'bg-bg-tertiary text-text-secondary'}`}>{source.enabled ? 'Enabled' : 'Disabled'}</span></header>
      <p className="break-words text-sm">{scheduleSummary(source)}</p><dl className="grid grid-cols-2 gap-2 text-xs"><div><dt className="text-text-secondary">Next fire (host local)</dt><dd className="mt-1 break-words">{source.nextFireAt?.replace('T', ' ') ?? 'No upcoming fire'}</dd></div><div><dt className="text-text-secondary">Revision</dt><dd className="mt-1">{source.revision}</dd></div></dl>
      <p className="break-all font-mono text-[11px] text-text-secondary">{source.id}</p>
      <div className="space-y-2 border-t border-border-default pt-3"><h4 className="text-xs font-semibold">{source.references?.length ?? 0} referencing subscribers</h4>{source.references?.map((ref) => <div key={ref.triggerId} className="flex min-w-0 flex-wrap items-center gap-2 text-xs"><button type="button" className="break-all text-left text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => jump('Trigger', ref.triggerId)}>{ref.name}</button>{ref.jobId && <button type="button" className="break-all text-left text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent" onClick={() => jump('Job', ref.jobId!)}>Job: {jobs.find((j) => j.jobId === ref.jobId)?.name ?? ref.jobId}</button>}<span className="text-text-secondary">{ref.enabled ? 'Enabled' : 'Disabled'}</span></div>)}</div>
      <div className="flex flex-wrap gap-2"><Button disabled={!!editor} size="sm" onClick={() => setEditor({ key: newId('source'), source })}>Edit {source.name}</Button><JsonDetail label={`Schema for ${source.name}`} value={schema} /></div>
    </article>)}</div>
    <section className="space-y-3 border-t border-border-default pt-5"><h3 className="font-semibold">System and plugin event types</h3><p className="text-xs text-text-secondary">Registered by their owning runtime. These definitions are read only here.</p><div className="grid min-w-0 gap-3 lg:grid-cols-2">{definitions.map((definition) => <article key={definition.type} className={`${cardClass} space-y-2`}><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="break-all text-sm font-medium">{definition.type}</h4><span className="text-xs text-text-secondary">{definition.available ? 'Available' : 'Unavailable'}</span></div><p className="text-xs text-text-secondary">Schema v{definition.schemaVersion} · Read only</p><JsonDetail label={`Schema ${definition.type}`} value={definition.schema} /></article>)}</div></section>
  </div>;
}

function SourceEditor({ initial, latest, creationKey, jobs, onSaved, onClose }: { initial: SchedulerSource | null; latest?: SchedulerSource; creationKey: string; jobs: ActionJob[]; onSaved: () => Promise<void>; onClose: () => void }) {
  const [basis, setBasis] = useState(initial);
  const [draft, setDraft] = useState<SchedulerConfig>(() => initial ? { name: initial.name, schedule: initial.schedule, enabled: initial.enabled, misfirePolicy: initial.misfirePolicy, graceSec: initial.graceSec } : defaultSource());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ack, setAck] = useState(false);
  const [dirty, setDirty] = useState(false);
  useAutomationDraft(dirty,busy);
  const references = basis?.references ?? [];
  const changed = !!initial && (!latest || latest.revision !== basis?.revision || latest.referencesVersion !== basis?.referencesVersion);
  async function save() {
    if (busy) return;
    setBusy(true); setError('');
    try { await automationRequest(`scheduler/event-sources${initial ? '/' + initial.id : ''}`, initial ? 'PUT' : 'POST', { config: draft, expectedRevision: basis?.revision ?? 0, ...(!initial ? { creationKey } : basis?.referencesVersion ? { expectedReferences: basis.referencesVersion } : {}) }); await onSaved(); onClose(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Save failed. Draft retained.'); await onSaved(); }
    finally { setBusy(false); }
  }
  return <section aria-label="Scheduler editor" className={`${cardClass} space-y-4`}><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{initial ? 'Edit Scheduler' : 'New Scheduler source'}</h3><Button disabled={busy} onClick={() => { if (!dirty || window.confirm('Discard Scheduler changes?')) onClose(); }}>Close Scheduler editor</Button></div>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {changed && <div role="alert" className="space-y-2 rounded-lg border border-border-default p-3 text-sm"><p>The source or its subscribers changed. Your draft is retained; compare before saving.</p><JsonDetail label="Compare server Scheduler" value={latest ?? 'Deleted source'} />{latest && <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => { setDraft({ name: latest.name, schedule: latest.schedule, enabled: latest.enabled, misfirePolicy: latest.misfirePolicy, graceSec: latest.graceSec }); setBasis(latest); setAck(false); setDirty(false); setError(''); }}>Load server Scheduler</Button><Button disabled={busy} onClick={() => { setBasis(latest); setAck(false); setDirty(true); setError(''); }}>Keep draft against latest source revision</Button></div>}</div>}
    <form onSubmit={(e) => { e.preventDefault(); void save(); }}><fieldset disabled={busy} className="min-w-0 space-y-4">
      <SchedulerFields value={draft} onChange={(value) => { setDraft(value); setDirty(true); }} />
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={(e) => { setDraft({ ...draft, enabled: e.target.checked }); setDirty(true); }} />Scheduler enabled</label>
      <section aria-label="Scheduler impact" className="min-w-0 space-y-2 rounded-lg border border-border-default p-3 text-sm"><h4 className="font-medium">{references.length ? `Changes affect ${references.length} subscribers` : 'No referencing subscribers'}</h4>{references.map((ref) => <p key={ref.triggerId} className="break-words text-xs">{ref.name}{ref.jobId ? ` · Job: ${jobs.find((j) => j.jobId === ref.jobId)?.name ?? ref.jobId}` : ''}</p>)}<p className="text-xs text-text-secondary">Disabling stops future time events. Already admitted runs continue. Deleting a Job keeps this shared source.</p>{references.length > 0 && <label className="flex items-start gap-2 text-xs"><input type="checkbox" required checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>I reviewed the affected subscribers.</span></label>}</section>
      <div className="flex flex-wrap gap-2"><Button type="submit" variant="primary" disabled={changed || references.length > 0 && !ack}>Save Scheduler</Button>{initial && <Button type="button" variant="danger" disabled={changed || basis?.enabled || references.length > 0} onClick={() => { if (!window.confirm('Delete this unreferenced Scheduler?')) return; setBusy(true); setError(''); void automationRequest(`scheduler/event-sources/${initial.id}?expectedRevision=${basis?.revision}`, 'DELETE').then(onSaved).then(onClose).catch((e: unknown) => setError(e instanceof Error ? e.message : 'Delete failed')).finally(() => setBusy(false)); }}>Delete Scheduler</Button>}</div>
      {initial && (initial.enabled || references.length > 0) && <p className="text-xs text-text-secondary">Disable this source and detach its references before deleting it.</p>}
    </fieldset></form>
  </section>;
}
