import { useEffect, useMemo, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { Button } from '@/components/ui/Button';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import type { Session } from '@/types';
import { Badge, CopyButton, Field, Section, inputClass, cx } from './ActionsUi';
import { ReferenceUpdates } from './ReferenceUpdates';
import { UseInSessionDialog } from './UseInSessionDialog';
import {
  bindableGroup,
  groupBuildBlockers,
  obj,
  rows,
  runningBlockers,
  text,
  type UnifiedActionRow,
  type UnifiedGroupRow,
} from './unifiedModel';
import { definitelyRejected, useOperationIdentity } from './useOperationIdentity';
import { Hammer, PackageOpen, Play, Plus, Trash2 } from 'lucide-react';

const TERMINAL = ['completed', 'rejected', 'cancelled'];

interface DraftTool {
  alias: string;
  actionRef: JsonObject;
}

/**
 * One Group entry: draft (fixed Action versions), the immutable release, the
 * reference upgrade preview and the Session binding. Publication and reference
 * upgrade never rewrite an existing release or a running Worker pin.
 */
export function CanonicalGroupPanel({
  row,
  catalog,
  references,
  sessions,
  onChanged,
  onInspect,
  onSelectAction,
  onClose,
}: {
  row: UnifiedGroupRow;
  catalog: UnifiedActionRow[];
  references: Record<string, JsonObject>;
  sessions: Session[];
  onChanged: () => Promise<void>;
  onInspect: (ref: JsonObject) => void;
  onSelectAction: (actionId: string) => void;
  onClose?: () => void;
}) {
  const [name, setName] = useState(row.name);
  const [tools, setTools] = useState<DraftTool[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const [release, setRelease] = useState<JsonObject | null>(null);
  const [history, setHistory] = useState<JsonObject[]>([]);
  const [releaseId, setReleaseId] = useState('');
  const [binding, setBinding] = useState(false);
  const [pending, setPending] = useOperationIdentity(`group:${row.id}`);
  const lock = useRef(false);

  // Committed history is a read-only projection; a new release or unbuild
  // refreshes it without touching the draft.
  const historySeq = useRef(0);
  async function loadHistory() {
    const seq = ++historySeq.current;
    try {
      const page = await canonicalRequest<JsonObject>(
        `action-groups/${encodeURIComponent(row.id)}/releases?limit=20`,
      );
      if (seq === historySeq.current) setHistory(rows(page.items));
    } catch {
      if (seq === historySeq.current) setHistory([]);
    }
  }
  useEffect(() => {
    void loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.id, row.release]);

  // Re-seed the draft only when the committed snapshot changes, so unrelated
  // renders (search, another row's mutation) cannot discard local edits.
  const signature = `${row.id}:${row.revision}:${row.release}:${row.name}`;
  const applied = useRef('');
  useEffect(() => {
    if (applied.current === signature) return;
    applied.current = signature;
    setName(row.name);
    setTools(row.tools.map((tool) => ({ alias: tool.alias, actionRef: { ...tool.actionRef } })));
  }, [signature, row.name, row.tools]);

  const blockers = useMemo(() => groupBuildBlockers(tools, catalog), [tools, catalog]);
  const blockers2 = useMemo(() => runningBlockers(row.pluginId, sessions), [row.pluginId, sessions]);
  const dirty =
    name !== row.name ||
    JSON.stringify(tools) !== JSON.stringify(row.tools.map((tool) => ({ alias: tool.alias, actionRef: tool.actionRef })));
  const built = Boolean(row.release);
  const group = bindableGroup(row, sessions);
  const aliasesOf = (value: JsonObject[]) =>
    rows(value)
      .map((tool) => `${text(tool.alias)}@v${text(obj(tool.actionRef).revision, '—')}`)
      .join(' · ');

  function patch(index: number, next: Partial<DraftTool>) {
    setTools((current) => current.map((tool, at) => (at === index ? { ...tool, ...next } : tool)));
    setError('');
  }

  async function run(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (caught) {
      if (definitelyRejected(caught)) setPending(null);
      setError(canonicalError(caught));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  async function settle(operation: string, work: (requestKey: string) => Promise<CanonicalReceipt>) {
    if (lock.current || pending) return;
    const identity = { operation, requestKey: crypto.randomUUID() };
    setPending(identity);
    setReceipt(null);
    await run(async () => {
      const result = await work(identity.requestKey);
      setReceipt(result);
      if (TERMINAL.includes(result.status)) {
        setPending(null);
        if (result.status === 'completed') await onChanged();
      }
    });
  }

  function save() {
    void settle('update', (requestKey) =>
      canonicalRequest<CanonicalReceipt>(
        `action-groups/${encodeURIComponent(row.id)}`,
        'PUT',
        {
          group: { id: row.id, name: name.trim(), tools },
          expectedRevision: row.revision,
          requestKey,
        },
      ),
    );
  }

  function build() {
    void settle('publish', (requestKey) =>
      canonicalRequest<CanonicalReceipt>(
        `action-groups/${encodeURIComponent(row.id)}/build`,
        'POST',
        { expectedRevision: row.revision, requestKey },
      ),
    );
  }

  function unbuild() {
    void settle('disable', (requestKey) =>
      canonicalRequest<CanonicalReceipt>(
        `action-groups/${encodeURIComponent(row.id)}/unbuild`,
        'POST',
        { expectedRevision: row.revision, requestKey },
      ),
    );
  }

  function remove() {
    void settle('retire', (requestKey) =>
      canonicalRequest<CanonicalReceipt>(
        `action-groups/${encodeURIComponent(row.id)}`,
        'DELETE',
        { expectedRevision: row.revision, requestKey },
      ),
    );
  }

  async function lookup() {
    if (!pending || lock.current) return;
    await run(async () => {
      const result = await canonicalJobs.lookup(pending.operation, pending.requestKey, receipt?.operationId);
      setReceipt(result);
      if (TERMINAL.includes(result.status)) setPending(null);
    });
  }

  async function loadRelease(version: string) {
    if (!version) return;
    await run(async () => {
      setRelease(await canonicalRequest<JsonObject>(`action-groups/releases/${encodeURIComponent(version)}`));
    });
  }

  return (
    <article className="min-w-0 space-y-3" aria-label={`Group ${row.name}`}>
      {onClose && <div className="ml-12 sm:ml-0"><Button size="sm" variant="ghost" disabled={busy || !!pending} onClick={() => { if (!dirty || window.confirm('Discard unsaved Group changes?')) onClose(); }}>Back to list</Button></div>}
      <header className="min-w-0 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-all text-lg font-semibold">{row.name}</h2>
          <Badge tone={built ? 'success' : 'muted'} dot>
            {built ? 'Published fixed release' : 'Unbuilt'}
          </Badge>
          {blockers2.length > 0 && (
            <Badge tone="warning" title={blockers2.map((item) => item.name).join(', ')}>
              {blockers2.length}  running Sessions
            </Badge>
          )}
        </div>
        <details className="text-[11px] text-text-tertiary"><summary className="cursor-pointer">Technical details</summary><p className="break-all font-mono">{row.id} · {row.pluginId}</p></details>
        <p className="text-[11px] text-text-tertiary">revision {String(row.revision ?? '—')}</p>
      </header>

      <Section
        title="Draft and fixed versions"
        description="Each tool pins an Action version (revision + digest).Switching versions updates the draft. Build explicitly to publish a new release."
        actions={
          <>
            <Button size="sm" disabled={busy || !!pending || !dirty} onClick={save}>
              Save draft
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={busy || !!pending || blockers.length > 0}
              title={blockers.join('; ') || 'Build immutable release'}
              onClick={build}
            >
              <Hammer size={13} /> {built ? 'Build again' : 'Build'}
            </Button>
            <Button
              size="sm"
              disabled={busy || !!pending || !built || blockers2.length > 0}
              title={!built ? 'Unpublished' : blockers2.length ? 'Running Sessions are using this Group' : 'Unbuild release'}
              onClick={unbuild}
            >
              <PackageOpen size={13} /> Unbuild
            </Button>
          </>
        }
        bodyClassName="space-y-3"
      >
        <Field label="Group Name" htmlFor="group-name">
          <input
            id="group-name"
            className={inputClass}
            value={name}
            disabled={busy || !!pending}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        {tools.map((tool, index) => (
          <div key={index} className="min-w-0 space-y-2 rounded-md border border-border-default p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-medium text-text-tertiary">Tools {index + 1}</span>
              <span className="ml-auto">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy || !!pending}
                  onClick={() => setTools((current) => current.filter((_, at) => at !== index))}
                >
                  <Trash2 size={12} /> Remove
                </Button>
              </span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Tool alias">
                <input
                  aria-label={`Tool alias ${index + 1}`}
                  className={inputClass}
                  value={tool.alias}
                  disabled={busy || !!pending}
                  onChange={(event) => patch(index, { alias: event.target.value })}
                />
              </Field>
              <Field label="Fixed Action">
                <select
                  aria-label={`Group Action ${index + 1}`}
                  className={inputClass}
                  value={text(tool.actionRef.id)}
                  disabled={busy || !!pending}
                  onChange={(event) => {
                    const id = event.target.value;
                    patch(index, { actionRef: id ? (references[id] ?? {}) : {} });
                  }}
                >
                  <option value="">Select Action</option>
                  {catalog
                    .filter((item) => item.canonicalId)
                    .map((item) => (
                      <option key={item.canonicalId} value={item.canonicalId}>
                        {item.name}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
            <FixedVersion
              actionRef={tool.actionRef}
              disabled={busy || !!pending}
              onChange={(actionRef) => patch(index, { actionRef })}
            />
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-text-tertiary">
              <details className="min-w-0"><summary className="cursor-pointer">Technical details</summary><span className="break-all font-mono">{JSON.stringify(tool.actionRef)}</span></details>
              {text(tool.actionRef.id) && (
                <Button size="sm" variant="ghost" onClick={() => onSelectAction(text(tool.actionRef.id))}>
                  Inspect Action
                </Button>
              )}
            </div>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={busy || !!pending}
            onClick={() => setTools((current) => [...current, { alias: '', actionRef: {} }])}
          >
            <Plus size={13} /> Add fixed tool
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onInspect(references[text(tools[0]?.actionRef.id)] ?? {})}>
            Inspect reference in catalog
          </Button>
        </div>
        {blockers.length > 0 && (
          <ul className="space-y-1 rounded-md border border-warning/35 bg-warning/5 p-2 text-[11px] text-warning">
            {blockers.map((item) => (
              <li key={item} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t border-border-default pt-2 text-[11px] text-text-tertiary">
          <Button
            size="sm"
            disabled={busy || !!pending || built}
            title={built ? 'Unbuild before deleting' : 'Delete Group'}
            onClick={remove}
          >
            <Trash2 size={12} /> Delete Group
          </Button>
          <span>Unbuild and remove references and Session bindings before deleting.</span>
        </div>
      </Section>

      <Section
        title="Fixed releases and Session bindings"
        description="release is immutable. Workers load a fixed release at startup; later publication preserves running pins."
        actions={
          <>
            <Button size="sm" disabled={!built} onClick={() => void loadRelease(row.release)}>
              View current release
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!built || !group}
              title={built ? 'Select an MCP mode for the Session' : 'Build before binding a Session'}
              onClick={() => setBinding(true)}
            >
              <Play size={13} /> Use in Session
            </Button>
          </>
        }
        bodyClassName="space-y-3"
      >
        <div className="grid gap-1 text-xs sm:grid-cols-2">
          <p className="break-all">
            <span className="text-text-tertiary">release Version:</span>
            <span className="font-mono">{row.release || 'Unpublished'}</span>
          </p>
          <p className="break-all">
            <span className="text-text-tertiary">Bound Session:</span>
            {bindableSessions(row, sessions)}
          </p>
        </div>
        <section role="region" aria-label="Release history" className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-xs font-semibold text-text-primary">Published release</h4>
            <span className="text-[11px] text-text-tertiary">
              Recent {history.length}  releases / newest first / immutable
            </span>
            <Button size="sm" className="ml-auto" disabled={busy} onClick={() => void loadHistory()}>
              Refresh
            </Button>
          </div>
          {history.length === 0 ? (
            <p className="text-[11px] text-text-tertiary">This Group has no published release.</p>
          ) : (
            <ul className="space-y-1">
              {history.map((item) => {
                const version = text(item.version);
                return (
                  <li
                    key={version}
                    className="flex min-w-0 flex-wrap items-center gap-2 rounded border border-border-muted px-2 py-1.5 text-[11px]"
                  >
                    <span className="min-w-0 break-all font-mono">{version}</span>
                    {version === row.release && <Badge tone="success">Current</Badge>}
                    <span className="text-text-tertiary">
                      revision {text(item.revision, '—')} · digest {text(item.digest).slice(0, 8)}
                    </span>
                    <span className="ml-auto">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Inspect ${version}`}
                        onClick={() => void loadRelease(version)}
                      >
                        Inspect
                      </Button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <div className="flex flex-wrap items-end gap-2">
          <Field
            label="Inspect release by version"
            hint="History is immutable. You can inspect a release by its version ID."
            className="min-w-0 flex-1 basis-64"
          >
            <input
              aria-label="Release version"
              className={inputClass}
              value={releaseId}
              placeholder="release version"
              onChange={(event) => setReleaseId(event.target.value)}
            />
          </Field>
          <Button size="sm" disabled={!releaseId || busy} onClick={() => void loadRelease(releaseId)}>
            Inspect
          </Button>
          {release && <CopyButton value={text(release.version)} label="Copy release version" />}
        </div>
        {release && (
          <div className="min-w-0 space-y-2 rounded-md border border-border-default p-2.5 text-xs">
            <p className="break-all">
              <span className="text-text-tertiary">version: </span>
              <span className="font-mono">{text(release.version)}</span>
            </p>
            <details><summary className="cursor-pointer">Technical details</summary><p className="break-all">Digest: <span className="font-mono">{text(release.digest)}</span></p></details>
            <p className="break-words">
              <span className="text-text-tertiary">Fixed tools:</span>
              {aliasesOf(rows(release.tools)) || '—'}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy || !!pending}
                title="Copy this release to the draft. Build again to publish the change"
                onClick={() =>
                  setTools(
                    rows(release.tools).map((tool) => ({
                      alias: text(tool.alias),
                      actionRef: obj(tool.actionRef),
                    })),
                  )
                }
              >
                Copy to draft
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onInspect(obj(rows(release.tools)[0]?.actionRef))}>
                Inspect first fixed Action
              </Button>
            </div>
            <details>
              <summary className="cursor-pointer text-text-secondary">Full release JSON</summary>
              <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all text-[11px]">
                {JSON.stringify(release, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </Section>

      <ReferenceUpdates
        initialGroupId={row.id}
        manageGroups={false}
        onInspect={onInspect}
      />

      {pending && (
        <div role="status" className="space-y-2 rounded-md border border-border-default p-2.5 text-xs">
          <p className="break-all">
            Original Group request {pending.requestKey} · {receipt?.status ?? 'unknown'}.Query only. No automatic replay.
          </p>
          <Button size="sm" disabled={busy} onClick={() => void lookup()}>
            Query original Group operation
          </Button>
        </div>
      )}
      {receipt && (
        <details className="rounded-md border border-border-default p-2.5 text-xs">
          <summary className="cursor-pointer">
            Receipt {receipt.status} · {receipt.operationId}
          </summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all text-[11px]">
            {JSON.stringify(receipt, null, 2)}
          </pre>
        </details>
      )}
      {error && (
        <p role="alert" className="break-words rounded border border-danger/30 bg-danger/5 px-2 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
      {binding && group && <UseInSessionDialog group={group} onClose={() => setBinding(false)} />}
    </article>
  );
}

function bindableSessions(row: UnifiedGroupRow, sessions: Session[]): string {
  const bound = sessions.filter((session) =>
    (session.pluginBindings ?? []).some((item) => item.plugin_id === row.pluginId),
  );
  if (!bound.length) return 'Unbound';
  return bound
    .map((session) => `${session.name}${session.workerStatus === 'running' ? ' (running)' : ''}`)
    .join(', ');
}

/**
 * Fixed-version picker. Only published, enabled revisions are selectable; the
 * panel never silently substitutes the newest revision for a fixed one.
 */
function FixedVersion({
  actionRef,
  disabled,
  onChange,
}: {
  actionRef: JsonObject;
  disabled: boolean;
  onChange: (value: JsonObject) => void;
}) {
  const [versions, setVersions] = useState<JsonObject[]>([]);
  const [error, setError] = useState('');
  const id = text(actionRef.id);
  useEffect(() => {
    let active = true;
    setVersions([]);
    setError('');
    if (!id) return () => undefined;
    canonicalRequest<JsonObject>(`jobs/actions/${encodeURIComponent(id)}/versions`)
      .then((page) => {
        if (active) setVersions(rows(page.items));
      })
      .catch((caught) => {
        if (active) setError(canonicalError(caught));
      });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <div className="min-w-0 space-y-1">
      <Field label="Fixed version" hint="Select a published, enabled version. Historical references stay fixed.">
        <select
          aria-label="Group fixed version"
          className={cx(inputClass, 'appearance-auto')}
          value={text(actionRef.revision)}
          disabled={disabled || !id}
          onChange={(event) => {
            const next = versions.find((item) => text(item.revision) === event.target.value);
            if (next) onChange(obj(next.ref));
          }}
        >
          <option value="">Select version</option>
          {versions.map((version) => (
            <option
              key={text(version.revision)}
              value={text(version.revision)}
              disabled={version.state !== 'published' || version.enabled !== true}
            >
              v{text(version.revision)} · {text(version.state)} · {version.enabled === true ? 'enabled' : 'disabled'}
            </option>
          ))}
        </select>
      </Field>
      {error && (
        <p role="alert" className="text-[11px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
