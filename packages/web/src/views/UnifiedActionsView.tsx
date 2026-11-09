import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { useAutomationAuth } from '@/stores/automationStore';
import { useActionRegistryStore, useActionSessionsStore } from '@/stores/actionRegistryStore';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { TypedValue } from '@/components/automation/CanonicalDefinitionFields';
import { Button } from '@/components/ui/Button';
import { PageShell, PageHeader, Notice } from '@/components/automation/display';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import type { Session } from '@/types';
import { useOperationIdentity, definitelyRejected } from '@/components/actions/useOperationIdentity';
import { ReferenceUpdates } from '@/components/actions/ReferenceUpdates';
import { CanonicalDefinitionEditor } from '@/components/actions/CanonicalDefinitionEditor';
import { CanonicalGroupPanel } from '@/components/actions/CanonicalGroupPanel';
import { LegacyRecordPanel } from '@/components/actions/LegacyRecordPanel';
import {
  Badge,
  Field,
  ListEmpty,
  LoadError,
  Segmented,
  SkeletonList,
  StaleBanner,
  inputClass,
} from '@/components/actions/ActionsUi';
import {
  ADOPTION_LABELS,
  adoptionTone,
  buildUnifiedActions,
  buildUnifiedGroups,
  obj,
  rows,
  text,
  withVersionIndex,
  type UnifiedActionRow,
  type UnifiedGroupRow,
} from '@/components/actions/unifiedModel';
import { Layers, Plus, RefreshCw, Search, X } from 'lucide-react';
import './actions.css';

export const field = inputClass;

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

type Tab = 'actions' | 'groups';

/**
 * One directory for Actions and Groups.
 *
 * The canonical catalog is the only writable definition store; the legacy
 * registry is merged in as row state so preserve/blocked/adoption is visible
 * per record instead of hiding behind a second page.
 */
export default function UnifiedActionsView() {
  const token = useAutomationAuth((s) => s.token || (s.authenticated ? 'cookie' : ''));
  return (
    <PageShell label="Actions Panel">
      <PageHeader
        title="Actions & Groups"
        eyebrow="Pan / Automation"
        description="Fixed versions, publication, reference updates and Session bindings"
      />
      <JobUserAuthorization />
      {token ? (
        <Directory key={token} />
      ) : (
        <Notice label="Action login required">Sign in to load the catalog.</Notice>
      )}
    </PageShell>
  );
}

