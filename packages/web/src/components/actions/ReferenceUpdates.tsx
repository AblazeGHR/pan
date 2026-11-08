import { useEffect, useRef, useState } from 'react';
import { canonicalRequest } from '@/services/api';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import { useOperationIdentity, definitelyRejected } from './useOperationIdentity';
import { CanonicalGroupEditor } from './CanonicalGroupEditor';
import { Button } from '@/components/ui/Button';
import type { JsonObject, CanonicalReceipt } from '@/types/canonicalJobs';

const rows = (v: unknown): JsonObject[] => (Array.isArray(v) ? (v as JsonObject[]) : []);
const field =
  'mt-1 block w-full min-w-0 rounded-lg border border-border-default bg-bg-primary p-2 text-sm';
const json = (v: unknown) => (
  <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">
    {JSON.stringify(v, null, 2)}
  </pre>
);
export function ReferenceUpdates({
  actionRef,
  onInspect,
  initialGroupId,
  manageGroups = true,
}: {
  actionRef?: JsonObject;
  onInspect?: (ref: JsonObject) => void;
  /** Deep link from a Group entry: preselect its scope and load the catalog. */
  initialGroupId?: string;
  /** The unified Group entry owns draft/build controls; only升级 is rendered here. */
  manageGroups?: boolean;
}) {
  const [groups, setGroups] = useState<JsonObject[]>([]),
    [groupId, setGroupId] = useState(initialGroupId ?? '');
  const [jobs, setJobs] = useState<JsonObject[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [subscribers, setSubscribers] = useState<JsonObject[]>([]);
  const [preview, setPreview] = useState<{
    kind: 'group' | 'job';
    scope: JsonObject;
    value: JsonObject;
  } | null>(null);
  const [selectedSteps, setSelectedSteps] = useState<Record<string, string[]>>({});
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [identity, setIdentity] = useOperationIdentity(
      'references:' + String(actionRef?.id ?? 'groups'),
    ),
    [receipt, setReceipt] = useState<CanonicalReceipt | null>(null);
  const pending = identity?.requestKey ?? null;
  const setPending = (key: string | null) =>
    setIdentity(key ? { operation: 'update', requestKey: key } : null);
  const active = useRef(true),
    lock = useRef(false);
  useEffect(() => {
    return () => {
      active.current = false;
    };
  }, []);
  async function task(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (e) {
      if (active.current) {
        setError(canonicalError(e));
      }
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  async function load() {
    const page = await canonicalRequest<JsonObject>('action-groups');
    if (active.current) setGroups(rows(page.items));
    if (actionRef) {
      const refs = await canonicalRequest<JsonObject>(
        `jobs/actions/${encodeURIComponent(String(actionRef.id))}/job-references?limit=100`,
      );
      if (active.current) {
        setJobs(rows(refs.items));
        if (refs.nextCursor) setError('引用超过一页；当前仅选择已显示的显式批次。');
      }
    }
  }
  useEffect(() => {
    // A Group entry deep-links its own upgrade scope; load the catalog so the
    // preview can be requested without a second click.
    if (initialGroupId) void task(load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialGroupId]);
  async function review(kind: 'group' | 'job', scope: JsonObject) {    setPreview(null);
    const value = await canonicalRequest<JsonObject>(
      kind === 'group'
        ? 'action-groups/preview-reference-update'
        : 'jobs/action-references/preview',
      'POST',
      scope,
    );
    if (active.current) setPreview({ kind, scope, value });
  }
  const blocked =
    !preview ||
    rows(preview.value.excluded).length > 0 ||
    rows(preview.value.blockers).length > 0 ||
    rows(preview.value.items).some((i) => rows(i.blockers).length > 0);
  return (
    <section
      className="min-w-0 space-y-3 rounded-xl border border-border-default p-3"
      aria-label="Reference updates"
    >
      <h3 className="font-semibold">固定引用与显式升级</h3>
      <p className="text-sm">
        Group 应用仅更新草稿，需显式 build 才产生新 release；原 Worker pin 保持不变。Job
        更新只影响未来 Run，已有 queued/retry/unknown Run 保留冻结计划。
      </p>
      <Button disabled={busy || !!pending} onClick={() => void task(load)}>
        加载引用与 Groups
      </Button>
      {manageGroups && (
        <CanonicalGroupEditor groups={groups} onChanged={load} onInspect={onInspect} />
      )}
      <fieldset disabled={busy || !!pending} className="min-w-0 space-y-2">
        <legend>Group 升级范围</legend>
        <select
          aria-label="Group to upgrade"
          className={field}
          value={groupId}
          onChange={(e) => {
            setGroupId(e.target.value);
            setPreview(null);
          }}
        >
          <option value="">选择 Group</option>
          {groups.map((g) => (
            <option key={String(g.id)} value={String(g.id)}>
              {String(g.name)} · revision {String(g.revision)}
            </option>
          ))}
        </select>
        {groups
          .filter((g) => g.id === groupId)
          .map((g) => (
            <div key={String(g.id)}>
              {json(g)}
              <Button onClick={() => void task(() => review('group', { scope: 'group', groupId }))}>
                预览此 Group 全部升级
              </Button>
            </div>
          ))}
        {actionRef && (
          <Button onClick={() => void task(() => review('group', { scope: 'action', actionRef }))}>
            预览此版本更新所有 Groups
          </Button>
        )}
      </fieldset>
      {actionRef && (
        <fieldset disabled={busy || !!pending} className="min-w-0 space-y-2">
          <legend>Job 引用批次</legend>
          {jobs.map((j) => (
            <label className="block break-all" key={String(j.jobId)}>
              <input
                type="checkbox"
                checked={selected.includes(String(j.jobId))}
                onChange={(e) => {
                  setSelected(
                    e.target.checked
                      ? [...selected, String(j.jobId)]
                      : selected.filter((id) => id !== j.jobId),
                  );
                  setPreview(null);
                  setSelectedSteps((old) => ({
                    ...old,
                    [String(j.jobId)]: rows(j.steps).map((s) => String(s.stepId)),
                  }));
                }}
              />
              {String(j.name)}
              {json(j.steps)}
              {selected.includes(String(j.jobId)) &&
                rows(j.steps).map((step) => (
                  <span className="block" key={String(step.stepId)}>
                    <input
                      aria-label={`Update ${String(j.jobId)} step ${String(step.stepId)}`}
                      type="checkbox"
                      checked={(
                        selectedSteps[String(j.jobId)] ?? rows(j.steps).map((s) => String(s.stepId))
                      ).includes(String(step.stepId))}
                      onChange={(e) => {
                        const current =
                          selectedSteps[String(j.jobId)] ??
                          rows(j.steps).map((s) => String(s.stepId));
                        setSelectedSteps({
                          ...selectedSteps,
                          [String(j.jobId)]: e.target.checked
                            ? [...current, String(step.stepId)]
                            : current.filter((s) => s !== step.stepId),
                        });
                        setPreview(null);
                      }}
                    />
                    仅替换 Step {String(step.stepId)}
                  </span>
                ))}
              {rows(j.steps).map((step) => (
                <Button
                  key={String(step.stepId)}
                  onClick={(e) => {
                    e.preventDefault();
                    onInspect?.(step.actionRef as JsonObject);
                  }}
                >
                  查看 Step {String(step.stepId)} 固定 v
                  {String((step.actionRef as JsonObject).revision)}
                </Button>
              ))}
            </label>
          ))}
          <Button
            disabled={!selected.length}
            onClick={() =>
              void task(() =>
                review('job', {
                  jobs: jobs
                    .filter((j) => selected.includes(String(j.jobId)))
                    .map((j) => ({
                      jobId: j.jobId,
                      expectedRevision: j.configRevision,
                      steps: rows(j.steps)
                        .filter(
                          (s) =>
                            !selectedSteps[String(j.jobId)] ||
                            selectedSteps[String(j.jobId)]?.includes(String(s.stepId)),
                        )
                        .map((s) => ({ stepId: s.stepId, actionRef })),
                    })),
                  triggers: subscribers,
                }),
              )
            }
          >
            预览未来 Run 引用更新
          </Button>
        </fieldset>
      )}
      {preview && (
        <div className="min-w-0 space-y-2">
          <h4>差异、范围与阻断原因</h4>
          {json(preview.value)}
          {rows(preview.value.excluded).length > 0 && (
            <p role="status">
              旧 JSON Group 尚未完成安全 writer adoption，当前只可预览；未迁移、未升级。
            </p>
          )}
          {preview.kind === 'job' &&
            rows(preview.value.subscribers)
              .filter((s) => s.pinned)
              .map((s) => (
                <label className="block" key={String(s.triggerId)}>
                  <input
                    type="checkbox"
                    checked={subscribers.some((t) => t.triggerId === s.triggerId)}
                    onChange={(e) => {
                      setSubscribers(
                        e.target.checked
                          ? [
                              ...subscribers,
                              { triggerId: s.triggerId, expectedRevision: s.revision },
                            ]
                          : subscribers.filter((t) => t.triggerId !== s.triggerId),
                      );
                      setPreview(null);
                    }}
                  />
                  同步固定订阅 Trigger {String(s.triggerId)}（选择后重新预览）
                </label>
              ))}
          <Button
            disabled={busy || !!pending || blocked}
            onClick={() =>
              void task(async () => {
                const requestKey = crypto.randomUUID();
                setReceipt(null);
                setPending(requestKey);
                const body =
                  preview.kind === 'group'
                    ? {
                        selection: preview.scope,
                        previewDigest: preview.value.previewDigest,
                        targets: rows(preview.value.items).map((i) => ({
                          groupId: i.groupId,
                          expectedRevision: i.expectedRevision,
                        })),
                        requestKey,
                      }
                    : {
                        scope: preview.scope,
                        previewDigest: preview.value.previewDigest,
                        requestKey,
                      };
                const result = await canonicalRequest<CanonicalReceipt>(
                  preview.kind === 'group'
                    ? 'action-groups/apply-reference-update'
                    : 'jobs/action-references/apply',
                  'POST',
                  body,
                ).catch((e) => {
                  if (definitelyRejected(e)) setPending(null);
                  throw e;
                });
                if (active.current) {
                  setReceipt(result);
                  if (['completed', 'rejected', 'cancelled'].includes(result.status)) {
                    setPending(null);
                    setPreview(null);
                    await load();
                  }
                }
              })
            }
          >
            应用已预览批次
          </Button>
        </div>
      )}
      {pending && (
        <div role="status">
          <p className="break-all">
            更新原身份 {pending} · {receipt?.status ?? 'unknown'}
          </p>
          <Button
            disabled={busy}
            onClick={() =>
              void task(async () => {
                const result = await canonicalJobs.lookup('update', pending, receipt?.operationId);
                if (active.current) {
                  setReceipt(result);
                  if (['completed', 'rejected', 'cancelled'].includes(result.status)) {
                    setPending(null);
                    setPreview(null);
                  }
                }
              })
            }
          >
            查询原更新操作
          </Button>
        </div>
      )}
      {receipt && json(receipt)}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
