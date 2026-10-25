import { useEffect, useRef, useState, type RefObject } from 'react';
import { Button } from '@/components/ui/Button';
import { automationMutation, useAutomationAuth } from '@/stores/automationStore';
import { comparison, triggerConfig, type AutomationData, type Trigger, type TriggerConfig } from '@/types/automation';
import { ArgumentsEditor, cardClass, ConditionEditor, fieldClass, JsonDetail } from './fields';
import type { ActionJob, SchedulerSource } from '@/types/automationJobs';
import { ChainEditor, chainErrors } from './ChainEditor';
import { useAutomationDraft } from './DraftBoundary';

export function TriggerEditor({ initial, data, onClose, leaveGuard, onBusyChange, jobs = [], sources = [] }: { initial: Trigger | null; data: AutomationData; onClose: () => void; leaveGuard: RefObject<(() => boolean) | null>; onBusyChange: (busy: boolean) => void; jobs?: ActionJob[]; sources?: SchedulerSource[] }) {
  const [draft, setDraft] = useState<TriggerConfig>(() => initial ? triggerConfig(initial) : { name: '', description: '', enabled: false, eventTypes: [], actionId: '', arguments: {}, executionMode: 'serial' });
  const [revision, setRevision] = useState(initial?.revision ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [review, setReview] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [inputEpoch, setInputEpoch] = useState(0);
  const busyRef = useRef(false);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const confirmLeave = () => !busyRef.current && (!dirtyRef.current || window.confirm('Discard unsaved Trigger changes?'));
  leaveGuard.current = confirmLeave;
  useAutomationDraft(dirty,busy);
  function setPending(value: boolean) { busyRef.current = value; setBusy(value); onBusyChange(value); }
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const token = useAutomationAuth((s) => s.token);
  const server = draft.id ? data.automation.triggers[draft.id] : undefined;
  const changed = !!draft.id && (!server || server.revision !== revision);
  const action = data.actions.actions.find((a) => a.id === draft.actionId);
  const eventTypes = [...new Set([...data.automation.definitions.map((d) => d.type), ...draft.eventTypes])];
  const update = (next: TriggerConfig) => { setDraft(next); setDirty(true); setReview(false); setSaved(false); };
  async function save(enabled = draft.enabled) {
    if (busyRef.current) return;
    if (draft.steps && chainErrors(draft.steps, data.actions.actions).length) { setError('Resolve Action chain errors before saving.'); return; }
    setPending(true); setError(''); setSaved(false);
    try {
      const { item } = await automationMutation<{ item: Trigger }>('automation/triggers', 'POST', { item: triggerConfig({ ...draft, enabled }), expectedRevision: revision });
      if (mounted.current) { setDraft(triggerConfig(item)); setRevision(item.revision); setInputEpoch((v) => v + 1); setDirty(false); setReview(false); setSaved(true); }
    } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : 'Save failed. Draft preserved.'); }
    finally { if (mounted.current) setPending(false); }
  }
  return <section aria-label="Trigger editor" className={cardClass}>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">{draft.id ? 'Edit Trigger' : 'New Trigger'}</h2><Button disabled={busy} onClick={() => { if (confirmLeave()) onClose(); }}>Close editor</Button></div>
    <p className="mb-4 break-all text-xs text-text-secondary">{draft.id ?? 'ID assigned on save'} · Revision {revision} · {draft.executionMode} · {draft.enabled ? 'Enabled' : 'Disabled draft'}</p>
    {initial && <p className="mb-3 break-all text-xs text-text-secondary">Created by {initial.createdBy.type}{initial.createdBy.id ? `: ${initial.createdBy.id}` : ''} · {initial.createdAt}</p>}
    {error && <p role="alert" className="mb-3 text-sm text-danger">{error}</p>}{saved && <p role="status" className="mb-3 text-sm">Trigger saved.</p>}
    {changed && <div role="alert" className="mb-4 space-y-2 text-sm"><p>Server configuration changed{server ? ` (revision ${server.revision})` : ' or this Trigger was deleted'}. Local draft is preserved.</p>
      <JsonDetail label="Compare server configuration" value={server ?? 'Deleted'} />
      {server && <><Button disabled={busy} onClick={() => { setDraft(triggerConfig(server)); setRevision(server.revision); setInputEpoch((v) => v + 1); setDirty(false); setReview(false); setError(''); }}>Discard draft and load server</Button>{' '}<Button disabled={busy} onClick={() => { setRevision(server.revision); setReview(false); setError(''); }}>Keep draft against latest revision</Button></>}
    </div>}
    <form onChangeCapture={() => { setDirty(true); setSaved(false); }} onSubmit={(e) => { e.preventDefault(); void save(); }}><fieldset disabled={busy} className="min-w-0 space-y-5">
      <label className="block text-sm">Trigger name<input required className={fieldClass} value={draft.name} onChange={(e) => update({ ...draft, name: e.target.value })} /></label>
      <label className="block text-sm">Description<textarea className={fieldClass} rows={2} value={draft.description ?? ''} onChange={(e) => update({ ...draft, description: e.target.value })} /></label>
      <div role="group" aria-label="Events (OR)" className="space-y-2"><h3 className="font-medium">Events (OR)</h3><p className="text-xs text-text-secondary">Any selected Event may activate this Trigger.</p>
        {!eventTypes.length && <p>No Event definitions available. Refresh when the runtime is ready.</p>}
        {eventTypes.map((type) => { const definition = data.automation.definitions.find((d) => d.type === type); return <div key={type} className="min-w-0"><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={draft.eventTypes.includes(type)} onChange={(e) => update({ ...draft, eventTypes: e.target.checked ? [...draft.eventTypes, type] : draft.eventTypes.filter((t) => t !== type) })} /><span className="min-w-0 break-all">{type} · {definition?.available ? 'Available' : 'Unavailable'}</span></label><JsonDetail label={`Schema: ${type}`} value={definition} /></div>; })}
      </div>
      <div className="space-y-2"><label className="text-sm"><input type="checkbox" checked={!!draft.condition} onChange={(e) => update({ ...draft, condition: e.target.checked ? comparison() : null })} /> Check State after Event</label><p className="text-xs text-text-secondary">Current core State: workers.runningCount only. Evaluated once on match; queued Runs keep that result.</p>{draft.condition && <ConditionEditor key={inputEpoch} value={draft.condition} onChange={(condition) => update({ ...draft, condition })} />}</div>
      {draft.eventTypes.includes('scheduler.time.v1') && <label className="block text-sm">Scheduler source filter<select className={fieldClass} value={String(draft.eventFilter?.['payload.scheduleId'] ?? '')} onChange={(e) => update({ ...draft, eventFilter: e.target.value ? { ...draft.eventFilter, 'payload.scheduleId': e.target.value } : Object.fromEntries(Object.entries(draft.eventFilter ?? {}).filter(([key]) => key !== 'payload.scheduleId')) })}><option value="">All Scheduler sources</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.enabled ? 'Enabled' : 'Disabled'}</option>)}</select></label>}
      <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm">Execution mode<select className={fieldClass} value={draft.executionMode} onChange={(e) => update({ ...draft, executionMode: e.target.value as 'serial' | 'parallel', maxConcurrency: e.target.value === 'serial' ? 1 : 3 })}><option value="serial">Serial</option><option value="parallel">Parallel</option></select></label>{draft.executionMode === 'parallel' && <label className="block text-sm">Maximum concurrent runs<input required type="number" min={1} max={32} className={fieldClass} value={draft.maxConcurrency ?? 3} onChange={(e) => update({ ...draft, maxConcurrency: Number(e.target.value) })} /></label>}</div>
      <label className="block text-sm">Execution target<select className={fieldClass} value={draft.targetJobId !== undefined ? 'job' : 'action'} onChange={(e) => update(e.target.value === 'job' ? { ...draft, targetJobId: '', steps: undefined } : { ...draft, targetJobId: undefined })}><option value="action">Action</option><option value="job">Job</option></select></label>
      {draft.targetJobId !== undefined && <label className="block text-sm">Target Job<select required className={fieldClass} value={draft.targetJobId} onChange={(e) => update({ ...draft, targetJobId: e.target.value, actionId: jobs.find((j) => j.jobId === e.target.value)?.config.actionId ?? draft.actionId })}><option value="">Choose a Job</option>{jobs.map((j) => <option key={j.jobId} value={j.jobId}>{j.name} · {j.enabled ? 'Enabled' : 'Disabled'}</option>)}</select></label>}
      <div hidden={draft.targetJobId !== undefined}>
      {draft.steps ? <ChainEditor value={draft.steps} actions={data.actions.actions} onChange={(steps) => update({ ...draft, steps, actionId: steps[0]?.actionId ?? '' })} /> : <label className="block text-sm">Action reference<select required={draft.targetJobId === undefined} className={fieldClass} value={draft.actionId} onChange={(e) => update({ ...draft, actionId: e.target.value })}><option value="">Choose an Action</option>{!action && draft.actionId && <option value={draft.actionId}>{draft.actionId} (missing)</option>}{data.actions.actions.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.enabled ? 'Enabled' : 'Disabled'} · {a.id}</option>)}</select></label>}
      </div>
      <p className="text-xs text-text-secondary">Actions are referenced directly. Changes affect later starts; no Group build is required. Parameters are kept when switching Action: review and remove incompatible fields.</p>
      <JsonDetail label="Referenced Action and input schema" value={action ?? 'Action missing'} />
      <h3 className="font-medium">{draft.targetJobId !== undefined ? 'Job input mapping' : 'Action parameters'}</h3><ArgumentsEditor key={inputEpoch} value={draft.arguments} names={Object.keys(action?.input_schema.properties ?? {})} onChange={(args) => update({ ...draft, arguments: args })} />
      {!token && <p className="text-sm text-text-secondary">Login is required to enable, disable, delete or edit an enabled Trigger. Disabled drafts can be saved before login.</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" variant="primary" disabled={changed || !draft.eventTypes.length || (!!draft.enabled && !token)}>Save {draft.enabled ? 'enabled configuration' : 'disabled draft'}</Button>
        {!draft.enabled && <Button type="button" disabled={!token || changed || !draft.name.trim() || !draft.eventTypes.length || !action?.enabled} onClick={(e) => { if (e.currentTarget.form?.reportValidity()) setReview(true); }}>Review and enable</Button>}
        {draft.enabled && <Button type="button" disabled={!token || changed} onClick={() => void save(false)}>Save and disable</Button>}
        {draft.id && <Button type="button" variant="danger" disabled={!token || draft.enabled || server?.enabled || changed} onClick={() => { if (busyRef.current || !window.confirm('Delete this disabled Trigger? Run history is retained.')) return; setPending(true); setError(''); void automationMutation(`automation/triggers/${encodeURIComponent(draft.id!)}?expectedRevision=${revision}`, 'DELETE').then(() => { if (mounted.current) { setPending(false); onClose(); } }).catch((e: unknown) => { if (mounted.current) setError(e instanceof Error ? e.message : 'Delete failed'); }).finally(() => { if (mounted.current) setPending(false); }); }}>Delete Trigger</Button>}
      </div>
      {review && <div role="region" aria-label="Enable review" className="min-w-0 space-y-3 rounded border border-accent p-3"><h3 className="font-semibold">Enable this automation?</h3><p className="break-all text-sm">{action?.name} · {draft.actionId}</p><p className="text-sm">Future matching Events can execute this Action without further approval. Disabling cancels queued Runs; started effects continue. Retry follows the declared idempotency and policy. Uncertain effects require reconciliation.</p><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify({ action, arguments: draft.arguments, eventTypes: draft.eventTypes, condition: draft.condition }, null, 2)}</pre><Button type="button" variant="primary" disabled={!token || changed || !action?.enabled} onClick={(e) => { if (e.currentTarget.form?.reportValidity()) void save(true); }}>Confirm enable</Button>{' '}<Button type="button" onClick={() => setReview(false)}>Cancel enable</Button></div>}
    </fieldset></form>
  </section>;
}
