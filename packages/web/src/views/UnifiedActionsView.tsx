import { useEffect, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { useAutomationAuth } from '@/stores/automationStore';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { TypedValue } from '@/components/automation/CanonicalDefinitionFields';
import { Button } from '@/components/ui/Button';
import { PageShell, PageHeader, Notice } from '@/components/automation/display';
import { LegacyActionsView } from './ActionsView';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import {
  useOperationIdentity,
  definitelyRejected,
} from '@/components/actions/useOperationIdentity';
import { ReferenceUpdates } from '@/components/actions/ReferenceUpdates';
import { CanonicalDefinitionEditor } from '@/components/actions/CanonicalDefinitionEditor';

export const obj = (v: unknown): JsonObject =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as JsonObject) : {};
export const rows = (v: unknown): JsonObject[] => (Array.isArray(v) ? (v as JsonObject[]) : []);
export const field =
  'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary p-2 text-sm';
export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-lg bg-bg-primary p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
export default function UnifiedActionsView() {
  const token = useAutomationAuth((s) => s.token || (s.authenticated ? 'cookie' : ''));
  const [legacy, setLegacy] = useState(false);
  return (
    <PageShell label="Actions Panel">
      <PageHeader
        title="Actions & Groups"
        eyebrow="Pan / Automation"
        description="固定版本、参数调用与显式引用更新"
      />
      <JobUserAuthorization />
      <Button onClick={() => setLegacy((v) => !v)}>
        {legacy ? '统一目录' : '旧定义与 Session 绑定'}
      </Button>
      {legacy ? (
        <LegacyActionsView />
      ) : token ? (
        <Catalog key={token} />
      ) : (
        <Notice label="Action login required">请登录以加载统一目录。</Notice>
      )}
    </PageShell>
  );
}
function Catalog() {
  const [items, setItems] = useState<JsonObject[]>([]),
    [versions, setVersions] = useState<JsonObject>({});
  const [selected, setSelected] = useState(''),
    [error, setError] = useState(''),
    [query, setQuery] = useState(''),
    [filter, setFilter] = useState('all');
  const [referenceRevision, setReferenceRevision] = useState<number | undefined>();
  const inspect = (ref: JsonObject) => {
    setSelected(String(ref.id));
    setReferenceRevision(Number(ref.revision));
  };
  const alive = useRef(true);
  async function load() {
    try {
      const page = await canonicalRequest<JsonObject>('jobs/actions');
      if (alive.current) {
        setItems(rows(page.items));
        setVersions(obj(page.versions));
        setError('');
      }
    } catch (e) {
      if (alive.current) setError(canonicalError(e));
    }
  }
  useEffect(() => {
    void load();
    return () => {
      alive.current = false;
    };
  }, []);
  const item = items.find((i) => i.id === selected);
  return (
    <div className="min-w-0 space-y-4">
      <CanonicalDefinitionEditor onChanged={load} />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void load()}>刷新目录</Button>
        <input
          aria-label="Search Actions"
          className={field}
          value={query}
          placeholder="搜索 Action"
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="Action origin"
          className={field}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">系统与用户</option>
          <option value="system">系统</option>
          <option value="user">用户</option>
        </select>
      </div>
      {error && <p role="alert">{error}</p>}
      {item ? (
        <>
          <Button onClick={() => setSelected('')}>返回目录</Button>
          <ActionDetail
            key={`${selected}:${referenceRevision ?? ''}`}
            item={item}
            initialRevision={referenceRevision}
            onInspect={inspect}
            onChanged={load}
          />
        </>
      ) : (
        <ul className="space-y-2">
          {items
            .filter(
              (i) =>
                (filter === 'all' || !!i.system === (filter === 'system')) &&
                `${i.name} ${i.id} ${i.description}`.toLowerCase().includes(query.toLowerCase()),
            )
            .map((i) => (
              <li
                key={String(i.id)}
                className="min-w-0 rounded-xl border border-border-default p-3"
              >
                <button
                  className="w-full break-all text-left"
                  onClick={() => {
                    setSelected(String(i.id));
                    setReferenceRevision(undefined);
                  }}
                >
                  <strong>{String(i.name)}</strong>
                  <span className="block text-sm text-text-secondary">
                    {i.system ? '系统' : '用户'} · {String(i.state)} ·{' '}
                    {String(obj(versions[String(i.id)]).count ?? 1)} 个版本
                  </span>
                  <span className="block text-sm">{String(i.description ?? '')}</span>
                  <span className="block text-xs">
                    展示版本 v
                    {String(obj(obj(versions[String(i.id)]).latest).revision ?? i.revision ?? '—')}
                  </span>
                </button>
              </li>
            ))}
        </ul>
      )}
      {!item && <ReferenceUpdates onInspect={inspect} />}
    </div>
  );
}
function ActionDetail({
  item,
  onChanged,
  initialRevision,
  onInspect,
}: {
  item: JsonObject;
  onChanged: () => Promise<void>;
  initialRevision?: number;
  onInspect: (ref: JsonObject) => void;
}) {
  const [versions, setVersions] = useState<JsonObject[]>([]),
    [revision, setRevision] = useState(initialRevision ?? 0),
    [argumentsValue, setArguments] = useState<JsonObject>({}),
    [error, setError] = useState('');
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null),
    [pending, setPending] = useOperationIdentity(String(item.id)),
    [busy, setBusy] = useState(false);
  const active = useRef(true),
    lock = useRef(false);
  const argumentFields = useRef<HTMLFieldSetElement>(null);
  const [rawArguments, setRawArguments] = useState<string | null>(null),
    [argumentError, setArgumentError] = useState('');
  const version = versions.find((v) => v.revision === revision),
    definition = obj(version?.definition),
    ref = obj(version?.ref);
  const load = async () => {
    const page = await canonicalRequest<JsonObject>(
      `jobs/actions/${encodeURIComponent(String(item.id))}/versions`,
    );
    if (!active.current) return;
    const next = rows(page.items);
    setVersions(next);
    setRevision(
      (old) =>
        old ||
        Number(
          [...next].reverse().find((v) => v.state === 'published' && v.enabled)?.revision ??
            next.at(-1)?.revision,
        ),
    );
  };
  useEffect(() => {
    void load().catch((e) => active.current && setError(canonicalError(e)));
    return () => {
      active.current = false;
    };
  }, []);
  async function mutate(operation: string) {
    if (lock.current || pending) return;
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
              expectedRevision: item.revision,
              expectedStateRevision: item.stateRevision,
              parameters: { revision },
            };
      const result = await canonicalRequest<CanonicalReceipt>(
        `jobs/actions/${encodeURIComponent(String(item.id))}/${operation === 'invoke' ? 'invocations' : 'operations'}`,
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
    } catch (e) {
      if (active.current) {
        if (definitelyRejected(e)) setPending(null);
        setError(`${canonicalError(e)}。响应不明时请查询原操作。`);
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
      const result = await canonicalJobs.lookup(
        pending.operation,
        pending.requestKey,
        receipt?.operationId,
      );
      if (active.current) {
        setReceipt(result);
        setError('');
        if (['completed', 'rejected', 'cancelled'].includes(result.status)) setPending(null);
      }
    } catch (e) {
      if (active.current) setError(canonicalError(e));
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <article className="min-w-0 space-y-3 rounded-xl border border-border-default p-4">
      <h2 className="break-all text-lg font-semibold">{String(item.name)}</h2>
      <label>
        查看版本
        <select
          aria-label="Action version"
          className={field}
          value={revision}
          disabled={!!pending || busy}
          onChange={(e) => {
            setRevision(Number(e.target.value));
            setArguments({});
            setRawArguments(null);
            setArgumentError('');
            setReceipt(null);
            setError('');
          }}
        >
          {versions.map((v) => (
            <option key={Number(v.revision)} value={Number(v.revision)}>
              v{String(v.revision)} · {String(v.state)} · {v.enabled ? 'enabled' : 'disabled'}
            </option>
          ))}
        </select>
      </label>
      <p className="text-sm">切换版本只改变查看对象。Group、Job 与已有 Run 保持原固定引用。</p>
      <JsonView value={ref} />
      <p className="break-all text-sm">{String(definition.description ?? '')}</p>
      <p className="text-sm">
        执行：{String(obj(definition.execution).operation ?? '—')} · 效果：
        {String(definition.effectClass ?? '—')}
      </p>
      <p className="text-sm">
        固定版本不固定外部路径的文件内容。外部脚本与工作目录沿用原资源和权限策略。
      </p>
      <details>
        <summary>输入、输出、执行配置与能力</summary>
        <JsonView value={definition} />
      </details>
      <details>
        <summary>与其他版本比较</summary>
        {versions
          .filter((v) => v.revision !== revision)
          .map((v) => (
            <div key={Number(v.revision)}>
              <h3>
                v{String(v.revision)} → v{revision}
              </h3>
              <JsonView
                value={Object.keys({ ...obj(v.definition), ...definition })
                  .filter(
                    (key) =>
                      JSON.stringify(obj(v.definition)[key]) !== JSON.stringify(definition[key]),
                  )
                  .map((key) => ({
                    field: key,
                    before: obj(v.definition)[key],
                    after: definition[key],
                  }))}
              />
            </div>
          ))}
      </details>
      {!item.system && version && (
        <CanonicalDefinitionEditor
          item={item}
          definition={definition}
          onChanged={async () => {
            await load();
            await onChanged();
          }}
        />
      )}
      <fieldset ref={argumentFields} disabled={!!pending || busy} className="min-w-0 space-y-3">
        <legend>调用参数</legend>
        {Object.entries(obj(obj(definition.inputSchema).properties)).map(([name, schema]) => (
          <div key={`${revision}:${name}`} className="min-w-0 space-y-1">
            <p className="text-xs text-text-secondary">
              {Array.isArray(obj(definition.inputSchema).required) &&
              (obj(definition.inputSchema).required as unknown[]).includes(name)
                ? '必填'
                : '可选'}{' '}
              · {String(obj(schema).description ?? name)}
            </p>
            {Array.isArray(obj(schema).enum) ? (
              <label>
                {name}
                <select
                  aria-label={name}
                  className={field}
                  value={JSON.stringify(argumentsValue[name]) ?? ''}
                  onChange={(e) => {
                    setRawArguments(null);
                    setArgumentError('');
                    setArguments({ ...argumentsValue, [name]: JSON.parse(e.target.value) });
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
                value={
                  Object.hasOwn(argumentsValue, name) ? argumentsValue[name] : obj(schema).default
                }
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
          <summary>完整参数 JSON（含额外声明字段）</summary>
          <label>
            调用参数 JSON
            <textarea
              aria-label="Invocation arguments JSON"
              className={`${field} min-h-32 font-mono`}
              value={rawArguments ?? JSON.stringify(argumentsValue, null, 2)}
              onChange={(e) => {
                setRawArguments(e.target.value);
                try {
                  const value: unknown = JSON.parse(e.target.value);
                  if (!value || typeof value !== 'object' || Array.isArray(value))
                    throw new Error('参数必须是 JSON 对象');
                  setArguments(value as JsonObject);
                  setArgumentError('');
                } catch {
                  setArgumentError('参数必须是有效的 JSON 对象；当前文本尚未提交。');
                }
              }}
            />
          </label>
          {argumentError && <p role="alert">{argumentError}</p>}
        </details>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={
              !!argumentError || !version || version.state !== 'published' || !version.enabled
            }
            onClick={() => void mutate('invoke')}
          >
            运行固定版本
          </Button>
          {['publish', 'enable', 'disable'].map((op) => (
            <Button key={op} onClick={() => void mutate(op)}>
              {op}
            </Button>
          ))}
        </div>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      {pending && (
        <div role="status">
          <p className="break-all">
            原请求 {pending.requestKey} · {receipt?.status ?? 'unknown'}。仅查询，不自动重放。
          </p>
          <Button disabled={busy} onClick={() => void lookup()}>
            查询原操作
          </Button>
        </div>
      )}
      {receipt && (
        <>
          <p role="status">
            调用状态：{receipt.result?.status === 'uncertain' ? 'unknown' : receipt.status} ·{' '}
            {String(receipt.result?.status ?? '')}（原回执 {receipt.status}）
          </p>
          {rows(receipt.result?.commands).map((command, index) => (
            <section key={index} className="min-w-0">
              <h3>
                命令 {index + 1} · {String(command.status ?? '')} · exitCode{' '}
                {String(command.exitCode ?? 'unknown')}
              </h3>
              {command.stdoutTruncated === true || command.stderrTruncated === true ? (
                <p>输出已截断</p>
              ) : null}
              <JsonView value={{ stdout: command.stdout, stderr: command.stderr }} />
            </section>
          ))}
          <details>
            <summary>完整输出与原操作回执</summary>
            <JsonView value={receipt} />
          </details>
        </>
      )}
      <ReferenceUpdates
        key={String(ref.digest ?? '')}
        actionRef={ref.id ? ref : undefined}
        onInspect={onInspect}
      />
    </article>
  );
}
