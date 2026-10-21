import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { createAutomationAction } from '@/stores/automationStore';
import type { ActionRegistry } from '@/types/actions';
import type { AutomationData } from '@/types/automation';
import { cardClass, fieldClass } from './fields';

export function PauseTemplate({ data, onClose }: { data: AutomationData; onClose: () => void }) {
  const [name, setName] = useState('Pause selected Sessions');
  const [mode, setMode] = useState<'explicit' | 'event'>('explicit');
  const [ids, setIds] = useState<string[]>([]);
  const [path, setPath] = useState('payload.sessionIds');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  return <section aria-label="Session Operation template" className={cardClass}>
    <div className="mb-4 flex flex-wrap justify-between gap-2"><h2 className="text-lg font-semibold">Pause Everything template</h2><Button onClick={onClose}>Close template</Button></div>
    <p className="mb-4 text-sm text-text-secondary">Create a normal enabled Session Operation Action. It does not pause anything until an enabled Trigger invokes it. Targets are fixed at execution start and processed individually. The Trigger remains disabled by default.</p>
    {message && <p role="status" className="mb-3 break-all text-sm">{message}</p>}
    <form onSubmit={(e) => { e.preventDefault(); if (pending.current) return; pending.current = true; setBusy(true); setMessage(''); void createAutomationAction<ActionRegistry & { item: { id: string } }>({
      name, description: 'Pause Everything for selected Sessions through the existing Session operation.', enabled: true, effect: 'write_local', output_format: 'json',
      input_schema: { type: 'object', properties: {}, additionalProperties: false },
      execution: { kind: 'session_operation', operation: 'pause_everything', selector: mode === 'explicit' ? { mode, ids } : { mode, path } },
    }).then((result) => { if (mounted.current) setMessage(`Action created: ${result.item.id}. Choose it in a Trigger. Creating another Action requires another explicit submit.`); }).catch((error: unknown) => { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Creation failed. No automatic retry.'); }).finally(() => { pending.current = false; if (mounted.current) setBusy(false); }); }}>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <label className="block text-sm">Action name<input required className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block text-sm">Target source<select className={fieldClass} value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}><option value="explicit">Select Sessions</option><option value="event">Event field</option></select></label>
        {mode === 'explicit' ? <div role="group" aria-label="Target Sessions" className="max-h-72 space-y-2 overflow-y-auto">{!data.sessions.length && <p className="text-sm">No Sessions available. Refresh after creating a Session.</p>}{data.sessions.map((s) => <label key={s.id} className="flex items-start gap-2 text-sm"><input type="checkbox" checked={ids.includes(s.id)} onChange={(e) => setIds(e.target.checked ? [...ids, s.id] : ids.filter((id) => id !== s.id))} /><span className="min-w-0 break-all">{s.name} · {s.id}</span></label>)}</div> : <label className="block text-sm">Target Event path<input required className={fieldClass} pattern="[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*" value={path} onChange={(e) => setPath(e.target.value)} /><span className="text-xs text-text-secondary">The Event field must contain one Session ID or an array of IDs.</span></label>}
        <p className="text-sm text-text-secondary">Query selection is not implemented in v0.1. A field whitelist and query selector are required follow-up work.</p>
        <Button type="submit" variant="primary" disabled={mode === 'explicit' && ids.length === 0}>Create Session Operation Action</Button>
      </fieldset>
    </form>
  </section>;
}