function Directory() {
  const registry = useActionRegistryStore((state) => state.registry);
  const registryError = useActionRegistryStore((state) => state.refreshError ?? state.error);
  const sessions = useActionSessionsStore((state) => state.sessions);
  const [page, setPage] = useState<JsonObject>({});
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [groupPage, setGroupPage] = useState<JsonObject[]>([]);
  const [tab, setTab] = useState<Tab>('actions');
  const [query, setQuery] = useState('');
  const [actionFilter, setActionFilter] = useState<'all' | 'system' | 'user'>('all');
  const [groupFilter, setGroupFilter] = useState<'all' | 'built' | 'unbuilt'>('all');
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [creatingAction, setCreatingAction] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    const [actions, groups] = await Promise.all([
      canonicalRequest<JsonObject>('jobs/actions'),
      canonicalRequest<JsonObject>('action-groups'),
    ]);
    if (!alive.current) return;
    setPage(actions);
    setGroupPage(rows(groups.items));
    setError('');
  }, []);

  const reloadAll = useCallback(async () => {
    await Promise.all([
      load().catch((caught) => {
        if (alive.current) setError(canonicalError(caught));
      }),
      useActionRegistryStore.getState().ensureRegistry({ force: true }),
    ]);
  }, [load]);

  useEffect(() => {
    void (async () => {
      try {
        await load();
      } catch (caught) {
        if (alive.current) setError(canonicalError(caught));
      } finally {
        if (alive.current) setLoading(false);
      }
      await useActionRegistryStore.getState().ensureRegistry();
    })();
    return () => {
      alive.current = false;
    };
  }, [load]);

  useEffect(() => {
    if (tab === 'groups') void useActionSessionsStore.getState().ensureSessions();
  }, [tab]);

  // One explicit projection: the canonical owner publishes legacyId -> actionId
  // for adopted records only. Never infer a pairing from names or ordering, and
  // never consult the singular alias route (a different table).
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const result = await canonicalRequest<JsonObject>(
          'action-groups/legacy-action-bindings?limit=100',
        );
        if (!active) return;
        const next: Record<string, string> = {};
        for (const item of rows(result.items)) {
          if (item.deleted === true) continue;
          const legacyId = text(item.legacyId);
          const actionId = text(item.actionId);
          if (legacyId && actionId) next[legacyId] = actionId;
        }
        setAliases(next);
      } catch {
        // The projection is additive: without it legacy rows keep their own state.
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const catalog = useMemo(
    () =>
      withVersionIndex(
        buildUnifiedActions({ catalog: rows(page.items), registry, groups: groupPage, aliases }),
        page,
      ),
    [page, registry, groupPage, aliases],
  );
  const groups = useMemo(
    () => buildUnifiedGroups({ canonical: groupPage, registry, catalog }),
    [groupPage, registry, catalog],
  );

  const filteredActions = catalog.filter((row) => {
    const value = query.trim().toLowerCase();
    const matches =
      !value ||
      row.name.toLowerCase().includes(value) ||
      row.description.toLowerCase().includes(value) ||
      row.canonicalId.toLowerCase().includes(value) ||
      row.legacyId.toLowerCase().includes(value);
    if (!matches) return false;
    if (actionFilter === 'system') return row.system;
    if (actionFilter === 'user') return !row.system;
    return true;
  });
  const filteredGroups = groups.filter((row) => {
    const value = query.trim().toLowerCase();
    const matches =
      !value ||
      row.name.toLowerCase().includes(value) ||
      row.id.toLowerCase().includes(value) ||
      row.tools.some((tool) => tool.alias.toLowerCase().includes(value));
    if (!matches) return false;
    if (groupFilter === 'built') return Boolean(row.release);
    if (groupFilter === 'unbuilt') return !row.release;
    return true;
  });

  const selectedAction = catalog.find((row) => row.key === selected);
  const selectedGroup = groups.find((row) => row.key === selected);
  const visible = tab === 'actions' ? filteredActions.length : filteredGroups.length;
  const total = tab === 'actions' ? catalog.length : groups.length;

  function select(key: string) {
    setSelected(key);
    setCreating(false);
  }

  return (
    <div className="min-w-0 space-y-3">
      <header className="flex flex-wrap items-center gap-2">
        <Segmented
          label="Directory view"
          value={tab}
          options={[
            { value: 'actions' as const, label: 'Actions', count: catalog.length },
            { value: 'groups' as const, label: 'Groups', count: groups.length },
          ]}
          onChange={(next) => {
            setTab(next);
            setSelected('');
            setCreating(false);
            setCreatingAction(false);
            setQuery('');
          }}
        />
        <span aria-live="polite" className="tabular text-[11px] text-text-tertiary">
          {loading ? 'Loading…' : `${visible} of ${total} ${tab === 'actions' ? 'Actions' : 'Groups'}`}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={loading} onClick={() => void reloadAll()}>
            <RefreshCw size={13} /> Refresh catalog
          </Button>
          {tab === 'groups' ? (
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus size={13} /> New Group
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={() => setCreatingAction(true)}>
              <Plus size={13} /> New Action
            </Button>
          )}
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-full sm:basis-56">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-tertiary" />
          <input
            aria-label={tab === 'actions' ? 'Search Actions' : 'Search Groups'}
            className={`${inputClass} pl-8 pr-8`}
            value={query}
            placeholder={tab === 'actions' ? 'Search Actions' : 'Search Groups'}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setQuery('')}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-tertiary hover:text-text-primary"
            >
              <X size={13} />
            </button>
          )}
        </div>
        <select
          aria-label={tab === 'actions' ? 'Action origin' : 'Group build state'}
          className={`${inputClass} max-w-40`}
          value={tab === 'actions' ? actionFilter : groupFilter}
          onChange={(event) => {
            if (tab === 'actions') setActionFilter(event.target.value as typeof actionFilter);
            else setGroupFilter(event.target.value as typeof groupFilter);
          }}
        >
          {tab === 'actions' ? (
            <>
              <option value="all">System and user</option>
              <option value="system">System</option>
              <option value="user">User</option>
            </>
          ) : (
            <>
              <option value="all">All</option>
              <option value="built">Published</option>
              <option value="unbuilt">Unbuilt</option>
            </>
          )}
        </select>
      </div>

      {registryError && !error && <StaleBanner message={registryError} onRetry={() => void reloadAll()} />}
      {error && (
        <LoadError message={error} onRetry={() => void reloadAll()} />
      )}

      {!error && loading && (
        <div className="overflow-hidden rounded-lg border border-border-default">
          <SkeletonList rows={6} kind={tab === 'actions' ? 'action' : 'group'} />
        </div>
      )}

      {!error && !loading && (selectedAction || selectedGroup) && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setSelected('')}>
            Back to catalog
          </Button>
          <span className="min-w-0 truncate text-xs text-text-tertiary">
            {tab === 'actions' ? selectedAction?.name : selectedGroup?.name}
          </span>
        </div>
      )}

      {!error && !loading && !creatingAction && !selectedAction && !selectedGroup && (
        <>
          {tab === 'actions' ? (
            filteredActions.length === 0 ? (
              <div className="rounded-lg border border-border-default">
                <ListEmpty
                  icon={<Layers size={18} />}
                  title="No Actions"
                  description="Actions are shared by Groups, Jobs and Sessions."
                />
              </div>
            ) : (
              <ul aria-label="Actions" className="space-y-2">
                {filteredActions.map((row) => (
                  <ActionRow key={row.key} row={row} onOpen={() => select(row.key)} />
                ))}
              </ul>
            )
          ) : filteredGroups.length === 0 ? (
            <div className="rounded-lg border border-border-default">
              <ListEmpty
                icon={<Layers size={18} />}
                title="No Groups"
                description="Group Package fixed Action versions as a Session plugin."
                action={
                  <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                    <Plus size={13} /> New Group
                  </Button>
                }
              />
            </div>
          ) : (
            <ul aria-label="Groups" className="space-y-2">
              {filteredGroups.map((row) => (
                <GroupRow key={row.key} row={row} onOpen={() => select(row.key)} />
              ))}
            </ul>
          )}
        </>
      )}

      {!error && !loading && !creatingAction && selectedAction && (
        <ActionDetail
          key={`${selectedAction.key}:${selectedAction.latestRevision ?? ''}`}
          row={selectedAction}
          catalog={catalog}
          groups={groups}
          sessions={sessions}
          onChanged={reloadAll}
          onSelect={select}
        />
      )}

      {!error && !loading && !creatingAction && selectedGroup && (
        selectedGroup.canonicalWritable ? (
          <CanonicalGroupPanel
            key={`${selectedGroup.key}:${selectedGroup.revision ?? ''}:${selectedGroup.release}`}
            row={selectedGroup}
            catalog={catalog}
            references={obj(page.references) as Record<string, JsonObject>}
            sessions={sessions}
            onChanged={reloadAll}
            onInspect={(ref) => {
              const id = text(ref.id);
              const row = catalog.find((item) => item.canonicalId === id);
              if (row) select(row.key);
            }}
            onSelectAction={(actionId) => {
              const row = catalog.find((item) => item.canonicalId === actionId);
              if (!row) return;
              setTab('actions');
              select(row.key);
            }}
          />
        ) : (
          <div className="min-w-0 space-y-2">
            <p className="rounded-md border border-border-muted bg-bg-secondary/40 px-2 py-1.5 text-[11px] leading-relaxed text-text-secondary">
              <Badge tone="muted">Retired</Badge> This legacy Group is retired. Source records remain read-only.
              unbuild / Retired Groups cannot be built or enabled. Existing source records are preserved.
            </p>
            <LegacyRecordPanel
              key={selectedGroup.key}
              group={selectedGroup}
              onClose={() => setSelected('')}
              onChanged={reloadAll}
            />
          </div>
        )
      )}

      {creatingAction && (
        <section className="min-w-0 space-y-2 rounded-lg border border-border-default p-3" aria-label="New Action">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">New Action draft</h3>
            <span className="text-[11px] text-text-tertiary">Save as a draft. Publish explicitly in the Action detail</span>
            <Button size="sm" className="ml-auto" onClick={() => setCreatingAction(false)}>
              Cancel
            </Button>
          </div>
          <CanonicalDefinitionEditor
            onChanged={async () => {
              setCreatingAction(false);
              await reloadAll();
            }}
          />
        </section>
      )}

      {creating && (
        <NewGroupForm
          catalog={catalog}
          references={obj(page.references) as Record<string, JsonObject>}
          onCancel={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            const fresh = await canonicalRequest<JsonObject>('action-groups');
            if (!alive.current) return;
            setGroupPage(rows(fresh.items));
            await useActionRegistryStore.getState().ensureRegistry({ force: true });
            setTab('groups');
            setSelected(`canonical:${id}`);
          }}
        />
      )}
    </div>
  );
}

