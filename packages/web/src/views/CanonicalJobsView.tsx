import { TriggerTarget } from '@/components/automation/TriggerTarget';
import { wsClient } from '@/services/ws';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiRequestError } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { useAutomationAuth } from '@/stores/automationStore';
import {
  canonicalError,
  canonicalErrorMessages,
  canonicalJobs,
  definitionOf,
  definitionSchemas,
  revisionOf,
  runOperations,
} from '@/services/canonicalJobs';
import type {
  CanonicalKind,
  CanonicalObject,
  CanonicalReceipt,
  CanonicalRun,
  JsonObject,
  RunOperation,
} from '@/types/canonicalJobs';
import { newId, type EventCreator } from '@/types/automationJobs';
import { useAutomationDraft } from '@/components/automation/DraftBoundary';
import {
  CanonicalDefinitionFields,
  canonicalFormErrors,
} from '@/components/automation/CanonicalDefinitionFields';
import { TimeTemplateFields } from '@/components/automation/TimeTemplateFields';
import { SimpleTemplateForm } from '@/components/automation/SimpleTemplateForm';
import { PreservedDataPanel } from '@/components/jobs/JobMaintenanceGate';
import {
  EmptyState,
  HeaderLink,
  Notice,
  PageHeader,
  PageShell,
  StateBadge,
} from '@/components/automation/display';

const kinds: CanonicalKind[] = ['jobs', 'triggers', 'event-creators', 'job-templates'];
const labels: Record<CanonicalKind, string> = {
  jobs: 'Jobs',
  triggers: 'Triggers',
  'event-creators': 'EventCreators',
  'job-templates': 'JobTemplates',
};
const hints: Record<CanonicalKind, string> = {
  jobs: 'Execution steps, retention and run history',
  triggers: 'Event subscriptions and target Jobs',
  'event-creators': 'Time sources, rules and cursors',
  'job-templates': 'Reusable Job compositions',
};
const field =
  'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-50';
const card = 'min-w-0 rounded border border-border-default bg-bg-secondary/40 p-3';
const detailsClass =
  'rounded-lg border border-border-muted bg-bg-primary/40 px-3 py-2 text-sm';
const summaryClass = 'cursor-pointer font-medium text-text-secondary hover:text-text-primary';
const examples: Record<CanonicalKind, JsonObject> = {
  jobs: {
    name: '',
    steps: [
      {
        stepId: 'step1',
        actionRef: { id: '', revision: 1, digest: '' },
        arguments: {},
        completion: { contract: 'effect.confirmed' },
      },
    ],
    retention: { policyRef: 'default' },
  },
  triggers: {
    name: '',
    eventTypes: ['scheduler.time.v2'],
    creatorSelector: { mode: 'ids', ids: [''] },
    targetJobId: '',
  },
  'event-creators': {
    name: '',
    providerRef: { type: 'time', id: 'scheduler', releaseDigest: '', generation: 1 },
    configSchemaRef: { id: '', revision: 1, digest: '' },
    config: {},
    eventTypes: ['scheduler.time.v2'],
    requiredCapabilities: [],
  },
  'job-templates': {
    name: '',
    jobDefinition: { name: '', steps: [], retention: { policyRef: 'default' } },
    composition: { creators: [], triggers: [] },
  },
};
function previewObject(text: string): JsonObject {
  try {
    return objectJson(text);
  } catch {
    return {};
  }
}
function objectJson(text: string): JsonObject {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Enter a JSON object.');
  return value as JsonObject;
}
type Pending = {
  operation: string;
  requestKey: string;
  receipt?: CanonicalReceipt;
  rejected?: boolean;
};
const unresolved = (value: Pending | null) =>
  !!value &&
  !value.rejected &&
  (!value.receipt || ['accepted', 'pending', 'unknown'].includes(value.receipt.status));
function duplicateName(name: string, names: string[]) {
  const used = new Set(names.map((value) => value.trim().toLowerCase()));
  const root = `${name.trim() || 'Untitled'} copy`;
  let candidate = root;
  let index = 2;
  while (used.has(candidate.toLowerCase())) {
    candidate = `${root} ${index}`;
    index += 1;
  }
  return candidate;
}

