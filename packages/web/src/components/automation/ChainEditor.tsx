import { Button } from '@/components/ui/Button';
import type { Action } from '@/types/actions';
import { newId, type ChainStep, type StepBinding } from '@/types/automationJobs';
import { fieldClass, LiteralInput } from './fields';

export function chainErrors(steps: ChainStep[], actions: Action[]): string[] {
  const errors: string[] = [];
  if (!steps.length) errors.push('Add at least one Action step.');
  const seen = new Set<string>();
  for (const [i, step] of steps.entries()) {
    const prefix = `Step ${i + 1}`;
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(step.stepId) || seen.has(step.stepId)) errors.push(`${prefix}: step ID must be unique and valid.`);
    const action = actions.find((a) => a.id === step.actionId);
    if (!action?.enabled) errors.push(`${prefix}: choose an enabled Action.`);
    if (action?.effect === 'high_risk' || action?.execution.kind === 'system_job' && action.execution.operation === 'main-lifecycle') errors.push(`${prefix}: this Action needs its protected entry point.`);
    for (const name of action?.input_schema.required ?? []) if (!(name in step.arguments) && action?.input_schema.properties[name]?.default === undefined) errors.push(`${prefix}: parameter ${name} is required.`);
    for (const [name, binding] of Object.entries(step.arguments)) {
      const spec = action?.input_schema.properties[name];
      if (action && !spec) errors.push(`${prefix}: parameter ${name} is not in this Action schema.`);
      if (binding.from === 'step' && !seen.has(binding.stepId)) errors.push(`${prefix}: ${name} must reference a preceding step.`);
      if (binding.from !== 'literal' && binding.path.split('.').some((part) => !part) && binding.path !== '') errors.push(`${prefix}: ${name} has an incomplete path.`);
      if (binding.from === 'literal' && spec) {
        const v = binding.value;
        const valid = spec.type === 'array' ? Array.isArray(v) : spec.type === 'object' ? v !== null && typeof v === 'object' && !Array.isArray(v) : spec.type === 'integer' ? Number.isInteger(v) : spec.type === 'null' ? v === null : typeof v === spec.type;
        if (!valid) errors.push(`${prefix}: ${name} must be ${spec.type}.`);
      }
    }
    seen.add(step.stepId);
  }
  return errors;
}

export function ChainEditor({ value, actions, onChange }: { value: ChainStep[]; actions: Action[]; onChange: (steps: ChainStep[]) => void }) {
  const setStep = (index: number, step: ChainStep) => onChange(value.map((s, i) => i === index ? step : s));
  const move = (index: number, delta: number) => { const next = [...value]; [next[index], next[index + delta]] = [next[index + delta]!, next[index]!]; onChange(next); };
  return <section aria-label="Action chain" className="min-w-0 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold">Action chain</h3><p className="mt-1 text-xs text-text-secondary">Steps run in order. Failure, cancellation or an uncertain result stops the chain.</p></div><Button type="button" disabled={value.length >= 32} onClick={() => onChange([...value, { stepId: newId('step'), actionId: '', arguments: {} }])}>Add step</Button></div>
    {value.map((step, index) => {
      const action = actions.find((a) => a.id === step.actionId);
      const names = Object.keys(action?.input_schema.properties ?? {}).filter((n) => !(n in step.arguments));
      const bind = (name: string, binding: StepBinding) => setStep(index, { ...step, arguments: { ...step.arguments, [name]: binding } });
      return <article key={step.stepId} aria-label={`Step ${index + 1}`} className="min-w-0 space-y-3 rounded-xl border border-border-default bg-bg-primary p-3">
        <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><h4 className="text-sm font-semibold">{index + 1}. {action?.name ?? 'Choose an Action'}</h4><p className="mt-1 break-all font-mono text-[11px] text-text-secondary">{step.stepId}</p></div><div className="flex gap-1"><Button type="button" size="sm" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}>↑</Button><Button type="button" size="sm" aria-label={`Move step ${index + 1} down`} disabled={index === value.length - 1} onClick={() => move(index, 1)}>↓</Button><Button type="button" size="sm" aria-label={`Remove step ${index + 1}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove</Button></div></div>
        <label className="block text-sm">Action for step {index + 1}<select required className={fieldClass} value={step.actionId} onChange={(e) => setStep(index, { ...step, actionId: e.target.value })}><option value="">Choose an Action</option>{step.actionId && !action && <option value={step.actionId}>{step.actionId} (missing)</option>}{actions.map((a) => <option key={a.id} value={a.id}>{a.name}{!a.enabled ? ' · Disabled' : ''}</option>)}</select></label>
        {Object.entries(step.arguments).map(([name, arg]) => <div key={name} className="min-w-0 space-y-2 rounded-lg border border-border-default p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="break-all text-sm font-medium">{name}</span><Button type="button" size="sm" aria-label={`Remove ${name} from step ${index + 1}`} onClick={() => setStep(index, { ...step, arguments: Object.fromEntries(Object.entries(step.arguments).filter(([n]) => n !== name)) })}>Remove parameter</Button></div>
          <label className="block text-sm">{name} source<select className={fieldClass} value={arg.from} onChange={(e) => { const from = e.target.value as StepBinding['from']; bind(name, from === 'literal' ? { from, value: '' } : from === 'step' ? { from, stepId: value[index - 1]?.stepId ?? '', path: '' } : { from, path: from === 'event' ? 'payload.' : '' }); }}><option value="literal">Literal</option><option value="input">Job input</option><option value="event">Event field</option><option value="step" disabled={!index}>Previous step output</option>{arg.from === 'system' && <option value="system">Trusted system field</option>}</select></label>
          {arg.from === 'literal' ? <LiteralInput label={`${name} value (JSON)`} value={arg.value} onChange={(next) => bind(name, { from: 'literal', value: next })} /> : <>
            {arg.from === 'step' && <label className="block text-sm">Output step<select required className={fieldClass} value={arg.stepId} onChange={(e) => bind(name, { ...arg, stepId: e.target.value })}><option value="">Choose preceding step</option>{value.slice(0, index).map((prior, i) => <option key={prior.stepId} value={prior.stepId}>{i + 1}. {actions.find((a) => a.id === prior.actionId)?.name ?? prior.actionId}</option>)}</select></label>}
            <label className="block text-sm">{name} path<input className={fieldClass} value={arg.path} placeholder={arg.from === 'event' ? 'payload.scheduleId' : 'Empty for the whole object'} onChange={(e) => bind(name, { ...arg, path: e.target.value })} /></label>
          </>}
        </div>)}
        {names.length > 0 && <label className="block text-sm">Add parameter to step {index + 1}<select className={fieldClass} value="" onChange={(e) => { const name = e.target.value; if (name) bind(name, { from: 'literal', value: action?.input_schema.properties[name]?.default ?? '' }); }}><option value="">Choose a parameter</option>{names.map((name) => <option key={name} value={name}>{name}{action?.input_schema.required?.includes(name) ? ' · Required' : ''}</option>)}</select></label>}
        {!Object.keys(action?.input_schema.properties ?? {}).length && action && <p className="text-xs text-text-secondary">This Action takes no parameters.</p>}
      </article>;
    })}
    {chainErrors(value, actions).length > 0 && <ul aria-label="Chain validation" className="list-inside list-disc space-y-1 text-xs text-danger">{chainErrors(value, actions).map((error) => <li key={error}>{error}</li>)}</ul>}
  </section>;
}