function ActionRow({ row, onOpen }: { row: UnifiedActionRow; onOpen: () => void }) {
  return (
    <li className="min-w-0 rounded-lg border border-border-default p-3">
      <button
        type="button"
        aria-label={`Open Action ${row.name}`}
        className="w-full min-w-0 text-left"
        onClick={onOpen}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <strong className="min-w-0 break-all text-sm">{row.name}</strong>
          <Badge tone={adoptionTone(row.adoption)}>
            {ADOPTION_LABELS[row.adoption]}
            {row.preservedReason ? ' · Writes blocked' : ''}
          </Badge>
          {row.system && <Badge tone="neutral">System</Badge>}
          <Badge tone={row.enabled ? 'success' : 'muted'} dot>
            {row.enabled ? 'Enabled' : 'Draft'}
          </Badge>
        </span>
        <span className="mt-1 block break-all text-xs text-text-secondary">
          {row.system ? 'System' : 'User'} · {row.state} · {row.versionCount}  versions
        </span>
        <span className="mt-0.5 block break-all text-xs text-text-tertiary">{row.description}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-tertiary">
          <span className="font-mono">{row.canonicalId || row.legacyId || '—'}</span>
          <span>Viewing version v{row.latestRevision ?? '—'}</span>
          <span>
            {row.usedByGroupIds.length ? `${row.usedByGroupIds.length}  Group references` : 'No Group references'}
          </span>
        </span>
      </button>
    </li>
  );
}

