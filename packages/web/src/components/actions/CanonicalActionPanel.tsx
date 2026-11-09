import { useEffect, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { Button } from '@/components/ui/Button';
import { TypedValue } from '@/components/automation/CanonicalDefinitionFields';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import { Badge, Field, inputClass } from './ActionsUi';
import { CanonicalDefinitionEditor } from './CanonicalDefinitionEditor';
import { definitelyRejected, useOperationIdentity } from './useOperationIdentity';
import { legacyDraftFromDefinition, obj, rows, text, type UnifiedActionRow } from './unifiedModel';
import type { Action } from '@/types/actions';

const TERMINAL = ['completed', 'rejected', 'cancelled'];

/**
 * Canonical Action detail for the designed directory page: immutable version
 * selector, fixed ActionRef, publish/enable/disable and a fixed-version call.
 * It is an added section, not a replacement for the designed Action editor.
 */
export function CanonicalActionPanel({
  row,
  onClose,
  onChanged,
  onEditForm,
}: {
  row: UnifiedActionRow;
  onClose: () => void;
  onChanged: () => Promise<void>;
  onEditForm?: (draft: Action) => void;
}) {
  const [versions, setVersions] = useState<JsonObject[]>([]);
  const [revision, setRevision] = useState(0);
  const [argumentsValue, setArguments] = useState<JsonObject>({});
  const [rawArguments, setRawArguments] = useState<string | null>(null);
  const [argumentError, setArgumentError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const [pending, setPending] = useOperationIdentity(`canonical:${row.canonicalId}`);
  const active = useRef(true);
  const lock = useRef(false);

  const version = versions.find((item) => item.revision === revision);
  const definition = obj(version?.definition);
  const ref = obj(version?.ref);
  const canonicalId = row.canonicalId;

  async function load() {
    if (!canonicalId) return;
    const page = await canonicalRequest<JsonObject>(
      `jobs/actions/${encodeURIComponent(canonicalId)}/versions`,
    );
    if (!active.current) return;
    const next = rows(page.items);
    setVersions(next);
    setRevision(
      (old) =>
        old ||
        Number(
          [...next].reverse().find((item) => item.state === 'published' && item.enabled)?.revision ??
            next.at(-1)?.revision ??
            0,
        ),
    );
  }

  useEffect(() => {
    void load().catch((caught) => active.current && setError(canonicalError(caught)));
    return () => {
      active.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canonicalId]);

  async function mutate(operation: string) {
    if (lock.current || pending || !canonicalId) return;
    if (operation === 'invoke') {
      if (argumentError) {
        setError(argumentError);
        return;
      }
    }
    lock.current = true;
    setBusy(true);
    setError('');
    setReceipt(null);
    const identity = { operation, requestKey: crypto.randomUUID() };
    setPending(identity);
    try {
      const body =
        operation === 'invoke'
          ? { actionRef: ref, arguments: argumentsValue, requestKey: identity.requestKey }
          : {
              ...identity,
              expectedRevision: row.canonical?.revision,
              expectedStateRevision: row.canonical?.stateRevision,
              parameters: { revision },
            };
      const result = await canonicalRequest<CanonicalReceipt>(
        `jobs/actions/${encodeURIComponent(canonicalId)}/${operation === 'invoke' ? 'invocations' : 'operations'}`,
        'POST',
        body,
      );
      if (!active.current) return;
      setReceipt(result);
      if (TERMINAL.includes(result.status)) {
        setPending(null);
        await load();
        await onChanged();
      }
    } catch (caught) {
      if (active.current) {
        if (definitelyRejected(caught)) setPending(null);
        setError(`${canonicalError(caught)}.Query the original operation if the response is unknown.`);
      }
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function lookup() {
    if (!pending || lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      const result = await canonicalJobs.lookup(pending.operation, pending.requestKey, receipt?.operationId);
      if (active.current) {
        setReceipt(result);
        setError('');
        if (TERMINAL.includes(result.status)) setPending(null);
      }
    } catch (caught) {
      if (active.current) setError(canonicalError(caught));
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }

  return (
    <section aria-label="Canonical Action detail" className="min-h-0 overflow-auto p-4 sm:p-6">
      <div className="mx-auto w-full max-w-[880px] space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-all text-lg font-semibold">{row.name}</h2>
          <Badge tone={row.enabled ? 'success' : 'muted'} dot>
            {row.enabled ? 'Enabled' : 'Draft'}
          </Badge>
          <span className="ml-auto">
            <Button size="sm" onClick={onClose}>
              Back to list
            </Button>
          </span>
        </div>
        <p className="break-all font-mono text-[11px] text-text-tertiary">
          {canonicalId}
          {row.legacyId && row.legacyId !== canonicalId ? ` · Legacy alias ${row.legacyId}` : ''}
        </p>

        <Field label="View version" hint="Switching versions changes the view. Groups, Jobs and existing Runs keep their fixed references.">
          <select
            aria-label="Action version"
            className={inputClass}
            value={revision}
            disabled={!!pending || busy}
            onChange={(event) => {
              setRevision(Number(event.target.value));
              setArguments({});
              setRawArguments(null);
              setArgumentError('');
              setReceipt(null);
              setError('');
            }}
          >
            {versions.map((item) => (
              <option key={Number(item.revision)} value={Number(item.revision)}>
                v{String(item.revision)} · {String(item.state)} · {item.enabled ? 'enabled' : 'disabled'}
              </option>
            ))}
          </select>
        </Field>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs">
          {JSON.stringify(ref, null, 2)}
        </pre>
        <p className="break-all text-sm">{text(definition.description)}</p>

        {!row.system && version && (() => {
          const draft = legacyDraftFromDefinition(definition, { enabled: row.enabled });
          return draft && onEditForm ? (
            <div className="rounded-lg border border-border-default p-3">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">Structured editor</h3>
                <span className="text-[11px] text-text-tertiary">
                  Edit supported fields using the form. Save creates a new version; publication is explicit.
                </span>
                <span className="ml-auto">
                  <Button size="sm" variant="primary" onClick={() => onEditForm(draft)}>
                    Edit with form
                  </Button>
                </span>
              </div>
            </div>
          ) : null;
        })()}

        {!row.system && version && (
          <div className="rounded-lg border border-border-default p-3">
            <CanonicalDefinitionEditor
              item={row.canonical}
              definition={definition}
              onChanged={async () => {
                await load();
                await onChanged();
              }}
            />
          </div>
        )}

        <div className="space-y-3 rounded-lg border border-border-default p-3">
          <h3 className="text-sm font-semibold">Fixed version publication and invocation</h3>
          <div className="flex flex-wrap gap-2">
            {['publish', 'enable', 'disable'].map((operation) => (
              <Button key={operation} size="sm" disabled={busy || !!pending} onClick={() => void mutate(operation)}>
                {operation}
              </Button>
            ))}
            <Button
              size="sm"
              variant="primary"
              disabled={
                busy || !!pending || !version || version.state !== 'published' || version.enabled !== true
              }
              onClick={() => void mutate('invoke')}
            >
              Run fixed version
            </Button>
          </div>
          {Object.entries(obj(obj(definition.inputSchema).properties)).map(([name, schema]) => (
            <Field
              key={`${revision}:${name}`}
              label={name}
              hint={
                Array.isArray(obj(definition.inputSchema).required) &&
                (obj(definition.inputSchema).required as unknown[]).includes(name)
                  ? 'Required'
                  : 'Optional'
              }
            >
              <TypedValue
                label={name}
                schema={obj(schema)}
                value={Object.hasOwn(argumentsValue, name) ? argumentsValue[name] : obj(schema).default}
                onChange={(value) => {
                  setRawArguments(null);
                  setArgumentError('');
                  setArguments({ ...argumentsValue, [name]: value });
                }}
              />
            </Field>
          ))}
          <details>
            <summary className="cursor-pointer text-xs text-text-secondary">Full arguments JSON (including declared extra fields)</summary>
            <textarea
              aria-label="Invocation arguments JSON"
              className={`${inputClass} min-h-24 font-mono`}
              value={rawArguments ?? JSON.stringify(argumentsValue, null, 2)}
              onChange={(event) => {
                setRawArguments(event.target.value);
                try {
                  const value: unknown = JSON.parse(event.target.value);
                  if (!value || typeof value !== 'object' || Array.isArray(value))
                    throw new Error('Arguments must be a JSON object');
                  setArguments(value as JsonObject);
                  setArgumentError('');
                } catch {
                  setArgumentError('Arguments must be a valid JSON object. The current text has not been submitted.');
                }
              }}
            />
            {argumentError && <p role="alert" className="text-xs text-danger">{argumentError}</p>}
          </details>
        </div>

        {error && <p role="alert" className="text-xs text-danger break-words">{error}</p>}
        {pending && (
          <div role="status" className="space-y-2 text-xs">
            <p className="break-all">
              Original request {pending.requestKey} · {receipt?.status ?? 'unknown'}.Query only. No automatic replay.
            </p>
            <Button size="sm" disabled={busy} onClick={() => void lookup()}>
              Query original operation
            </Button>
          </div>
        )}
        {receipt && (
          <details className="rounded border border-border-default p-2 text-xs">
            <summary className="cursor-pointer">
              Invocation status:{receipt.result?.status === 'uncertain' ? 'unknown' : receipt.status} ·{' '}
              {String(receipt.result?.status ?? '')}
            </summary>
            <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all text-[11px]">
              {JSON.stringify(receipt, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </section>
  );
}
