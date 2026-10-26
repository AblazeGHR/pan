import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { AutomationError, automationRequest } from '@/stores/automationStore';
import type { Action } from '@/types/actions';
import type { Trigger } from '@/types/automation';
import { defaultJob, newId, normalizeChainSteps, type ActionJob, type ChainStep, type JobConfig, type SchedulerSource, type TimeLink } from '@/types/automationJobs';
import { cardClass, fieldClass, LiteralInput } from './fields';
import { ChainEditor, chainErrors } from './ChainEditor';
import { TimeTriggerEditor } from './SchedulerFields';
import { useAutomationDraft } from './DraftBoundary';

export function normalizeSteps(config: JobConfig): ChainStep[] {
  const steps = config.steps ?? [{ stepId: newId('step'), actionId: config.actionId, arguments: Object.fromEntries(Object.keys(config.arguments).map((name) => [name, { from: 'input', path: name }])) }];
  return normalizeChainSteps(steps);
}

export function JobEditor({ initial, actions, sources, triggers, onClose, onSaved }: { initial: ActionJob | null; actions: Action[]; sources: SchedulerSource[]; triggers: Trigger[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState<JobConfig>(() => initial ? { ...initial.config, steps: normalizeSteps(initial.config) } : defaultJob());
  const [links, setLinks] = useState<TimeLink[]>(() => triggers.filter((t) => !!initial && t.ownerJobId === initial.jobId).map((t) => { const id = String(t.eventFilter?.['payload.scheduleId'] ?? ''); return { linkId: t.ownerLinkId ?? newId('link'), mode: 'existing', sourceId: id, sourceRevision: sources.find((s) => s.id === id)?.revision ?? 0 }; }));
  const [key] = useState(() => newId('edit'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [inputError, setInputError] = useState(false);
  const [frozenRequest, setFrozenRequest] = useState<unknown>(null);
  const [dirty, setDirty] = useState(false);
  const existingTiming = !!initial && initial.config.trigger.kind !== 'manual';
  const [noticeText, setNoticeText] = useState(initial?.config.notification.sessionIds?.join(', ') ?? '');
  useAutomationDraft(dirty || !!frozenRequest,busy);
  const update = (next: JobConfig) => { setDraft(next); setDirty(true); };
  const errors = chainErrors(draft.steps ?? [], actions);
  if (inputError) errors.push('Job input must be a JSON object.');
  if (draft.overlap.mode === 'parallel' && draft.recovery.monitoring) errors.push('Monitoring Jobs use forbid or queue overlap.');
  const monitoringUnsupported = draft.recovery.monitoring && (draft.steps ?? []).some((s) => !actions.find((a) => a.id === s.actionId)?.monitoring);
  if (monitoringUnsupported) errors.push('Every step must declare monitoring capability.');
  if (draft.recovery.enabled && (draft.steps ?? []).some((s) => !actions.find((a) => a.id === s.actionId)?.recoverable)) errors.push('Every step must declare recoverable capability.');
  async function save(enabled: boolean, retry = false) {
    if (busy) return;
    const request = retry ? frozenRequest : { key, ...(initial ? { jobId: initial.jobId, expectedRevision: initial.configRevision } : {}), config: { ...draft, enabled, actionId: draft.steps?.[0]?.actionId ?? '', trigger: existingTiming ? initial!.config.trigger : { kind: 'manual' }, schedule: existingTiming ? initial!.config.schedule : [] }, schedulers: links.map((link) => link.mode === 'new' ? { ...link, config: { ...link.config, enabled } } : link) };
    setBusy(true); setError(''); setFrozenRequest(request);
    try { await automationRequest('automation/jobs/compose', 'POST', request); setDirty(false); setFrozenRequest(null); await onSaved(); onClose(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Setup failed. Your disabled draft is preserved.'); await onSaved(); }
    finally { setBusy(false); }
  }
  async function closePending() {
    if (busy) return;
    if (frozenRequest) { setBusy(true); try { await automationRequest(`automation/job-edits/${key}/cancel`, 'POST'); } catch (e) { if (!(e instanceof AutomationError) || e.status !== 404) { setError(e instanceof Error ? e.message : 'Could not close setup'); setBusy(false); return; } } setBusy(false); await onSaved(); }
    else if (dirty && !window.confirm('Discard unsaved Job changes?')) return;
    onClose();
  }
  return <section aria-label="Job editor" className={`${cardClass} space-y-5`}><header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{initial ? 'Edit Job' : 'New Job'}</h2><p className="mt-1 text-xs text-text-secondary">{initial ? `Revision ${initial.configRevision}` : 'Configure a chain and optional time triggers'}</p></div><Button type="button" disabled={busy} onClick={() => void closePending()}>{frozenRequest ? 'Keep disabled draft and close' : 'Close editor'}</Button></header>
    {error && <div role="alert" className="space-y-2 rounded-lg border border-danger p-3 text-sm text-danger"><p>{error}</p><p className="text-xs">Setup is retained under the same operation. Retry resumes it; closing keeps any created drafts disabled.</p><Button disabled={busy} onClick={() => void save(draft.enabled, true)}>Retry setup</Button></div>}
    <form onSubmit={(e) => { e.preventDefault(); void save(false); }} onChangeCapture={() => setDirty(true)}><fieldset disabled={busy || !!frozenRequest} className="min-w-0 space-y-5">
      <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm">Job name<input required maxLength={200} className={fieldClass} value={draft.name} onChange={(e) => update({ ...draft, name: e.target.value })} /></label><label className="block text-sm">Description<input className={fieldClass} value={draft.description} onChange={(e) => update({ ...draft, description: e.target.value })} /></label></div>
      <ChainEditor value={draft.steps ?? []} actions={actions} onChange={(steps) => update({ ...draft, steps, actionId: steps[0]?.actionId ?? '' })} />
      <details className="min-w-0 rounded-lg border border-border-default p-3"><summary className="cursor-pointer text-sm font-medium">Job input</summary><div className="mt-3"><LiteralInput label="Job input (JSON object)" value={draft.arguments} onChange={(value) => { const valid = value !== null && typeof value === 'object' && !Array.isArray(value); setInputError(!valid); if (valid) update({ ...draft, arguments: value as Record<string, unknown> }); }} /></div></details>
      <section className="space-y-3" aria-label="Time triggers"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold">Time triggers</h3><p className="mt-1 text-xs text-text-secondary">Choose a shared source or create a reusable Scheduler.</p></div><Button type="button" disabled={existingTiming || links.length >= 8} onClick={() => { setLinks([...links, { linkId: newId('link'), mode: 'existing', sourceId: '', sourceRevision: 0 }]); setDirty(true); }}>Add time trigger</Button></div>{existingTiming ? <p className="rounded-lg border border-border-default p-3 text-sm text-text-secondary">Existing {initial!.config.trigger.kind} trigger. Timing is kept when saving this editor. <Link className="text-accent hover:underline" to="/automation/canonical">Open Canonical Job controls</Link> to manage it.</p> : !links.length && <p className="rounded-lg border border-dashed border-border-default p-3 text-sm text-text-secondary">No time triggers. This Job can be run manually.</p>}{links.map((link, index) => <TimeTriggerEditor key={link.linkId} index={index} value={link} sources={sources} onChange={(next) => { setLinks(links.map((l, i) => i === index ? next : l)); setDirty(true); }} onRemove={() => { setLinks(links.filter((_, i) => i !== index)); setDirty(true); }} />)}</section>
      <details className="min-w-0 rounded-lg border border-border-default p-3"><summary className="cursor-pointer text-sm font-medium">Overlap, recovery and notifications</summary><div className="mt-4 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm">Overlap<select className={fieldClass} value={draft.overlap.mode} onChange={(e) => update({ ...draft, overlap: { ...draft.overlap, mode: e.target.value as JobConfig['overlap']['mode'] } })}><option value="forbid">Forbid overlapping runs</option><option value="queue">Queue later runs</option><option value="parallel">Parallel runs</option></select></label>{draft.overlap.mode === 'parallel' && <label className="block text-sm">Parallel limit<input required type="number" min={1} max={32} className={fieldClass} value={draft.overlap.parallelLimit} onChange={(e) => update({ ...draft, overlap: { ...draft.overlap, parallelLimit: Number(e.target.value) } })} /></label>}</div>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={draft.recovery.monitoring} onChange={(e) => update({ ...draft, recovery: { ...draft.recovery, monitoring: e.target.checked, enabled: false }, completion: e.target.checked ? { kind: 'monitoring', signalType: 'done' } : { kind: 'ordinary' } })} /><span>Require a monitoring completion signal</span></label>
        {draft.recovery.monitoring && <div className="space-y-3"><label className="block text-sm">Completion signal type<input required className={fieldClass} value={draft.completion.signalType ?? ''} onChange={(e) => update({ ...draft, completion: { kind: 'monitoring', signalType: e.target.value } })} /></label><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={draft.recovery.enabled} onChange={(e) => update({ ...draft, recovery: { ...draft.recovery, enabled: e.target.checked } })} /><span>Allow bounded recovery of the current step</span></label>{draft.recovery.enabled && <label className="block text-sm">Maximum attempts<input required type="number" min={1} max={10} className={fieldClass} value={draft.recovery.maxAttempts} onChange={(e) => update({ ...draft, recovery: { ...draft.recovery, maxAttempts: Number(e.target.value) } })} /></label>}</div>}
        <p className="text-xs text-text-secondary">Completed steps are not rerun. Unknown effects require reconciliation. Monitoring recovery uses the configured backoff and stable reset.</p>
        {draft.recovery.enabled && <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{draft.recovery.backoffSec.map((seconds, i) => <label key={i} className="block text-sm">Backoff {i + 1} (sec)<input required type="number" min={0} max={86400} className={fieldClass} value={seconds} onChange={(e) => update({ ...draft, recovery: { ...draft.recovery, backoffSec: draft.recovery.backoffSec.map((s, j) => i === j ? Number(e.target.value) : s) } })} /></label>)}<label className="block text-sm">Stable reset (sec)<input required type="number" min={0} max={86400} className={fieldClass} value={draft.recovery.stableResetSec} onChange={(e) => update({ ...draft, recovery: { ...draft.recovery, stableResetSec: Number(e.target.value) } })} /></label></div>}
        <fieldset><legend className="text-sm font-medium">Notify on</legend><div className="mt-2 flex flex-wrap gap-3">{['completed', 'failed', 'cancelled', 'uncertain', 'cleaning'].map((status) => <label key={status} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.notification.on.includes(status)} onChange={(e) => update({ ...draft, notification: { ...draft.notification, on: e.target.checked ? [...draft.notification.on, status] : draft.notification.on.filter((s) => s !== status) } })} />{status}</label>)}</div></fieldset>
        <label className="block text-sm">Notification Session IDs (comma separated)<input className={fieldClass} value={noticeText} onChange={(e) => { setNoticeText(e.target.value); update({ ...draft, notification: { ...draft.notification, sessionIds: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) } }); }} /></label>
      </div></details>
      {errors.length > 0 && <p role="status" className="text-sm text-text-secondary">Resolve the chain and settings errors before saving.</p>}
      <footer className="flex flex-wrap items-center gap-2 border-t border-border-default pt-4"><Button type="submit" disabled={!!errors.length || !draft.name.trim()}>{initial?.enabled ? 'Save and disable' : 'Save draft'}</Button><Button type="button" variant="primary" disabled={!!errors.length || !draft.name.trim()} onClick={(e) => { if (e.currentTarget.form?.reportValidity()) void save(true); }}>Save and enable</Button><p className="basis-full text-xs text-text-secondary">New sources stay disabled until setup is complete. Shared sources keep their current enabled state.</p></footer>
    </fieldset></form>
  </section>;
}