function GroupRow({ row, onOpen }: { row: UnifiedGroupRow; onOpen: () => void }) {
  return (
    <li className="min-w-0 rounded-lg border border-border-default p-3">
      <button
        type="button"
        aria-label={`Open Group ${row.name}`}
        className="w-full min-w-0 text-left"
        onClick={onOpen}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <strong className="min-w-0 break-all text-sm">{row.name}</strong>
          <Badge tone={adoptionTone(row.adoption)}>{ADOPTION_LABELS[row.adoption]}</Badge>
          <Badge tone={row.release ? 'success' : 'muted'} dot>
            {row.release ? 'Published release' : 'Unbuilt'}
          </Badge>
          {row.buildBlockers.length > 0 && <Badge tone="warning">Draft needs attention</Badge>}
        </span>
        <span className="mt-1 block break-all text-xs text-text-secondary">
          {row.tools.length}  fixed tools / revision {String(row.revision ?? '—')}
        </span>
        <span className="mt-0.5 block break-all font-mono text-[11px] text-text-tertiary">
          {row.pluginId}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-tertiary">
          <span>release {row.release ? row.release.slice(0, 8) : '—'}</span>
          <span>
            {row.adoption === 'legacy'
              ? 'Retired Group / read-only history'
              : 'Edit draft / Build / Bind Session'}
          </span>
        </span>
      </button>
    </li>
  );
}

/**
 * Canonical Action detail: one logical entry, many immutable versions. The
 * viewing version never rewrites a Group, Job or Run reference.
 */
