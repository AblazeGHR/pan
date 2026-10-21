import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useUIStore } from '@/stores/uiStore';
import { SessionOperationFields } from './SessionOperationFields';
import type { Action, ActionGroup, InputSchema } from '@/types/actions';
import {
  EFFECT_LABELS,
  actionRoute,
  effectTone,
  executionLabel,
  buildGate,
  groupComposition,
  pluginIdFor,
  shortVersion,
  validateSchemaText,
  type ActionEditorState,
  type GroupEditorState,
  isEditorDirty,
  sessionOperationError,
} from '@/components/actions/actionsModel';
import {
  Badge,
  CopyButton,
  Field,
  IconTile,
  Kbd,
  Section,
  Segmented,
  Select,
  Switch,
  cx,
  inputClass,
} from '@/components/actions/ActionsUi';
import {
  ArrowLeft,
  Braces,
  Check,
  CircleAlert,
  CornerDownLeft,
  FileJson,
  Hammer,
  Info,
  Layers,
  LoaderCircle,
  PackageOpen,
  Plus,
  Search,
  ShieldAlert,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';

const EFFECT_HINTS: Record<Action['effect'], string> = {
  read: 'Read-only access. Safe to expose through a built Group.',
  write_local: 'May change local files or Pan state. Scope the input schema carefully.',
  write_external: 'May change systems outside this machine. Review before sharing.',
  high_risk: 'Requires an approval policy. Groups containing this Action cannot be built.',
};

function EditorHeader({
  listLabel,
  modeLabel,
  title,
  dirty,
  status,
  onBack,
}: {
  listLabel: string;
  modeLabel: string;
  title: string;
  dirty: boolean;
  status?: React.ReactNode;
  onBack: () => void;
}) {
  return (
    <header className="shrink-0 border-b border-border-default bg-bg-primary py-3 pl-10 pr-4 sm:px-6">
      <div className="mx-auto flex w-full max-w-[1120px] items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          aria-label={`Back to ${listLabel}`}
          className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border-default text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
        >
          <ArrowLeft size={15} />
        </button>
        <div className="min-w-0 flex-1">
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-[11px] text-text-tertiary">
            <button type="button" onClick={onBack} className="truncate hover:text-text-primary">
              {listLabel}
            </button>
            <span aria-hidden="true">/</span>
            <span className="truncate">{modeLabel}</span>
          </nav>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold leading-6 text-text-primary">{title}</h1>
            {status}
            {dirty && (
              <Badge tone="warning" dot>
                Unsaved
              </Badge>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

function EditorFooter({
  dirty,
  busy,
  saveLabel,
  onSave,
  onCancel,
  deleteLabel,
  onDelete,
  deleteDisabledReason,
  hint,
}: {
  dirty: boolean;
  busy: boolean;
  saveLabel: string;
  onSave: () => void;
  onCancel: () => void;
  deleteLabel?: string;
  onDelete?: () => void;
  deleteDisabledReason?: string;
  hint?: React.ReactNode;
}) {
  const toastCount = useUIStore((state) => state.toastQueue.length);
  // Global toasts are bottom-right. Lift the editor action row above the
  // current toast stack so Save/Cancel cannot be covered while feedback shows.
  const toastLift = toastCount ? { marginBottom: `${16 + toastCount * 72}px` } : undefined;
  return (
    <footer
      className="shrink-0 border-t border-border-default bg-bg-primary/95 px-4 py-3 backdrop-blur sm:px-6"
      style={toastLift}
    >
      <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center gap-2">
        {onDelete && deleteLabel && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy || Boolean(deleteDisabledReason)}
            title={deleteDisabledReason}
            className="text-danger hover:text-danger"
            onClick={onDelete}
          >
            <Trash2 size={13} /> {deleteLabel}
          </Button>
        )}
        <span className="hidden items-center gap-1 text-[11px] text-text-tertiary md:inline-flex">
          {dirty ? (
            <>
              <CircleAlert size={12} className="text-warning" /> Unsaved changes
            </>
          ) : (
            <>
              <Check size={12} className="text-success" /> No unsaved changes
            </>
          )}
          {hint && <span className="ml-1">· {hint}</span>}
          <span className="ml-1 flex items-center gap-1">
            · <Kbd>Ctrl</Kbd> <Kbd>S</Kbd> save · <Kbd>Esc</Kbd> back
          </span>
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" size="sm" variant="primary" onClick={onSave} disabled={busy}>
            {busy ? <LoaderCircle size={13} className="animate-spin" /> : <Check size={13} />}
            {busy ? 'Saving…' : saveLabel}
          </Button>
        </div>
      </div>
    </footer>
  );
}

/* ── Action editor ─────────────────────────────────────────────────────── */

export function ActionEditorPage({
  state,
  groups,
  busy,
  onChange,
  onSave,
  onCancel,
  onDelete,
  error = '',
}: {
  state: ActionEditorState;
  groups: ActionGroup[];
  busy: boolean;
  onChange: (next: ActionEditorState) => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
  error?: string;
}) {
  const [attempted, setAttempted] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(Boolean(state.schemaError));
  const [fieldName, setFieldName] = useState('');
  const [fieldError, setFieldError] = useState('');
  const action = state.draft;
  const usage = groups.filter((group) => group.action_ids.includes(action.id));
  const nameError = attempted && !action.name.trim() ? 'Name is required.' : '';
  const descriptionError = attempted && !action.description.trim() ? 'Description is required.' : '';
  const schemaError = state.schemaError;
  const operationError = attempted ? sessionOperationError(action) : '';

  useEffect(() => {
    if (state.schemaError) setAdvancedOpen(true);
  }, [state.schemaError]);

  function patchDraft(patch: Partial<Action>) {
    onChange({ ...state, draft: { ...action, ...patch } });
  }

  function patchCommand(patch: Partial<Extract<Action['execution'], { kind: 'command' }>>) {
    if (action.execution.kind !== 'command') return;
    onChange({ ...state, draft: { ...action, execution: { ...action.execution, ...patch } } });
  }

  function updateSchema(schema: InputSchema) {
    onChange({
      ...state,
      draft: { ...action, input_schema: schema },
      schemaText: JSON.stringify(schema, null, 2),
      schemaError: '',
    });
  }

  function changeSchemaText(text: string) {
    const result = validateSchemaText(text);
    onChange({
      ...state,
      schemaText: text,
      schemaError: result.error,
      draft: result.schema ? { ...action, input_schema: result.schema } : action,
    });
  }

  function addField() {
    const name = fieldName.trim();
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) {
      setFieldError('Use a letter first, then letters, digits or underscores.');
      return;
    }
    if (name in (action.input_schema.properties ?? {})) {
      setFieldError('That input field already exists.');
      return;
    }
    setFieldError('');
    setFieldName('');
    updateSchema({
      ...action.input_schema,
      properties: { ...action.input_schema.properties, [name]: { type: 'string' } },
      required: [...(action.input_schema.required ?? []), name],
    });
  }

  function submit() {
    setAttempted(true);
    if (busy || !action.name.trim() || !action.description.trim() || schemaError || sessionOperationError(action)) return;
    onSave();
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
      event.preventDefault();
      if (!document.querySelector('[role="dialog"]')) submit();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  function removeField(name: string) {
    const properties = { ...action.input_schema.properties };
    delete properties[name];
    updateSchema({
      ...action.input_schema,
      properties,
      required: (action.input_schema.required ?? []).filter((item) => item !== name),
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <EditorHeader
        listLabel="Actions"
        modeLabel={state.mode === 'new' ? 'New action' : 'Edit action'}
        title={action.name.trim() || (state.mode === 'new' ? 'New Action' : 'Untitled Action')}
        dirty={isEditorDirty(state)}
        status={
          action.enabled ? (
            <Badge tone="success" dot>
              Enabled
            </Badge>
          ) : (
            <Badge tone="muted" dot>
              Draft
            </Badge>
          )
        }
        onBack={onCancel}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[1120px] px-4 py-5 sm:px-6">
          {state.mode === 'new' && (
            <div className="mb-4 flex items-start gap-2 rounded-md border border-accent/25 bg-accent/5 px-3 py-2 text-xs text-text-secondary">
              <Info size={14} className="mt-0.5 shrink-0 text-accent" />
              <p>
                A stable ID is assigned automatically when you save. It never changes, so Groups keep
                their reference even if the name changes later.
              </p>
            </div>
          )}
          {error && (
            <div role="alert" className="mb-4 whitespace-pre-wrap break-words rounded-md border border-danger/35 bg-danger/5 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          )}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_288px]">
            <div className="space-y-4">
              <Section title="Identity" description="What this Action is called and when it can be built.">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Name" required htmlFor="action-name" error={nameError}>
                    <input
                      id="action-name"
                      className={inputClass}
                      value={action.name}
                      onChange={(event) => patchDraft({ name: event.target.value })}
                      placeholder="List Sessions"
                      autoComplete="off"
                    />
                  </Field>
                  <Field label="Description" required htmlFor="action-description" error={descriptionError}>
                    <textarea
                      id="action-description"
                      className={cx(inputClass, 'min-h-[58px] resize-y')}
                      rows={2}
                      value={action.description}
                      onChange={(event) => patchDraft({ description: event.target.value })}
                      placeholder="What the Action returns, in one sentence."
                    />
                  </Field>
                </div>
                <div className="mt-4 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-border-default bg-bg-primary px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary">{action.execution.kind === 'session_operation' ? 'Enable for automation' : 'Enable for build'}</p>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-text-tertiary">
                      {action.execution.kind === 'session_operation' ? 'Only enabled Actions can execute through a Trigger. Group build is unavailable.' : 'Disabled Actions stay in the registry but cannot be included in a release.'}
                    </p>
                  </div>
                  <Switch
                    label={action.execution.kind === 'session_operation' ? 'Enable for automation' : 'Enable for build'}
                    checked={action.enabled}
                    onChange={(enabled) => patchDraft({ enabled })}
                  />
                </div>
              </Section>

              <Section
                title="Behavior"
                description="The effect is disclosed to the Agent and enforced by the build gate."
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Effect" htmlFor="action-effect" hint={EFFECT_HINTS[action.effect]}>
                    <Select
                      id="action-effect"
                      label="Effect"
                      value={action.effect}
                      disabled={action.execution.kind === 'session_operation'}
                      onChange={(value) => patchDraft({ effect: value as Action['effect'] })}
                    >
                      <option value="read">Read</option>
                      <option value="write_local">Write local</option>
                      <option value="write_external">Write external</option>
                      <option value="high_risk">High risk — build blocked</option>
                    </Select>
                  </Field>
                  <Field
                    label="Output"
                    htmlFor="action-output"
                    hint="JSON is best for tools; Text is shown verbatim to the Agent."
                  >
                    <Select
                      id="action-output"
                      label="Output format"
                      value={action.output_format}
                      onChange={(value) => patchDraft({ output_format: value as Action['output_format'] })}
                    >
                      <option value="json">JSON</option>
                      <option value="text">Text</option>
                    </Select>
                  </Field>
                </div>
              </Section>

              <Section
                title="Input fields"
                description="Add scalar fields here. Use the advanced JSON schema for nested objects, defaults and constraints."
                actions={<Badge tone="muted">{Object.keys(action.input_schema.properties ?? {}).length} fields</Badge>}
              >
                {Object.keys(action.input_schema.properties ?? {}).length === 0 ? (
                  <p className="rounded-md border border-dashed border-border-default px-3 py-4 text-center text-xs text-text-tertiary">
                    No input fields. The tool will be called with an empty object.
                  </p>
                ) : (
                  <div className="overflow-hidden rounded-md border border-border-default">
                    <div className="hidden grid-cols-[minmax(0,1fr)_120px_90px_36px] gap-2 border-b border-border-default bg-bg-tertiary/40 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-text-tertiary sm:grid">
                      <span>Field</span>
                      <span>Type</span>
                      <span>Required</span>
                      <span />
                    </div>
                    {Object.entries(action.input_schema.properties ?? {}).map(([name, schema]) => (
                      <div
                        key={name}
                        className="grid grid-cols-[minmax(0,1fr)_36px] items-center gap-2 border-b border-border-muted px-3 py-2 last:border-0 sm:grid-cols-[minmax(0,1fr)_120px_90px_36px]"
                      >
                        <div className="min-w-0">
                          <code className="block truncate text-xs text-text-primary">{name}</code>
                          {schema.description && (
                            <p className="mt-0.5 hidden truncate text-[10px] text-text-tertiary sm:block">
                              {schema.description}
                            </p>
                          )}
                        </div>
                        <Select
                          label={`${name} type`}
                          value={schema.type ?? ''}
                          className="col-span-1 max-sm:col-start-1 max-sm:row-start-2"
                          onChange={(value) =>
                            updateSchema({
                              ...action.input_schema,
                              properties: {
                                ...action.input_schema.properties,
                                [name]: { ...schema, type: value },
                              },
                            })
                          }
                        >
                          <option value="" disabled>
                            Custom schema
                          </option>
                          {['string', 'integer', 'number', 'boolean', 'object', 'array'].map((type) => (
                            <option key={type} value={type}>
                              {type}
                            </option>
                          ))}
                        </Select>
                        <label className="flex items-center gap-1.5 text-[11px] text-text-secondary max-sm:col-start-1 max-sm:row-start-3">
                          <input
                            type="checkbox"
                            className="accent-accent"
                            checked={action.input_schema.required?.includes(name) ?? false}
                            onChange={(event) =>
                              updateSchema({
                                ...action.input_schema,
                                required: event.target.checked
                                  ? [...(action.input_schema.required ?? []), name]
                                  : (action.input_schema.required ?? []).filter((item) => item !== name),
                              })
                            }
                          />
                          Required
                        </label>
                        <button
                          type="button"
                          aria-label={`Remove ${name}`}
                          title={`Remove ${name}`}
                          className="col-start-2 row-start-1 flex size-7 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-danger sm:col-start-4"
                          onClick={() => removeField(name)}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1 basis-48">
                    <input
                      aria-label="New input field"
                      className={inputClass}
                      value={fieldName}
                      placeholder="Field name"
                      onChange={(event) => {
                        setFieldName(event.target.value);
                        setFieldError('');
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          addField();
                        }
                      }}
                    />
                    {fieldError && (
                      <p role="alert" className="mt-1 text-[11px] text-danger">
                        {fieldError}
                      </p>
                    )}
                  </div>
                  <Button type="button" size="sm" onClick={addField} disabled={!fieldName.trim()}>
                    <Plus size={13} /> Add field
                  </Button>
                </div>

                <div className="mt-4 rounded-md border border-border-default">
                  <button
                    type="button"
                    aria-expanded={advancedOpen}
                    onClick={() => setAdvancedOpen((value) => !value)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-text-secondary hover:text-text-primary"
                  >
                    <FileJson size={14} className="text-text-tertiary" />
                    Advanced JSON schema
                    {schemaError && <Badge tone="danger">Invalid</Badge>}
                    <span className="ml-auto text-text-tertiary">{advancedOpen ? 'Hide' : 'Show'}</span>
                  </button>
                  {advancedOpen && (
                    <div className="border-t border-border-default p-3">
                      <p className="mb-2 text-[11px] leading-relaxed text-text-tertiary">
                        This JSON replaces the field settings when saved. Nested properties, enums and
                        defaults are preserved exactly.
                      </p>
                      <textarea
                        aria-label="Input schema JSON"
                        className={cx(inputClass, 'min-h-40 font-mono text-xs leading-relaxed')}
                        spellCheck={false}
                        value={state.schemaText}
                        onChange={(event) => changeSchemaText(event.target.value)}
                      />
                      {schemaError && (
                        <p role="alert" className="mt-2 break-words rounded border border-danger/30 bg-danger/5 px-2 py-1.5 text-[11px] text-danger">
                          {schemaError}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </Section>

              <Section
                title="Execution"
                description="Internal API calls are proxied by Pan. Command arguments are passed literally — never interpreted by a shell."
                actions={
                  <Segmented
                    label="Execution kind"
                    value={action.execution.kind}
                    options={[
                      { value: 'api' as const, label: 'Internal API', icon: <Braces size={12} /> },
                      { value: 'command' as const, label: 'Command', icon: <Terminal size={12} /> },
                      { value: 'session_operation' as const, label: 'Session Operation', icon: <Layers size={12} /> },
                    ]}
                    onChange={(kind) =>
                      patchDraft({
                        ...(kind === 'session_operation' ? { effect: 'write_local' as const } : {}),
                        execution:
                          kind === 'api'
                            ? { kind: 'api', method: 'GET', path: '/api/sessions' }
                            : kind === 'session_operation'
                              ? { kind, operation: 'pause_everything', selector: { mode: 'explicit', ids: [] } }
                              : { kind: 'command', executable: '', args: [], cwd: '', timeout_sec: 30 },
                      })
                    }
                  />
                }
              >
                {action.execution.kind === 'api' ? (
                  <div className="grid gap-4 sm:grid-cols-[110px_minmax(0,1fr)]">
                    <Field label="Method" hint="Read-only routes only.">
                      <div className="flex h-[34px] items-center rounded-md border border-border-default bg-bg-tertiary/40 px-2.5">
                        <Badge tone="accent">GET</Badge>
                      </div>
                    </Field>
                    <Field
                      label="API route"
                      htmlFor="action-route"
                      hint="Choose a Pan route. Path parameters are filled from the input schema."
                    >
                      <input
                        id="action-route"
                        className={cx(inputClass, 'font-mono text-xs')}
                        list="action-routes"
                        value={action.execution.path}
                        onChange={(event) =>
                          patchDraft({ execution: { kind: 'api', method: 'GET', path: event.target.value } })
                        }
                      />
                      <datalist id="action-routes">
                        {[
                          '/api/sessions',
                          '/api/sessions/{session_id}/search',
                          '/api/models',
                          '/api/workspaces',
                          '/api/mcp/servers',
                          '/api/release/status',
                        ].map((path) => (
                          <option key={path} value={path} />
                        ))}
                      </datalist>
                    </Field>
                  </div>
                ) : action.execution.kind === 'session_operation' ? <>
                  <SessionOperationFields value={action.execution} onChange={(execution) => patchDraft({ execution })} />
                  {operationError && <p role="alert" className="mt-2 text-xs text-danger">{operationError}</p>}
                </> : (
                  <div className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Executable" required htmlFor="action-executable">
                        <input
                          id="action-executable"
                          className={cx(inputClass, 'font-mono text-xs')}
                          value={action.execution.executable}
                          placeholder="python"
                          onChange={(event) => patchCommand({ executable: event.target.value })}
                        />
                      </Field>
                      <Field label="Working directory" required htmlFor="action-cwd">
                        <input
                          id="action-cwd"
                          className={cx(inputClass, 'font-mono text-xs')}
                          value={action.execution.cwd}
                          placeholder="D:/project/Pan"
                          onChange={(event) => patchCommand({ cwd: event.target.value })}
                        />
                      </Field>
                    </div>
                    <Field
                      label="Arguments"
                      htmlFor="action-args"
                      hint="One argument per line. Use {field} for one complete argument; it is never shell-expanded."
                    >
                      <textarea
                        id="action-args"
                        className={cx(inputClass, 'min-h-24 font-mono text-xs leading-relaxed')}
                        value={action.execution.args.join('\n')}
                        onChange={(event) =>
                          patchDraft({
                            execution: {
                              ...action.execution,
                              args: event.target.value ? event.target.value.split('\n') : [],
                            } as Action['execution'],
                          })
                        }
                      />
                    </Field>
                    <Field label="Timeout" htmlFor="action-timeout" hint="1–30 seconds." className="max-w-40">
                      <div className="flex items-center gap-2">
                        <input
                          id="action-timeout"
                          type="number"
                          min={1}
                          max={30}
                          className={inputClass}
                          value={action.execution.timeout_sec ?? 30}
                          onChange={(event) => patchCommand({ timeout_sec: Number(event.target.value) })}
                        />
                        <span className="text-xs text-text-tertiary">sec</span>
                      </div>
                    </Field>
                  </div>
                )}
              </Section>
            </div>

            <aside className="space-y-4">
              <Section title="Identity">
                <dl className="space-y-3 text-xs">
                  <div>
                    <dt className="mb-1 text-[11px] text-text-tertiary">Stable Action ID</dt>
                    <dd className="break-all font-mono text-[11px] text-text-secondary">
                      {action.id || 'Assigned when you save'}
                    </dd>
                    {action.id && (
                      <dd className="mt-1">
                        <CopyButton value={action.id} />
                      </dd>
                    )}
                  </div>
                  <div>
                    <dt className="mb-1 text-[11px] text-text-tertiary">Used by</dt>
                    <dd className="flex flex-wrap gap-1.5">
                      {usage.length ? (
                        usage.map((group) => (
                          <Badge key={group.id} tone="neutral">
                            {group.name}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-text-tertiary">Not used by any Group yet.</span>
                      )}
                    </dd>
                  </div>
                </dl>
              </Section>

              <Section title="Build safety">
                <ul className="space-y-2.5 text-xs text-text-secondary">
                  <li className="flex items-start gap-2">
                    <span className={cx('mt-1 size-1.5 shrink-0 rounded-full', action.enabled ? 'bg-success' : 'bg-text-tertiary')} />
                    {action.execution.kind === 'session_operation' ? 'Automation only. Session Operation cannot be built into a Group.' : action.enabled ? 'Enabled Actions can be built.' : 'Draft Actions are excluded until enabled.'}
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-1 size-1.5 shrink-0 rounded-full bg-text-tertiary" />
                    Effect: <Badge tone={effectTone(action.effect)}>{EFFECT_LABELS[action.effect]}</Badge>
                  </li>
                  {action.effect === 'high_risk' && (
                    <li className="flex items-start gap-2 rounded border border-danger/25 bg-danger/5 p-2 text-danger">
                      <ShieldAlert size={13} className="mt-0.5 shrink-0" />
                      Any Group containing this Action is blocked from building.
                    </li>
                  )}
                </ul>
              </Section>

              <Section title="Agent contract">
                <p className="text-xs leading-relaxed text-text-secondary">
                  {action.execution.kind === 'session_operation' ? 'Triggers reference this Action directly. Saving changes affects later execution starts without a Group rebuild. Existing Group releases and loaded pins remain unchanged.' : 'When a built Group exposes this Action, the Agent receives the name, description, input schema and effect. Changes require saving and rebuilding the Group.'}
                </p>
                <dl className="mt-3 space-y-2 text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <dt className="text-text-tertiary">Execution</dt>
                    <dd className="text-text-secondary">{executionLabel(action)}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <dt className="text-text-tertiary">Route</dt>
                    <dd className="min-w-0 truncate font-mono text-text-secondary" title={actionRoute(action)}>
                      {actionRoute(action)}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <dt className="text-text-tertiary">Output</dt>
                    <dd className="text-text-secondary">{action.output_format.toUpperCase()}</dd>
                  </div>
                </dl>
              </Section>
            </aside>
          </div>
        </div>
      </div>
      <EditorFooter
        dirty={isEditorDirty(state)}
        busy={busy}
        saveLabel={state.mode === 'new' ? 'Create Action' : 'Save Action'}
        onSave={submit}
        onCancel={onCancel}
        deleteLabel={state.mode === 'edit' ? 'Delete Action' : undefined}
        onDelete={state.mode === 'edit' ? onDelete : undefined}
        deleteDisabledReason={
          usage.length
            ? `Referenced by ${usage.map((group) => group.name).join(', ')}. Remove it from those Groups first.`
            : undefined
        }
        hint={<span className="inline-flex items-center gap-1"><CornerDownLeft size={11} /> Enter adds an input field</span>}
      />
    </div>
  );
}

/* ── Group editor ──────────────────────────────────────────────────────── */

export function GroupEditorPage({
  state,
  liveGroup,
  actions,
  busy,
  onChange,
  onSave,
  onCancel,
  onDelete,
  onBuild,
  onUnbuild,
  onUseInSession,
  error = '',
}: {
  state: GroupEditorState;
  liveGroup: ActionGroup | null;
  actions: Action[];
  busy: boolean;
  onChange: (next: GroupEditorState) => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onBuild: () => void;
  onUnbuild: () => void;
  onUseInSession: () => void;
  error?: string;
}) {
  const [actionSearch, setActionSearch] = useState('');
  const gate = useMemo(
    () => buildGate(state.draft.action_ids, actions),
    [state.draft.action_ids, actions],
  );
  const build = liveGroup?.build;
  const dirty = serializeGroup(state.draft) !== state.snapshot;
  const stale =
    Boolean(build) &&
    [...(build?.action_ids ?? [])].sort().join('|') !== [...state.draft.action_ids].sort().join('|');
  const filteredActions = actions.filter((action) => {
    const query = actionSearch.trim().toLowerCase();
    return !query || action.name.toLowerCase().includes(query) || action.id.toLowerCase().includes(query);
  });
  const selectedActions = state.draft.action_ids
    .map((id) => actions.find((action) => action.id === id))
    .filter((action): action is Action => Boolean(action));

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
      event.preventDefault();
      if (!document.querySelector('[role="dialog"]')) onSave();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  function toggleAction(actionId: string, selected: boolean) {
    onChange({
      ...state,
      draft: {
        ...state.draft,
        action_ids: selected
          ? [...state.draft.action_ids, actionId]
          : state.draft.action_ids.filter((id) => id !== actionId),
      },
    });
  }

  const canBuild = Boolean(liveGroup) && !dirty && !gate && !busy;
  const canUnbuild = Boolean(liveGroup?.build) && !dirty && !busy && !(liveGroup?.blockers.length);
  const deleteReason = build
    ? 'Unbuild the Group before deleting it.'
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <EditorHeader
        listLabel="Groups"
        modeLabel={state.mode === 'new' ? 'New group' : 'Edit group'}
        title={state.draft.name.trim() || (state.mode === 'new' ? 'New Group' : 'Untitled Group')}
        dirty={dirty}
        status={
          !liveGroup ? (
            <Badge tone="neutral">Not saved</Badge>
          ) : build ? (
            <Badge tone="success" dot>
              Registered
            </Badge>
          ) : (
            <Badge tone="muted" dot>
              Unbuilt
            </Badge>
          )
        }
        onBack={onCancel}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[1120px] px-4 py-5 sm:px-6">
          {error && (
            <div role="alert" className="mb-4 whitespace-pre-wrap break-words rounded-md border border-danger/35 bg-danger/5 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          )}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="space-y-4">
              <Section
                title="Group identity"
                description="A Group is the unit you build and bind to a Session. Its plugin ID is derived from the stable Group ID."
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Group name" required htmlFor="group-name">
                    <input
                      id="group-name"
                      className={inputClass}
                      value={state.draft.name}
                      onChange={(event) =>
                        onChange({ ...state, draft: { ...state.draft, name: event.target.value } })
                      }
                      placeholder="Browser Tools"
                      autoComplete="off"
                    />
                  </Field>
                  <Field label="Group ID" hint={state.mode === 'new' ? 'Assigned when you save.' : 'Stable; used to derive the plugin ID.'}>
                    <div className="flex h-[34px] items-center gap-2 rounded-md border border-border-default bg-bg-tertiary/30 px-2.5">
                      <code className="min-w-0 flex-1 truncate text-[11px] text-text-secondary">
                        {state.draft.id || 'Assigned on save'}
                      </code>
                      {state.draft.id && <CopyButton value={state.draft.id} />}
                    </div>
                  </Field>
                </div>
              </Section>

              <Section
                title="Shared Actions"
                description="Only enabled, non-high-risk Actions can be released. Changes require saving and rebuilding."
                actions={<Badge tone="accent">{state.draft.action_ids.length} selected</Badge>}
              >
                <div className="relative mb-3">
                  <Search
                    size={13}
                    className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-tertiary"
                  />
                  <input
                    className={cx(inputClass, 'pl-8 text-xs')}
                    value={actionSearch}
                    onChange={(event) => setActionSearch(event.target.value)}
                    placeholder="Search Actions by name or ID"
                    aria-label="Search Actions"
                  />
                </div>
                <div className="max-h-[340px] overflow-auto rounded-md border border-border-default">
                  {filteredActions.length === 0 ? (
                    <p className="px-3 py-6 text-center text-xs text-text-tertiary">
                      No Actions match “{actionSearch}”.
                    </p>
                  ) : (
                    filteredActions.map((action) => {
                      const selected = state.draft.action_ids.includes(action.id);
                      return (
                        <label
                          key={action.id}
                          className={cx(
                            'flex cursor-pointer items-start gap-2.5 border-b border-border-muted px-3 py-2.5 transition-colors last:border-0',
                            selected ? 'bg-accent/5' : 'hover:bg-bg-tertiary/50',
                          )}
                        >
                          <input
                            type="checkbox"
                            className="mt-1 accent-accent"
                            checked={selected}
                            onChange={(event) => toggleAction(action.id, event.target.checked)}
                          />
                          <IconTile tone={action.execution.kind === 'command' ? 'neutral' : 'accent'}>
                            {action.execution.kind === 'api' ? <Braces size={14} /> : <Terminal size={14} />}
                          </IconTile>
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-1.5">
                              <span className="truncate text-sm font-medium text-text-primary">{action.name}</span>
                              {action.enabled ? (
                                <Badge tone="success" dot>
                                  Enabled
                                </Badge>
                              ) : (
                                <Badge tone="muted">Draft</Badge>
                              )}
                              {action.effect === 'high_risk' && <Badge tone="danger">High risk</Badge>}
                            </span>
                            <span className="mt-0.5 block truncate text-[11px] text-text-tertiary">
                              {executionLabel(action)} · {actionRoute(action)}
                            </span>
                          </span>
                          <span className="hidden shrink-0 sm:block">
                            <Badge tone={effectTone(action.effect)}>{EFFECT_LABELS[action.effect]}</Badge>
                          </span>
                        </label>
                      );
                    })
                  )}
                </div>
                <p className="mt-2 text-[11px] text-text-tertiary">
                  {selectedActions.length === 0
                    ? 'Select at least one Action to build this Group.'
                    : `Exposes ${selectedActions.length} ${selectedActions.length === 1 ? 'Action' : 'Actions'} when built.`}
                </p>
              </Section>

              <Section
                title="Sessions using this Group"
                description="A Session can enable the built Group in one of three modes. Loaded releases stay pinned until the Worker restarts."
                actions={
                  build && !dirty ? (
                    <Button type="button" size="sm" onClick={onUseInSession}>
                      <Plus size={13} /> Use in Session
                    </Button>
                  ) : undefined
                }
              >
                {!liveGroup || liveGroup.sessions.length === 0 ? (
                  <p className="rounded-md border border-dashed border-border-default px-3 py-5 text-center text-xs text-text-tertiary">
                    {build
                      ? 'No Sessions use this Group yet. Use it in a Session to add a binding.'
                      : 'Build the Group, then use it in a Session.'}
                  </p>
                ) : (
                  <ul className="divide-y divide-border-muted overflow-hidden rounded-md border border-border-default">
                    {liveGroup.sessions.map((session) => (
                      <li key={session.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-text-primary">{session.name}</p>
                          <p className="truncate font-mono text-[10px] text-text-tertiary">{session.id}</p>
                        </div>
                        <Badge tone={session.enabled ? 'success' : 'muted'} dot>
                          {session.enabled ? 'Enabled' : 'Disabled'}
                        </Badge>
                        <Badge tone="neutral">{MODE_LABEL[session.mode]}</Badge>
                        <span className="w-full text-right text-[10px] text-text-tertiary sm:w-28">
                          {session.loadedVersion ? (
                            <span className="font-mono" title={session.loadedVersion}>
                              loaded {shortVersion(session.loadedVersion)}
                            </span>
                          ) : (
                            <span className="text-warning">applies next start</span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            </div>

            <aside className="space-y-4">
              <Section title="Release">
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {build ? (
                      <Badge tone="success" dot>
                        Registered
                      </Badge>
                    ) : (
                      <Badge tone="muted" dot>
                        Unbuilt
                      </Badge>
                    )}
                    {build && (
                      <span className="font-mono text-[10px] text-text-tertiary" title={build.version}>
                        v {shortVersion(build.version)}
                      </span>
                    )}
                  </div>
                  <dl className="space-y-2 text-[11px]">
                    <div>
                      <dt className="text-text-tertiary">Plugin ID</dt>
                      <dd className="mt-0.5 break-all font-mono text-text-secondary">
                        {build?.plugin_id ?? (state.draft.id ? pluginIdFor(state.draft.id) : 'Assigned after save')}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <dt className="text-text-tertiary">Exposed</dt>
                      <dd className="text-text-secondary">
                        {build?.action_ids.length ?? state.draft.action_ids.length} Actions
                      </dd>
                    </div>
                  </dl>
                  {stale && (
                    <div className="flex items-start gap-2 rounded border border-warning/30 bg-warning/5 p-2 text-[11px] leading-relaxed text-warning">
                      <CircleAlert size={13} className="mt-0.5 shrink-0" />
                      Selected Actions changed since this build. Rebuild to publish a new version.
                    </div>
                  )}
                  {dirty && build && !stale && (
                    <p className="text-[11px] leading-relaxed text-text-tertiary">
                      Unsaved changes are not part of the registered release.
                    </p>
                  )}
                  <div className="flex flex-col gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant={build ? 'secondary' : 'primary'}
                      disabled={!canBuild}
                      title={
                        !liveGroup
                          ? 'Save the Group before building.'
                          : dirty
                            ? 'Save changes before building.'
                            : gate?.message
                      }
                      onClick={onBuild}
                    >
                      <Hammer size={13} /> {build ? 'Rebuild Group' : 'Build Group'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={!canUnbuild}
                      title={
                        liveGroup?.blockers.length
                          ? 'Unbuild is blocked by running Sessions.'
                          : dirty
                            ? 'Save changes before unbuilding.'
                            : undefined
                      }
                      onClick={onUnbuild}
                    >
                      <PackageOpen size={13} /> Unbuild Group
                    </Button>
                  </div>
                  <p className="text-[10px] leading-relaxed text-text-tertiary">
                    Build approves the current execution definitions. Running Workers keep their loaded
                    release until restart.
                  </p>
                </div>
              </Section>

              <Section title="Build gates">
                <ul className="space-y-2 text-[11px]">
                  <GateRow ok={state.draft.action_ids.length > 0} label="At least one Action selected" />
                  <GateRow ok={!gate || gate.kind !== 'disabled'} label="All selected Actions enabled" />
                  <GateRow ok={!selectedActions.some((action) => action.effect === 'high_risk')} label="No high-risk Actions" />
                  <GateRow ok={!dirty} label="Changes saved" />
                </ul>
                {gate && (
                  <div className="mt-3 flex items-start gap-2 rounded border border-warning/30 bg-warning/5 p-2 text-[11px] leading-relaxed text-warning">
                    <CircleAlert size={13} className="mt-0.5 shrink-0" />
                    {gate.message}
                  </div>
                )}
              </Section>

              {liveGroup && liveGroup.blockers.length > 0 && (
                <Section title="Unbuild blocked">
                  <div className="flex items-start gap-2 text-[11px] leading-relaxed text-danger">
                    <ShieldAlert size={13} className="mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium">Running Sessions have this Group enabled.</p>
                      <ul className="mt-1.5 space-y-1">
                        {liveGroup.blockers.map((blocker) => (
                          <li key={blocker.id} className="break-all">
                            {blocker.name} <span className="font-mono text-[10px] opacity-80">({blocker.id})</span>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1.5 text-text-tertiary">Stop or disable the Group for these Sessions first.</p>
                    </div>
                  </div>
                </Section>
              )}

              <Section title="Composition">
                <p className="text-[11px] leading-relaxed text-text-secondary">
                  {liveGroup ? groupComposition(liveGroup, actions) || 'No Actions' : 'Save to preview composition.'}
                </p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {selectedActions.slice(0, 6).map((action) => (
                    <Badge key={action.id} tone={action.enabled ? 'neutral' : 'muted'}>
                      {action.name}
                    </Badge>
                  ))}
                  {selectedActions.length > 6 && <Badge tone="muted">+{selectedActions.length - 6}</Badge>}
                </div>
              </Section>
            </aside>
          </div>
        </div>
      </div>
      <EditorFooter
        dirty={dirty}
        busy={busy}
        saveLabel={state.mode === 'new' ? 'Create Group' : 'Save Group'}
        onSave={onSave}
        onCancel={onCancel}
        deleteLabel={state.mode === 'edit' ? 'Delete Group' : undefined}
        onDelete={state.mode === 'edit' ? onDelete : undefined}
        deleteDisabledReason={deleteReason}
        hint={<span className="inline-flex items-center gap-1"><Layers size={11} /> {selectedActions.length} Actions selected</span>}
      />
    </div>
  );
}

function GateRow({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={cx('flex items-start gap-2', ok ? 'text-text-secondary' : 'text-warning')}>
      <span
        className={cx(
          'mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border',
          ok ? 'border-success/30 bg-success/10 text-success' : 'border-warning/30 bg-warning/10 text-warning',
        )}
      >
        {ok ? <Check size={9} /> : <X size={9} />}
      </span>
      {label}
    </li>
  );
}

function serializeGroup(draft: GroupEditorState['draft']): string {
  return JSON.stringify(draft);
}

const MODE_LABEL: Record<ActionGroup['sessions'][number]['mode'], string> = {
  standalone: 'Standalone MCP',
  pan_native: 'Pan native',
  gateway: 'Discover / call',
};
