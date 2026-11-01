import type { SchedulerConfig, SchedulerSource, TimeLink } from '@/types/automationJobs';
import { defaultSource, scheduleSummary } from '@/types/automationJobs';
import { Button } from '@/components/ui/Button';
import { fieldClass } from './fields';
import { useState } from 'react';

export function SchedulerFields({ value, onChange }: { value: SchedulerConfig; onChange: (value: SchedulerConfig) => void }) {
  const schedule = value.schedule;
  return <div className="grid min-w-0 gap-3 sm:grid-cols-2">
    <label className="block text-sm sm:col-span-2">Scheduler name<input required maxLength={200} className={fieldClass} value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} /></label>
    <label className="block text-sm">Time rule<select className={fieldClass} value={schedule.kind} onChange={(e) => { const kind = e.target.value as typeof schedule.kind; onChange({ ...value, schedule: { kind, timezone: schedule.timezone, ...(kind === 'interval' ? { intervalSec: 3600 } : kind === 'cron' ? { cron: '0 9 * * *' } : { at: '' }) } }); }}><option value="interval">Interval</option><option value="cron">Cron</option><option value="once">Once</option></select></label>
    {schedule.kind === 'interval' ? <label className="block text-sm">Interval seconds<input required type="number" min={1} step={1} className={fieldClass} value={schedule.intervalSec ?? schedule.interval_sec ?? 3600} onChange={(e) => onChange({ ...value, schedule: { ...schedule, intervalSec: Number(e.target.value) } })} /></label> : schedule.kind === 'cron' ? <label className="block text-sm">Cron expression<input required className={fieldClass} value={schedule.cron ?? ''} onChange={(e) => onChange({ ...value, schedule: { ...schedule, cron: e.target.value } })} /></label> : <label className="block text-sm">Scheduled time<input required type="datetime-local" className={fieldClass} value={schedule.at?.slice(0, 16) ?? ''} onChange={(e) => onChange({ ...value, schedule: { ...schedule, at: e.target.value } })} /></label>}
    <label className="block text-sm">Timezone<input className={fieldClass} placeholder="Asia/Shanghai" value={schedule.timezone ?? ''} onChange={(e) => onChange({ ...value, schedule: { ...schedule, timezone: e.target.value } })} /></label>
    <label className="block text-sm">Missed trigger policy<select className={fieldClass} value={value.misfirePolicy} onChange={(e) => onChange({ ...value, misfirePolicy: e.target.value as typeof value.misfirePolicy })}><option value="fire_now">Fire once after downtime</option><option value="skip">Skip missed times</option></select></label>
    <label className="block text-sm">Grace period (seconds)<input required type="number" min={0} max={86400} className={fieldClass} value={value.graceSec} onChange={(e) => onChange({ ...value, graceSec: Number(e.target.value) })} /></label>
    <p className="self-end text-xs leading-relaxed text-text-secondary">Cron uses the selected timezone. Once times use host local time and expire beyond the grace period. Missed-time policy belongs to this Scheduler.</p>
  </div>;
}

export function TimeTriggerEditor({ value, sources, onChange, onRemove, index }: { value: TimeLink; sources: SchedulerSource[]; onChange: (value: TimeLink) => void; onRemove: () => void; index: number }) {
  const [search, setSearch] = useState('');
  const selected = value.mode === 'existing' ? sources.find((s) => s.id === value.sourceId) : undefined;
  const results = sources.filter((s) => s.id === selected?.id || `${s.name} ${scheduleSummary(s)}`.toLowerCase().includes(search.toLowerCase()));
  return <section aria-label={`Time trigger ${index + 1}`} className="min-w-0 space-y-3 rounded-xl border border-border-default bg-bg-primary p-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">Time trigger {index + 1}</h4><Button type="button" size="sm" onClick={onRemove}>Remove time trigger</Button></div>
    <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label={`Scheduler path ${index + 1}`}>
      <Button type="button" aria-pressed={value.mode === 'existing'} variant={value.mode === 'existing' ? 'primary' : 'secondary'} onClick={() => onChange({ linkId: value.linkId, mode: 'existing', sourceId: '', sourceRevision: 0 })}>Existing Scheduler Event</Button>
      <Button type="button" aria-pressed={value.mode === 'new'} variant={value.mode === 'new' ? 'primary' : 'secondary'} onClick={() => onChange({ linkId: value.linkId, mode: 'new', config: defaultSource() })}>New Scheduler</Button>
    </div>
    {value.mode === 'new' ? <><SchedulerFields value={value.config} onChange={(config) => onChange({ ...value, config })} /><p className="text-xs text-text-secondary">A separate, reusable Scheduler will appear in Event Register. Saving a draft keeps it disabled; saving and enabling activates it after setup.</p></> : <>
      <label className="block text-sm">Search Schedulers<input type="search" className={fieldClass} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, rule or timezone" /></label>
      <label className="block text-sm">Scheduler Event<select required className={fieldClass} value={value.sourceId} onChange={(e) => { const source = sources.find((s) => s.id === e.target.value); onChange({ ...value, sourceId: e.target.value, sourceRevision: source?.revision ?? 0 }); }}><option value="">Choose an existing source</option>{value.sourceId && !selected && <option value={value.sourceId}>{value.sourceId} · Missing source</option>}{results.map((source) => <option key={source.id} value={source.id}>{source.name} · {source.enabled ? 'Enabled' : 'Disabled'} · {scheduleSummary(source)}</option>)}</select></label>
      {selected && <div className="space-y-1 rounded-lg border border-border-default p-3 text-xs leading-relaxed"><p className="font-medium">{selected.name}</p><p>{scheduleSummary(selected)}</p><p>Revision {selected.revision} · {selected.enabled ? 'Enabled' : 'Disabled'}</p>{!selected.enabled && <p className="text-warning">This source is disabled. The Job receives no new time events until it is enabled in Event Register.</p>}</div>}
    </>}
  </section>;
}
