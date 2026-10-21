import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useActionSessionsStore } from '@/stores/actionRegistryStore';
import type { Action } from '@/types/actions';
import { Field, inputClass, Select } from './ActionsUi';

type Operation = Extract<Action['execution'], { kind: 'session_operation' }>;

/** Uses the shared lazy Session cache. Selected missing IDs remain editable. */
export function SessionOperationFields({ value, onChange }: { value: Operation; onChange: (next: Operation) => void }) {
  const { sessions, status, error, refreshing } = useActionSessionsStore();
  const [id, setId] = useState('');
  useEffect(() => { void useActionSessionsStore.getState().ensureSessions(); }, []);
  const selector = value.selector;
  const ids = selector.mode === 'explicit' ? selector.ids : [];
  const choices = [...sessions.map((s) => ({ id: s.id, name: s.name })), ...ids.filter((selected) => !sessions.some((s) => s.id === selected)).map((selected) => ({ id: selected, name: 'Not in current Session list' }))];
  function select(next: string[]) { onChange({ ...value, selector: { mode: 'explicit', ids: [...new Set(next)] } }); }
  return <div className="min-w-0 space-y-4">
    <p className="text-xs text-text-secondary">Pause Everything uses the existing Session operation. Targets are fixed at execution start; creating or saving this Action does not execute it.</p>
    <Field label="Target source" htmlFor="operation-target-source">
      <Select id="operation-target-source" label="Target source" value={selector.mode} onChange={(mode) => onChange({ ...value, selector: mode === 'event' ? { mode, path: 'payload.sessionIds' } : { mode: 'explicit', ids: [] } })}>
        <option value="explicit">Select Sessions</option><option value="event">Event field</option>
      </Select>
    </Field>
    {selector.mode === 'event' ? <Field label="Target Event path" htmlFor="operation-event-path" hint="Dictionary path to one Session ID or an array of IDs. No scripts.">
      <input id="operation-event-path" className={inputClass} value={selector.path} onChange={(e) => onChange({ ...value, selector: { mode: 'event', path: e.target.value } })} />
    </Field> : <>
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-text-secondary">{ids.length} selected · Empty selection matches zero targets.</p><Button type="button" size="sm" disabled={refreshing} onClick={() => void useActionSessionsStore.getState().refreshSessions()}>Refresh Sessions</Button></div>
      {error && <p role="alert" className="break-words text-xs text-danger">{error}. Cached Sessions and selected IDs are retained.</p>}
      {status === 'loading' && <p role="status" className="text-xs">Loading Sessions…</p>}
      <div role="group" aria-label="Target Sessions" className="max-h-64 space-y-2 overflow-y-auto rounded border border-border-default p-3">
        {!choices.length && <p className="text-xs text-text-tertiary">No Sessions in the cached list. Add an explicit ID or refresh.</p>}
        {choices.map((s) => <label key={s.id} className="flex min-w-0 items-start gap-2 text-xs"><input type="checkbox" checked={ids.includes(s.id)} onChange={(e) => select(e.target.checked ? [...ids, s.id] : ids.filter((selected) => selected !== s.id))} /><span className="min-w-0 break-all">{s.name} · {s.id}</span></label>)}
      </div>
      <Field label="Session ID to add" htmlFor="operation-new-id" hint="Explicit IDs are retained even if the Session is unavailable."><div className="flex min-w-0 flex-wrap gap-2"><input id="operation-new-id" className={`${inputClass} min-w-0 flex-1 basis-40`} value={id} onChange={(e) => setId(e.target.value)} /><Button type="button" size="sm" disabled={!id.trim() || ids.includes(id.trim())} onClick={() => { select([...ids, id.trim()]); setId(''); }}>Add Session ID</Button></div></Field>
    </>}
    <p className="text-xs text-text-tertiary">Query selection is not implemented. Its contract is reserved for a separate follow-up.</p>
  </div>;
}
