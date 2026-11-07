import { useEffect, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError } from '@/services/canonicalJobs';
import type { CanonicalObject, JsonObject } from '@/types/canonicalJobs';
const obj = (v: unknown): JsonObject =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as JsonObject) : {};
export function TriggerTarget({
  value,
  jobs,
  onChange,
}: {
  value: JsonObject;
  jobs: CanonicalObject[];
  onChange: (v: JsonObject) => void;
}) {
  const [actions, setActions] = useState<JsonObject[]>([]),
    [versions, setVersions] = useState<JsonObject[]>([]),
    [error, setError] = useState('');
  const [loadedJobs, setLoadedJobs] = useState<CanonicalObject[]>([]);
  useEffect(() => {
    let active = true;
    if (!jobs.length)
      void canonicalRequest<{ items: CanonicalObject[] }>('jobs?limit=100')
        .then((page) => {
          if (active) setLoadedJobs(page.items);
        })
        .catch((e) => {
          if (active) setError(canonicalError(e));
        });
    return () => {
      active = false;
    };
  }, [jobs.length]);
  const availableJobs = jobs.length ? jobs : loadedJobs;
  const target = obj(value.target),
    ref = obj(target.actionRef),
    type = String(target.type ?? 'job');
  useEffect(() => {
    let active = true;
    canonicalRequest<{ items: JsonObject[] }>('jobs/actions')
      .then((p) => {
        if (active) setActions(p.items);
      })
      .catch((e) => active && setError(canonicalError(e)));
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setVersions([]);
    if (ref.id)
      canonicalRequest<{ items: JsonObject[] }>(
        `jobs/actions/${encodeURIComponent(String(ref.id))}/versions`,
      )
        .then((p) => {
          if (active) setVersions(p.items);
        })
        .catch((e) => active && setError(canonicalError(e)));
    return () => {
      active = false;
    };
  }, [ref.id]);
  const update = (next: JsonObject) => {
    const copy: JsonObject = { ...value, target: next };
    delete copy.targetJobId;
    onChange(copy);
  };
  const cls = 'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary p-2';
  return (
    <div className="min-w-0 space-y-3">
      <label>
        目标类型
        <select
          aria-label="Trigger target type"
          className={cls}
          value={type}
          onChange={(e) =>
            update(
              e.target.value === 'action'
                ? { type: 'action', actionRef: { id: '', revision: 1, digest: '' } }
                : { type: 'job', jobId: '', configRevision: 1 },
            )
          }
        >
          <option value="job">Job</option>
          <option value="action">Action</option>
        </select>
      </label>
      {type === 'job' ? (
        <label>
          Target canonical Job
          <select
            aria-label="Target canonical Job"
            className={cls}
            value={String(target.jobId ?? value.targetJobId ?? '')}
            onChange={(e) => {
              const job = availableJobs.find((j) => j.id === e.target.value);
              update({
                type: 'job',
                jobId: e.target.value,
                configRevision: job?.configRevision ?? 1,
              });
            }}
          >
            <option value="">Choose a Job</option>
            {availableJobs.map((j) => (
              <option key={j.id} value={j.id} disabled={['draft', 'retired'].includes(j.state)}>
                {j.name} · {j.state}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <>
          <label>
            目标 Action
            <select
              aria-label="Trigger Action"
              className={cls}
              value={String(ref.id ?? '')}
              onChange={(e) =>
                update({
                  type: 'action',
                  actionRef: { id: e.target.value, revision: 1, digest: '' },
                })
              }
            >
              <option value="">选择 Action</option>
              {actions.map((a) => (
                <option key={String(a.id)} value={String(a.id)}>
                  {String(a.name)}
                </option>
              ))}
            </select>
          </label>
          <label>
            固定 Action 版本
            <select
              aria-label="Trigger Action version"
              className={cls}
              value={ref.digest ? String(ref.revision) : ''}
              onChange={(e) =>
                update({
                  type: 'action',
                  actionRef: versions.find((v) => String(v.revision) === e.target.value)?.ref,
                })
              }
            >
              <option value="">选择发布版本</option>
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
          </label>
          <p>
            直接 Action 无 Job 自动恢复策略。失败或 unknown 查询原事件与
            invocation，不重新执行效果。
          </p>
        </>
      )}
      <p className="break-all text-xs">
        固定目标：{JSON.stringify(value.target ?? { targetJobId: value.targetJobId })}
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
