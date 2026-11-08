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
        description="统一目录：固定版本、发布、引用升级与 Session 绑定"
      />
      <JobUserAuthorization />
      {token ? (
        <Directory key={token} />
      ) : (
        <Notice label="Action login required">请登录以加载统一目录。</Notice>
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

  const canonicalIds = useMemo(
    () => new Set(rows(page.items).map((item) => text(item.id))),
    [page],
  );
  const unresolved = useMemo(
    () =>
      registry.actions
        .map((action) => action.id)
        .filter((id) => !canonicalIds.has(id) && !aliases[id])
        .slice(0, 100),
    // The alias map itself is a dependency: resolving one alias must not restart
    // the remaining lookups.
    [registry.actions, canonicalIds, aliases],
  );
  useEffect(() => {
    if (!unresolved.length) return;
    let active = true;
    void (async () => {
      const resolved: Record<string, string> = {};
      await Promise.all(
        unresolved.map(async (legacyId) => {
          try {
            const value = await canonicalRequest<JsonObject>(
              `jobs/actions/aliases/${encodeURIComponent(legacyId)}`,
            );
            if (typeof value.id === 'string') resolved[legacyId] = value.id;
          } catch {
            // No canonical successor: the record keeps its legacy writer state.
          }
        }),
      );
      if (active && Object.keys(resolved).length) setAliases((current) => ({ ...current, ...resolved }));
    })();
    return () => {
      active = false;
    };
  }, [unresolved]);

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
            <RefreshCw size={13} /> 刷新目录
          </Button>
          {tab === 'groups' ? (
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus size={13} /> 新建 Group
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={() => setCreatingAction(true)}>
              <Plus size={13} /> 新建 Action
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
            placeholder={tab === 'actions' ? '搜索 Action' : '搜索 Group'}
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
              <option value="all">系统与用户</option>
              <option value="system">系统</option>
              <option value="user">用户</option>
            </>
          ) : (
            <>
              <option value="all">全部</option>
              <option value="built">已发布</option>
              <option value="unbuilt">未构建</option>
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
            返回目录
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
                  title="没有 Action"
                  description="统一目录中的 Action 同时供 Group、Job 与 Session 使用。"
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
                title="没有 Group"
                description="Group 把固定版本的 Action 组成一个 Session 插件。"
                action={
                  <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                    <Plus size={13} /> 新建 Group
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
              <Badge tone="muted">旧记录</Badge> 该 Group 仍由旧 JSON writer 持有：可继续 build /
              unbuild / 绑定 Session，但 canonical 草稿与发布需要先完成接管；引用升级只能预览。
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
            <h3 className="text-sm font-semibold">新建 Action 草稿</h3>
            <span className="text-[11px] text-text-tertiary">保存为 draft，发布需在 Action 条目中显式 publish</span>
            <Button size="sm" className="ml-auto" onClick={() => setCreatingAction(false)}>
              取消
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
            {row.preservedReason ? ' · 写入阻断' : ''}
          </Badge>
          {row.system && <Badge tone="neutral">系统</Badge>}
          <Badge tone={row.enabled ? 'success' : 'muted'} dot>
            {row.enabled ? 'Enabled' : 'Draft'}
          </Badge>
        </span>
        <span className="mt-1 block break-all text-xs text-text-secondary">
          {row.system ? '系统' : '用户'} · {row.state} · {row.versionCount} 个版本
        </span>
        <span className="mt-0.5 block break-all text-xs text-text-tertiary">{row.description}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-tertiary">
          <span className="font-mono">{row.canonicalId || row.legacyId || '—'}</span>
          <span>展示版本 v{row.latestRevision ?? '—'}</span>
          <span>
            {row.usedByGroupIds.length ? `${row.usedByGroupIds.length} 个 Group 引用` : '未被 Group 引用'}
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
            {row.release ? '已发布 release' : '未构建'}
          </Badge>
          {row.buildBlockers.length > 0 && <Badge tone="warning">需先修正草稿</Badge>}
        </span>
        <span className="mt-1 block break-all text-xs text-text-secondary">
          {row.tools.length} 个固定工具 · revision {String(row.revision ?? '—')}
        </span>
        <span className="mt-0.5 block break-all font-mono text-[11px] text-text-tertiary">
          {row.pluginId}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-tertiary">
          <span>release {row.release ? row.release.slice(0, 8) : '—'}</span>
          <span>
            {row.adoption === 'legacy'
              ? '旧 JSON Group · 仅预览升级'
              : '可编辑草稿 / Build / 绑定 Session'}
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
        setError(argumentError || '请修正参数格式。');
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
        setError(`${canonicalError(caught)}。响应不明时请查询原操作。`);
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
          {row.legacyId && row.legacyId !== row.canonicalId ? ` · 旧别名 ${row.legacyId}` : ''}
        </p>
      </header>

      {pinnedByGroups(row, groups).length > 0 && (
        <p className="break-words rounded-md border border-border-default bg-bg-secondary/40 p-2 text-[11px] text-text-secondary">
          被 {pinnedByGroups(row, groups).length} 个 Group 固定引用；切换查看版本不会改变这些固定引用。
        </p>
      )}

      {!canonicalId ? (
        <Notice label="Legacy record">
          该 Action 只有旧记录，没有统一目录版本。旧编辑器仍可写入；固定版本与引用升级需要先完成
          canonical 接管。
        </Notice>
      ) : (
        <>
          <label className="block min-w-0">
            <span className="text-xs font-medium text-text-secondary">查看版本</span>
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
            切换版本只改变查看对象。Group、Job 与已有 Run 保持原固定引用。
          </p>
          <JsonView value={ref} />
          <p className="break-all text-sm">{String(definition.description ?? '')}</p>
          <p className="text-xs text-text-secondary">
            执行：{String(obj(definition.execution).operation ?? '—')} · 效果：
            {String(definition.effectClass ?? '—')}
          </p>
          <p className="text-xs text-text-secondary">
            固定版本不固定外部路径的文件内容。外部脚本与工作目录沿用原资源和权限策略。
          </p>
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">输入、输出、执行配置与能力</summary>
            <JsonView value={definition} />
          </details>
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">与其他版本比较</summary>
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
            <legend className="text-sm font-semibold">调用参数</legend>
            {Object.entries(obj(obj(definition.inputSchema).properties)).map(([name, schema]) => (
              <div key={`${revision}:${name}`} className="min-w-0 space-y-1">
                <p className="text-[11px] text-text-tertiary">
                  {Array.isArray(obj(definition.inputSchema).required) &&
                  (obj(definition.inputSchema).required as unknown[]).includes(name)
                    ? '必填'
                    : '可选'}{' '}
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
                      <option value="">选择值</option>
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
                完整参数 JSON（含额外声明字段）
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
                      throw new Error('参数必须是 JSON 对象');
                    setArguments(value as JsonObject);
                    setArgumentError('');
                  } catch {
                    setArgumentError('参数必须是有效的 JSON 对象；当前文本尚未提交。');
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
                运行固定版本
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
            原请求 {pending.requestKey} · {receipt?.status ?? 'unknown'}。仅查询，不自动重放。
          </p>
          <Button disabled={busy} size="sm" onClick={() => void lookup()}>
            查询原操作
          </Button>
        </div>
      )}
      {receipt && (
        <>
          <p role="status" className="text-xs">
            调用状态：{receipt.result?.status === 'uncertain' ? 'unknown' : receipt.status} ·{' '}
            {String(receipt.result?.status ?? '')}（原回执 {receipt.status}）
          </p>
          {rows(receipt.result?.commands).map((command, index) => (
            <section key={index} className="min-w-0">
              <h3 className="text-xs">
                命令 {index + 1} · {String(command.status ?? '')} · exitCode{' '}
                {String(command.exitCode ?? 'unknown')}
              </h3>
              {command.stdoutTruncated === true || command.stderrTruncated === true ? (
                <p className="text-xs">输出已截断</p>
              ) : null}
              <JsonView value={{ stdout: command.stdout, stderr: command.stderr }} />
            </section>
          ))}
          <details>
            <summary className="cursor-pointer text-sm text-text-secondary">完整输出与原操作回执</summary>
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
          <h3 className="text-xs font-semibold text-text-primary">旧记录与原功能</h3>
          <Badge tone={adoptionTone(row.adoption)}>{ADOPTION_LABELS[row.adoption]}</Badge>
          <Button size="sm" className="ml-auto" onClick={() => setLegacyOpen((open) => !open)}>
            {legacyOpen ? '收起旧记录' : '打开旧记录编辑器'}
          </Button>
        </div>
        <p className="text-[11px] leading-relaxed text-text-tertiary">
          保留、阻断与接管状态逐条显示；旧编辑器沿用原权限、registry CAS 与引用检查。Session 绑定
          只影响未来连接，已运行 Worker 继续使用原固定 release。
          {sessions.length === 0 ? ' 当前没有已加载的 Session 列表。' : ''}
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
      setError('Group ID 必须匹配 ^[a-z][a-z0-9_]{0,31}$。');
      return;
    }
    if (!name.trim()) {
      setError('Group 名称必填。');
      return;
    }
    const fixed = tools
      .filter((tool) => tool.alias.trim() && tool.actionId)
      .map((tool) => ({ alias: tool.alias.trim(), actionRef: references[tool.actionId] ?? {} }));
    if (!fixed.length) {
      setError('至少需要一个固定版本的 Action 工具。');
      return;
    }
    if (fixed.some((tool) => !tool.actionRef.id)) {
      setError('所选 Action 没有可用的已发布版本，请先在 Action 中发布。');
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
        <h3 className="text-sm font-semibold">新建固定版本 Group</h3>
        <Button size="sm" className="ml-auto" onClick={onCancel}>
          取消
        </Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Group ID" hint="小写字母开头，可含数字与下划线">
          <input aria-label="Group ID" className={inputClass} value={id} onChange={(event) => setId(event.target.value)} />
        </Field>
        <Field label="Group 名称">
          <input aria-label="Group name" className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
      </div>
      {tools.map((tool, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-2">
          <Field label={`工具别名 ${index + 1}`}>
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
          <Field label="固定 Action（最新已发布版本）">
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
              <option value="">选择 Action</option>
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
          <Plus size={13} /> 添加工具
        </Button>
        <Button size="sm" variant="primary" disabled={busy || !!pending} onClick={() => void create()}>
          创建草稿
        </Button>
      </div>
      <p className="text-[11px] text-text-tertiary">
        创建只写入草稿；固定版本与发布请在 Group 条目中显式 Build。
      </p>
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
