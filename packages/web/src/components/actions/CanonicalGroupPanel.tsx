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
}: {
  row: UnifiedGroupRow;
  catalog: UnifiedActionRow[];
  references: Record<string, JsonObject>;
  sessions: Session[];
  onChanged: () => Promise<void>;
  onInspect: (ref: JsonObject) => void;
  onSelectAction: (actionId: string) => void;
}) {
  const [name, setName] = useState(row.name);
  const [tools, setTools] = useState<DraftTool[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const [release, setRelease] = useState<JsonObject | null>(null);
  const [releaseId, setReleaseId] = useState('');
  const [binding, setBinding] = useState(false);
  const [pending, setPending] = useOperationIdentity(`group:${row.id}`);
  const lock = useRef(false);

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
      <header className="min-w-0 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-all text-lg font-semibold">{row.name}</h2>
          <Badge tone={built ? 'success' : 'muted'} dot>
            {built ? '已发布固定 release' : '未构建'}
          </Badge>
          {blockers2.length > 0 && (
            <Badge tone="warning" title={blockers2.map((item) => item.name).join(', ')}>
              {blockers2.length} 个运行中的 Session
            </Badge>
          )}
        </div>
        <p className="break-all font-mono text-[11px] text-text-tertiary">
          {row.id} · {row.pluginId}
        </p>
        <p className="text-[11px] text-text-tertiary">revision {String(row.revision ?? '—')}</p>
      </header>

      <Section
        title="草稿与固定版本"
        description="每个工具固定一个 Action 版本（revision + digest）。切换版本只改草稿，需显式 Build 才产生新 release。"
        actions={
          <>
            <Button size="sm" disabled={busy || !!pending || !dirty} onClick={save}>
              保存草稿
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={busy || !!pending || blockers.length > 0}
              title={blockers.join('；') || '构建不可变 release'}
              onClick={build}
            >
              <Hammer size={13} /> {built ? '重新 Build' : 'Build'}
            </Button>
            <Button
              size="sm"
              disabled={busy || !!pending || !built || blockers2.length > 0}
              title={!built ? '未发布' : blockers2.length ? '运行中的 Session 正在使用该 Group' : '撤销 release'}
              onClick={unbuild}
            >
              <PackageOpen size={13} /> Unbuild
            </Button>
          </>
        }
        bodyClassName="space-y-3"
      >
        <Field label="Group 名称" htmlFor="group-name">
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
              <span className="text-[11px] font-medium text-text-tertiary">工具 {index + 1}</span>
              <span className="ml-auto">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy || !!pending}
                  onClick={() => setTools((current) => current.filter((_, at) => at !== index))}
                >
                  <Trash2 size={12} /> 移除
                </Button>
              </span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="工具别名">
                <input
                  aria-label={`Tool alias ${index + 1}`}
                  className={inputClass}
                  value={tool.alias}
                  disabled={busy || !!pending}
                  onChange={(event) => patch(index, { alias: event.target.value })}
                />
              </Field>
              <Field label="固定 Action">
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
            <FixedVersion
              actionRef={tool.actionRef}
              disabled={busy || !!pending}
              onChange={(actionRef) => patch(index, { actionRef })}
            />
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-text-tertiary">
              <span className="break-all font-mono">{JSON.stringify(tool.actionRef)}</span>
              {text(tool.actionRef.id) && (
                <Button size="sm" variant="ghost" onClick={() => onSelectAction(text(tool.actionRef.id))}>
                  查看该 Action
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
            <Plus size={13} /> 添加固定工具
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onInspect(references[text(tools[0]?.actionRef.id)] ?? {})}>
            在当前目录查看引用
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
            title={built ? '需先 Unbuild 才能删除' : '删除该 Group'}
            onClick={remove}
          >
            <Trash2 size={12} /> 删除 Group
          </Button>
          <span>删除仅在未发布时可用；引用与 Session 绑定需先解除。</span>
        </div>
      </Section>

      <Section
        title="固定 release 与 Session 绑定"
        description="release 是不可变文件；Worker 启动时固定加载，之后的发布不会改写已运行的 pin。"
        actions={
          <>
            <Button size="sm" disabled={!built} onClick={() => void loadRelease(row.release)}>
              查看当前 release
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!built || !group}
              title={built ? '在三处 Session 界面中选择一种 MCP 模式' : '先 Build 再绑定 Session'}
              onClick={() => setBinding(true)}
            >
              <Play size={13} /> 在 Session 中使用
            </Button>
          </>
        }
        bodyClassName="space-y-3"
      >
        <div className="grid gap-1 text-xs sm:grid-cols-2">
          <p className="break-all">
            <span className="text-text-tertiary">release 版本：</span>
            <span className="font-mono">{row.release || '未发布'}</span>
          </p>
          <p className="break-all">
            <span className="text-text-tertiary">绑定 Session：</span>
            {bindableSessions(row, sessions)}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Field
            label="按版本号查看 release"
            hint="服务端按版本号返回单个不可变 release；完整历史列表需要新的后端索引。"
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
            查看
          </Button>
          {release && <CopyButton value={text(release.version)} label="复制 release 版本" />}
        </div>
        {release && (
          <div className="min-w-0 space-y-2 rounded-md border border-border-default p-2.5 text-xs">
            <p className="break-all">
              <span className="text-text-tertiary">version：</span>
              <span className="font-mono">{text(release.version)}</span>
            </p>
            <p className="break-all">
              <span className="text-text-tertiary">digest：</span>
              <span className="font-mono">{text(release.digest)}</span>
            </p>
            <p className="break-words">
              <span className="text-text-tertiary">固定工具：</span>
              {aliasesOf(rows(release.tools)) || '—'}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy || !!pending}
                title="把该 release 的固定版本复制到草稿；需要再次 Build 才生效"
                onClick={() =>
                  setTools(
                    rows(release.tools).map((tool) => ({
                      alias: text(tool.alias),
                      actionRef: obj(tool.actionRef),
                    })),
                  )
                }
              >
                复制到草稿
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onInspect(obj(rows(release.tools)[0]?.actionRef))}>
                查看第一个固定 Action
              </Button>
            </div>
            <details>
              <summary className="cursor-pointer text-text-secondary">完整 release JSON</summary>
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
            原 Group 请求 {pending.requestKey} · {receipt?.status ?? 'unknown'}。仅查询，不自动重放。
          </p>
          <Button size="sm" disabled={busy} onClick={() => void lookup()}>
            查询原 Group 操作
          </Button>
        </div>
      )}
      {receipt && (
        <details className="rounded-md border border-border-default p-2.5 text-xs">
          <summary className="cursor-pointer">
            回执 {receipt.status} · {receipt.operationId}
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
  if (!bound.length) return '未绑定';
  return bound
    .map((session) => `${session.name}${session.workerStatus === 'running' ? '（运行中）' : ''}`)
    .join('、');
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
      <Field label="固定版本" hint="仅可选择已发布且启用的版本；历史引用保持原版本。">
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
          <option value="">选择版本</option>
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