function ActionDetail({
  row,
  catalog,
  groups,
  sessions,
  onChanged,
  onSelect,
}: {
  row: UnifiedActionRow;
  catalog: UnifiedActionRow[];
  groups: UnifiedGroupRow[];
  sessions: Session[];
  onChanged: () => Promise<void>;
  onSelect: (key: string) => void;
}) {
  const [versions, setVersions] = useState<JsonObject[]>([]);
  const [revision, setRevision] = useState(0);
  const [argumentsValue, setArguments] = useState<JsonObject>({});
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const [pending, setPending] = useOperationIdentity(row.canonicalId || row.legacyId);
  const [busy, setBusy] = useState(false);
  const [legacyOpen, setLegacyOpen] = useState(row.adoption !== 'canonical');
  const active = useRef(true);
  const lock = useRef(false);
  const argumentFields = useRef<HTMLFieldSetElement>(null);
  const [rawArguments, setRawArguments] = useState<string | null>(null);
  const [argumentError, setArgumentError] = useState('');

  const version = versions.find((item) => item.revision === revision);
  const definition = obj(version?.definition);
  const ref = obj(version?.ref);
  const canonicalId = row.canonicalId;

  useEffect(() => {
    if (!canonicalId) return;
    let mounted = true;
    canonicalRequest<JsonObject>(`jobs/actions/${encodeURIComponent(canonicalId)}/versions`)
      .then((page) => {
        if (!mounted) return;
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
      })
      .catch((caught) => {
        if (mounted) setError(canonicalError(caught));
      });
    return () => {
      mounted = false;
      active.current = false;
    };
  }, [canonicalId]);

  async function load() {
    if (!canonicalId) return;
    const page = await canonicalRequest<JsonObject>(`jobs/actions/${encodeURIComponent(canonicalId)}/versions`);
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

  async function mutate(operation: string) {
    if (lock.current || pending || !canonicalId) return;
    if (operation === 'invoke') {
      const invalid = argumentFields.current?.querySelector<HTMLInputElement>(
        'input:invalid,textarea:invalid,select:invalid',
      );
      if (argumentError || invalid) {
        invalid?.reportValidity();
        setError(argumentError || 'Correct the argument format.');
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
      if (['completed', 'rejected', 'cancelled'].includes(result.status)) {
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
        if (['completed', 'rejected', 'cancelled'].includes(result.status)) setPending(null);
      }
    } catch (caught) {
      if (active.current) setError(canonicalError(caught));
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }

  return (
    <article className="min-w-0 space-y-3">
      <header className="min-w-0 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-all text-lg font-semibold">{row.name}</h2>
          <Badge tone={adoptionTone(row.adoption)}>{ADOPTION_LABELS[row.adoption]}</Badge>
          <Badge tone={row.enabled ? 'success' : 'muted'} dot>
            {row.enabled ? 'Enabled' : 'Draft'}
          </Badge>
        </div>
        <p className="break-all font-mono text-[11px] text-text-tertiary">
          {row.canonicalId || '—'}
          {row.legacyId && row.legacyId !== row.canonicalId ? ` · Legacy alias ${row.legacyId}` : ''}
        </p>
      </header>

      {pinnedByGroups(row, groups).length > 0 && (
        <p className="break-words rounded-md border border-border-default bg-bg-secondary/40 p-2 text-[11px] text-text-secondary">
          Referenced by {pinnedByGroups(row, groups).length}  Groups hold fixed references. Viewing another version preserves these references.
        </p>
      )}

      {!canonicalId ? (
        <Notice label="Legacy record">
          This retired Action has no canonical version. Create a new Action; legacy execution is disabled.
          canonical authority.
        </Notice>
      ) : (
        <>
          <label className="block min-w-0">
            <span className="text-xs font-medium text-text-secondary">View version</span>
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
          </label>
          <p className="text-xs text-text-secondary">
            Switching versions changes the view. Groups, Jobs and existing Runs keep their fixed references.
          </p>
          <JsonView value={ref} />
          <p className="break-all text-sm">{String(definition.description ?? '')}</p>
          <p className="text-xs text-text-secondary">
            Execution:{String(obj(definition.execution).operation ?? '—')} · Effect:
            {String(definition.effectClass ?? '—')}
          </p>
          <p className="text-xs text-text-secondary">
            Fixed versions do not snapshot external file contents. Scripts and working directories use the existing resource and permission policy.
          </p>
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">Input, output, execution and capabilities</summary>
            <JsonView value={definition} />
          </details>
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">Compare versions</summary>
            {versions
              .filter((item) => item.revision !== revision)
              .map((item) => (
                <div key={Number(item.revision)}>
                  <h3 className="text-xs font-medium">
                    v{String(item.revision)} → v{revision}
                  </h3>
                  <JsonView
                    value={Object.keys({ ...obj(item.definition), ...definition })
                      .filter(
                        (key) =>
                          JSON.stringify(obj(item.definition)[key]) !== JSON.stringify(definition[key]),
                      )
                      .map((key) => ({
                        field: key,
                        before: obj(item.definition)[key],
                        after: definition[key],
                      }))}
                  />
                </div>
              ))}
          </details>
          {!row.system && version && (
            <CanonicalDefinitionEditor
              item={row.canonical}
              definition={definition}
              onChanged={async () => {
                await load();
                await onChanged();
              }}
            />
          )}
          <fieldset ref={argumentFields} disabled={!!pending || busy} className="min-w-0 space-y-3">
            <legend className="text-sm font-semibold">Invocation arguments</legend>
            {Object.entries(obj(obj(definition.inputSchema).properties)).map(([name, schema]) => (
              <div key={`${revision}:${name}`} className="min-w-0 space-y-1">
                <p className="text-[11px] text-text-tertiary">
                  {Array.isArray(obj(definition.inputSchema).required) &&
                  (obj(definition.inputSchema).required as unknown[]).includes(name)
                    ? 'Required'
                    : 'Optional'}{' '}
                  · {String(obj(schema).description ?? name)}
                </p>
                {Array.isArray(obj(schema).enum) ? (
                  <label className="block">
                    <span className="text-xs">{name}</span>
                    <select
                      aria-label={name}
                      className={inputClass}
                      value={JSON.stringify(argumentsValue[name]) ?? ''}
                      onChange={(event) => {
                        setRawArguments(null);
                        setArgumentError('');
                        setArguments({ ...argumentsValue, [name]: JSON.parse(event.target.value) });
                      }}
                    >
                      <option value="">Select value</option>
                      {(obj(schema).enum as unknown[]).map((value) => (
                        <option key={JSON.stringify(value)} value={JSON.stringify(value)}>
                          {JSON.stringify(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
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
                )}
              </div>
            ))}
            <details>
              <summary className="cursor-pointer text-xs text-text-secondary">
                Full arguments JSON (including declared extra fields)
              </summary>
              <textarea
                aria-label="Invocation arguments JSON"
                className={`${inputClass} min-h-32 font-mono`}
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
              {argumentError && <p role="alert">{argumentError}</p>}
            </details>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={!!argumentError || !version || version.state !== 'published' || version.enabled !== true}
                onClick={() => void mutate('invoke')}
              >
                Run fixed version
              </Button>
              {['publish', 'enable', 'disable'].map((operation) => (
                <Button key={operation} onClick={() => void mutate(operation)}>
                  {operation}
                </Button>
              ))}
            </div>
          </fieldset>
        </>
      )}

      {error && <p role="alert">{error}</p>}
      {pending && (
        <div role="status" className="space-y-2">
          <p className="break-all text-xs">
            Original request {pending.requestKey} · {receipt?.status ?? 'unknown'}.Query only. No automatic replay.
          </p>
          <Button disabled={busy} size="sm" onClick={() => void lookup()}>
            Query original operation
          </Button>
        </div>
      )}
      {receipt && (
        <>
          <p role="status" className="text-xs">
            Invocation status:{receipt.result?.status === 'uncertain' ? 'unknown' : receipt.status} ·{' '}
            {String(receipt.result?.status ?? '')} (Original receipt {receipt.status})
          </p>
          {rows(receipt.result?.commands).map((command, index) => (
            <section key={index} className="min-w-0">
              <h3 className="text-xs">
                Command {index + 1} · {String(command.status ?? '')} · exitCode{' '}
                {String(command.exitCode ?? 'unknown')}
              </h3>
              {command.stdoutTruncated === true || command.stderrTruncated === true ? (
                <p className="text-xs">Output truncated</p>
              ) : null}
              <JsonView value={{ stdout: command.stdout, stderr: command.stderr }} />
            </section>
          ))}
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">Full output and original receipt</summary>
            <JsonView value={receipt} />
          </details>
        </>
      )}

      <ReferenceUpdates key={String(ref.digest ?? '')} actionRef={ref.id ? ref : undefined} manageGroups={false} onInspect={(next) => {
        const id = text(next.id);
        const target = catalog.find((item) => item.canonicalId === id);
        if (target) onSelect(target.key);
      }} />

      <section className="min-w-0 space-y-2 rounded-md border border-border-default p-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xs font-semibold text-text-primary">Preserved source record</h3>
          <Badge tone={adoptionTone(row.adoption)}>{ADOPTION_LABELS[row.adoption]}</Badge>
          <Button size="sm" className="ml-auto" onClick={() => setLegacyOpen((open) => !open)}>
            {legacyOpen ? 'Collapse source record' : 'Inspect source record'}
          </Button>
        </div>
        <p className="text-[11px] leading-relaxed text-text-tertiary">
          Preserved source records are read-only. Session bindings
          affect future connections. Running Workers keep their fixed release.
          {sessions.length === 0 ? ' No Session list is loaded.' : ''}
        </p>
        {legacyOpen && (
          <LegacyRecordPanel
            key={`legacy:${row.key}`}
            action={row}
            onClose={() => setLegacyOpen(false)}
            onChanged={onChanged}
          />
        )}
      </section>
    </article>
  );
}

function pinnedByGroups(row: UnifiedActionRow, groups: UnifiedGroupRow[]): UnifiedGroupRow[] {
  return groups.filter(
    (group) =>
      group.tools.some((tool) => text(tool.actionRef.id) === row.canonicalId) ||
      (Boolean(row.legacyId) && (group.legacy?.action_ids ?? []).includes(row.legacyId)),
  );
}

/** Create the draft with fixed refs; publishing stays an explicit build. */
function NewGroupForm({
  catalog,
  references,
  onCancel,
  onCreated,
}: {
  catalog: UnifiedActionRow[];
  references: Record<string, JsonObject>;
  onCancel: () => void;
  onCreated: (id: string) => Promise<void>;
}) {
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [tools, setTools] = useState<Array<{ alias: string; actionId: string }>>([{ alias: '', actionId: '' }]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useOperationIdentity('group:new');

  async function create() {
    if (busy || pending) return;
    setError('');
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(id)) {
      setError('Group ID must match ^[a-z][a-z0-9_]{0,31}$.');
      return;
    }
    if (!name.trim()) {
      setError('Group Name is required.');
      return;
    }
    const fixed = tools
      .filter((tool) => tool.alias.trim() && tool.actionId)
      .map((tool) => ({ alias: tool.alias.trim(), actionRef: references[tool.actionId] ?? {} }));
    if (!fixed.length) {
      setError('At least one fixed Action tool is required.');
      return;
    }
    if (fixed.some((tool) => !tool.actionRef.id)) {
      setError('Publish and enable the selected Action before adding it.');
      return;
    }
    setBusy(true);
    const requestKey = crypto.randomUUID();
    setPending({ operation: 'create', requestKey });
    try {
      await canonicalRequest<CanonicalReceipt>(`action-groups/${encodeURIComponent(id)}`, 'PUT', {
        group: { id, name: name.trim(), tools: fixed },
        expectedRevision: 0,
        requestKey,
      });
      setPending(null);
      await onCreated(id);
    } catch (caught) {
      if (definitelyRejected(caught)) setPending(null);
      setError(canonicalError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="min-w-0 space-y-3 rounded-lg border border-border-default p-3" aria-label="New Group">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">New Group with fixed versions</h3>
        <Button size="sm" className="ml-auto" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Group ID" hint="Start with a lowercase letter; digits and underscores are allowed">
          <input aria-label="Group ID" className={inputClass} value={id} onChange={(event) => setId(event.target.value)} />
        </Field>
        <Field label="Group Name">
          <input aria-label="Group name" className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
      </div>
      {tools.map((tool, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-2">
          <Field label={`Tool alias ${index + 1}`}>
            <input
              aria-label={`New tool alias ${index + 1}`}
              className={inputClass}
              value={tool.alias}
              onChange={(event) =>
                setTools((current) =>
                  current.map((item, at) => (at === index ? { ...item, alias: event.target.value } : item)),
                )
              }
            />
          </Field>
          <Field label="Fixed Action (latest published version)">
            <select
              aria-label={`New tool Action ${index + 1}`}
              className={inputClass}
              value={tool.actionId}
              onChange={(event) =>
                setTools((current) =>
                  current.map((item, at) => (at === index ? { ...item, actionId: event.target.value } : item)),
                )
              }
            >
              <option value="">Select Action</option>
              {catalog
                .filter((item) => item.canonicalId)
                .map((item) => (
                  <option key={item.canonicalId} value={item.canonicalId}>
                    {item.name} · {item.canonicalId}
                  </option>
                ))}
            </select>
          </Field>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => setTools((current) => [...current, { alias: '', actionId: '' }])}
        >
          <Plus size={13} /> Add tool
        </Button>
        <Button size="sm" variant="primary" disabled={busy || !!pending} onClick={() => void create()}>
          Create draft
        </Button>
      </div>
      <p className="text-[11px] text-text-tertiary">
        Create a draft, then explicitly Build the Group to publish fixed versions.
      </p>
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
