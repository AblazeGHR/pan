import { useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { ApiRequestError } from '@/services/api';
import { canonicalError, canonicalJobs, definitionOf } from '@/services/canonicalJobs';
import { newId, type CreatorReferences, type EventCreator, type SchedulerProvider } from '@/types/automationJobs';
import type { CanonicalReceipt, JsonObject } from '@/types/canonicalJobs';
import { cardClass, fieldClass, JsonDetail } from './fields';
import { StateBadge } from './display';
import { useAutomationDraft } from './DraftBoundary';
import type { AutomationSnapshot } from '@/types/automation';
import { CanonicalSchedulerFields } from './CanonicalSchedulerFields';

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
  return <div className="min-w-0 space-y-5">
    <header className="flex flex-col gap-3 border-b border-border-muted pb-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h2 className="text-lg font-semibold">Event Register</h2>
        <p className="mt-0.5 max-w-2xl text-sm text-text-secondary">Reusable canonical Event Creators. Each Scheduler keeps its own rules and cursor.</p>
      </div>
      <Button size="sm" variant="primary" className="self-start sm:self-auto" disabled={!!editor || !timeProvider} onClick={() => setEditor({ key: newId('creator'), source: null })}>New Scheduler</Button>
    </header>
    {editor && <SourceEditor key={editor.key} initial={editor.source} latest={sources.find((s) => s.id === editor.source?.id)} provider={timeProvider} refs={editor.source ? references[editor.source.id] ?? emptyRefs : emptyRefs} refresh={refresh} close={() => setEditor(null)} />}
    <label className="block max-w-[28rem] text-sm font-medium">Search event sources<input className={fieldClass} type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
    {!sources.length && <p className="rounded-xl border border-dashed border-border-default bg-bg-secondary/40 px-4 py-8 text-center text-sm text-text-secondary">No Scheduler sources yet.</p>}
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">{sources.filter((s) => `${s.name} ${JSON.stringify(s.config)}`.toLowerCase().includes(search.toLowerCase())).map((source) => <article id={source.id} key={source.id} aria-label={`Scheduler ${source.name}`} className={`${cardClass} flex flex-col gap-3 ${focusId === source.id ? 'ring-2 ring-accent' : ''}`}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2"><h3 className="min-w-0 break-words text-base font-semibold">{source.name}</h3><StateBadge state={source.state} /></div>
      <p className="text-xs text-text-secondary">Revision <span className="font-mono text-text-secondary">{source.revision}</span><span aria-hidden> · </span>State revision <span className="font-mono text-text-secondary">{source.stateRevision}</span></p>
      <p className="break-all font-mono text-xs text-text-tertiary">{source.id}</p>
      <JsonDetail label={`Time rules for ${source.name}`} value={source.config} />
      <section className="rounded-lg border border-border-muted bg-bg-primary/40 px-3 py-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-tertiary">{references[source.id]?.references.length ?? 0} referencing subscribers</h4>
        {references[source.id]?.references.map((ref) => <div key={ref.triggerId} className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <button className="text-left font-medium text-accent hover:text-accent-hover" onClick={() => jump('Trigger', ref.triggerId)}>{ref.name}</button>
          <span className="text-text-tertiary" aria-hidden>→</span>
          <button className="text-left text-text-secondary hover:text-text-primary" onClick={() => jump('Job', ref.jobId)}>{ref.jobName}</button>
          <span className={`ml-auto rounded-full border px-2 py-0.5 text-[11px] font-medium ${ref.enabled ? 'border-success/40 bg-success/10 text-success' : 'border-border-default bg-bg-tertiary text-text-secondary'}`}>{ref.enabled ? 'Enabled' : 'Disabled'}</span>
        </div>)}
      </section>
      <div className="mt-auto border-t border-border-muted pt-3"><Button size="sm" disabled={!!editor} onClick={() => setEditor({ key: newId('edit'), source })}>Edit {source.name}</Button></div>
    </article>)}</div>
    <section className="space-y-3 rounded-xl border border-border-default bg-bg-secondary p-4">
      <h3 className="text-base font-semibold">System and plugin event types</h3>
      <div className="divide-y divide-border-muted">{definitions.map((definition) => <div key={definition.type} className="py-2 first:pt-0 last:pb-0">
        <p className="flex flex-wrap items-center gap-2 text-sm"><span className="break-all font-mono text-xs text-text-secondary">{definition.type}</span><span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${definition.available ? 'border-success/40 bg-success/10 text-success' : 'border-warning/40 bg-warning/10 text-warning'}`}>{definition.available ? 'Available' : 'Unavailable'}</span></p>
        <JsonDetail label={`Schema ${definition.type}`} value={definition.schema} />
      </div>)}</div>
    </section>
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
    <header className="flex flex-wrap items-start justify-between gap-2 border-b border-border-muted pb-3"><div><h3 className="text-base font-semibold">{initial ? 'Edit Scheduler' : 'New Scheduler source'}</h3><p className="mt-0.5 text-xs text-text-secondary">规则与游标保存在该来源上；引用它的订阅会单独显示。</p></div><Button size="sm" disabled={busy || !!pending} onClick={() => { if (!dirty || window.confirm('Discard Scheduler changes?')) close(); }}>Close Scheduler editor</Button></header>
    {error && <p role="alert" className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}
    {pending && <section aria-label="Pending source operation" className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning"><p>Outcome unconfirmed. Query the original operation before continuing.</p><p className="break-all font-mono text-xs">{pending.key}</p><Button size="sm" disabled={busy} onClick={() => { setBusy(true); void canonicalJobs.lookup(pending.operation, pending.key, pending.receipt?.operationId).then(async (receipt) => { if (receipt.status === 'completed') { setBasis(receiptObject(receipt)); setPending(null); setError(''); await refresh(); } else setPending({ ...pending, receipt }); }).catch((e: unknown) => setError(canonicalError(e))).finally(() => setBusy(false)); }}>Query original operation</Button></section>}
    {changed && <div role="alert" className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning"><p>The source or its subscribers changed. Your draft is retained; compare before saving.</p><JsonDetail label="Compare server Scheduler" value={latest ?? 'Retired source'} />{latest && <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy || !!pending} onClick={() => { setBasis(latest); setReferenceBasis(refs); setReviewed(false); setError(''); }}>Keep draft against latest source revision</Button><Button size="sm" disabled={busy || !!pending} onClick={() => { setName(latest.name); setDescription(String(latest.description ?? '')); setConfig(JSON.stringify(latest.config, null, 2)); setEnabled(latest.state === 'enabled'); setBasis(latest); setReferenceBasis(refs); setReviewed(false); setDirty(false); setError(''); }}>Load server Scheduler</Button></div>}</div>}
    <form onSubmit={(e) => { e.preventDefault(); void save(); }} onChange={() => setDirty(true)}><fieldset disabled={busy || !!pending} className="min-w-0 space-y-4">
      <label className="block text-sm">Scheduler name<input required maxLength={200} className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="block text-sm">Description<textarea className={fieldClass} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      {(() => { try { const value: unknown = JSON.parse(config); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); return <CanonicalSchedulerFields value={value as JsonObject} onChange={(next) => { setConfig(JSON.stringify(next, null, 2)); setDirty(true); }} />; } catch { return <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">Fix the advanced configuration before using the time-rule form. Your text is retained.</p>; } })()}
      <details className="rounded-lg border border-border-muted bg-bg-primary/40 px-3 py-2 text-sm"><summary className="cursor-pointer font-medium text-text-secondary hover:text-text-primary">Advanced Scheduler JSON</summary><label className="mt-2 block text-sm">Scheduler config JSON<textarea spellCheck={false} rows={18} className={`${fieldClass} font-mono`} value={config} onChange={(e) => setConfig(e.target.value)} /></label><JsonDetail label="Scheduler config schema" value={provider?.configSchema} /></details>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Scheduler enabled</label>
      <section aria-label="Scheduler impact" className="space-y-2 rounded-lg border border-border-muted bg-bg-primary/40 px-3 py-3"><h4 className="text-sm font-semibold">{referenceBasis.references.length} referencing subscribers</h4>{referenceBasis.references.map((ref) => <p key={ref.triggerId} className="text-sm text-text-secondary">{ref.name} · {ref.jobName}</p>)}<p className="text-xs leading-5 text-text-secondary">Disabling stops future time events. Already admitted Runs continue. Shared sources outlive their Jobs.</p>{referenceBasis.references.length > 0 && <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />I reviewed the affected subscribers.</label>}</section>
      <div className="flex flex-wrap gap-2 border-t border-border-muted pt-3"><Button type="submit" variant="primary" disabled={busy || !!pending || changed || (referenceBasis.references.length > 0 && !reviewed)}>Save Scheduler</Button>{initial && <Button type="button" disabled={busy || !!pending || changed || basis?.state === 'enabled' || referenceBasis.references.length > 0} onClick={() => void retire()}><span className="text-danger">Delete Scheduler</span></Button>}</div>
    </fieldset></form>
  </section>;
}
