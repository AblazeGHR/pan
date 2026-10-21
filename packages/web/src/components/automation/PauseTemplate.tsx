import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { createAutomationAction } from '@/stores/automationStore';
import { useSessionOperationTemplateStore } from '@/stores/sessionOperationTemplateStore';
import { SessionOperationFields } from '@/components/actions/SessionOperationFields';
import { sessionOperationError } from '@/components/actions/actionsModel';
import type { Action, ActionRegistry } from '@/types/actions';
import { cardClass, fieldClass } from './fields';

export function PauseTemplate({ onClose }: { onClose: () => void }) {
  const { data, error, load } = useSessionOperationTemplateStore();
  const [draft, setDraft] = useState<Omit<Action, 'id'> | null>(null);
  const [raw, setRaw] = useState<{ text: string; error: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const mounted = useRef(true); const pending = useRef(false);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);
  const action = draft ?? data?.item;
  return <section aria-label="Session Operation template" className={cardClass}>
    <div className="mb-4 flex flex-wrap justify-between gap-2"><h2 className="text-lg font-semibold">Pause Everything template</h2><Button disabled={busy} onClick={onClose}>Close template</Button></div>
    <p className="mb-4 text-sm text-text-secondary">Create a disabled ordinary Action from the server template. Review and enable it in Actions before a user-enabled Trigger can invoke it. Creation never executes Pause Everything.</p>
    {message && <p role="status" className="mb-3 break-all text-sm">{message}</p>}
    {error && <p role="alert">{error} <Button onClick={() => void load()}>Retry template</Button></p>}
    {!action && !error && <p role="status">Loading Session Operation template…</p>}
    {action && <form onSubmit={(e) => {
      e.preventDefault(); if (pending.current || raw?.error) return;
      const validation = sessionOperationError({ ...action, id: '' }); if (validation) { setMessage(validation); return; }
      pending.current = true; setBusy(true); setMessage('');
      void createAutomationAction<ActionRegistry & { item: { id: string } }>({ ...action, enabled: false }).then((result) => {
        if (mounted.current) setMessage(`Disabled Action created: ${result.item.id}. Review and enable it in Actions. No effect was executed.`);
      }).catch((error: unknown) => { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Creation failed. No automatic retry.'); })
        .finally(() => { pending.current = false; if (mounted.current) setBusy(false); });
    }}><fieldset disabled={busy} className="min-w-0 space-y-4">
      <label className="block text-sm">Action name<input required className={fieldClass} value={action.name} onChange={(e) => setDraft({ ...action, name: e.target.value })} /></label>
      {action.execution.kind === 'session_operation' && <SessionOperationFields value={action.execution} rawText={raw?.text} rawError={raw?.error}
        onChange={(execution) => { setDraft({ ...action, execution }); setRaw(null); }}
        onRawChange={(text, error) => { setRaw({ text, error }); if (!error && action.execution.kind === 'session_operation') setDraft({ ...action, execution: { ...action.execution, selector: { mode: 'query', query: JSON.parse(text) } } }); }} />}
      <Button type="submit" variant="primary" disabled={!!raw?.error || !!sessionOperationError({ ...action, id: '' })}>Create Session Operation Action</Button>
    </fieldset></form>}
  </section>;
}
