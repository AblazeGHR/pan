import { useEffect, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { Button } from '@/components/ui/Button';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';
import { useOperationIdentity, definitelyRejected } from './useOperationIdentity';
const rows = (v: unknown): JsonObject[] => (Array.isArray(v) ? (v as JsonObject[]) : []);
const obj = (v: unknown): JsonObject =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as JsonObject) : {};
const field =
  'block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary p-2 text-sm';
export function CanonicalGroupEditor({
  groups,
  onChanged,
  onInspect,
}: {
  groups: JsonObject[];
  onChanged: () => Promise<void>;
  onInspect?: (ref: JsonObject) => void;
}) {
  const [editing, setEditing] = useState<JsonObject | null>(null),
    [original, setOriginal] = useState<JsonObject | null>(null),
    [catalog, setCatalog] = useState<JsonObject[]>([]),
    [refs, setRefs] = useState<Record<string, JsonObject>>({});
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [receipt, setReceipt] = useState<CanonicalReceipt | null>(null),
    [release, setRelease] = useState<JsonObject | null>(null);
  const [pending, setPending] = useOperationIdentity('group-editor');
  const [releaseVersion, setReleaseVersion] = useState('');
  const lock = useRef(false);
  useEffect(() => {
    let active = true;
    canonicalRequest<JsonObject>('jobs/actions')
      .then((p) => {
        if (active) {
          setCatalog(rows(p.items));
          setRefs(obj(p.references) as Record<string, JsonObject>);
        }
      })
      .catch((e) => active && setError(canonicalError(e)));
    return () => {
      active = false;
    };
  }, []);
  async function finish(result: CanonicalReceipt) {
    setReceipt(result);
    if (['completed', 'rejected', 'cancelled'].includes(result.status)) {
      setPending(null);
      if (result.status === 'completed') {
        setEditing(null);
        await onChanged();
      }
    }
  }
  async function write(operation: 'update' | 'publish' | 'disable' | 'delete', group: JsonObject) {
    if (lock.current || pending) return;
    lock.current = true;
    setBusy(true);
    setError('');
    const identity = { operation, requestKey: crypto.randomUUID() };
    setPending(identity);
    setReceipt(null);
    const base = `action-groups/${encodeURIComponent(String(group.id))}`;
    try {
      const body = {
        expectedRevision: operation === 'update' ? (original?.revision ?? 0) : group.revision,
        requestKey: identity.requestKey,
        ...(operation === 'update'
          ? { group: { id: group.id, name: group.name, tools: group.tools } }
          : {}),
      };
      await finish(
        await canonicalRequest<CanonicalReceipt>(
          base + (operation === 'publish' ? '/build' : operation === 'disable' ? '/unbuild' : ''),
          operation === 'update' ? 'PUT' : operation === 'delete' ? 'DELETE' : 'POST',
          body,
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
    <section className="min-w-0 space-y-2" aria-label="Canonical Groups">
      <h4>Group 草稿与发布</h4>
      <label>
        历史 release 身份
        <input
          className={field}
          value={releaseVersion}
          onChange={(e) => setReleaseVersion(e.target.value)}
        />
      </label>
      <Button
        disabled={!releaseVersion || busy}
        onClick={async () => {
          try {
            setRelease(
              await canonicalRequest<JsonObject>(
                `action-groups/releases/${encodeURIComponent(releaseVersion)}`,
              ),
            );
          } catch (e) {
            setError(canonicalError(e));
          }
        }}
      >
        查看历史 release
      </Button>
      <Button
        disabled={busy || !!pending}
        onClick={() => {
          setOriginal(null);
          setEditing({ id: '', name: '', tools: [] });
        }}
      >
        新建固定版本 Group
      </Button>
      {groups.map((g) => (
        <div
          className="min-w-0 space-y-2 rounded border border-border-default p-2"
          key={String(g.id)}
        >
          <p className="break-all">
            {String(g.name)} · revision {String(g.revision)} · release{' '}
            {String(g.release ?? '未发布')}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy || !!pending}
              onClick={() => {
                setOriginal(g);
                setEditing(structuredClone(g));
              }}
            >
              编辑 {String(g.name)}
            </Button>
            <Button disabled={busy || !!pending} onClick={() => void write('publish', g)}>
              Build {String(g.name)}
            </Button>
            <Button
              disabled={busy || !!pending || !g.release}
              onClick={() => void write('disable', g)}
            >
              Unbuild {String(g.name)}
            </Button>
            {!!g.release && (
              <Button
                onClick={async () => {
                  try {
                    setRelease(
                      await canonicalRequest<JsonObject>(
                        `action-groups/releases/${encodeURIComponent(String(g.release))}`,
                      ),
                    );
                  } catch (e) {
                    setError(canonicalError(e));
                  }
                }}
              >
                查看固定 release
              </Button>
            )}
          </div>
        </div>
      ))}
      {editing && (
        <fieldset disabled={busy || !!pending} className="min-w-0 space-y-2">
          <legend>固定工具定义</legend>
          {['id', 'name'].map((key) => (
            <label className="block" key={key}>
              {key}
              <input
                className={field}
                disabled={key === 'id' && !!original}
                value={String(editing[key] ?? '')}
                onChange={(e) => setEditing({ ...editing, [key]: e.target.value })}
              />
            </label>
          ))}
          {rows(editing.tools).map((tool, index) => (
            <div className="min-w-0 space-y-2" key={index}>
              <label>
                工具别名
                <input
                  aria-label={`Tool alias ${index + 1}`}
                  className={field}
                  value={String(tool.alias)}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      tools: rows(editing.tools).map((t, i) =>
                        i === index ? { ...t, alias: e.target.value } : t,
                      ),
                    })
                  }
                />
              </label>
              <label>
                固定 Action
                <select
                  aria-label={`Group Action ${index + 1}`}
                  className={field}
                  value={String(obj(tool.actionRef).id ?? '')}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      tools: rows(editing.tools).map((t, i) =>
                        i === index ? { ...t, actionRef: refs[e.target.value] } : t,
                      ),
                    })
                  }
                >
                  <option value="">选择 Action</option>
                  {catalog.map((a) => (
                    <option key={String(a.id)} value={String(a.id)}>
                      {String(a.name)}
                    </option>
                  ))}
                </select>
              </label>
              <FixedVersion
                refValue={obj(tool.actionRef)}
                onChange={(actionRef) =>
                  setEditing({
                    ...editing,
                    tools: rows(editing.tools).map((t, i) =>
                      i === index ? { ...t, actionRef } : t,
                    ),
                  })
                }
              />
              <Button
                onClick={() =>
                  setEditing({
                    ...editing,
                    tools: rows(editing.tools).filter((_, i) => i !== index),
                  })
                }
              >
                移除工具 {index + 1}
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() =>
                setEditing({
                  ...editing,
                  tools: [...rows(editing.tools), { alias: '', actionRef: {} }],
                })
              }
            >
              添加固定工具
            </Button>
            <Button onClick={() => void write('update', editing)}>保存 Group 草稿</Button>
            <Button onClick={() => setEditing(null)}>取消 Group 编辑</Button>
          </div>
        </fieldset>
      )}
      {release &&
        rows(release.tools).map((tool, index) => (
          <Button key={index} onClick={() => onInspect?.(obj(tool.actionRef))}>
            查看 {String(tool.alias)} 固定 v{String(obj(tool.actionRef).revision)}
          </Button>
        ))}
      {release && (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">
          {JSON.stringify(release, null, 2)}
        </pre>
      )}
      {pending && (
        <div role="status">
          <p className="break-all">
            Group 原请求 {pending.requestKey} · {receipt?.status ?? 'unknown'}
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
            查询原 Group 操作
          </Button>
        </div>
      )}
      {receipt && (
        <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all text-xs">
          {JSON.stringify(receipt, null, 2)}
        </pre>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
function FixedVersion({
  refValue,
  onChange,
}: {
  refValue: JsonObject;
  onChange: (v: unknown) => void;
}) {
  const [versions, setVersions] = useState<JsonObject[]>([]),
    [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setVersions([]);
    if (refValue.id)
      canonicalRequest<{ items: JsonObject[] }>(
        `jobs/actions/${encodeURIComponent(String(refValue.id))}/versions`,
      )
        .then((p) => active && setVersions(p.items))
        .catch((e) => active && setError(canonicalError(e)));
    return () => {
      active = false;
    };
  }, [refValue.id]);
  return (
    <label>
      固定版本
      <select
        aria-label="Group fixed version"
        className={field}
        value={String(refValue.revision ?? '')}
        onChange={(e) => onChange(versions.find((v) => String(v.revision) === e.target.value)?.ref)}
      >
        <option value="">选择版本</option>
        {versions.map((v) => (
          <option
            key={Number(v.revision)}
            value={Number(v.revision)}
            disabled={v.state !== 'published' || !v.enabled}
          >
            v{String(v.revision)} · {String(v.state)}
          </option>
        ))}
      </select>
      <span className="block break-all text-xs">{JSON.stringify(refValue)}</span>
      {error && <span role="alert">{error}</span>}
    </label>
  );
}
