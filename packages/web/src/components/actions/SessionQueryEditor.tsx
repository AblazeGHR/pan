import { useLayoutEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import type { SessionPredicate, SessionQueryField } from '@/types/actions';
import { defaultQuery, defaultQueryValue, queryFields, queryOperators, validateSessionQuery } from './sessionQueryModel';
import { inputClass } from './ActionsUi';

const identities = new WeakMap<SessionPredicate, number>(); let serial = 0;
function keyOf(value: SessionPredicate) { let key = identities.get(value); if (!key) { key = ++serial; identities.set(value, key); } return key; }
export function SessionQueryEditor({ value, onChange, onRawChange, rawText, rawError, depth = 1 }: {
  value: SessionPredicate; onChange: (value: SessionPredicate) => void;
  onRawChange?: (text: string, error: string) => void; rawText?: string; rawError?: string; depth?: number;
}) {
  const [advanced, setAdvanced] = useState(false);
  const rawRef = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => { rawRef.current?.setCustomValidity(rawError ?? ''); }, [rawError, advanced]);
  const kind = 'and' in value ? 'and' : 'or' in value ? 'or' : 'not' in value ? 'not' : 'comparison';
  const error = validateSessionQuery(value);
  return <div className="min-w-0 space-y-3 rounded border border-border-default p-2 sm:p-3">
    <label className="block text-xs">Query condition type<select className={`${inputClass} mt-1`} value={kind} onChange={(e) => onChange(e.target.value === 'and' ? { and: [defaultQuery()] } : e.target.value === 'or' ? { or: [defaultQuery()] } : e.target.value === 'not' ? { not: defaultQuery() } : defaultQuery())}><option value="comparison">Compare field</option><option disabled={depth >= 8} value="and">AND — all</option><option disabled={depth >= 8} value="or">OR — any</option><option disabled={depth >= 8} value="not">NOT</option></select></label>
    {'field' in value ? <div className="space-y-3">
      <label className="block text-xs">Query field<select className={`${inputClass} mt-1`} value={value.field} onChange={(e) => { const field = e.target.value as SessionQueryField; onChange({ field, op: 'eq', value: defaultQueryValue(field, 'eq') }); }}>{queryFields.map((field) => <option key={field}>{field}</option>)}</select></label>
      <label className="block text-xs">Query operator<select className={`${inputClass} mt-1`} value={value.op} onChange={(e) => { const op = e.target.value as typeof value.op; onChange({ ...value, op, value: defaultQueryValue(value.field, op) }); }}>{queryOperators(value.field).map((op) => <option key={op}>{op}</option>)}</select></label>
      {value.op === 'in' || value.field === 'workspaceIds' && value.op !== 'contains' ? <div className="space-y-2"><p className="text-xs">{value.op === 'in' ? 'Match any listed typed value (1–128)' : 'Exact ordered workspace ID array (0–128)'}</p>
        {(value.value as unknown[]).map((item, i) => <div key={i} className="flex min-w-0 gap-2"><Scalar key={`${value.field}-${typeof item}-${i}`} field={value.field === 'workspaceIds' ? 'id' : value.field} value={item} onChange={(next) => onChange({ ...value, value: (value.value as unknown[]).map((v, index) => index === i ? next : v) })} /><Button type="button" size="sm" onClick={() => onChange({ ...value, value: (value.value as unknown[]).filter((_, index) => index !== i) })}>Remove value {i + 1}</Button></div>)}
        <Button type="button" size="sm" disabled={(value.value as unknown[]).length >= 128} onClick={() => onChange({ ...value, value: [...value.value as unknown[], defaultQueryValue(value.field === 'workspaceIds' ? 'id' : value.field, 'eq')] })}>Add query value</Button>
      </div> : <Scalar field={value.field === 'workspaceIds' ? 'id' : value.field} value={value.value} onChange={(next) => onChange({ ...value, value: next })} />}
      {value.op === 'contains' && <p className="text-xs text-text-tertiary">{value.field === 'name' ? 'Case-sensitive literal substring. No regex or normalization.' : 'One exact workspace ID element; not a substring.'}</p>}
    </div> : 'not' in value ? <SessionQueryEditor depth={depth + 1} value={value.not} onChange={(not) => onChange({ not })} /> : (() => {
      const children = 'and' in value ? value.and : value.or;
      const update = (next: SessionPredicate[]) => onChange('and' in value ? { and: next } : { or: next });
      return <>{children.map((child, i) => { const key = keyOf(child); return <div key={key} className="space-y-2"><SessionQueryEditor depth={depth + 1} value={child} onChange={(next) => { identities.set(next, key); update(children.map((c, j) => i === j ? next : c)); }} /><Button type="button" size="sm" disabled={children.length === 1} onClick={() => update(children.filter((_, j) => i !== j))}>Remove query condition {i + 1}</Button></div>; })}<Button type="button" size="sm" disabled={depth >= 8 || children.length >= 64} onClick={() => update([...children, defaultQuery()])}>Add query condition</Button></>;
    })()}
    {depth === 1 && <>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {onRawChange && <><Button type="button" size="sm" onClick={() => setAdvanced(!advanced)}>Advanced query JSON</Button>{advanced && <label className="block text-xs">Query JSON<textarea aria-label="Query JSON" ref={rawRef} className={`${inputClass} mt-1 min-h-32 font-mono text-xs`} value={rawText ?? JSON.stringify(value, null, 2)} onChange={(e) => {
        const text = e.target.value; let error: string;
        try { const parsed: unknown = JSON.parse(text); error = validateSessionQuery(parsed); if (!error) onChange(parsed as SessionPredicate); } catch { error = 'Enter valid query JSON.'; }
        e.target.setCustomValidity(error); onRawChange(text, error);
      }} />{rawError && <span role="alert" className="text-danger">{rawError}</span>}</label>}</>}
    </>}
  </div>;
}
function Scalar({ field, value, onChange }: { field: SessionQueryField; value: unknown; onChange: (v: unknown) => void }) {
  if (field === 'pinned' || field === 'readonlySession') return <label className="block min-w-0 flex-1 text-xs">Boolean value<select className={`${inputClass} mt-1`} value={String(value)} onChange={(e) => onChange(e.target.value === 'true')}><option value="false">false</option><option value="true">true</option></select></label>;
  return <div className="min-w-0 flex-1 space-y-1">{field === 'managedBy' && <label className="block text-xs">Manager value type<select className={inputClass} value={value === null ? 'null' : 'string'} onChange={(e) => onChange(e.target.value === 'null' ? null : '')}><option value="string">Session ID (string)</option><option value="null">No manager (null)</option></select></label>}{value !== null && <label className="block text-xs">Query text value<input className={`${inputClass} mt-1`} value={String(value)} onChange={(e) => onChange(e.target.value)} /></label>}</div>;
}
