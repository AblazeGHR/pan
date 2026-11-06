import { useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { Button } from '@/components/ui/Button';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import { useOperationIdentity, definitelyRejected } from './useOperationIdentity';

/** Pass the whole definition unchanged: advanced business fields are never projected away. */
export function CanonicalDefinitionEditor({
  item,
  definition,
  onChanged,
}: {
  item?: JsonObject;
  definition?: JsonObject;
  onChanged: () => Promise<void>;
}) {
  const [text, setText] = useState<string | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [pending, setPending] = useOperationIdentity('definition:' + String(item?.id ?? 'new'));
  const [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const lock = useRef(false);
  const finish = async (value: CanonicalReceipt) => {
    setReceipt(value);
    if (['completed', 'rejected', 'cancelled'].includes(value.status)) {
      setPending(null);
      if (value.status === 'completed') {
        setText(null);
        await onChanged();
      }
    }
  };
  async function save() {
    if (lock.current || pending || text === null) return;
    let value: unknown;
    try {
      value = JSON.parse(text);
      if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new Error('定义必须是 JSON 对象');
    } catch (e) {
      setError(canonicalError(e));
      return;
    }
    lock.current = true;
    setBusy(true);
    setError('');
    const identity = { operation: item ? 'update' : 'create', requestKey: crypto.randomUUID() };
    setPending(identity);
    setReceipt(null);
    try {
      await finish(
        await canonicalRequest<CanonicalReceipt>(
          item ? `jobs/actions/${encodeURIComponent(String(item.id))}` : 'jobs/actions',
          item ? 'PUT' : 'POST',
          {
            definition: value,
            expectedRevision: item?.revision ?? 0,
            requestKey: identity.requestKey,
          },
        ),
      );
    } catch (e) {
      if (definitelyRejected(e)) setPending(null);
      setError(canonicalError(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="min-w-0 space-y-2">
      <Button
        disabled={busy || !!pending}
        onClick={async () => {
          if (text !== null) {
            setText(null);
            return;
          }
          if (definition) {
            setText(JSON.stringify(definition, null, 2));
            return;
          }
          setBusy(true);
          setError('');
          try {
            const catalog = await canonicalJobs.actions();
            const template = catalog.items.find(
              (a) => (a.execution as JsonObject)?.operation === 'process.command',
            );
            if (!template) throw new Error('服务未提供命令定义模板，请检查目录。');
            const page = await canonicalRequest<{ items: JsonObject[] }>(
              `jobs/actions/${encodeURIComponent(String(template.id))}/versions`,
            );
            const source = page.items.at(-1)?.definition as JsonObject;
            setText(JSON.stringify({ ...source, name: 'New Action', system: false }, null, 2));
          } catch (e) {
            setError(canonicalError(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {item ? '编辑为新草稿版本' : '新建统一 Action'}
      </Button>
      {text !== null && (
        <>
          <p className="text-sm">
            完整定义使用现有服务 schema 校验；保存不发布。系统定义不可改写。
          </p>
          <label>
            Action definition
            <textarea
              aria-label="Action definition"
              className="block min-h-60 w-full min-w-0 rounded border border-border-default bg-bg-primary p-2 font-mono text-xs"
              value={text}
              disabled={busy || !!pending}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <Button disabled={busy || !!pending} onClick={() => void save()}>
            保存草稿版本
          </Button>
        </>
      )}
      {pending && (
        <div role="status">
          <p className="break-all">
            原请求 {pending.requestKey} · {receipt?.status ?? 'unknown'}
          </p>
          <Button
            disabled={busy}
            onClick={async () => {
              if (lock.current) return;
              lock.current = true;
              setBusy(true);
              try {
                await finish(
                  await canonicalJobs.lookup(
                    pending.operation,
                    pending.requestKey,
                    receipt?.operationId,
                  ),
                );
              } catch (e) {
                setError(canonicalError(e));
              } finally {
                lock.current = false;
                setBusy(false);
              }
            }}
          >
            查询原保存操作
          </Button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {receipt && (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">
          {JSON.stringify(receipt, null, 2)}
        </pre>
      )}
    </section>
  );
}
