import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { newId } from '@/types/automationJobs';
import type { CanonicalKind, CanonicalObject, JsonObject } from '@/types/canonicalJobs';
import type { Condition } from '@/types/automation';
import { comparison } from '@/types/automation';
import { ConditionEditor, fieldClass, LiteralInput } from './fields';

const obj = (value: unknown): JsonObject =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : {};
const list = (value: unknown): JsonObject[] =>
  Array.isArray(value) ? (value as JsonObject[]) : [];
type Catalog = { items: JsonObject[]; references: Record<string, JsonObject> };
function initialLiteral(schema: JsonObject): unknown {
  if ('default' in schema) return schema.default;
  if (schema.type === 'boolean') return false;
  if (schema.type === 'number' || schema.type === 'integer') return 0;
  if (schema.type === 'array') return [];
  if (schema.type === 'object') return {};
  if (schema.type === 'null') return null;
  return '';
}

function TypedValue({
  label,
  value,
  schema,
  onChange,
}: {
  label: string;
  value: unknown;
  schema?: JsonObject;
  onChange: (value: unknown) => void;
}) {
  const inferred = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
  const [type, setType] = useState(
    typeof schema?.type === 'string' ? schema.type : inferred === 'undefined' ? 'string' : inferred,
  );
  return (
    <div className="space-y-2">
      <label className="block text-sm">
        {label} type
        <select
          aria-label={`${label} type`}
          className={fieldClass}
          value={type}
          onChange={(e) => {
            const next = e.target.value;
            setType(next);
            onChange(
              next === 'string'
                ? ''
                : next === 'number' || next === 'integer'
                  ? 0
                  : next === 'boolean'
                    ? false
                    : next === 'null'
                      ? null
                      : next === 'array'
                        ? []
                        : {},
            );
          }}
        >
          {['string', 'number', 'integer', 'boolean', 'null', 'array', 'object'].map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </label>
      {type === 'string' ? (
        <label className="block text-sm">
          {label}
          <input
            className={fieldClass}
            value={String(value ?? '')}
            onChange={(e) => onChange(e.target.value)}
          />
        </label>
      ) : type === 'boolean' ? (
        <label className="block text-sm">
          {label}
          <select
            aria-label={label}
            className={fieldClass}
            value={String(value)}
            onChange={(e) => onChange(e.target.value === 'true')}
          >
            <option value="false">False</option>
            <option value="true">True</option>
          </select>
        </label>
      ) : type === 'number' || type === 'integer' ? (
        <label className="block text-sm">
          {label}
          <input
            required
            type="number"
            step={type === 'integer' ? '1' : 'any'}
            className={fieldClass}
            value={typeof value === 'number' ? value : ''}
            onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
          />
        </label>
      ) : (
        type !== 'null' && (
          <LiteralInput label={`${label} (JSON)`} value={value} onChange={onChange} />
        )
      )}
    </div>
  );
}

function Bindings({
  value,
  schema = {},
  steps = [],
  onChange,
  trigger = false,
  suffix = '',
}: {
  value: JsonObject;
  schema?: JsonObject;
  steps?: JsonObject[];
  onChange: (value: JsonObject) => void;
  trigger?: boolean;
  suffix?: string;
}) {
  const [newName, setName] = useState('');
  const properties = obj(schema.properties);
  const set = (name: string, binding: JsonObject) => onChange({ ...value, [name]: binding });
  return (
    <section aria-label={`Parameter bindings${suffix}`} className="space-y-3">
      {Object.entries(value).map(([name, raw]) => {
        const binding = obj(raw);
        return (
          <fieldset
            key={name}
            aria-label={`Parameter ${name}${suffix}`}
            className="min-w-0 space-y-2 rounded border border-border-default p-3"
          >
            <legend>{name}</legend>
            <label className="block text-sm">
              {name} source
              <select
                aria-label={`${name} source`}
                className={fieldClass}
                value={String(binding.from)}
                onChange={(e) => {
                  const from = e.target.value;
                  set(
                    name,
                    from === 'literal'
                      ? { from, value: initialLiteral(obj(properties[name])) }
                      : from === 'step'
                        ? { from, stepId: steps.at(-1)?.stepId ?? '', path: '' }
                        : { from, path: '' },
                  );
                }}
              >
                <option value="literal">Literal</option>
                {!trigger && <option value="input">Job input</option>}
                <option value="event">Event field</option>
                {!trigger && (
                  <option value="step" disabled={!steps.length}>
                    Previous step output
                  </option>
                )}
                {binding.from === 'system' && <option value="system">Trusted system field</option>}
              </select>
            </label>
            {binding.from === 'literal' ? (
              <TypedValue
                key={JSON.stringify(obj(properties[name]).type)}
                label={`${name} value`}
                value={binding.value}
                schema={obj(properties[name])}
                onChange={(next) => set(name, { ...binding, value: next })}
              />
            ) : (
              <>
                {binding.from === 'step' && (
                  <label className="block text-sm">
                    Output step
                    <select
                      aria-label={'Output step'}
                      required
                      className={fieldClass}
                      value={String(binding.stepId ?? '')}
                      onChange={(e) => set(name, { ...binding, stepId: e.target.value })}
                    >
                      <option value="">Choose preceding step</option>
                      {!steps.some((s) => s.stepId === binding.stepId) && !!binding.stepId && (
                        <option value={String(binding.stepId)}>
                          {String(binding.stepId)} (not preceding)
                        </option>
                      )}
                      {steps.map((step) => (
                        <option key={String(step.stepId)} value={String(step.stepId)}>
                          {String(step.stepId)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="block text-sm">
                  {name} path
                  <input
                    className={fieldClass}
                    value={String(binding.path ?? '')}
                    onChange={(e) => set(name, { ...binding, path: e.target.value })}
                  />
                </label>
              </>
            )}
            <Button
              type="button"
              onClick={() =>
                onChange(Object.fromEntries(Object.entries(value).filter(([key]) => key !== name)))
              }
            >
              Remove {name}
            </Button>
          </fieldset>
        );
      })}
      <label className="block text-sm">
        Add parameter{suffix}
        <select
          aria-label={`Add parameter${suffix}`}
          className={fieldClass}
          value=""
          onChange={(e) => {
            const name = e.target.value;
            if (name)
              set(name, {
                from: 'literal',
                value: initialLiteral(obj(properties[name])),
              });
          }}
        >
          <option value="">Choose a parameter</option>
          {Object.keys(properties)
            .filter((name) => !(name in value))
            .map((name) => (
              <option key={name}>{name}</option>
            ))}
        </select>
      </label>
      <details>
        <summary>Additional parameter</summary>
        <label className="block text-sm">
          Parameter name{suffix}
          <input className={fieldClass} value={newName} onChange={(e) => setName(e.target.value)} />
        </label>
        <Button
          type="button"
          disabled={!newName || newName in value}
          onClick={() => {
            set(newName, { from: 'literal', value: '' });
            setName('');
          }}
        >
          Add named parameter{suffix}
        </Button>
      </details>
    </section>
  );
}

export function canonicalFormErrors(kind: CanonicalKind, value: JsonObject): string[] {
  if (kind !== 'jobs') return [];
  const seen = new Set<string>(),
    errors: string[] = [];
  for (const step of list(value.steps)) {
    if (!obj(step.actionRef).id) errors.push('Choose an Action for each step.');
    for (const raw of Object.values(obj(step.arguments))) {
      const binding = obj(raw);
      if (binding.from === 'step' && !seen.has(String(binding.stepId)))
        errors.push('Output bindings must reference a preceding step.');
    }
    if (seen.has(String(step.stepId))) errors.push('Step IDs must be unique.');
    seen.add(String(step.stepId));
  }
  if (!seen.size) errors.push('Add at least one Action step.');
  return errors;
}

export function CanonicalDefinitionFields({
  kind,
  value,
  onChange,
  targetJob,
}: {
  kind: 'jobs' | 'triggers';
  value: JsonObject;
  onChange: (value: JsonObject) => void;
  targetJob?: CanonicalObject;
}) {
  const [catalog, setCatalog] = useState<Catalog>({ items: [], references: {} });
  const [error, setError] = useState('');
  const [eventName, setEventName] = useState('');
  const catalogGeneration = useRef(0);
  const loadCatalog = useCallback(async () => {
    const generation = ++catalogGeneration.current;
    try {
      const page = await canonicalJobs.actions();
      if (!Array.isArray(page.items)) throw new Error('Action catalog unavailable');
      if (generation !== catalogGeneration.current) return;
      setCatalog({ items: page.items, references: page.references ?? {} });
      setError(
        page.references
          ? ''
          : 'Action reference metadata unavailable. Existing references are preserved; refresh when the service is ready.',
      );
    } catch (failure) {
      if (generation === catalogGeneration.current) setError(canonicalError(failure));
    }
  }, []);
  useEffect(() => {
    if (kind === 'jobs') void loadCatalog();
    return () => {
      catalogGeneration.current++;
    };
  }, [kind, loadCatalog]);
  const steps = list(value.steps);
  const updateStep = (index: number, step: JsonObject) =>
    onChange({ ...value, steps: steps.map((s, i) => (i === index ? step : s)) });
  const move = (i: number, delta: number) => {
    const next = [...steps];
    [next[i], next[i + delta]] = [next[i + delta]!, next[i]!];
    onChange({ ...value, steps: next });
  };
  return (
    <section
      aria-label={kind === 'jobs' ? 'Structured Job definition' : 'Structured Trigger definition'}
      className="space-y-4"
    >
      <label className="block text-sm">
        Description
        <textarea
          className={fieldClass}
          value={String(value.description ?? '')}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </label>
      {kind === 'jobs' ? (
        <>
          {error && <p role="status">{error}</p>}
          <Button type="button" onClick={() => void loadCatalog()}>
            Refresh Actions
          </Button>
          <JobInputs value={value} onChange={onChange} />
          <section aria-label="Action chain" className="space-y-3">
            {steps.map((step, index) => {
              const ref = obj(step.actionRef),
                action = catalog.items.find((a) => a.id === ref.id);
              const currentRef = obj(catalog.references[String(ref.id)]);
              const exactAction =
                ref.revision === currentRef.revision && ref.digest === currentRef.digest
                  ? action
                  : undefined;
              const completions = list(exactAction?.completionContracts);
              return (
                <fieldset
                  key={String(step.stepId)}
                  aria-label={`Step ${index + 1}`}
                  className="min-w-0 space-y-3 rounded border border-border-default p-3"
                >
                  <legend>Step {index + 1}</legend>
                  <p className="break-all text-xs">{String(step.stepId)}</p>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      aria-label={`Move step ${index + 1} up`}
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      ↑
                    </Button>
                    <Button
                      type="button"
                      aria-label={`Move step ${index + 1} down`}
                      disabled={index === steps.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      ↓
                    </Button>
                    <Button
                      type="button"
                      onClick={() =>
                        onChange({ ...value, steps: steps.filter((_, i) => i !== index) })
                      }
                    >
                      Remove step {index + 1}
                    </Button>
                  </div>
                  <label className="block text-sm">
                    Action for step {index + 1}
                    <select
                      aria-label={`Action for step ${index + 1}`}
                      required
                      className={fieldClass}
                      value={String(ref.id ?? '')}
                      onChange={(e) => {
                        const item = catalog.items.find((a) => a.id === e.target.value),
                          reference = catalog.references[e.target.value];
                        if (!item || !reference) return;
                        const properties = obj(obj(item.inputSchema).properties);
                        const args = Object.fromEntries(
                          (Array.isArray(obj(item.inputSchema).required)
                            ? (obj(item.inputSchema).required as string[])
                            : []
                          ).map((name) => [
                            name,
                            {
                              from: 'literal',
                              value: initialLiteral(obj(properties[name])),
                            },
                          ]),
                        );
                        updateStep(index, {
                          ...step,
                          actionRef: reference,
                          arguments: args,
                          completion: { contract: item.defaultCompletion },
                        });
                      }}
                    >
                      <option value="">Choose an Action</option>
                      {!!ref.id && !action && (
                        <option value={String(ref.id)}>
                          {String(ref.id)} (preserved reference)
                        </option>
                      )}
                      {catalog.items.map((a) => (
                        <option
                          key={String(a.id)}
                          value={String(a.id)}
                          disabled={
                            a.state !== 'published' ||
                            !a.enabled ||
                            !catalog.references[String(a.id)]
                          }
                        >
                          {String(a.name)} · Revision {String(a.revision)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!!ref.id && (
                    <p className="break-all text-xs">
                      Fixed Action: {String(ref.id)} · Revision {String(ref.revision)} ·{' '}
                      {String(ref.digest)}
                    </p>
                  )}
                  <Bindings
                    value={obj(step.arguments)}
                    schema={obj(exactAction?.inputSchema)}
                    steps={steps.slice(0, index)}
                    suffix={` for step ${index + 1}`}
                    onChange={(argumentsValue) =>
                      updateStep(index, { ...step, arguments: argumentsValue })
                    }
                  />
                  <label className="block text-sm">
                    Completion for step {index + 1}
                    <select
                      aria-label={`Completion for step ${index + 1}`}
                      className={fieldClass}
                      value={String(obj(step.completion).contract ?? '')}
                      onChange={(e) =>
                        updateStep(index, {
                          ...step,
                          completion: { ...obj(step.completion), contract: e.target.value },
                        })
                      }
                    >
                      {[
                        ...new Set([
                          String(obj(step.completion).contract ?? ''),
                          ...completions.map((c) => String(c.id)),
                        ]),
                      ].map((id) => (
                        <option key={id}>{id}</option>
                      ))}
                    </select>
                  </label>
                </fieldset>
              );
            })}
            <Button
              type="button"
              disabled={steps.length >= 32}
              onClick={() =>
                onChange({
                  ...value,
                  steps: [
                    ...steps,
                    {
                      stepId: newId('step'),
                      actionRef: { id: '', revision: 1, digest: '' },
                      arguments: {},
                      completion: { contract: '' },
                    },
                  ],
                })
              }
            >
              Add step
            </Button>
          </section>
          {canonicalFormErrors('jobs', value).length > 0 && (
            <ul aria-label="Chain validation" className="text-sm text-danger">
              {canonicalFormErrors('jobs', value).map((message, index) => (
                <li key={index}>{message}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>Job policies</summary>
            <div className="space-y-3">
              <label className="block text-sm">
                Overlap mode
                <select
                  aria-label={'Overlap mode'}
                  className={fieldClass}
                  value={String(obj(value.overlap).mode ?? 'forbid')}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      overlap: {
                        ...obj(value.overlap),
                        maxActive:
                          e.target.value === 'parallel'
                            ? Math.max(1, Number(obj(value.overlap).maxActive ?? 3))
                            : 1,
                        maxPending:
                          e.target.value === 'forbid'
                            ? 0
                            : Math.max(
                                e.target.value === 'queue' ? 1 : 0,
                                Number(obj(value.overlap).maxPending ?? 0),
                              ),
                        mode: e.target.value,
                      },
                    })
                  }
                >
                  {['forbid', 'queue', 'parallel'].map((mode) => (
                    <option key={mode}>{mode}</option>
                  ))}
                </select>
              </label>
              {['maxActive', 'maxPending'].map((key) => (
                <label key={key} className="block text-sm">
                  {key === 'maxActive' ? 'Maximum active Runs' : 'Maximum pending Runs'}
                  <input
                    type="number"
                    min={key === 'maxActive' ? 1 : 0}
                    step={1}
                    className={fieldClass}
                    value={String(obj(value.overlap)[key] ?? (key === 'maxActive' ? 1 : 0))}
                    onChange={(e) =>
                      onChange({
                        ...value,
                        overlap: {
                          mode: 'forbid',
                          maxActive: 1,
                          maxPending: 0,
                          ...obj(value.overlap),
                          [key]: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              ))}
              <label className="block text-sm">
                Retention policy
                <input
                  required
                  className={fieldClass}
                  value={String(obj(value.retention).policyRef ?? '')}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      retention: { ...obj(value.retention), policyRef: e.target.value },
                    })
                  }
                />
              </label>
              <label className="block text-sm">
                <input
                  type="checkbox"
                  checked={obj(value.retention).pinned === true}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      retention: { ...obj(value.retention), pinned: e.target.checked },
                    })
                  }
                />
                Pin Job retention
              </label>
              <label className="block text-sm">
                <input
                  type="checkbox"
                  checked={!!value.runLimit}
                  onChange={(e) => {
                    const next = { ...value };
                    if (e.target.checked)
                      next.runLimit = { maxRuns: 1, counterDomain: 'run-admission' };
                    else delete next.runLimit;
                    onChange(next);
                  }}
                />
                Limit admitted Runs
              </label>
              {!!value.runLimit && (
                <label className="block text-sm">
                  Maximum Runs
                  <input
                    required
                    type="number"
                    min={1}
                    step={1}
                    className={fieldClass}
                    value={String(obj(value.runLimit).maxRuns)}
                    onChange={(e) =>
                      onChange({
                        ...value,
                        runLimit: { ...obj(value.runLimit), maxRuns: Number(e.target.value) },
                      })
                    }
                  />
                </label>
              )}
              <NotificationFields
                value={obj(value.notification)}
                onChange={(notification) => onChange({ ...value, notification })}
              />
              <RecoveryFields
                value={obj(value.recovery)}
                onChange={(recovery) => onChange({ ...value, recovery })}
              />
            </div>
          </details>
        </>
      ) : (
        <>
          <section aria-label="Events (OR)" className="space-y-2">
            {(Array.isArray(value.eventTypes) ? (value.eventTypes as string[]) : []).map((type) => (
              <div key={type} className="flex flex-wrap gap-2">
                <span>{type}</span>
                <Button
                  type="button"
                  aria-label={`Remove Event ${type}`}
                  onClick={() =>
                    onChange({
                      ...value,
                      eventTypes: (value.eventTypes as string[]).filter((t) => t !== type),
                    })
                  }
                >
                  Remove Event
                </Button>
              </div>
            ))}
            <label className="block text-sm">
              Event type
              <input
                className={fieldClass}
                value={eventName}
                onChange={(e) => setEventName(e.target.value)}
                list="canonical-event-types"
              />
            </label>
            <datalist id="canonical-event-types">
              <option value="scheduler.time.v2" />
              <option value="workers.all_idle" />
            </datalist>
            <Button
              type="button"
              disabled={!eventName.trim()}
              onClick={() => {
                onChange({
                  ...value,
                  eventTypes: [
                    ...new Set([
                      ...(Array.isArray(value.eventTypes) ? (value.eventTypes as string[]) : []),
                      eventName.trim(),
                    ]),
                  ],
                });
                setEventName('');
              }}
            >
              Add Event
            </Button>
          </section>
          <label className="block text-sm">
            <input
              type="checkbox"
              checked={!!value.condition}
              onChange={(e) =>
                onChange({ ...value, condition: e.target.checked ? comparison() : null })
              }
            />
            Check State after Event
          </label>
          {!!value.condition && (
            <ConditionEditor
              customFields
              valueEditor={(conditionValue, change) => (
                <TypedValue label="Compare value" value={conditionValue} onChange={change} />
              )}
              value={value.condition as Condition}
              onChange={(condition) => onChange({ ...value, condition })}
            />
          )}
          <h3>Trigger parameter bindings</h3>
          <Bindings
            trigger
            schema={obj(targetJob?.inputSchema)}
            value={obj(value.arguments)}
            onChange={(argumentsValue) => onChange({ ...value, arguments: argumentsValue })}
          />
          <ScalarFilter
            value={obj(value.eventFilter)}
            onChange={(eventFilter) => onChange({ ...value, eventFilter })}
          />
        </>
      )}
    </section>
  );
}

function NotificationFields({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const recipients = list(value.recipients);
  return (
    <section className="space-y-2">
      <h3>Notifications</h3>
      <fieldset>
        <legend>Notify on</legend>
        {['completed', 'failed', 'cancelled', 'uncertain'].map((state) => (
          <label key={state} className="mr-3 inline-flex gap-1">
            <input
              type="checkbox"
              checked={Array.isArray(value.on) && value.on.includes(state)}
              onChange={(e) =>
                onChange({
                  ...value,
                  on: e.target.checked
                    ? [...(Array.isArray(value.on) ? value.on : []), state]
                    : (Array.isArray(value.on) ? value.on : []).filter((s) => s !== state),
                })
              }
            />
            {state}
          </label>
        ))}
      </fieldset>
      {recipients.map((recipient, index) => (
        <fieldset
          key={String(recipient.recipientId)}
          aria-label={`Notification recipient ${index + 1}`}
          className="space-y-2"
        >
          <p className="break-all text-xs">Recipient ID: {String(recipient.recipientId)}</p>
          {['channelId', 'targetRef'].map((key) => (
            <label key={key} className="block text-sm">
              {key}
              <input
                required
                className={fieldClass}
                value={String(recipient[key] ?? '')}
                onChange={(e) =>
                  onChange({
                    ...value,
                    recipients: recipients.map((r, i) =>
                      i === index ? { ...r, [key]: e.target.value } : r,
                    ),
                  })
                }
              />
            </label>
          ))}
          <Button
            type="button"
            onClick={() =>
              onChange({ ...value, recipients: recipients.filter((_, i) => i !== index) })
            }
          >
            Remove recipient {index + 1}
          </Button>
        </fieldset>
      ))}
      <Button
        type="button"
        onClick={() =>
          onChange({
            ...value,
            on: value.on ?? [],
            recipients: [
              ...recipients,
              { recipientId: newId('recipient'), channelId: '', targetRef: '' },
            ],
          })
        }
      >
        Add notification recipient
      </Button>
    </section>
  );
}

function RecoveryFields({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const backoff = Array.isArray(value.backoffSeconds) ? value.backoffSeconds : [];
  return (
    <section className="space-y-2">
      <h3>Recovery policy</h3>
      <p className="text-xs text-text-secondary">
        Automatic recovery requires a bound runtime policy. The server keeps its capability and
        authorization checks; unknown effects require reconciliation.
      </p>
      <label className="block text-sm">
        <input
          type="checkbox"
          checked={value.automatic === true}
          onChange={(e) => onChange({ ...value, automatic: e.target.checked })}
        />
        Automatic recovery
      </label>
      <label className="block text-sm">
        Maximum attempts
        <input
          required
          type="number"
          min={1}
          max={10}
          step={1}
          className={fieldClass}
          value={String(value.maxAttempts ?? 3)}
          onChange={(e) => onChange({ ...value, maxAttempts: Number(e.target.value) })}
        />
      </label>
      {backoff.map((seconds, index) => (
        <label key={index} className="block text-sm">
          Backoff {index + 1} (seconds)
          <input
            required
            className={fieldClass}
            value={String(seconds)}
            onChange={(e) =>
              onChange({
                ...value,
                backoffSeconds: backoff.map((s, i) => (i === index ? e.target.value : s)),
              })
            }
          />
        </label>
      ))}
      <Button
        type="button"
        onClick={() => onChange({ ...value, backoffSeconds: [...backoff, '0'] })}
      >
        Add recovery backoff
      </Button>
      <label className="block text-sm">
        Stable reset (seconds)
        <input
          className={fieldClass}
          value={String(value.stableResetSeconds ?? '')}
          onChange={(e) => onChange({ ...value, stableResetSeconds: e.target.value })}
        />
      </label>
    </section>
  );
}

function JobInputs({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const [name, setName] = useState('');
  const schema = obj(value.inputSchema),
    properties = obj(schema.properties);
  const set = (next: JsonObject) =>
    onChange({ ...value, inputSchema: { type: 'object', ...schema, properties: next } });
  return (
    <details>
      <summary>Job input fields</summary>
      <div className="space-y-3">
        {Object.entries(properties).map(([key, raw]) => (
          <fieldset key={key} aria-label={`Job input ${key}`} className="min-w-0 space-y-2">
            <legend>{key}</legend>
            <label className="block text-sm">
              {key} input type
              <select
                aria-label={`${key} input type`}
                className={fieldClass}
                value={String(obj(raw).type ?? 'string')}
                onChange={(e) =>
                  set({ ...properties, [key]: { ...obj(raw), type: e.target.value } })
                }
              >
                {['string', 'number', 'integer', 'boolean', 'object', 'array', 'null'].map(
                  (type) => (
                    <option key={type}>{type}</option>
                  ),
                )}
              </select>
            </label>
            <label className="block text-sm">
              <input
                type="checkbox"
                checked={Array.isArray(schema.required) && schema.required.includes(key)}
                onChange={(e) =>
                  onChange({
                    ...value,
                    inputSchema: {
                      ...schema,
                      required: e.target.checked
                        ? [...(Array.isArray(schema.required) ? schema.required : []), key]
                        : (Array.isArray(schema.required) ? schema.required : []).filter(
                            (item) => item !== key,
                          ),
                    },
                  })
                }
              />
              {key} required
            </label>
            <Button
              type="button"
              onClick={() => {
                const next = { ...properties };
                delete next[key];
                const defaults = { ...obj(value.arguments) };
                delete defaults[key];
                onChange({
                  ...value,
                  inputSchema: {
                    ...schema,
                    properties: next,
                    required: (Array.isArray(schema.required) ? schema.required : []).filter(
                      (item) => item !== key,
                    ),
                  },
                  arguments: defaults,
                });
              }}
            >
              Remove input {key}
            </Button>
            <label className="block text-sm">
              <input
                type="checkbox"
                checked={key in obj(value.arguments)}
                onChange={(e) => {
                  const defaults = { ...obj(value.arguments) };
                  if (e.target.checked) defaults[key] = initialLiteral(obj(raw));
                  else delete defaults[key];
                  onChange({ ...value, arguments: defaults });
                }}
              />
              {key} has default
            </label>
            {key in obj(value.arguments) && (
              <TypedValue
                key={String(obj(raw).type)}
                label={`${key} default`}
                schema={obj(raw)}
                value={obj(value.arguments)[key]}
                onChange={(next) =>
                  onChange({ ...value, arguments: { ...obj(value.arguments), [key]: next } })
                }
              />
            )}
          </fieldset>
        ))}
        <label className="block text-sm">
          New input name
          <input className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <Button
          type="button"
          disabled={!name.trim() || name in properties}
          onClick={() => {
            set({ ...properties, [name]: { type: 'string' } });
            setName('');
          }}
        >
          Add Job input
        </Button>
      </div>
    </details>
  );
}

function ScalarFilter({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const [name, setName] = useState('');
  return (
    <details>
      <summary>Event field filters</summary>
      <div className="space-y-3">
        {Object.entries(value).map(([key, item]) => (
          <fieldset key={key} className="min-w-0 space-y-2">
            <legend>{key}</legend>
            <TypedValue
              label={`Filter ${key}`}
              value={item}
              onChange={(next) => onChange({ ...value, [key]: next })}
            />
            <Button
              type="button"
              onClick={() =>
                onChange(Object.fromEntries(Object.entries(value).filter(([k]) => k !== key)))
              }
            >
              Remove filter {key}
            </Button>
          </fieldset>
        ))}
        <label className="block text-sm">
          Filter field
          <input className={fieldClass} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <Button
          type="button"
          disabled={!name.trim() || name in value}
          onClick={() => {
            onChange({ ...value, [name]: '' });
            setName('');
          }}
        >
          Add filter
        </Button>
      </div>
    </details>
  );
}
