import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
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

const kinds: CanonicalKind[] = ['jobs', 'triggers', 'event-creators', 'job-templates'];
const labels: Record<CanonicalKind, string> = {
  jobs: 'Jobs',
  triggers: 'Triggers',
  'event-creators': 'EventCreators',
  'job-templates': 'JobTemplates',
};
const field =
  'mt-1 block w-full min-w-0 rounded border border-border-default bg-bg-primary p-2 text-sm';
const card = 'min-w-0 rounded-lg border border-border-default bg-bg-secondary p-3';
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
function objectJson(text: string): JsonObject {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('请输入 JSON 对象。');
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

/** All state and requests belong to this canonical page, isolated from legacy stores. */
export default function CanonicalJobsView() {
  const token = useAutomationAuth((s) => s.token);
  // Changing authorization remounts the workspace: late responses cannot expose an earlier user's data.
  return (
    <section
      aria-label="Canonical management"
      className="h-full min-w-0 overflow-y-auto p-3 pt-12 text-text-primary sm:p-6"
    >
      <div className="mx-auto min-w-0 max-w-6xl space-y-4">
        <header className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">Canonical Jobs</h1>
            <p className="text-sm text-text-secondary">统一定义与 Run 管理 · 手动刷新</p>
          </div>
          <nav className="flex flex-wrap gap-3 text-sm">
            <Link to="/automation">Automation</Link>
          </nav>
        </header>
        <JobUserAuthorization />
        {token ? <Workspace key={token} /> : <p role="status">请登录以加载 canonical 数据。</p>}
      </div>
    </section>
  );
}
function Workspace() {
  const [kind, setKind] = useState<CanonicalKind>('jobs');
  return (
    <>
      <nav aria-label="Canonical sections" className="flex min-w-0 flex-wrap gap-2">
        {kinds.map((value) => (
          <Button
            type="button"
            key={value}
            aria-pressed={kind === value}
            onClick={() => setKind(value)}
          >
            {labels[value]}
          </Button>
        ))}
      </nav>
      <Collection key={kind} kind={kind} />
    </>
  );
}
function Collection({ kind }: { kind: CanonicalKind }) {
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
  const [retiring, setRetiring] = useState<CanonicalObject | null>(null);
  const [selectedJob, setSelectedJob] = useState<CanonicalObject | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const busy = working || unresolved(pending);
  const alive = useRef(true);
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
  }, [load]);
  async function mutate(operation: string, task: (key: string) => Promise<CanonicalReceipt>) {
    if (busy) return;
    const requestKey = crypto.randomUUID();
    setBusy(true);
    setError('');
    setPending({ operation, requestKey });
    try {
      const receipt = await task(requestKey);
      if (!alive.current) return;
      setPending({ operation, requestKey, receipt });
      if (receipt.status === 'completed') {
        setEditor(null);
        setRetiring(null);
      }
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
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-semibold">Canonical {labels[kind]}</h2>
        <Button type="button" disabled={loading || busy} onClick={() => void load()}>
          刷新列表
        </Button>
        <Button
          type="button"
          disabled={busy || !!editor}
          onClick={() => {
            const definition = { ...examples[kind] };
            delete definition.name;
            setEditor({ item: null, name: '', json: JSON.stringify(definition, null, 2) });
          }}
        >
          创建 {labels[kind]}
        </Button>
      </div>
      {loading && <p role="status">加载中…</p>}
      {error && (
        <p role="alert" className="break-words text-danger">
          {error}
        </p>
      )}
      {pending && (
        <OperationStatus
          pending={pending}
          onChange={(value) => {
            setPending(value);
            if (value.receipt?.status === 'completed') {
              setEditor(null);
              setRetiring(null);
              void load();
            }
          }}
        />
      )}
      {editor && (
        <form
          aria-label={`编辑 ${labels[kind]}`}
          className={`${card} space-y-3`}
          onSubmit={(e) => {
            e.preventDefault();
            try {
              const definition = { ...objectJson(editor.json), name: editor.name };
              void mutate(editor.item ? 'update' : 'create', (key) =>
                canonicalJobs.write(kind, editor.item, definition, key),
              );
            } catch (failure) {
              setError(canonicalError(failure));
            }
          }}
        >
          <label className="block text-sm">
            名称
            <input
              className={field}
              disabled={busy}
              required
              maxLength={200}
              value={editor.name}
              onChange={(e) => setEditor({ ...editor, name: e.target.value })}
            />
          </label>
          <label className="block text-sm">
            定义 JSON（名称单独填写）
            <textarea
              disabled={busy}
              spellCheck={false}
              className={`${field} h-72 font-mono`}
              value={editor.json}
              onChange={(e) => setEditor({ ...editor, json: e.target.value })}
            />
          </label>
          <p className="text-xs text-text-secondary">
            只提交定义字段。引用需包含服务端的
            id、revision、digest；空示例需要补全。编辑使用打开时的版本{' '}
            {editor.item ? revisionOf(editor.item) : 0}。
          </p>
          <details>
            <summary>定义字段与约束</summary>
            <pre className="max-w-full whitespace-pre-wrap break-all text-xs">
              {JSON.stringify(definitionSchemas[kind], null, 2)}
            </pre>
          </details>
          {kind === 'jobs' && <ActionCatalog />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              保存定义
            </Button>
            <Button type="button" disabled={busy} onClick={() => setEditor(null)}>
              关闭编辑
            </Button>
          </div>
        </form>
      )}
      {retiring && (
        <section aria-label="确认 retire" className={`${card} space-y-2`}>
          <p className="break-all">
            退休 {retiring.name}（{retiring.id}）？
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={busy}
              onClick={() =>
                void mutate('retire', (key) => canonicalJobs.retire(kind, retiring, key))
              }
            >
              确认 retire
            </Button>
            <Button type="button" disabled={busy} onClick={() => setRetiring(null)}>
              返回
            </Button>
          </div>
        </section>
      )}
      {!loading && !items.length && <p>暂无 canonical {labels[kind]}。</p>}
      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        {items.map((item) => (
          <article
            key={item.id}
            aria-label={`${labels[kind]} ${item.name}`}
            className={`${card} space-y-2`}
          >
            <h3 className="break-words font-medium">{item.name}</h3>
            <p className="break-all text-xs">{item.id}</p>
            <p className="break-words text-sm">
              {item.state} · 定义版本 {revisionOf(item)} · 状态版本 {item.stateRevision}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                aria-label={`编辑 ${item.name}`}
                className="max-w-full break-all"
                disabled={busy || !!editor}
                onClick={() => {
                  const definition = definitionOf(kind, item);
                  delete definition.name;
                  setEditor({ item, name: item.name, json: JSON.stringify(definition, null, 2) });
                }}
              >
                编辑
              </Button>
              <Button
                type="button"
                aria-label={`retire ${item.name}`}
                className="max-w-full break-all"
                disabled={busy || item.state === 'retired'}
                onClick={() => setRetiring(item)}
              >
                retire
              </Button>
              {kind === 'jobs' && (
                <Button
                  type="button"
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
        <Button type="button" disabled={loading || busy} onClick={() => void load(cursor)}>
          加载更多
        </Button>
      )}
      {selectedJob && (
        <RunList key={selectedJob.id} job={selectedJob} onClose={() => setSelectedJob(null)} />
      )}
    </div>
  );
}
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
    <section aria-label="操作回执" className={`${card} space-y-2 break-all text-sm`}>
      <p role="status">
        {receipt
          ? `操作状态：${receipt.status}`
          : pending.rejected
            ? '请求已拒绝；请修正问题后重新提交。'
            : '请求结果未确认，请按原身份查询。'}
        {receipt && ['accepted', 'pending', 'unknown'].includes(receipt.status)
          ? '；尚未确认效果成功。'
          : ''}
      </p>
      <p>requestKey: {pending.requestKey}</p>
      {receipt && <p>operationId: {receipt.operationId}</p>}
      {receipt?.error && (
        <p role="alert">{canonicalErrorMessages[receipt.error.code] ?? receipt.error.code}</p>
      )}
      <Button
        type="button"
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
        查询原操作
      </Button>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
function ActionCatalog() {
  const [items, setItems] = useState<JsonObject[]>([]);
  const [error, setError] = useState('');
  return (
    <details
      onToggle={(e) => {
        if (e.currentTarget.open && !items.length)
          void canonicalJobs
            .actions()
            .then((page) => setItems(page.items))
            .catch((failure: unknown) => setError(canonicalError(failure)));
      }}
    >
      <summary>可引用的 published Actions</summary>
      {error && <p role="alert">{error}</p>}
      <pre className="max-w-full whitespace-pre-wrap break-all text-xs">
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
    const requestKey = crypto.randomUUID();
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
      <div className="flex flex-wrap gap-2">
        <h2 className="mr-auto break-all font-semibold">Runs · {job.name}</h2>
        <Button type="button" disabled={busy || loading} onClick={() => void load()}>
          刷新 Runs
        </Button>
        <Button type="button" disabled={busy} onClick={onClose}>
          关闭 Runs
        </Button>
      </div>
      {loading && <p role="status">加载 Runs…</p>}
      {error && (
        <p role="alert" className="break-words text-danger">
          {error}
        </p>
      )}
      {pending && <OperationStatus pending={pending} onChange={setPending} />}
      {!loading && !runs.length && <p>暂无 Runs。</p>}
      {runs.map((run) => (
        <article key={run.runId} className={`${card} space-y-2`}>
          <p className="break-all">
            {run.runId} · {run.state} · 状态版本 {run.stateRevision}
          </p>
          <div className="flex flex-wrap gap-2">
            {runOperations.map((operation) => (
              <Button
                type="button"
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
        <Button type="button" disabled={loading || busy} onClick={() => void load(cursor)}>
          更多 Runs
        </Button>
      )}
      {selected && (
        <form
          aria-label="Run operation"
          className="min-w-0 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            void execute();
          }}
        >
          <p className="break-all">
            确认 {selected.operation} · {selected.run.runId}？
          </p>
          {selected.operation === 'retry' && (
            <label className="block text-sm">
              Step ID（可选，默认失败边界）
              <input className={field} value={stepId} onChange={(e) => setStepId(e.target.value)} />
            </label>
          )}
          {['run-again', 'clone-and-run'].includes(selected.operation) && (
            <label className="block text-sm">
              运行参数 JSON（input；clone 可含 overrides.name/description）
              <textarea
                className={`${field} h-24 font-mono`}
                value={parameters}
                onChange={(e) => setParameters(e.target.value)}
              />
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} type="submit">
              确认操作
            </Button>
            <Button type="button" disabled={busy} onClick={() => setSelected(null)}>
              返回
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
