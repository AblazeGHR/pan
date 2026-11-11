import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { CanonicalDefinitionFields, canonicalFormErrors } from '@/components/automation/CanonicalDefinitionFields';
import { definitelyRejected, useOperationIdentity } from '@/components/actions/useOperationIdentity';
import { canonicalError, canonicalJobs, definitionOf } from '@/services/canonicalJobs';
import { ApiRequestError } from '@/services/api';
import { useAutomationDraft } from '@/components/automation/DraftBoundary';
import type { CanonicalObject, CanonicalReceipt, JsonObject } from '@/types/canonicalJobs';

const initialDefinition = (): JsonObject => ({ name: '', steps: [{ stepId: 'step1', actionRef: { id: '', revision: 1, digest: '' }, arguments: {}, completion: { contract: '' } }], retention: { policyRef: 'default' } });

/** Edit the complete canonical definition, preserving unknown supported fields. */
export function CanonicalJobEditor({ item, onSaved, onDirtyChange, onPendingChange }: {
  item?: CanonicalObject; onSaved: (receipt: CanonicalReceipt) => void | Promise<void>; onDirtyChange?: (dirty: boolean) => void; onPendingChange?: (pending: boolean) => void;
}) {
  const [value, setValue] = useState<JsonObject>(() => item ? definitionOf('jobs', item) : initialDefinition());
  const [baseItem, setBaseItem] = useState(item);
  const [conflict, setConflict] = useState(false);
  const [serverItem, setServerItem] = useState<CanonicalObject | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [json, setJson] = useState('');
  const [pending, setPending] = useOperationIdentity(`jobs-editor:${item?.id ?? 'new'}`);
  const lock = useRef(false);
  useAutomationDraft(dirty, !!pending);
  useEffect(() => { onDirtyChange?.(dirty || !!pending); onPendingChange?.(!!pending); }, [dirty, pending, onDirtyChange, onPendingChange]);
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
      if (!String(definition.name ?? '').trim()) {
        if (baseItem) throw new Error('Name is required.');
        definition = { ...definition }; delete definition.name;
      }
      const issues = canonicalFormErrors('jobs', definition);
      if (issues.length) throw new Error(issues.join(' '));
    } catch (e) { setError(canonicalError(e)); return; }
    const identity = { operation: baseItem ? 'update' : 'create', requestKey: crypto.randomUUID() };
    setPending(identity); lock.current = true; setBusy(true); setError('');
    try { await settle(await canonicalJobs.write('jobs', baseItem ?? null, definition, identity.requestKey)); }
    catch (e) { if (definitelyRejected(e)) setPending(null); if (e instanceof ApiRequestError && e.status === 409 && baseItem) setConflict(true); setError(canonicalError(e)); }
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
    <label className="block text-sm">Name<input aria-label="Name" placeholder={baseItem ? undefined : "Blank uses server-generated job-N"} className="mt-1 block w-full rounded border border-border-default bg-bg-primary p-2" value={String(value.name ?? '')} disabled={busy || !!pending || advanced} onChange={e => update({ ...value, name: e.target.value })} /></label>
    {!advanced && <fieldset disabled={busy || !!pending}><CanonicalDefinitionFields kind="jobs" value={value} onChange={update} /></fieldset>}
    <details open={advanced}><summary onClick={e => {
      e.preventDefault();
      if (!advanced) { setJson(JSON.stringify(value, null, 2)); setAdvanced(true); return; }
      try { const next: unknown = JSON.parse(json); if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('Definition must be an object.'); update(next as JsonObject); setAdvanced(false); }
      catch (failure) { setError(canonicalError(failure)); }
    }}>Advanced definition JSON</summary><textarea aria-label="Advanced Job definition" className="min-h-48 w-full rounded border border-border-default bg-bg-primary p-2 font-mono" value={json} disabled={busy || !!pending} onChange={e => { setJson(e.target.value); setDirty(true); }} /></details>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {conflict && baseItem && <section aria-label="Compare canonical configuration" className="space-y-2 rounded border border-warning/50 p-3 text-sm">
      <p>The Job changed on the server. Your draft is preserved until you compare and choose what to keep.</p>
      <Button disabled={busy} onClick={() => void canonicalJobs.get('jobs', baseItem.id).then(setServerItem).catch(e => setError(canonicalError(e)))}>Compare server definition</Button>
      {serverItem && <><p>Server: {serverItem.name} · revision {serverItem.configRevision ?? serverItem.revision}</p>
        <details><summary>Server definition</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(definitionOf('jobs', serverItem), null, 2)}</pre></details>
        <Button disabled={busy} onClick={() => { setBaseItem(serverItem); setConflict(false); setServerItem(null); setError(''); }}>Keep draft against server revision</Button>
        <Button disabled={busy} onClick={() => { const latest = definitionOf('jobs', serverItem); setBaseItem(serverItem); setValue(latest); setJson(JSON.stringify(latest, null, 2)); setConflict(false); setServerItem(null); setDirty(false); setError(''); }}>Load server definition</Button></>}
    </section>}
    {pending ? <div role="status">Original request {pending.requestKey} <Button disabled={busy} onClick={() => void query()}>Query original operation</Button></div> : <Button variant="primary" disabled={busy || conflict || (!advanced && canonicalFormErrors('jobs', value).length > 0)} onClick={() => void save()}>{item ? 'Save Action Job' : 'Create Action Job'}</Button>}
  </div>;
}
