import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/jobs/ScheduleListEditor';
import type { UnifiedJobConfig, UnifiedTriggerKind, OverlapMode } from '@/types/jobs';
import type { Action } from '@/types/actions';
import { ApiRequestError } from '@/services/api';

const DEFAULT_CONFIG: UnifiedJobConfig = {
  name: '', description: '', actionId: '', arguments: {}, schedule: [{ kind: 'interval', intervalSec: 3600 }],
  trigger: { kind: 'schedule' }, recovery: { enabled: false, monitoring: false, maxAttempts: 3, backoffSec: [5, 15, 60], stableResetSec: 300 },
  overlap: { mode: 'forbid', parallelLimit: 3 }, notification: { on: ['completed', 'failed', 'cancelled', 'unknown', 'uncertain', 'cleaning'], target: 'job' },
  completion: { kind: 'ordinary' }, enabled: false, maxRuns: null, misfirePolicy: 'fire_now',
};

/** Unified editor: Action reference is the primary input; legacy kind is absent. */
export function UnifiedJobForm({ initial, actions = [], submitting = false, errorMessage, expectedRevision, onSubmit, onDirtyChange, submitLabel = 'Save unified Job' }: { initial?: Partial<UnifiedJobConfig>; actions?: Action[]; submitting?: boolean; errorMessage?: string; expectedRevision?: number; onSubmit: (config: UnifiedJobConfig) => Promise<void> | void; onDirtyChange?: (dirty: boolean) => void; submitLabel?: string }) {
  const [config, setConfig] = useState<UnifiedJobConfig>(() => ({ ...DEFAULT_CONFIG, ...initial,
    trigger: { kind: initial?.trigger?.kind ?? 'schedule', query: initial?.trigger?.query, eventTypes: initial?.trigger?.eventTypes },
    recovery: { enabled: initial?.recovery?.enabled ?? false, monitoring: initial?.recovery?.monitoring ?? false, maxAttempts: initial?.recovery?.maxAttempts ?? 3, backoffSec: initial?.recovery?.backoffSec ?? [5, 15, 60], stableResetSec: initial?.recovery?.stableResetSec ?? 300 },
    overlap: { mode: initial?.overlap?.mode ?? 'forbid', parallelLimit: initial?.overlap?.parallelLimit ?? 3 },
    notification: { on: initial?.notification?.on ?? DEFAULT_CONFIG.notification!.on, target: 'job', sessionIds: initial?.notification?.sessionIds },
    completion: { kind: initial?.completion?.kind ?? 'ordinary', signalType: initial?.completion?.signalType },
  }));
  const [args, setArgs] = useState(JSON.stringify(config.arguments, null, 2));
  const [schedule, setSchedule] = useState(JSON.stringify(config.schedule, null, 2));
  const [query, setQuery] = useState(JSON.stringify(config.trigger?.query ?? { field: 'adapter', op: 'eq', value: 'codex' }, null, 2));
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const patch = <K extends keyof UnifiedJobConfig>(key: K, value: UnifiedJobConfig[K]) => { setDirty(true); setConfig((old) => ({ ...old, [key]: value })); };
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const selectedAction = actions.find((action) => action.id === config.actionId);
  const submit = async () => {
    try {
      const parsed = JSON.parse(args);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Arguments must be a JSON object');
      if (!config.name.trim() || !config.actionId.trim()) throw new Error('Name and Action ID are required');
      if (actions.length && !selectedAction) throw new Error(`Incomplete: missing Action ${config.actionId} at /config/actionId`);
      if (selectedAction && Object.keys(parsed).some((key) => !(key in (selectedAction.input_schema.properties ?? {})))) throw new Error('Arguments contain undeclared Action fields');
      const specs: unknown = JSON.parse(schedule);
      if (!Array.isArray(specs) || (!specs.length && !['immediate','event'].includes(config.trigger?.kind ?? 'schedule'))) throw new Error('Complete schedule specs required');
      const triggerValue = config.trigger?.kind === 'event' ? { ...config.trigger, query: JSON.parse(query) } : config.trigger;
      await onSubmit({ ...config, name: config.name.trim(), actionId: config.actionId.trim(), arguments: parsed, schedule: specs, trigger: triggerValue }); setError(''); setDirty(false);
    } catch (e) {
      setError(e instanceof ApiRequestError && e.status === 401 ? '401: User confirmation required. Draft kept.' : e instanceof ApiRequestError && e.status === 409 ? '409: Revision changed. Reload before confirming. Draft kept.' : e instanceof Error ? e.message : 'Invalid unified Job configuration');
    }
  };
  const trigger = config.trigger?.kind ?? 'schedule';
  return <div className="flex flex-col gap-3" data-testid="unified-job-form">
    <div className="rounded border border-accent/40 bg-accent/5 p-2 text-xs">Unified Job · Action reference + Trigger + policies</div>
    <Field label="Name"><input className={inputClass} value={config.name} onChange={(e) => patch('name', e.target.value)} /></Field>
    <Field label="Description"><textarea className={inputClass} value={config.description ?? ''} onChange={(e) => patch('description', e.target.value)} /></Field>
    <Field label="Action reference">{actions.length > 0 ? <select aria-label="Action reference" className={inputClass} value={config.actionId} onChange={(e) => patch('actionId', e.target.value)}><option value="">Select an Action…</option>{config.actionId && !selectedAction && <option value={config.actionId}>Missing Action · {config.actionId}</option>}{actions.map((action) => <option key={action.id} value={action.id}>{action.name} · {action.id}{action.system ? ' · system' : ''}</option>)}</select> : <input aria-label="Action reference" className={inputClass} value={config.actionId} placeholder="Action ID or system Action ID" onChange={(e) => patch('actionId', e.target.value)} />}</Field>
    {actions.length > 0 && config.actionId && !selectedAction && <p role="alert" className="text-xs text-danger">Incomplete: missing Action {config.actionId} at /config/actionId. Choose a replacement; repair stays disabled.</p>}
    {selectedAction && <p className="text-xs text-text-secondary break-words">Declared fields: {Object.entries(selectedAction.input_schema.properties ?? {}).map(([key, spec]) => `${key}: ${spec.type}`).join(', ') || 'none; only {} accepted'}</p>}
    <Field label="Declared arguments (JSON)"><textarea aria-label="Declared arguments (JSON)" className={`${inputClass} min-h-24 font-mono`} value={args} onChange={(e) => { setArgs(e.target.value); setDirty(true); }} /></Field>
    <Field label="Trigger"><select aria-label="Trigger" className={inputClass} value={trigger} onChange={(e) => patch('trigger', { kind: e.target.value as UnifiedTriggerKind })}><option value="immediate">Immediate</option><option value="once">Once</option><option value="interval">Interval</option><option value="cron">Cron</option><option value="event">Event query</option><option value="schedule">Existing schedule</option></select></Field>
    <Field label="Complete schedule specs (JSON)"><textarea aria-label="Complete schedule specs (JSON)" className={`${inputClass} min-h-24 font-mono`} value={schedule} onChange={(e) => { setSchedule(e.target.value); setDirty(true); }} /></Field>
    {trigger === 'event' && <Field label="Event query (JSON)"><textarea aria-label="Event query (JSON)" className={`${inputClass} min-h-20 font-mono`} value={query} onChange={(e) => { setQuery(e.target.value); setDirty(true); }} /></Field>}
    {trigger === 'event' && <Field label="Event types (comma-separated)"><input className={inputClass} value={config.trigger?.eventTypes?.join(',') ?? ''} onChange={(e) => patch('trigger', { ...config.trigger!, eventTypes: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })} /></Field>}
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2"><Field label="Completion"><select aria-label="Completion" className={inputClass} value={config.completion?.kind} onChange={(e) => { const monitoring = e.target.value === 'monitoring'; patch('completion', { ...config.completion!, kind: monitoring ? 'monitoring' : 'ordinary' }); patch('recovery', { ...config.recovery!, monitoring, enabled: monitoring && config.recovery!.enabled }); }}><option value="ordinary">Ordinary result</option><option value="monitoring">Monitoring signal</option></select></Field><Field label="Signal type"><input className={inputClass} value={config.completion?.signalType ?? ''} onChange={(e) => patch('completion', { ...config.completion!, signalType: e.target.value || undefined })} /></Field></div>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2"><Field label="Recovery"><select aria-label="Recovery" className={inputClass} disabled={config.completion?.kind !== 'monitoring'} value={config.recovery?.enabled ? 'on' : 'off'} onChange={(e) => patch('recovery', { ...config.recovery!, enabled: e.target.value === 'on' })}><option value="off">Off</option><option value="on">On</option></select></Field><Field label="Overlap"><select aria-label="Overlap" className={inputClass} value={config.overlap?.mode} onChange={(e) => patch('overlap', { ...config.overlap!, mode: e.target.value as OverlapMode })}><option value="forbid">No duplicate</option><option value="queue">Queue</option><option value="parallel">Parallel</option></select></Field></div>
    {config.completion?.kind === 'monitoring' && <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      <Field label="Maximum attempts"><input className={inputClass} type="number" min={1} max={10} value={config.recovery!.maxAttempts} onChange={(e) => patch('recovery', { ...config.recovery!, maxAttempts: Number(e.target.value) })} /></Field>
      <Field label="Stable running reset (seconds)"><input className={inputClass} type="number" min={0} value={config.recovery!.stableResetSec} onChange={(e) => patch('recovery', { ...config.recovery!, stableResetSec: Number(e.target.value) })} /></Field>
      {config.recovery!.backoffSec.map((seconds, index) => <Field key={index} label={`Backoff ${index + 1} (seconds)`}><input className={inputClass} type="number" min={0} value={seconds} onChange={(e) => { const backoffSec: [number, number, number] = [...config.recovery!.backoffSec]; backoffSec[index] = Number(e.target.value); patch('recovery', { ...config.recovery!, backoffSec }); }} /></Field>)}
    </div>}
    {config.overlap?.mode === 'parallel' && <Field label="Parallel limit (1–32)"><input className={inputClass} type="number" min={1} max={32} value={config.overlap.parallelLimit} onChange={(e) => patch('overlap', { ...config.overlap!, parallelLimit: Number(e.target.value) })} /></Field>}
    <Field label="Notification statuses"><input className={inputClass} value={config.notification?.on.join(',') ?? ''} onChange={(e) => patch('notification', { ...config.notification!, target: 'job', on: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) as NonNullable<UnifiedJobConfig['notification']>['on'] })} /></Field>
    <Field label="Notification Session IDs (separate from Action targets)"><input className={inputClass} value={config.notification?.sessionIds?.join(',') ?? ''} onChange={(e) => patch('notification', { ...config.notification!, sessionIds: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })} /></Field>
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={config.enabled} onChange={(e) => patch('enabled', e.target.checked)} /> Enable after user confirmation</label>
    <div className="flex items-center justify-between text-[11px] text-text-tertiary"><span>{dirty ? 'Unsaved changes' : 'No unsaved changes'}{expectedRevision != null ? ` · revision ${expectedRevision}` : ''}</span>{errorMessage && <span className="text-danger">{errorMessage}</span>}</div>
    {error && <div className="text-xs text-danger">{error}</div>}
    <Button type="button" variant="primary" disabled={submitting} onClick={submit}>{submitting ? 'Saving…' : submitLabel}</Button>
  </div>;
}
