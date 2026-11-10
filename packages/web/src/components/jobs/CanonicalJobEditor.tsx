import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { CanonicalDefinitionFields, canonicalFormErrors } from '@/components/automation/CanonicalDefinitionFields';
import { definitelyRejected, useOperationIdentity } from '@/components/actions/useOperationIdentity';
import { canonicalError, canonicalJobs, definitionOf } from '@/services/canonicalJobs';
import type { CanonicalObject, CanonicalReceipt, JsonObject } from '@/types/canonicalJobs';

const initialDefinition = (): JsonObject => ({ name: '', steps: [{ stepId: 'step1', actionRef: { id: '', revision: 1, digest: '' }, arguments: {}, completion: { contract: '' } }], retention: { policyRef: 'default' } });

/** Edit the complete canonical definition, preserving unknown supported fields. */
export function CanonicalJobEditor({ item, onSaved, onDirtyChange }: {
  item?: CanonicalObject; onSaved: (receipt: CanonicalReceipt) => void | Promise<void>; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [value, setValue] = useState<JsonObject>(() => item ? definitionOf('jobs', item) : initialDefinition());
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [json, setJson] = useState('');
  const [pending, setPending] = useOperationIdentity(`jobs-editor:${item?.id ?? 'new'}`);
  const lock = useRef(false);
  useEffect(() => { onDirtyChange?.(dirty || !!pending); }, [dirty, pending, onDirtyChange]);
  const update = (next: JsonObject) => { setValue(next); setDirty(true); };
  const settle = async (receipt: CanonicalReceipt) => {
    if (receipt.status === 'completed') { setPending(null); setDirty(false); await onSaved(receipt); }
    else if (receipt.status === 'rejected' || receipt.status === 'cancelled') {
      setPending(null); setError(receipt.error?.message ?? `Operation ${receipt.status}.`);
    } else setError(`Operation ${receipt.status}; query the original request before continuing.`);
  };
  const save = async () => {
    if (lock.current || pending) return;
    let definition = value;
    try {
      if (advanced) {
        const parsed: unknown = JSON.parse(json);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Definition must be an object.');
        definition = parsed as JsonObject;
      }
      if (!String(definition.name ?? '').trim()) throw new Error('Name is required.');
      const issues = canonicalFormErrors('jobs', definition);
      if (issues.length) throw new Error(issues.join(' '));
    } catch (e) { setError(canonicalError(e)); return; }
    const identity = { operation: item ? 'update' : 'create', requestKey: crypto.randomUUID() };
    setPending(identity); lock.current = true; setBusy(true); setError('');
    try { await settle(await canonicalJobs.write('jobs', item ?? null, definition, identity.requestKey)); }
    catch (e) { if (definitelyRejected(e)) setPending(null); setError(canonicalError(e)); }
    finally { lock.current = false; setBusy(false); }
  };
  const query = async () => {
    if (!pending || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await settle(await canonicalJobs.lookup(pending.operation, pending.requestKey)); }
    catch (e) { setError(canonicalError(e)); }
    finally { lock.current = false; setBusy(false); }
  };
  return <div className="space-y-4" aria-label="Action Job editor">
    <label className="block text-sm">Name<input aria-label="Name" className="mt-1 block w-full rounded border border-border-default bg-bg-primary p-2" value={String(value.name ?? '')} disabled={busy || !!pending || advanced} onChange={e => update({ ...value, name: e.target.value })} /></label>
    {!advanced && <fieldset disabled={busy || !!pending}><CanonicalDefinitionFields kind="jobs" value={value} onChange={update} /></fieldset>}
    <details open={advanced} onToggle={e => {
      const open = e.currentTarget.open;
      if (open && !advanced) setJson(JSON.stringify(value, null, 2));
      if (!open && advanced) {
        try { const next: unknown = JSON.parse(json); if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('Definition must be an object.'); update(next as JsonObject); }
        catch (failure) { setError(canonicalError(failure)); e.currentTarget.open = true; return; }
      }
      setAdvanced(open);
    }}><summary>Advanced definition JSON</summary><textarea aria-label="Advanced Job definition" className="min-h-48 w-full rounded border border-border-default bg-bg-primary p-2 font-mono" value={json} disabled={busy || !!pending} onChange={e => { setJson(e.target.value); setDirty(true); }} /></details>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {pending ? <div role="status">Original request {pending.requestKey} <Button disabled={busy} onClick={() => void query()}>Query original operation</Button></div> : <Button variant="primary" disabled={busy} onClick={() => void save()}>{item ? 'Save Action Job' : 'Create Action Job'}</Button>}
  </div>;
}
