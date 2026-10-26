import { useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { ApiRequestError } from '@/services/api';
import { canonicalError, canonicalJobs, definitionOf } from '@/services/canonicalJobs';
import { newId, type CreatorReferences, type EventCreator, type SchedulerProvider } from '@/types/automationJobs';
import type { CanonicalReceipt, JsonObject } from '@/types/canonicalJobs';
import { cardClass, fieldClass, JsonDetail } from './fields';
import { useAutomationDraft } from './DraftBoundary';
import type { AutomationSnapshot } from '@/types/automation';

const emptyRefs: CreatorReferences = { references: [], referenceDigest: '' };
function receiptObject(receipt: CanonicalReceipt): EventCreator {
  if (receipt.status !== 'completed' || !receipt.result?.object) throw new Error('Operation not completed; query the original operation.');
  return receipt.result.object as EventCreator;
}
export function EventRegister({ sources, providers, references, definitions = [], focusId, refresh, jump }: {
  sources: EventCreator[]; providers: SchedulerProvider[]; references: Record<string, CreatorReferences>; focusId?: string;
  refresh: () => Promise<void>; jump: (tab: 'Job' | 'Trigger' | 'Event Register', id: string) => void;
  definitions?: AutomationSnapshot['definitions'];
}) {
  const [search, setSearch] = useState('');
  const [editor, setEditor] = useState<{ key: string; source: EventCreator | null } | null>(null);
  const timeProvider = providers.find((p) => p.providerRef.type === 'time' && p.providerRef.id === 'scheduler');
  return <div className="min-w-0 space-y-5"><header className="flex flex-wrap justify-between gap-3"><div><h2 className="text-lg font-semibold">Event Register</h2><p>Reusable canonical Event Creators. Each Scheduler keeps its own rules and cursor.</p></div><Button disabled={!!editor || !timeProvider} onClick={() => setEditor({ key: newId('creator'), source: null })}>New Scheduler</Button></header>
    {editor && <SourceEditor key={editor.key} initial={editor.source} latest={sources.find((s) => s.id === editor.source?.id)} provider={timeProvider} refs={editor.source ? references[editor.source.id] ?? emptyRefs : emptyRefs} refresh={refresh} close={() => setEditor(null)} />}
    <label className="block text-sm">Search event sources<input className={fieldClass} type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
    {!sources.length && <p>No Scheduler sources yet.</p>}
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">{sources.filter((s) => `${s.name} ${JSON.stringify(s.config)}`.toLowerCase().includes(search.toLowerCase())).map((source) => <article id={source.id} key={source.id} aria-label={`Scheduler ${source.name}`} className={`${cardClass} space-y-3 ${focusId === source.id ? 'ring-2 ring-accent' : ''}`}>
      <h3>{source.name}</h3><p>{source.state} · Revision {source.revision} · State revision {source.stateRevision}</p><p className="break-all text-xs">{source.id}</p>
      <JsonDetail label={`Time rules for ${source.name}`} value={source.config} />
      <h4>{references[source.id]?.references.length ?? 0} referencing subscribers</h4>{references[source.id]?.references.map((ref) => <div key={ref.triggerId} className="flex flex-wrap gap-2 text-sm"><button onClick={() => jump('Trigger', ref.triggerId)}>{ref.name}</button><button onClick={() => jump('Job', ref.jobId)}>{ref.jobName}</button><span>{ref.enabled ? 'Enabled' : 'Disabled'}</span></div>)}
      <Button disabled={!!editor} onClick={() => setEditor({ key: newId('edit'), source })}>Edit {source.name}</Button>
    </article>)}</div>
    <section><h3>System and plugin event types</h3>{definitions.map((definition) => <div key={definition.type}><p>{definition.type} · {definition.available ? 'Available' : 'Unavailable'}</p><JsonDetail label={`Schema ${definition.type}`} value={definition.schema} /></div>)}</section>
  </div>;
}
function SourceEditor({ initial, latest, provider, refs, refresh, close }: { initial: EventCreator | null; latest?: EventCreator; provider?: SchedulerProvider; refs: CreatorReferences; refresh: () => Promise<void>; close: () => void }) {
  const [basis, setBasis] = useState(initial), [referenceBasis, setReferenceBasis] = useState(refs);
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(String(initial?.description ?? ''));
  const [config, setConfig] = useState(() => JSON.stringify(initial?.config ?? { rules: [{ ruleId: 'interval', type: 'interval', seconds: '3600', anchor: new Date().toISOString(), advanceMode: 'grid' }], misfirePolicy: 'fire_now', graceSeconds: '300', pausePolicy: 'advance' }, null, 2));
  const [enabled, setEnabled] = useState(initial?.state === 'enabled');
  const [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false), [reviewed, setReviewed] = useState(false), [error, setError] = useState('');
  const [pending, setPending] = useState<{ operation: string; key: string; receipt?: CanonicalReceipt } | null>(null);
  const busyRef = useRef(false);
  useAutomationDraft(dirty, busy || !!pending);
  const changed = !!initial && (!latest || latest.revision !== basis?.revision || latest.stateRevision !== basis?.stateRevision || refs.referenceDigest !== referenceBasis.referenceDigest);
  async function operation(op: string, task: (key: string) => Promise<CanonicalReceipt>) {
    const key = newId(op); setPending({ operation: op, key });
    try {
      const receipt = await task(key);
      setPending(receipt.status === 'completed' ? null : { operation: op, key, receipt });
      return receiptObject(receipt);
    } catch (e) {
      if (e instanceof ApiRequestError && e.status < 500) setPending(null);
      throw e;
    }
  }
  async function save() {
    if (busyRef.current || pending) return;
    busyRef.current = true; setBusy(true); setError('');
    try {
      const parsed: unknown = JSON.parse(config);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Scheduler config must be a JSON object.');
      const definition: JsonObject = basis ? definitionOf('event-creators', basis) : { ...provider, description: '' };
      delete definition.configSchema;
      const updated = await operation(basis ? 'update' : 'create', (key) => canonicalJobs.write('event-creators', basis, { ...definition, name, description, config: parsed }, key, basis ? referenceBasis.referenceDigest : undefined));
      setBasis(updated);
      let current = updated;
      if (enabled !== (updated.state === 'enabled')) {
        const op = enabled ? updated.state === 'paused' ? 'resume' : 'enable' : 'disable';
        current = await operation(op, (key) => canonicalJobs.controlDefinition('event-creators', updated, op, key, basis ? referenceBasis.referenceDigest : undefined));
      }
      setBasis(current); setDirty(false); await refresh(); close();
    } catch (e) { setError(canonicalError(e)); await refresh(); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function retire() {
    if (!basis || pending || busyRef.current || !window.confirm('Retire this unreferenced Scheduler? Event history is retained.')) return;
    busyRef.current = true; setBusy(true); setError('');
    try { await operation('retire', (key) => canonicalJobs.retire('event-creators', basis, key, referenceBasis.referenceDigest)); await refresh(); close(); }
    catch (e) { setError(canonicalError(e)); await refresh(); }
    finally { busyRef.current = false; setBusy(false); }
  }
  return <section aria-label="Scheduler editor" className={`${cardClass} space-y-4`}>
    <header className="flex justify-between gap-2"><h3>{initial ? 'Edit Scheduler' : 'New Scheduler source'}</h3><Button disabled={busy || !!pending} onClick={() => { if (!dirty || window.confirm('Discard Scheduler changes?')) close(); }}>Close Scheduler editor</Button></header>
    {error && <p role="alert">{error}</p>}
    {pending && <section aria-label="Pending source operation"><p>Outcome unconfirmed. Query the original operation before continuing.</p><p className="break-all">{pending.key}</p><Button disabled={busy} onClick={() => { setBusy(true); void canonicalJobs.lookup(pending.operation, pending.key, pending.receipt?.operationId).then(async (receipt) => { if (receipt.status === 'completed') { setBasis(receiptObject(receipt)); setPending(null); await refresh(); } else setPending({ ...pending, receipt }); }).catch((e: unknown) => setError(canonicalError(e))).finally(() => setBusy(false)); }}>Query original operation</Button></section>}
    {changed && <div role="alert"><p>The source or its subscribers changed. Your draft is retained; compare before saving.</p><JsonDetail label="Compare server Scheduler" value={latest ?? 'Retired source'} />{latest && <><Button disabled={busy || !!pending} onClick={() => { setBasis(latest); setReferenceBasis(refs); setReviewed(false); setError(''); }}>Keep draft against latest source revision</Button><Button disabled={busy || !!pending} onClick={() => { setName(latest.name); setDescription(String(latest.description ?? '')); setConfig(JSON.stringify(latest.config, null, 2)); setEnabled(latest.state === 'enabled'); setBasis(latest); setReferenceBasis(refs); setReviewed(false); setDirty(false); setError(''); }}>Load server Scheduler</Button></>}</div>}
    <form onSubmit={(e) => { e.preventDefault(); void save(); }} onChange={() => setDirty(true)}><fieldset disabled={busy || !!pending} className="min-w-0 space-y-4">
      <label className="block">Scheduler name<input required maxLength={200} className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="block">Description<textarea className={fieldClass} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      <label className="block">Scheduler config JSON<textarea required spellCheck={false} rows={18} className={`${fieldClass} font-mono`} value={config} onChange={(e) => setConfig(e.target.value)} /></label>
      <p className="text-xs">All rules are preserved: once with RFC3339 seconds/fractions; decimal interval seconds and anchor; weekly or cron with IANA/fixed-offset timezone and explicit DST policy. Configure misfirePolicy, graceSeconds, pausePolicy and optional driveLimit here.</p>
      <JsonDetail label="Scheduler config schema" value={provider?.configSchema} />
      <label><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Scheduler enabled</label>
      <section aria-label="Scheduler impact"><h4>{referenceBasis.references.length} referencing subscribers</h4>{referenceBasis.references.map((ref) => <p key={ref.triggerId}>{ref.name} · {ref.jobName}</p>)}<p>Disabling stops future time events. Already admitted Runs continue. Shared sources outlive their Jobs.</p>{referenceBasis.references.length > 0 && <label><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />I reviewed the affected subscribers.</label>}</section>
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={changed || (referenceBasis.references.length > 0 && !reviewed)}>Save Scheduler</Button>{initial && <Button type="button" disabled={changed || basis?.state === 'enabled' || referenceBasis.references.length > 0} onClick={() => void retire()}>Delete Scheduler</Button>}</div>
    </fieldset></form>
  </section>;
}
