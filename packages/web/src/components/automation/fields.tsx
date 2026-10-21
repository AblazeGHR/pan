import { useLayoutEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import type { Argument, Condition } from '@/types/automation';
import { comparison } from '@/types/automation';

export const fieldClass = 'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary p-2 text-sm';
export const cardClass = 'min-w-0 rounded-xl border border-border-default bg-bg-secondary p-4';
// UI identity never enters the persisted condition. Immutable edits retain the
// edited row's key; deletion retains the surviving node's key, not its index.
const conditionKeys = new WeakMap<Condition, number>();
let nextConditionKey = 0;
function conditionKey(value: Condition) {
  let key = conditionKeys.get(value);
  if (key === undefined) { key = ++nextConditionKey; conditionKeys.set(value, key); }
  return key;
}
export function JsonDetail({ label, value }: { label: string; value: unknown }) {
  return <details className="min-w-0 text-sm"><summary className="cursor-pointer">{label}</summary><pre className="mt-2 max-h-96 overflow-y-auto whitespace-pre-wrap break-all rounded bg-bg-primary p-3 text-xs">{JSON.stringify(value ?? null, null, 2)}</pre></details>;
}
export function ConditionEditor({ value, onChange, depth = 0 }: { value: Condition; onChange: (value: Condition) => void; depth?: number }) {
  const kind = 'and' in value ? 'and' : 'or' in value ? 'or' : 'not' in value ? 'not' : 'comparison';
  return <div key={kind} className="min-w-0 space-y-2 rounded border border-border-default p-2">
    <label className="block text-sm">Condition type<select className={fieldClass} value={kind} onChange={(e) => onChange(e.target.value === 'and' ? { and: [comparison()] } : e.target.value === 'or' ? { or: [comparison()] } : e.target.value === 'not' ? { not: comparison() } : comparison())}>
      <option value="comparison">Compare State</option><option value="and">AND</option><option value="or">OR</option><option value="not">NOT</option></select></label>
    {'field' in value ? <>
      <label className="block text-sm">State field<select className={fieldClass} value={value.field} onChange={(e) => onChange({ ...value, field: e.target.value })}><option>workers.runningCount</option>{value.field !== 'workers.runningCount' && <option>{value.field}</option>}</select></label>
      <label className="block text-sm">Comparison<select aria-label="Comparison" className={fieldClass} value={value.op} onChange={(e) => onChange({ ...value, op: e.target.value as typeof value.op })}>{['eq', 'ne', 'gt', 'gte', 'lt', 'lte'].map((op) => <option key={op}>{op}</option>)}</select></label>
      <LiteralInput label="Compare value (JSON)" value={value.value} onChange={(next) => onChange({ ...value, value: next })} />
    </> : 'not' in value ? <ConditionEditor depth={depth + 1} value={value.not} onChange={(not) => onChange({ not })} /> : (() => {
      const children = 'and' in value ? value.and : value.or;
      const update = (items: Condition[]) => onChange('and' in value ? { and: items } : { or: items });
      return <>{children.map((child, i) => { const key = conditionKey(child); return <div key={key} className="space-y-2"><ConditionEditor depth={depth + 1} value={child} onChange={(next) => { conditionKeys.set(next, key); update(children.map((c, j) => j === i ? next : c)); }} /><Button type="button" size="sm" disabled={children.length === 1} onClick={() => update(children.filter((_, j) => j !== i))}>Remove condition {i + 1}</Button></div>; })}<Button type="button" size="sm" disabled={depth >= 8} onClick={() => update([...children, comparison()])}>Add condition</Button></>;
    })()}
  </div>;
}

function LiteralInput({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
  const source = JSON.stringify(value);
  const [buffer, setBuffer] = useState(() => ({ source, text: source, error: '' }));
  const inputRef = useRef<HTMLInputElement>(null);
  // A local valid edit records its emitted value before notifying the parent,
  // preserving lexemes such as 22.0. Unrelated renders retain incomplete JSON.
  // An external value replaces the buffer and any obsolete validity error.
  if (buffer.source !== source) setBuffer({ source, text: source, error: '' });
  useLayoutEffect(() => { inputRef.current?.setCustomValidity(buffer.error); }, [buffer.error, buffer.source]);
  return <label className="block text-sm">{label}<input ref={inputRef} required className={`${fieldClass} font-mono`} value={buffer.text} onChange={(e) => {
    const input = e.currentTarget;
    try { const next: unknown = JSON.parse(input.value); input.setCustomValidity(''); setBuffer({ source: JSON.stringify(next), text: input.value, error: '' }); onChange(next); }
    catch { const error = 'Enter valid JSON. Strings need double quotes.'; input.setCustomValidity(error); setBuffer({ source, text: input.value, error }); }
  }} /></label>;
}
export function ArgumentsEditor({ value, onChange, names }: { value: Record<string, Argument>; onChange: (value: Record<string, Argument>) => void; names: string[] }) {
  const [name, setName] = useState('');
  return <div className="min-w-0 space-y-3"><p className="text-xs text-text-secondary">Literal values use JSON (for example: "hello", 0, true, ["id"]). Event paths use dictionary keys, for example payload.sessionId. No scripts.</p>
    {Object.entries(value).map(([key, arg]) => <div key={key} className="min-w-0 space-y-2 rounded border border-border-default p-3">
      <p className="break-all text-sm font-medium">{key}</p><label className="block text-sm">Parameter source<select className={fieldClass} value={arg.from} onChange={(e) => onChange({ ...value, [key]: e.target.value === 'event' ? { from: 'event', path: 'payload.' } : { from: 'literal', value: '' } })}><option value="literal">Literal</option><option value="event">Event field</option></select></label>
      {arg.from === 'event' ? <label className="block text-sm">Event path<input required pattern="[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*" className={fieldClass} value={arg.path} onChange={(e) => onChange({ ...value, [key]: { from: 'event', path: e.target.value } })} /></label> : <LiteralInput key={`${key}-literal`} label="Literal value (JSON)" value={arg.value} onChange={(next) => onChange({ ...value, [key]: { from: 'literal', value: next } })} />}
      <Button type="button" size="sm" onClick={() => onChange(Object.fromEntries(Object.entries(value).filter(([k]) => k !== key)))}>Remove parameter</Button>
    </div>)}
    <label className="block text-sm">Parameter name<input className={fieldClass} list="automation-parameters" value={name} onChange={(e) => setName(e.target.value)} /></label><datalist id="automation-parameters">{names.map((n) => <option key={n}>{n}</option>)}</datalist>
    <Button type="button" disabled={!name.trim() || Object.hasOwn(value, name)} onClick={() => { onChange({ ...value, [name]: { from: 'literal', value: '' } }); setName(''); }}>Add parameter</Button>
  </div>;
}
