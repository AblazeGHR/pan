import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import type { JsonObject } from '@/types/canonicalJobs';
import type { Action, CanonicalLegacyAction } from '@/types/actions';
import { definitelyRejected, useOperationIdentity } from './useOperationIdentity';
import { CanonicalDefinitionEditor } from './CanonicalDefinitionEditor';

const field =
  'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary px-3 py-2 text-sm';

function copyName(base: string, names: string[]) {
  const used = new Set(names.map((value) => value.trim().toLowerCase()));
  const root = `${base.trim() || 'Untitled Action'} copy`;
  let candidate = root;
  let index = 2;
  while (used.has(candidate.toLowerCase())) {
    candidate = `${root} ${index}`;
    index += 1;
  }
  return candidate;
}

export function MappedActionPanel({
  mapped,
  legacyAction,
  existingNames,
  onClose,
  onChanged,
  onOpenCopy,
  onRequestDelete,
  error: parentError = '',
  defaultDuplicateOpen = false,
  onDirtyChange,
}: {
  mapped: CanonicalLegacyAction & { legacyId: string };
  legacyAction?: Action;
  existingNames: string[];
  onClose: () => void;
  onChanged: () => Promise<void>;
  onOpenCopy: (object: JsonObject, definition: JsonObject) => void;
  onRequestDelete: (action: Action) => void;
  error?: string;
  defaultDuplicateOpen?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const object = mapped.object;
  const definition = mapped.definition;
  const [current, setCurrent] = useState(object);
  useEffect(() => setCurrent(object), [object]);
  const state = String(current.state ?? 'draft');
  const enabled = current.enabled === true;
  const system = current.system === true;
  const [copyNameValue, setCopyNameValue] = useState(() =>
    copyName(String(current.name ?? 'Action'), existingNames),
  );
  const [duplicateOpen, setDuplicateOpen] = useState(defaultDuplicateOpen);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState<{
    operationId: string;
    status: string;
    result?: JsonObject;
  } | null>(null);
  const [pending, setPending] = useOperationIdentity('mapped-action:' + mapped.legacyId);

  async function run(
    operation: string,
    task: (requestKey: string) => Promise<{
      operationId: string;
      status: string;
      result?: JsonObject;
    }>,
    after?: (result: { operationId: string; status: string; result?: JsonObject }) => Promise<void> | void,
  ) {
    if (busy || pending || system) return;
    setBusy(true);
    setError('');
    setReceipt(null);
    const identity = { operation, requestKey: crypto.randomUUID() };
    setPending(identity);
    try {
      const value = await task(identity.requestKey);
      setReceipt(value);
      if (['completed', 'rejected', 'cancelled'].includes(value.status)) {
        setPending(null);
        if (value.status === 'completed') {
          const updated = value.result?.object as JsonObject | undefined;
          if (updated && typeof updated.id === 'string') setCurrent(updated);
          await onChanged();
          await after?.(value);
        }
      }
    } catch (caught) {
      if (definitelyRejected(caught)) setPending(null);
      setError(
        `${canonicalError(caught)}${definitelyRejected(caught) ? '' : '.Query the original operation if the response is unknown.'}`,
      );
    } finally {
      setBusy(false);
    }
  }

  const lifecycle = (operation: 'publish' | 'enable' | 'disable') =>
    run(operation, (requestKey) => canonicalJobs.actionControl(current, operation, requestKey));

  async function lookup() {
    if (!pending || busy) return;
    setBusy(true);
    setError('');
    try {
      const value = await canonicalJobs.lookup(
        pending.operation,
        pending.requestKey,
        receipt?.operationId,
      );
      setReceipt(value);
      const updated = value.result?.object as JsonObject | undefined;
      if (updated && typeof updated.id === 'string') setCurrent(updated);
      if (['completed', 'rejected', 'cancelled'].includes(value.status)) setPending(null);
    } catch (caught) {
      setError(canonicalError(caught));
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || !!pending || system;
  return (
    <section aria-label="Mapped canonical Action" className="min-h-0 space-y-4 overflow-auto p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-default pb-3">
        <div className="min-w-0">
          <h2 className="break-all text-lg font-semibold">
            {String(current.name ?? 'Action')} · {mapped.legacyId}
          </h2>
          <p className="mt-1 break-all text-xs text-text-secondary">
            Logical ID <span className="font-mono">{String(current.id)}</span> · revision{' '}
            {String(current.revision)} · stateRevision {String(current.stateRevision)} · {state} ·{' '}
            {enabled ? 'enabled' : 'disabled'}
          </p>
        </div>
        <Button type="button" size="sm" onClick={onClose}>
          Back to catalog
        </Button>
      </header>

      <p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
        This Action has a canonical definition. Original source bytes are preserved. Copy, lifecycle and delete use canonical
        identity and permissions. Complete definitions retain all advanced fields.
      </p>
      <p className="text-sm text-text-secondary">Preserved reason:{mapped.reason}</p>
      {system && (
        <p role="status" className="text-sm text-text-secondary">
          System Action is product managed. Legacy controls are unavailable.
        </p>
      )}
      {(error || parentError) && <p role="alert">{error || parentError}</p>}

      <div className="flex flex-wrap gap-2 border-y border-border-muted py-3">
        {!system && state === 'draft' && (
          <Button size="sm" variant="primary" disabled={disabled} onClick={() => void lifecycle('publish')}>
            Publish canonical Action
          </Button>
        )}
        {!system && state === 'published' && enabled && (
          <Button size="sm" disabled={disabled} onClick={() => void lifecycle('disable')}>
            Disable canonical Action
          </Button>
        )}
        {!system && state === 'published' && !enabled && (
          <Button size="sm" variant="primary" disabled={disabled} onClick={() => void lifecycle('enable')}>
            Enable canonical Action
          </Button>
        )}
        {!system && (
          <Button
            size="sm"
            disabled={disabled}
            onClick={() => setDuplicateOpen((value) => !value)}
          >
            Duplicate canonical Action
          </Button>
        )}
        {!system && legacyAction && (
          <Button
            size="sm"
            variant="danger"
            disabled={disabled}
            onClick={() => onRequestDelete(legacyAction)}
          >
            Delete canonical Action
          </Button>
        )}
      </div>

      {duplicateOpen && !system && (
        <div className="space-y-2 rounded-lg border border-border-default p-3">
          <p className="text-sm text-text-secondary">
            Copy the complete definition, including commands, environment and timezone. The new copy has its own identity.
          </p>
          <label className="block text-sm">
            Copy name
            <input
              className={field}
              value={copyNameValue}
              disabled={disabled}
              onChange={(event) => setCopyNameValue(event.target.value)}
            />
          </label>
          <Button
            size="sm"
            variant="primary"
            disabled={disabled || !copyNameValue.trim()}
            onClick={() =>
              void run(
                'duplicate',
                (requestKey) => canonicalJobs.actionCopy(definition, copyNameValue.trim(), requestKey),
                (value) => {
                  const created = value.result?.object as JsonObject | undefined;
                  if (created && typeof created.id === 'string') {
                    onOpenCopy(created, { ...definition, name: copyNameValue.trim() });
                  } else {
                    setError('The copy was created. Refresh the catalog to locate its identity.');
                  }
                },
              )
            }
          >
            Create canonical copy
          </Button>
        </div>
      )}

      {pending && (
        <div role="status" className="space-y-2 rounded-lg border border-warning/30 bg-warning/10 p-3">
          <p className="break-all text-sm">
            Original request {pending.requestKey} · {receipt?.status ?? 'unknown'}.Query only. No automatic replay.
          </p>
          <Button size="sm" disabled={busy} onClick={() => void lookup()}>
            Query original lifecycle operation
          </Button>
        </div>
      )}
      {receipt && (
        <p role="status" className="text-sm">
          Operation status:{receipt.status}
        </p>
      )}

      <details open className="rounded-lg border border-border-default p-3">
        <summary className="cursor-pointer text-sm font-medium">Full definition editor</summary>
        <p className="my-2 text-sm text-text-secondary">
          Save a new draft version. Publish and Group updates require separate operations.
        </p>
        <CanonicalDefinitionEditor
          key={String(current.id) + ':' + String(current.revision)}
          item={current}
          definition={definition}
          onDirtyChange={onDirtyChange}
          onChanged={async () => {
            await onChanged();
            onClose();
          }}
        />
      </details>
    </section>
  );
}