/** All state and requests belong to this canonical page, isolated from legacy stores. */
export default function CanonicalJobsView() {
  const token = useAutomationAuth((s) => s.token || (s.authenticated ? 'cookie' : ''));
  // Changing authorization remounts the workspace: late responses cannot expose an earlier user's data.
  return (
    <PageShell label="Canonical management">
      <div className="space-y-5">
        <PageHeader
          eyebrow="Pan / Automation"
          title="Jobs"
          description="Definitions and run management"
          actions={<HeaderLink to="/automation">Automation</HeaderLink>}
        />
        <JobUserAuthorization />
        {token ? (
          <Workspace key={token} />
        ) : (
          <Notice role="status" label="Canonical login required">
            <p>Sign in to load Jobs.</p>
          </Notice>
        )}
        {token && (
          <div className="rounded-xl border border-border-muted bg-bg-secondary/40 p-4 text-sm [&_article]:rounded-lg [&_article]:border-border-muted [&_article]:p-3 [&_h2]:text-base [&_h2]:font-semibold">
            <PreservedDataPanel key={token} authorization={token} />
          </div>
        )}
      </div>
    </PageShell>
  );
}
function Workspace() {
  const [kind, setKind] = useState<CanonicalKind>('jobs');
  return (
    <>
      <nav
        aria-label="Canonical sections"
        className="flex w-fit max-w-full min-w-0 flex-wrap gap-1 rounded-xl border border-border-default bg-bg-secondary p-1"
      >
        {kinds.map((value) => (
          <button
            type="button"
            key={value}
            aria-pressed={kind === value}
            onClick={() => setKind(value)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
              kind === value
                ? 'bg-bg-tertiary text-text-primary shadow-card'
                : 'text-text-secondary hover:bg-bg-tertiary/60 hover:text-text-primary'
            }`}
          >
            {labels[value]}
          </button>
        ))}
      </nav>
      <Collection key={kind} kind={kind} />
    </>
  );
}
export function CanonicalCollection({
  kind,
  focusId,
  onChanged,
  sources = [],
  jobs = [],
  reviewSourceRefs = false,
  refreshToken,
}: {
  kind: CanonicalKind;
  focusId?: string;
  onChanged?: () => Promise<void>;
  sources?: EventCreator[];
  jobs?: CanonicalObject[];
  reviewSourceRefs?: boolean;
  refreshToken?: CanonicalObject[];
}) {
  const [items, setItems] = useState<CanonicalObject[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState<{
    item: CanonicalObject | null;
    name: string;
    json: string;
  } | null>(null);
  const [creatorRevisions, setCreatorRevisions] = useState<Record<string, number>>({});
  const [conflict, setConflict] = useState(false);
  const [structuredEdited, setStructuredEdited] = useState(false);
  const [advancedEditing, setAdvancedEditing] = useState(false);
  const [instantiating, setInstantiating] = useState<CanonicalObject | null>(null);
  const [simpleKind, setSimpleKind] = useState<'assign' | 'shell' | null>(null);
  const [activateTemplate, setActivateTemplate] = useState(false);
  const [enabling, setEnabling] = useState<CanonicalObject | null>(null);
  const [enableReviewed, setEnableReviewed] = useState(false);
  const [retiring, setRetiring] = useState<CanonicalObject | null>(null);
  const [selectedJob, setSelectedJob] = useState<CanonicalObject | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const busy = working || unresolved(pending);
  useAutomationDraft(!!editor || !!enabling || !!simpleKind, busy);
  const alive = useRef(true);
  const mutationActive = useRef(false);
  const load = useCallback(
    async (next?: string | null, signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const page = await canonicalJobs.list(kind, next, signal);
        if (alive.current && !signal?.aborted) {
          setItems((old) => (next ? [...old, ...page.items] : page.items));
          setCursor(page.nextCursor);
        }
      } catch (e) {
        if (alive.current && !signal?.aborted) setError(canonicalError(e));
      } finally {
        if (alive.current && !signal?.aborted) setLoading(false);
      }
    },
    [kind],
  );
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(null, controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load, refreshToken]);
  useEffect(
    () =>
      wsClient.on('canonical.changed', () => {
        if (!mutationActive.current) void load();
      }),
    [load],
  );
  async function mutate(operation: string, task: (key: string) => Promise<CanonicalReceipt>) {
    if (busy || mutationActive.current) return;
    mutationActive.current = true;
    const requestKey = newId('operation');
    setBusy(true);
    setError('');
    setPending({ operation, requestKey });
    try {
      const receipt = await task(requestKey);
      if (!alive.current) return;
      setPending({ operation, requestKey, receipt });
      if (receipt.status === 'completed') {
        setSimpleKind(null);
        setInstantiating(null);
        setEditor(null);
        setRetiring(null);
        setEnabling(null);
        setConflict(false);
      }
      await load();
      await onChanged?.();
    } catch (e) {
      if (alive.current) {
        setError(canonicalError(e));
        if (e instanceof ApiRequestError && e.status === 409) setConflict(true);
        if (e instanceof ApiRequestError && e.status < 500)
          setPending({ operation, requestKey, rejected: true });
      }
    } finally {
      mutationActive.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-col gap-3 border-b border-border-muted pb-4 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="min-w-0 sm:mr-auto">
          <h2 className="text-lg font-semibold">{labels[kind]}</h2>
          <p className="mt-0.5 text-sm text-text-secondary">{hints[kind]}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={loading || busy} onClick={() => void load()}>
            Refresh list
          </Button>
          <Button
            type="button"
            size="sm"
            variant="primary"
            disabled={busy || !!editor || !!enabling || !!simpleKind}
            onClick={() => {
              const definition = { ...examples[kind] };
              delete definition.name;
              setAdvancedEditing(false);
              setStructuredEdited(false);
              setConflict(false);
              setCreatorRevisions({});
              setEditor({ item: null, name: '', json: JSON.stringify(definition, null, 2) });
            }}
          >
            Create {labels[kind]}
          </Button>
          {kind === 'job-templates' &&
            (['assign', 'shell'] as const).map((value) => (
              <Button
                key={value}
                size="sm"
                disabled={busy || !!editor || !!simpleKind}
                onClick={() => setSimpleKind(value)}
              >
                {value === 'assign' ? 'New assign Template' : 'New shell Template'}
              </Button>
            ))}
        </div>
      </div>
      {loading && (
        <div role="status" className="space-y-3">
          <p className="text-sm text-text-secondary">Loading...</p>
          {!items.length && (
            <div aria-hidden className="grid gap-3 lg:grid-cols-2">
              {[0, 1].map((index) => (
                <div
                  key={index}
                  className="h-32 animate-pulse rounded-xl border border-border-muted bg-bg-secondary"
                />
              ))}
            </div>
          )}
        </div>
      )}
      {error && (
        <Notice tone="danger" role="alert">
          <p className="break-words">{error}</p>
        </Notice>
      )}
      {pending && (
        <OperationStatus
          pending={pending}
          onChange={(value) => {
            setPending(value);
            if (value.receipt?.status === 'completed') {
              setInstantiating(null);
              setError('');
              setEditor(null);
              setSimpleKind(null);
              setRetiring(null);
              setEnabling(null);
              void load();
            }
          }}
        />
      )}
      {simpleKind && (
        <SimpleTemplateForm
          key={simpleKind}
          kind={simpleKind}
          busy={busy}
          onCancel={() => setSimpleKind(null)}
          onSubmit={(definition, form) =>
            void mutate('create', (key) => canonicalJobs.writeFormTemplate(definition, form, key))
          }
        />
      )}
      {editor && (
        <form
          aria-label={`Edit ${labels[kind]}`}
          className={`${card} space-y-3`}
          onSubmit={(e) => {
            e.preventDefault();
            try {
              const definition: JsonObject = { ...objectJson(editor.json), name: editor.name };
              if (kind === 'jobs' && !editor.item && !editor.name.trim()) delete definition.name;
              if (structuredEdited && canonicalFormErrors(kind, definition).length)
                throw new Error(canonicalFormErrors(kind, definition).join(' '));
              void mutate(editor.item ? 'update' : 'create', (key) =>
                canonicalJobs.write(
                  kind,
                  editor.item,
                  definition,
                  key,
                  undefined,
                  kind === 'triggers' && reviewSourceRefs ? creatorRevisions : undefined,
                ),
              );
            } catch (failure) {
              setError(canonicalError(failure));
            }
          }}
        >
          <div className="border-b border-border-muted pb-3">
            <h3 className="text-base font-semibold">
              {editor.item ? `Edit ${labels[kind]}` : `Create ${labels[kind]}`}
            </h3>
            <p className="mt-0.5 text-xs text-text-secondary">
              Save uses the definition revision observed when opened {editor.item ? revisionOf(editor.item) : 0}.
            </p>
          </div>
          <label className="block text-sm">
            Name
            <input
              className={field}
              disabled={busy}
              required={kind !== 'jobs' || !!editor.item}
              maxLength={200}
              value={editor.name}
              onChange={(e) => setEditor({ ...editor, name: e.target.value })}
            />
          </label>
          {(kind === 'jobs' || kind === 'triggers') && (
            <fieldset
              hidden={advancedEditing}
              disabled={busy || advancedEditing}
              className="min-w-0"
            >
              {(() => {
                try {
                  return (
                    <CanonicalDefinitionFields
                      kind={kind}
                      value={objectJson(editor.json)}
                      targetJob={jobs.find((job) => job.id === (objectJson(editor.json).targetJobId ?? (objectJson(editor.json).target as JsonObject | undefined)?.jobId))}
                      onChange={(value) => {
                        setStructuredEdited(true);
                        setEditor({ ...editor, json: JSON.stringify(value, null, 2) });
                      }}
                    />
                  );
                } catch {
                  return (
                    <p role="status">
                      Fix the advanced definition before using the form. Your draft is retained.
                    </p>
                  );
                }
              })()}
            </fieldset>
          )}
          {kind === 'job-templates' &&
            !!previewObject(editor.json).jobDefinition &&
            !!previewObject(editor.json).composition && (
              <fieldset hidden={advancedEditing} disabled={busy || advancedEditing}>
                <TimeTemplateFields
                  value={previewObject(editor.json)}
                  onChange={(value) => {
                    setStructuredEdited(true);
                    setEditor({ ...editor, json: JSON.stringify(value, null, 2) });
                  }}
                />
              </fieldset>
            )}
          <details className={detailsClass}>
            <summary className={summaryClass}>Advanced definition JSON</summary>
            <label className="mt-2 block text-sm">
              Definition JSON (name is entered separately)
              <textarea
                disabled={busy}
                spellCheck={false}
                className={`${field} h-72 font-mono`}
                value={editor.json}
                onChange={(e) => {
                  setAdvancedEditing(true);
                  setStructuredEdited(false);
                  setEditor({ ...editor, json: e.target.value });
                }}
              />
            </label>
            {advancedEditing && (
              <Button
                type="button"
                disabled={busy}
                onClick={() => {
                  try {
                    objectJson(editor.json);
                    setAdvancedEditing(false);
                  } catch (failure) {
                    setError(canonicalError(failure));
                  }
                }}
              >
                Use structured form
              </Button>
            )}
          </details>
          <details className={detailsClass}>
            <summary className={summaryClass}>Definition fields and constraints</summary>
            <pre className="mt-2 max-h-72 max-w-full overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs text-text-secondary">
              {JSON.stringify(definitionSchemas[kind], null, 2)}
            </pre>
          </details>
          {conflict && editor.item && (
            <section
              aria-label="Compare canonical configuration"
              className="space-y-3 rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm text-warning"
            >
              <p>
                The draft is retained. Compare the current server definition before explicitly
                rebasing.
              </p>
              <Button
                type="button"
                disabled={busy}
                onClick={() => {
                  const id = editor.item!.id;
                  void canonicalJobs
                    .get(kind, id)
                    .then((current) => {
                      if (!alive.current) return;
                      setItems((old) => old.map((item) => (item.id === id ? current : item)));
                    })
                    .catch((failure: unknown) => setError(canonicalError(failure)));
                }}
              >
                Compare server definition
              </Button>
              <pre className="max-h-72 max-w-full overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs text-text-secondary">
                {JSON.stringify(
                  items.find((item) => item.id === editor.item?.id),
                  null,
                  2,
                )}
              </pre>
              <Button
                type="button"
                disabled={busy}
                onClick={() => {
                  const current = items.find((item) => item.id === editor.item?.id);
                  if (current) {
                    setEditor({ ...editor, item: current });
                    setConflict(false);
                    setPending(null);
                  }
                }}
              >
                Keep draft against server revision
              </Button>
              <Button
                type="button"
                disabled={busy}
                onClick={() => {
                  const current = items.find((item) => item.id === editor.item?.id);
                  if (current) {
                    const value = definitionOf(kind, current);
                    delete value.name;
                    setEditor({
                      item: current,
                      name: current.name,
                      json: JSON.stringify(value, null, 2),
                    });
                    setConflict(false);
                    setPending(null);
                  }
                }}
              >
                Load server definition
              </Button>
            </section>
          )}
          {kind === 'jobs' && <ActionCatalog />}
          {kind === 'triggers' && (
            <fieldset
              disabled={busy}
              className="min-w-0 space-y-3 rounded-xl border border-border-muted p-4"
            >
              <legend className="px-1 text-sm font-semibold text-text-secondary">Subscription target</legend>
              <TriggerTarget value={previewObject(editor.json)} jobs={jobs} onChange={value => setEditor({ ...editor, json: JSON.stringify(value, null, 2) })} />
              <label className="block">
                Creator selection
                <select
                  aria-label="Creator selection"
                  className={field}
                  value={String(
                    (previewObject(editor.json).creatorSelector as { mode?: string })?.mode ??
                      'ids',
                  )}
                  onChange={(e) => {
                    setCreatorRevisions({});
                    setEditor({
                      ...editor,
                      json: JSON.stringify(
                        {
                          ...objectJson(editor.json),
                          creatorSelector:
                            e.target.value === 'ids'
                              ? { mode: 'ids', ids: [] }
                              : { mode: 'registeredType', providerType: 'time' },
                        },
                        null,
                        2,
                      ),
                    });
                  }}
                >
                  <option value="ids">Selected source instances</option>
                  <option value="registeredType">Registered provider type</option>
                </select>
              </label>
              {(previewObject(editor.json).creatorSelector as { mode?: string })?.mode ===
              'registeredType' ? (
                <label className="block">
                  Provider type
                  <input
                    required
                    className={field}
                    value={String(
                      (previewObject(editor.json).creatorSelector as { providerType?: string })
                        ?.providerType ?? '',
                    )}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        json: JSON.stringify(
                          {
                            ...objectJson(editor.json),
                            creatorSelector: {
                              ...(objectJson(editor.json).creatorSelector as object),
                              providerType: e.target.value,
                            },
                          },
                          null,
                          2,
                        ),
                      })
                    }
                  />
                </label>
              ) : (
                <>
                  <label className="block">
                    Scheduler sources
                    <select
                      aria-label="Scheduler sources"
                      multiple
                      className={field}
                      value={
                        (previewObject(editor.json).creatorSelector as { ids?: string[] })?.ids ??
                        []
                      }
                      onChange={(e) => {
                        const ids = Array.from(e.target.selectedOptions, (o) => o.value);
                        setCreatorRevisions(
                          Object.fromEntries(
                            sources
                              .filter((s) => ids.includes(s.id))
                              .map((s) => [s.id, s.revision]),
                          ),
                        );
                        const value = previewObject(editor.json);
                        setEditor({
                          ...editor,
                          json: JSON.stringify(
                            {
                              ...value,
                              creatorSelector: { mode: 'ids', ids },
                              eventTypes: [
                                ...new Set([
                                  ...(Array.isArray(value.eventTypes)
                                    ? (value.eventTypes as string[])
                                    : []),
                                  ...sources
                                    .filter((s) => ids.includes(s.id))
                                    .flatMap((s) => s.eventTypes),
                                ]),
                              ],
                            },
                            null,
                            2,
                          ),
                        });
                      }}
                    >
                      {sources.map((source) => (
                        <option key={source.id} value={source.id}>
                          {source.name} · Revision {source.revision} · {source.state}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              {reviewSourceRefs && (
                <Button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    const ids =
                      (previewObject(editor.json).creatorSelector as { ids?: string[] } | undefined)
                        ?.ids ?? [];
                    setCreatorRevisions(
                      Object.fromEntries(
                        sources
                          .filter((source) => ids.includes(source.id))
                          .map((source) => [source.id, source.revision]),
                      ),
                    );
                  }}
                >
                  Review selected source revisions
                </Button>
              )}
            </fieldset>
          )}
          <div className="flex flex-wrap gap-2 border-t border-border-muted pt-3">
            <Button
              type="submit"
              variant="primary"
              disabled={
                busy ||
                (structuredEdited &&
                  canonicalFormErrors(kind, previewObject(editor.json)).length > 0)
              }
            >
              Save definition
            </Button>
            <Button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm('Discard this definition draft?')) {
                  setEditor(null);
                  setConflict(false);
                }
              }}
            >
              Close editor
            </Button>
          </div>
        </form>
      )}
      {enabling && (
        <section
          aria-label="Review before enabling"
          className={`${card} space-y-3 border-warning/40 bg-warning/10`}
        >
          <h3 className="text-base font-semibold text-warning">
            Review {enabling.name} before enabling
          </h3>
          <pre className="max-h-72 max-w-full overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs text-text-secondary">
            {JSON.stringify(definitionOf(kind, enabling), null, 2)}
          </pre>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              disabled={busy}
              checked={enableReviewed}
              onChange={(event) => setEnableReviewed(event.target.checked)}
            />
            I reviewed this definition, Actions, parameters and source references.
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={busy || !enableReviewed}
              onClick={() =>
                void mutate('enable', (key) =>
                  canonicalJobs.controlDefinition(kind, enabling, 'enable', key),
                )
              }
            >
              Confirm enable
            </Button>
            <Button disabled={busy} onClick={() => setEnabling(null)}>
              Cancel enable review
            </Button>
          </div>
        </section>
      )}
      {retiring && (
        <section
          aria-label="Confirm retirement"
          className={`${card} space-y-3 border-danger/40 bg-danger/10`}
        >
          <p className="break-all text-sm">
            Retired {retiring.name} ({retiring.id})？
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="danger"
              disabled={busy}
              onClick={() =>
                void mutate('retire', (key) => canonicalJobs.retire(kind, retiring, key))
              }
            >
              Confirm retirement
            </Button>
            <Button type="button" disabled={busy} onClick={() => setRetiring(null)}>
              Back
            </Button>
          </div>
        </section>
      )}
      {instantiating && (
        <section
          aria-label="Instantiate Template"
          className={`${card} space-y-3 border-accent/40 bg-accent/10`}
        >
          <p className="text-sm">
            Create a new Job and any configured Scheduler/subscriptions from {instantiating.name}.
            The Job is not run immediately.
          </p>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={activateTemplate}
              disabled={busy}
              onChange={(e) => setActivateTemplate(e.target.checked)}
            />
            Enable Job and configured subscriptions
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={busy}
              onClick={() =>
                void mutate('instantiate-template', (key) =>
                  canonicalJobs.instantiateTimeTemplate(instantiating, key, activateTemplate),
                )
              }
            >
              Create from Template
            </Button>
            <Button disabled={busy} onClick={() => setInstantiating(null)}>
              Cancel instantiation
            </Button>
          </div>
        </section>
      )}
      {!loading && !items.length && (
        <EmptyState
          title={`No records ${labels[kind]}.`}
          hint={`Create ${labels[kind]} or refresh the list to inspect the current definition.`}
        />
      )}
      <div className="min-w-0 divide-y divide-border-muted overflow-hidden rounded border border-border-default">
        {items.map((item) => (
          <article
            key={item.id}
            id={item.id}
            aria-label={`${labels[kind]} ${item.name}`}
            className={`min-w-0 flex flex-col gap-2 bg-bg-secondary/20 p-3 hover:bg-bg-tertiary/35 ${focusId === item.id ? 'ring-2 ring-accent' : ''}`}
          >
            <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
              <h3 className="min-w-0 break-words text-base font-semibold">{item.name}</h3>
              <StateBadge state={item.state} />
            </div>
            <div className="min-w-0 space-y-1 text-xs text-text-secondary">
              <p className="break-all font-mono text-text-tertiary">{item.id}</p>
              <p>
                Definition revision{' '}
                <span className="font-mono text-text-secondary">{revisionOf(item)}</span>
                <span aria-hidden> · </span>
                State revision <span className="font-mono text-text-secondary">{item.stateRevision}</span>
              </p>
            </div>
            <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border-muted pt-3">
              {kind === 'job-templates' && (
                <>
                  {item.state === 'draft' && (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !!editor}
                      onClick={() =>
                        void mutate('publish', (key) =>
                          canonicalJobs.controlDefinition(kind, item, 'publish', key),
                        )
                      }
                    >
                      Publish {item.name}
                    </Button>
                  )}
                  {item.state === 'published' && (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !!editor || !!instantiating}
                      onClick={() => {
                        setActivateTemplate(false);
                        setInstantiating(item);
                      }}
                    >
                      Instantiate {item.name}
                    </Button>
                  )}
                  <Button
                    type="button"
                    size="sm"
                    aria-label={`duplicate ${item.name}`}
                    disabled={busy || !!editor}
                    onClick={() => {
                      const definition = definitionOf(kind, item);
                      delete definition.name;
                      delete definition.provenance;
                      void mutate('duplicate', (key) =>
                        canonicalJobs.write(
                          kind,
                          null,
                          {
                            ...definition,
                            name: duplicateName(
                              item.name,
                              items.map((value) => value.name),
                            ),
                          },
                          key,
                        ),
                      );
                    }}
                  >
                    duplicate
                  </Button>
                </>
              )}
              {kind !== 'job-templates' && (
                <>
                  {(item.state === 'draft' || item.state === 'disabled') && (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !!editor || !!enabling}
                      onClick={() => {
                        setEnableReviewed(false);
                        setEnabling(item);
                      }}
                    >
                      Enable {item.name}
                    </Button>
                  )}
                  {item.state === 'enabled' && (
                    <Button
                      size="sm"
                      disabled={busy || !!editor || !!enabling}
                      onClick={() =>
                        void mutate('pause', (key) =>
                          canonicalJobs.controlDefinition(kind, item, 'pause', key),
                        )
                      }
                    >
                      Pause {item.name}
                    </Button>
                  )}
                  {item.state === 'paused' && (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !!editor || !!enabling}
                      onClick={() =>
                        void mutate('resume', (key) =>
                          canonicalJobs.controlDefinition(kind, item, 'resume', key),
                        )
                      }
                    >
                      Resume {item.name}
                    </Button>
                  )}
                  {(item.state === 'enabled' || item.state === 'paused') && (
                    <Button
                      size="sm"
                      disabled={busy || !!editor || !!enabling}
                      onClick={() =>
                        void mutate('disable', (key) =>
                          canonicalJobs.controlDefinition(kind, item, 'disable', key),
                        )
                      }
                    >
                      Disable {item.name}
                    </Button>
                  )}
                </>
              )}
              <Button
                type="button"
                size="sm"
                aria-label={`Edit ${item.name}`}
                className="max-w-full break-all"
                disabled={busy || !!editor || !!enabling}
                onClick={() => {
                  setAdvancedEditing(false);
                  setStructuredEdited(false);
                  setConflict(false);
                  const definition = definitionOf(kind, item);
                  delete definition.name;
                  const ids =
                    (definition.creatorSelector as { ids?: string[] } | undefined)?.ids ?? [];
                  setCreatorRevisions(
                    Object.fromEntries(
                      sources.filter((s) => ids.includes(s.id)).map((s) => [s.id, s.revision]),
                    ),
                  );
                  setEditor({ item, name: item.name, json: JSON.stringify(definition, null, 2) });
                }}
              >
                Edit
              </Button>
              <Button
                type="button"
                size="sm"
                aria-label={`retire ${item.name}`}
                className="max-w-full break-all"
                disabled={busy || !!editor || !!enabling || item.state === 'retired'}
                onClick={() => setRetiring(item)}
              >
                <span className="text-danger">retire</span>
              </Button>
              {kind === 'jobs' && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Runs ${item.name}`}
                  className="max-w-full break-all"
                  disabled={busy}
                  onClick={() => setSelectedJob(item)}
                >
                  Runs
                </Button>
              )}
            </div>
          </article>
        ))}
      </div>
      {cursor && (
        <div className="flex justify-center">
          <Button type="button" size="sm" disabled={loading || busy} onClick={() => void load(cursor)}>
            Load more
          </Button>
        </div>
      )}
      {selectedJob && (
        <RunList key={selectedJob.id} job={selectedJob} onClose={() => setSelectedJob(null)} />
      )}
    </div>
  );
}
const Collection = CanonicalCollection;
function OperationStatus({
  pending,
  onChange,
}: {
  pending: Pending;
  onChange: (value: Pending) => void;
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const receipt = pending.receipt;
  return (
    <section aria-label="Operation receipt" className={`${card} space-y-3 text-sm`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p role="status" className="min-w-0 break-words">
          {receipt
            ? `Operation status:${receipt.status}`
            : pending.rejected
              ? 'Request rejected. Correct the fields before submitting again.'
              : 'The result is unconfirmed. Query the original identity.'}
          {receipt && ['accepted', 'pending', 'unknown'].includes(receipt.status)
            ? '; The effect is not confirmed.'
            : ''}
        </p>
        {receipt && <StateBadge state={receipt.status} />}
      </div>
      <dl className="space-y-1 text-xs text-text-secondary">
        <div className="flex flex-wrap gap-1">
          <dt>requestKey:</dt>
          <dd className="break-all font-mono">{pending.requestKey}</dd>
        </div>
        {receipt && (
          <div className="flex flex-wrap gap-1">
            <dt>operationId:</dt>
            <dd className="break-all font-mono">{receipt.operationId}</dd>
          </div>
        )}
      </dl>
      {receipt?.error && (
        <p role="alert" className="break-words text-danger">
          {canonicalErrorMessages[receipt.error.code] ?? receipt.error.code}
        </p>
      )}
      <Button
        type="button"
        size="sm"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError('');
          void canonicalJobs
            .lookup(pending.operation, pending.requestKey, receipt?.operationId)
            .then((value) => onChange({ ...pending, receipt: value }))
            .catch((e: unknown) => setError(canonicalError(e)))
            .finally(() => setBusy(false));
        }}
      >
        Query original operation
      </Button>
      {error && (
        <p role="alert" className="break-words text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
function ActionCatalog() {
  const [items, setItems] = useState<JsonObject[]>([]);
  const [error, setError] = useState('');
  return (
    <details
      className={detailsClass}
      onToggle={(e) => {
        if (e.currentTarget.open && !items.length)
          void canonicalJobs
            .actions()
            .then((page) => setItems(page.items))
            .catch((failure: unknown) => setError(canonicalError(failure)));
      }}
    >
      <summary className={summaryClass}>Published Actions available for reference</summary>
      {error && <p role="alert">{error}</p>}
      <pre className="mt-2 max-h-72 max-w-full overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs text-text-secondary">
        {JSON.stringify(items, null, 2)}
      </pre>
    </details>
  );
}
function RunList({ job, onClose }: { job: CanonicalObject; onClose: () => void }) {
  const [runs, setRuns] = useState<CanonicalRun[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [selected, setSelected] = useState<{ run: CanonicalRun; operation: RunOperation } | null>(
    null,
  );
  const [parameters, setParameters] = useState('{}');
  const [stepId, setStepId] = useState('');
  const busy = working || unresolved(pending);
  const alive = useRef(true);
  const load = useCallback(
    async (next?: string | null, signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const page = await canonicalJobs.runs(job.id, next, signal);
        if (alive.current && !signal?.aborted) {
          setRuns((old) => (next ? [...old, ...page.items] : page.items));
          setCursor(page.nextCursor);
        }
      } catch (e) {
        if (alive.current && !signal?.aborted) setError(canonicalError(e));
      } finally {
        if (alive.current && !signal?.aborted) setLoading(false);
      }
    },
    [job.id],
  );
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(null, controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load]);
  async function execute() {
    if (!selected || busy) return;
    let params: JsonObject;
    try {
      params = objectJson(parameters);
    } catch (e) {
      setError(canonicalError(e));
      return;
    }
    const requestKey = newId('operation');
    const { run, operation } = selected;
    setBusy(true);
    setError('');
    setPending({ operation, requestKey });
    try {
      // run-again/clone use the current Job config, independently from the frozen Run config.
      const current =
        operation === 'run-again' || operation === 'clone-and-run'
          ? await canonicalJobs.get('jobs', job.id)
          : job;
      const receipt = await canonicalJobs.operate(
        run,
        operation,
        revisionOf(current),
        requestKey,
        params,
        stepId,
      );
      if (!alive.current) return;
      setPending({ operation, requestKey, receipt });
      setSelected(null);
      await load();
    } catch (e) {
      if (alive.current) {
        setError(canonicalError(e));
        if (e instanceof ApiRequestError && e.status < 500)
          setPending({ operation, requestKey, rejected: true });
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Job runs" className={`${card} space-y-3`}>
      <div className="flex flex-col gap-3 border-b border-border-muted pb-3 sm:flex-row sm:flex-wrap sm:items-center">
        <div className="min-w-0 sm:mr-auto">
          <h2 className="break-words text-lg font-semibold">Runs · {job.name}</h2>
          <p className="mt-0.5 text-xs text-text-secondary">
            Query unconfirmed outcomes using the original identity. Do not retry automatically.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={busy || loading} onClick={() => void load()}>
            Refresh Runs
          </Button>
          <Button type="button" size="sm" disabled={busy} onClick={onClose}>
            Close Runs
          </Button>
        </div>
      </div>
      {loading && (
        <p role="status" className="text-sm text-text-secondary">
          Loading Runs...
        </p>
      )}
      {error && (
        <Notice tone="danger" role="alert">
          <p className="break-words">{error}</p>
        </Notice>
      )}
      {pending && <OperationStatus pending={pending} onChange={setPending} />}
      {!loading && !runs.length && <EmptyState title="No Runs yet." />}
      {runs.map((run) => (
        <article
          key={run.runId}
          className="min-w-0 space-y-3 rounded-xl border border-border-muted bg-bg-primary/40 p-4"
        >
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <p className="min-w-0 break-all font-mono text-sm">{run.runId}</p>
            <StateBadge state={run.state} />
          </div>
          <p className="text-xs text-text-secondary">State revision {run.stateRevision}</p>
          {!!run.deliveryRuntime && (
            <details className={detailsClass} aria-label={`Delivery receipts ${run.runId}`}>
              <summary className={summaryClass}>
                Delivery Manager · runtime reservation receipts
              </summary>
              <pre className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs">
                {JSON.stringify(run.deliveryRuntime, null, 2)}
              </pre>
            </details>
          )}
          {Array.isArray(run.notifications) && run.notifications.length > 0 && (
            <details className={detailsClass} aria-label={`Notification receipts ${run.runId}`}>
              <summary className={summaryClass}>
                Notification Manager · original delivery receipts
              </summary>
              <p className="mt-2 text-xs leading-5 text-text-secondary">
                Only proven unsent pending notices may select a current target. Accepted and unknown
                notices keep the original identity.
              </p>
              <pre className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs">
                {JSON.stringify(run.notifications, null, 2)}
              </pre>
            </details>
          )}
          <div className="flex flex-wrap gap-2 border-t border-border-muted pt-3">
            {runOperations.map((operation) => (
              <Button
                type="button"
                size="sm"
                key={operation}
                disabled={busy}
                onClick={() => {
                  setSelected({ run, operation });
                  setParameters('{}');
                  setStepId('');
                }}
              >
                {operation}
              </Button>
            ))}
          </div>
        </article>
      ))}
      {cursor && (
        <div className="flex justify-center">
          <Button type="button" size="sm" disabled={loading || busy} onClick={() => void load(cursor)}>
            More Runs
          </Button>
        </div>
      )}
      {selected && (
        <form
          aria-label="Run operation"
          className="min-w-0 space-y-3 rounded-xl border border-accent/40 bg-accent/10 p-4 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            void execute();
          }}
        >
          <p className="break-all font-medium">
            Confirm {selected.operation} · {selected.run.runId}？
          </p>
          {selected.operation === 'retry' && (
            <label className="block text-sm">
              Step ID (optional; defaults to the failure boundary)
              <input className={field} value={stepId} onChange={(e) => setStepId(e.target.value)} />
            </label>
          )}
          {['run-again', 'clone-and-run'].includes(selected.operation) && (
            <label className="block text-sm">
              Run parameters JSON (input; clone may include overrides.name/description)
              <textarea
                className={`${field} h-24 font-mono`}
                value={parameters}
                onChange={(e) => setParameters(e.target.value)}
              />
            </label>
          )}
          <div className="flex flex-wrap gap-2 border-t border-accent/30 pt-3">
            <Button variant="primary" disabled={busy} type="submit">
              Confirm operation
            </Button>
            <Button type="button" disabled={busy} onClick={() => setSelected(null)}>
              Back
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
