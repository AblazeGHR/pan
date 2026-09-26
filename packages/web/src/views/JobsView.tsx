import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ListChecks, MoreHorizontal, Pause, Play, Plus, Trash2 } from 'lucide-react';
import { NewJobForm } from '@/components/jobs/NewJobForm';
import { JobDetailDrawer } from '@/components/jobs/JobDetailDrawer';
import { entryToSpec } from '@/components/jobs/ScheduleListEditor';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { wsClient } from '@/services/ws';
import { scheduleEntries } from '@/types/jobs';
import {
  createJob,
  deleteJob as deleteJobApi,
  fetchJob,
  fetchJobKinds,
  fetchJobRuns,
  fetchJobs,
  patchJob,
  runJobNow,
} from '@/services/api';
import type {
  Job,
  JobCreateInput,
  JobKind,
  JobKindMeta,
  JobPatchInput,
  JobRunRecord,
  JobStatus,
} from '@/types/jobs';

type Tab = 'list' | 'create';

type StatusFilter =
  'all' | 'active' | 'scheduled' | 'completed' | 'failed' | 'timed_out' | 'undeliverable';

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'scheduled', label: 'Scheduled' },
  { key: 'completed', label: 'Completed' },
  { key: 'failed', label: 'Failed' },
  { key: 'timed_out', label: 'Timeout' },
  { key: 'undeliverable', label: 'Undeliverable' },
];

const FALLBACK_KINDS: JobKind[] = [
  'background-process',
  'session-message',
  'session-broadcast',
  'main-lifecycle',
  'scheduled-task',
];

const RUNS_PAGE = 20;

const selectClass =
  'w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50';

const errMsg = (e: unknown) => (e instanceof Error ? e.message : 'Request failed');

/** 状态 → 徽标配色（沿用主题 token）。 */
function statusColor(status: string): string {
  switch (status) {
    case 'running':
    case 'starting':
      return 'border-accent/50 bg-accent/10 text-accent';
    case 'scheduled':
      return 'border-warning/50 bg-warning/10 text-warning';
    case 'completed':
      return 'border-success/50 bg-success/10 text-success';
    case 'failed':
    case 'timed_out':
      return 'border-danger/50 bg-danger/10 text-danger';
    default:
      return 'border-border-default bg-bg-tertiary text-text-secondary';
  }
}

function sourceSummary(job: Job): string {
  const s = job.source;
  if (s.type === 'plugin') return `plugin:${s.pluginName ?? '?'}`;
  if (s.sessionId) return `${s.type}:${s.sessionId.slice(0, 8)}…`;
  return s.type;
}

function formatDateTime(value?: number | string | null): string {
  if (value === null || value === undefined || value === '') return '—';
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 下次触发 = 各 enabled entry 中最早的 nextFireAt。 */
function nextFireAt(job: Job): string | null {
  let min: string | null = null;
  for (const e of scheduleEntries(job.schedule)) {
    if (e.enabled && e.nextFireAt && (min === null || e.nextFireAt < min)) min = e.nextFireAt;
  }
  return min;
}

/** 最近结果摘要：lastDelivery（含 partial）> lastError > lastStatus。 */
function lastResultSummary(job: Job): string {
  if (job.lastDelivery) {
    const { status, results, errors } = job.lastDelivery;
    const bad = errors?.length ?? 0;
    const ok = results?.length ?? 0;
    return `last ${status}${bad > 0 ? ` · ${ok} ok / ${bad} err` : ''}`;
  }
  if (job.lastError) return `last error: ${job.lastError}`;
  if (job.lastStatus) return `last ${job.lastStatus}`;
  return `runs ${job.runCount}`;
}

/** 活跃优先排序档位：running → starting → scheduled → pending → completed → failed → timed_out → cancelled。 */
function activeRank(status: JobStatus): number {
  switch (status) {
    case 'running':
      return 0;
    case 'starting':
      return 1;
    case 'scheduled':
      return 2;
    case 'pending':
      return 3;
    case 'completed':
      return 4;
    case 'failed':
      return 5;
    case 'timed_out':
      return 6;
    case 'cancelled':
      return 7;
    default:
      return 8;
  }
}

function updatedAtMs(job: Job): number {
  const v = job.updatedAt;
  return typeof v === 'number' ? v * 1000 : Date.parse(v) || 0;
}

/** 活跃优先；同组内按 updatedAt 倒序（新在前）。 */
function compareJobs(a: Job, b: Job): number {
  const ra = activeRank(a.status);
  const rb = activeRank(b.status);
  if (ra !== rb) return ra - rb;
  return updatedAtMs(b) - updatedAtMs(a);
}

/** 状态 chips 匹配（与 kind 下拉 AND 叠加）。 */
function matchesStatusFilter(job: Job, filter: StatusFilter): boolean {
  switch (filter) {
    case 'active':
      return job.status === 'pending' || job.status === 'starting' || job.status === 'running';
    case 'scheduled':
      return job.status === 'scheduled';
    case 'completed':
      return job.status === 'completed';
    case 'failed':
      return job.status === 'failed';
    case 'timed_out':
      return job.status === 'timed_out';
    case 'undeliverable':
      return job.lastStatus === 'undeliverable' || (job.undeliveredFires?.length ?? 0) > 0;
    default:
      return true;
  }
}

const menuItemClass =
  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20';

type BulkAction = 'pause' | 'delete';

interface BulkFeedback {
  action: BulkAction;
  succeeded: number;
  alreadyDeleted: number;
  failed: Record<string, { name: string; error: string }>;
}

function JobRow({
  job,
  kindLabel,
  onOpen,
  menuOpen,
  onToggleMenu,
  onRunNow,
  onTogglePaused,
  onDelete,
  selectionMode,
  selected,
  selectionDisabled,
  onToggleSelected,
}: {
  job: Job;
  kindLabel: string;
  onOpen: () => void;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onRunNow: () => void;
  onTogglePaused: () => void;
  onDelete: () => void;
  selectionMode: boolean;
  selected: boolean;
  selectionDisabled: boolean;
  onToggleSelected: () => void;
}) {
  const next = nextFireAt(job);
  const backlog = job.undeliveredFires?.length ?? 0;
  const isScheduledTask = job.kind === 'scheduled-task';
  const hasTarget = !!job.target.sessionId;
  return (
    <div
      onClick={onOpen}
      className="flex flex-col gap-1.5 rounded border border-border-default bg-bg-primary px-3 py-2.5 transition-colors hover:bg-bg-secondary/50 cursor-pointer"
    >
      <div className="flex min-w-0 items-center gap-2">
        {selectionMode && (
          <label
            className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              aria-label={`Select job ${job.name}`}
              checked={selected}
              disabled={selectionDisabled}
              onClick={(e) => e.stopPropagation()}
              onChange={onToggleSelected}
              className="h-3.5 w-3.5 cursor-pointer accent-accent disabled:cursor-not-allowed"
            />
          </label>
        )}
        <span
          className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-medium ${statusColor(job.status)}`}
        >
          {job.status}
        </span>
        <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1.5 py-px text-[10px] text-text-secondary">
          {kindLabel}
        </span>
        <div className="min-w-0 flex-1 truncate text-sm text-text-primary" title={job.name}>
          {job.name}
        </div>
        {job.paused && (
          <span className="shrink-0 rounded border border-warning/50 bg-warning/10 px-1 py-px text-[10px] text-warning">
            Paused
          </span>
        )}
        <div className="relative shrink-0">
          <button
            type="button"
            disabled={selectionDisabled}
            onClick={(e) => {
              e.stopPropagation();
              onToggleMenu();
            }}
            title="Job actions"
            aria-label="Job actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="shrink-0 rounded border border-transparent p-1 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:cursor-wait disabled:opacity-50"
          >
            <MoreHorizontal size={14} />
          </button>
          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-20"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleMenu();
                }}
              />
              <div
                role="menu"
                className="absolute right-0 top-full mt-1 z-30 w-40 rounded border border-border-default bg-bg-tertiary py-1 shadow-xl"
              >
                {isScheduledTask && (
                  <button
                    role="menuitem"
                    type="button"
                    disabled={!hasTarget}
                    title={hasTarget ? undefined : '无 target，无法立即派发'}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!hasTarget) return;
                      onToggleMenu();
                      onRunNow();
                    }}
                    className={`${menuItemClass} disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent`}
                  >
                    <Play size={13} />
                    Run now
                  </button>
                )}
                <button
                  role="menuitem"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleMenu();
                    onTogglePaused();
                  }}
                  className={menuItemClass}
                >
                  {job.paused ? <Play size={13} /> : <Pause size={13} />}
                  {job.paused ? 'Resume' : 'Pause'}
                </button>
                <button
                  role="menuitem"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleMenu();
                    onDelete();
                  }}
                  className={`${menuItemClass} text-danger hover:bg-danger/10`}
                >
                  <Trash2 size={13} />
                  Delete
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-text-tertiary">
        <span>
          {sourceSummary(job)} → {job.target.sessionId ?? 'no target'}
        </span>
        {next && <span>next {formatDateTime(next)}</span>}
        <span>{lastResultSummary(job)}</span>
        {backlog > 0 && (
          <span className="rounded border border-warning/50 bg-warning/10 px-1 py-px text-[10px] text-warning">
            {backlog} backlogged
          </span>
        )}
      </div>

      {job.description && (
        <div className="truncate text-[11px] text-text-secondary" title={job.description}>
          {job.description}
        </div>
      )}

      {job.logPath && (
        <div className="truncate font-mono text-[11px] text-text-tertiary" title={job.logPath}>
          log: {job.logPath}
        </div>
      )}

      {(job.lastStatus === 'undeliverable' || backlog > 0) && (
        <div className="rounded border border-danger/50 bg-danger/10 px-2 py-1 text-[11px] text-danger">
          undeliverable — target missing
        </div>
      )}
    </div>
  );
}

/**
 * JobsView（GUI）— 真实 /api/jobs 数据。列表/创建/编辑/详情/run-now 全走后端
 * 契约；WS 订阅 5 类 job 事件驱动列表与 toast。客户端做排序与筛选。
 */
export default function JobsView() {
  const navigate = useNavigate();
  const showToast = useUIStore((s) => s.showToast);
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);

  const [tab, setTab] = useState<Tab>('list');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [kindFilter, setKindFilter] = useState<'all' | JobKind>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [kindMetas, setKindMetas] = useState<JobKindMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(() => new Set());
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkFeedback, setBulkFeedback] = useState<BulkFeedback | null>(null);
  const [batchDeleteIds, setBatchDeleteIds] = useState<string[] | null>(null);
  const bulkActionRef = useRef<BulkAction | null>(null);
  const bulkActiveIdsRef = useRef<Set<string>>(new Set());
  const bulkDeletedIdsRef = useRef<Set<string>>(new Set());
  const selectVisibleRef = useRef<HTMLInputElement>(null);

  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [menuJobId, setMenuJobId] = useState<string | null>(null);
  const [changeTargetJobId, setChangeTargetJobId] = useState<string | null>(null);
  const [newTargetId, setNewTargetId] = useState('');
  const [deleteJobId, setDeleteJobId] = useState<string | null>(null);
  const [editJobId, setEditJobId] = useState<string | null>(null);

  const [runs, setRuns] = useState<JobRunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsLimit, setRunsLimit] = useState(RUNS_PAGE);

  const upsert = useCallback((job: Job) => {
    setJobs((prev) =>
      prev.some((j) => j.jobId === job.jobId)
        ? prev.map((j) => (j.jobId === job.jobId ? job : j))
        : [...prev, job],
    );
  }, []);

  const loadJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setJobs(await fetchJobs());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  useEffect(() => {
    void fetchJobKinds()
      .then(setKindMetas)
      .catch(() => {});
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // WS：job.created/updated/deleted 驱动列表；fired/partial_failed 弹 toast。
  useEffect(() => {
    const offCreated = wsClient.on('job.created', (ev) => {
      const job = (ev as unknown as { job?: Job }).job;
      if (job) upsert(job);
    });
    const offUpdated = wsClient.on('job.updated', (ev) => {
      const job = (ev as unknown as { job?: Job }).job;
      if (job) upsert(job);
    });
    const offDeleted = wsClient.on('job.deleted', (ev) => {
      const jobId = (ev as unknown as { jobId?: string }).jobId;
      if (!jobId) return;
      if (bulkActiveIdsRef.current.has(jobId)) bulkDeletedIdsRef.current.add(jobId);
      setJobs((prev) => prev.filter((j) => j.jobId !== jobId));
      setSelectedJobId((cur) => (cur === jobId ? null : cur));
      setSelectedJobIds((prev) => {
        if (!prev.has(jobId)) return prev;
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
      setBatchDeleteIds((prev) => {
        if (!prev) return null;
        const remaining = prev.filter((id) => id !== jobId);
        return remaining.length > 0 ? remaining : null;
      });
      setBulkFeedback((prev) => {
        if (!prev?.failed[jobId]) return prev;
        const failed = { ...prev.failed };
        delete failed[jobId];
        return { ...prev, failed };
      });
    });
    const offFired = wsClient.on('scheduler.task.fired', (ev) => {
      const e = ev as unknown as {
        task?: { name?: string };
        taskId?: string;
        name?: string;
        status?: string;
      };
      const name = e.task?.name ?? e.name ?? e.taskId ?? 'job';
      showToast(`Fired: ${name}${e.status ? ` (${e.status})` : ''}`);
    });
    const offPartial = wsClient.on('job.partial_failed', (ev) => {
      const e = ev as unknown as { name?: string; jobId?: string; errors?: unknown[] };
      showToast(
        `Partial failure: ${e.name ?? e.jobId ?? 'job'} (${(e.errors ?? []).length} error(s))`,
        'warning',
      );
    });
    return () => {
      offCreated();
      offUpdated();
      offDeleted();
      offFired();
      offPartial();
    };
  }, [showToast, upsert]);

  // 详情 runs：随选中 job / limit 拉取。
  useEffect(() => {
    if (!selectedJobId) {
      setRuns([]);
      return;
    }
    let cancelled = false;
    setRunsLoading(true);
    fetchJobRuns(selectedJobId, runsLimit)
      .then((r) => {
        if (!cancelled) setRuns(r);
      })
      .catch(() => {
        if (!cancelled) setRuns([]);
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedJobId, runsLimit]);

  const kindLabels = useMemo(() => {
    const map: Partial<Record<JobKind, string>> = {};
    for (const m of kindMetas) map[m.kind] = m.label;
    return map;
  }, [kindMetas]);
  const labelFor = useCallback((kind: JobKind) => kindLabels[kind] ?? kind, [kindLabels]);

  const kindOptions = useMemo(
    () => (kindMetas.length > 0 ? kindMetas.map((m) => m.kind) : FALLBACK_KINDS),
    [kindMetas],
  );

  const visibleJobs = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return jobs
      .filter((job) => matchesStatusFilter(job, statusFilter))
      .filter((job) => kindFilter === 'all' || job.kind === kindFilter)
      .filter(
        (job) =>
          !query ||
          String(job.name ?? '')
            .toLowerCase()
            .includes(query) ||
          String(job.jobId ?? '')
            .toLowerCase()
            .includes(query) ||
          String(job.description ?? '')
            .toLowerCase()
            .includes(query),
      )
      .sort(compareJobs);
  }, [jobs, statusFilter, kindFilter, searchQuery]);

  const visibleJobIds = useMemo(() => visibleJobs.map((job) => job.jobId), [visibleJobs]);
  const selectedVisibleJobs = useMemo(
    () => visibleJobs.filter((job) => selectedJobIds.has(job.jobId)),
    [visibleJobs, selectedJobIds],
  );
  const allVisibleSelected =
    visibleJobs.length > 0 && visibleJobs.every((job) => selectedJobIds.has(job.jobId));

  useEffect(() => {
    if (selectVisibleRef.current) {
      selectVisibleRef.current.indeterminate =
        selectedVisibleJobs.length > 0 && selectedVisibleJobs.length < visibleJobs.length;
    }
  }, [selectedVisibleJobs.length, visibleJobs.length, allVisibleSelected]);

  // WS upserts and other list changes can move selected Jobs outside the active view.
  useEffect(() => {
    const visible = new Set(visibleJobIds);
    setSelectedJobIds((prev) => {
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [visibleJobIds]);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const selectedJob = jobs.find((j) => j.jobId === selectedJobId) ?? null;
  const changeTargetJob = jobs.find((j) => j.jobId === changeTargetJobId) ?? null;
  const deleteJob = jobs.find((j) => j.jobId === deleteJobId) ?? null;
  const editJob = jobs.find((j) => j.jobId === editJobId) ?? null;
  // 抽屉 z-50 会盖住 z-40 的居中 Modal → 有对话框时隐藏抽屉。
  const dialogOpen =
    changeTargetJobId !== null ||
    deleteJobId !== null ||
    editJobId !== null ||
    batchDeleteIds !== null ||
    bulkAction !== null;

  const clearSelectionForViewChange = () => {
    setSelectedJobIds(new Set());
    setBulkFeedback(null);
    setBatchDeleteIds(null);
    setMenuJobId(null);
  };

  const toggleSelectedJob = (jobId: string) => {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
    setBulkFeedback(null);
  };

  const toggleVisibleSelection = () => {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) visibleJobIds.forEach((id) => next.delete(id));
      else visibleJobIds.forEach((id) => next.add(id));
      return next;
    });
    setBulkFeedback(null);
  };

  const runBulkAction = async (action: BulkAction, requestedIds: string[]) => {
    if (bulkActionRef.current) return;
    const requested = new Set(requestedIds);
    const candidates = visibleJobs.filter((job) => requested.has(job.jobId));
    if (candidates.length === 0) {
      setBatchDeleteIds(null);
      return;
    }

    bulkActionRef.current = action;
    bulkActiveIdsRef.current = new Set(candidates.map((job) => job.jobId));
    bulkDeletedIdsRef.current = new Set();
    setBulkAction(action);
    setBulkFeedback(null);
    setBatchDeleteIds(null);

    try {
      let succeeded = 0;
      const failed: BulkFeedback['failed'] = {};
      const succeededIds = new Set<string>();

      if (action === 'pause') {
        const alreadyPaused = candidates.filter((job) => job.paused);
        alreadyPaused.forEach((job) => succeededIds.add(job.jobId));
        succeeded += alreadyPaused.length;
        const toPause = candidates.filter((job) => !job.paused);
        const results = await Promise.allSettled(
          toPause.map((job) => patchJob(job.jobId, { paused: true })),
        );
        results.forEach((result, index) => {
          const job = toPause[index];
          if (!job) return;
          if (result.status === 'fulfilled') {
            succeeded += 1;
            succeededIds.add(job.jobId);
            upsert(result.value);
          } else if (!bulkDeletedIdsRef.current.has(job.jobId)) {
            failed[job.jobId] = { name: job.name, error: errMsg(result.reason) };
          }
        });
      } else {
        const results = await Promise.allSettled(candidates.map((job) => deleteJobApi(job.jobId)));
        results.forEach((result, index) => {
          const job = candidates[index];
          if (!job) return;
          if (result.status === 'fulfilled') {
            succeeded += 1;
            succeededIds.add(job.jobId);
          } else if (!bulkDeletedIdsRef.current.has(job.jobId)) {
            failed[job.jobId] = { name: job.name, error: errMsg(result.reason) };
          }
        });
        if (succeededIds.size > 0) {
          setJobs((prev) => prev.filter((job) => !succeededIds.has(job.jobId)));
          setSelectedJobId((current) => (current && succeededIds.has(current) ? null : current));
        }
      }

      const alreadyDeleted = candidates.filter(
        (job) => bulkDeletedIdsRef.current.has(job.jobId) && !succeededIds.has(job.jobId),
      ).length;
      const removedIds = new Set([...succeededIds, ...bulkDeletedIdsRef.current]);
      setSelectedJobIds((prev) => new Set([...prev].filter((id) => !removedIds.has(id))));
      setBulkFeedback({ action, succeeded, alreadyDeleted, failed });
      showToast(
        `${action === 'pause' ? 'Pause' : 'Delete'}: ${succeeded} succeeded, ${Object.keys(failed).length} failed${alreadyDeleted ? `, ${alreadyDeleted} already deleted` : ''}`,
        Object.keys(failed).length > 0 ? 'warning' : undefined,
      );
    } finally {
      bulkActionRef.current = null;
      bulkActiveIdsRef.current = new Set();
      setBulkAction(null);
    }
  };

  const openDetail = useCallback(
    async (jobId: string) => {
      if (bulkActionRef.current) return;
      setSelectedJobId(jobId);
      setRunsLimit(RUNS_PAGE);
      try {
        upsert(await fetchJob(jobId));
      } catch {
        // keep the list copy if the detail fetch fails
      }
    },
    [upsert],
  );

  const handleCreate = async (input: JobCreateInput) => {
    setSubmitting(true);
    try {
      const job = await createJob(input);
      upsert(job);
      setTab('list');
      showToast(`Created job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdate = async (jobId: string, patch: JobPatchInput) => {
    setSubmitting(true);
    try {
      const job = await patchJob(jobId, patch);
      upsert(job);
      setEditJobId(null);
      showToast(`Saved job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRunNow = async (job: Job) => {
    if (bulkActionRef.current) return;
    try {
      upsert(await runJobNow(job.jobId));
      showToast(`Ran job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  const handleTogglePaused = async (job: Job) => {
    if (bulkActionRef.current) return;
    const next = !job.paused;
    try {
      upsert(await patchJob(job.jobId, { paused: next }));
      showToast(next ? `Paused job "${job.name}"` : `Resumed job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  const handleToggleEntry = async (job: Job, entryId: string) => {
    if (bulkActionRef.current) return;
    if (job.kind !== 'scheduled-task') return;
    const specs = scheduleEntries(job.schedule).map((e) => {
      const spec = entryToSpec(e);
      if (e.id === entryId) spec.enabled = !e.enabled;
      return spec;
    });
    try {
      upsert(await patchJob(job.jobId, { schedule: specs }));
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  const openChangeTarget = (job: Job) => {
    setNewTargetId(job.target.sessionId ?? '');
    setChangeTargetJobId(job.jobId);
  };

  const handleChangeTarget = async () => {
    if (!changeTargetJob) return;
    const hadBacklog = (changeTargetJob.undeliveredFires?.length ?? 0) > 0;
    const backlogCount = changeTargetJob.undeliveredFires?.length ?? 0;
    try {
      const job = await patchJob(changeTargetJob.jobId, {
        target: { sessionId: newTargetId.trim() || null },
      });
      upsert(job);
      setChangeTargetJobId(null);
      showToast(
        hadBacklog
          ? `Target updated; ${backlogCount} backlogged fire(s) requeued`
          : `Target updated for "${job.name}"`,
      );
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  const handleDelete = async () => {
    if (!deleteJob || bulkActionRef.current) return;
    try {
      await deleteJobApi(deleteJob.jobId);
      setJobs((prev) => prev.filter((j) => j.jobId !== deleteJob.jobId));
      if (selectedJobId === deleteJob.jobId) setSelectedJobId(null);
      setDeleteJobId(null);
      showToast(`Deleted job "${deleteJob.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0 bg-bg-primary">
      {/* Header */}
      <div className="flex items-center gap-2 pl-10 md:pl-3 pr-3 py-2.5 border-b border-border-default bg-bg-secondary/50 shrink-0">
        <button
          type="button"
          onClick={() => navigate('/')}
          aria-label="Back"
          title="Back"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border-default bg-bg-tertiary text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
        >
          <ArrowLeft size={14} />
        </button>
        <h1 className="text-sm font-semibold text-text-primary">Jobs</h1>
      </div>

      {/* In-page tabs */}
      <div className="flex gap-1 px-3 py-2 border-b border-border-muted bg-bg-secondary/30 shrink-0">
        <button
          type="button"
          aria-pressed={tab === 'list'}
          disabled={bulkAction !== null}
          onClick={() => {
            setTab('list');
            setSelectionMode(false);
            clearSelectionForViewChange();
          }}
          className={`inline-flex items-center gap-1 rounded border px-2.5 py-1 text-xs font-medium transition-colors ${
            tab === 'list'
              ? 'border-accent/50 bg-accent/10 text-accent'
              : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
          }`}
        >
          <ListChecks size={12} />
          Jobs
        </button>
        <button
          type="button"
          aria-pressed={tab === 'create'}
          disabled={bulkAction !== null}
          onClick={() => {
            setTab('create');
            setSelectionMode(false);
            clearSelectionForViewChange();
          }}
          className={`inline-flex items-center gap-1 rounded border px-2.5 py-1 text-xs font-medium transition-colors ${
            tab === 'create'
              ? 'border-accent/50 bg-accent/10 text-accent'
              : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
          }`}
        >
          <Plus size={12} />
          New Job
        </button>
      </div>

      {tab === 'list' ? (
        <div className="flex min-h-0 flex-1 flex-col">
          {/* Keep filters, search, selection actions, and counts visible while rows scroll. */}
          <div className="shrink-0 border-b border-border-muted bg-bg-primary">
            <div className="mx-auto flex w-full max-w-2xl min-w-0 flex-col gap-2 p-4">
              <div className="flex flex-wrap items-center gap-1.5">
                {STATUS_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    aria-pressed={statusFilter === f.key}
                    disabled={bulkAction !== null}
                    onClick={() => {
                      clearSelectionForViewChange();
                      setStatusFilter(f.key);
                    }}
                    className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors disabled:cursor-wait disabled:opacity-60 ${
                      statusFilter === f.key
                        ? 'border-accent/50 bg-accent/10 text-accent'
                        : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>

              <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
                <label className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 text-[11px] text-text-tertiary">Kind</span>
                  <select
                    id="job-kind-filter"
                    aria-label="Kind"
                    value={kindFilter}
                    disabled={bulkAction !== null}
                    onChange={(e) => {
                      clearSelectionForViewChange();
                      setKindFilter(e.target.value as 'all' | JobKind);
                    }}
                    className="min-w-0 flex-1 rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent/50"
                  >
                    <option value="all">All kinds</option>
                    {kindOptions.map((k) => (
                      <option key={k} value={k}>
                        {labelFor(k)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={bulkAction !== null}
                    onClick={() => void loadJobs()}
                    className="shrink-0 rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60"
                  >
                    Refresh
                  </button>
                </label>
                <label className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 text-[11px] text-text-tertiary">Search</span>
                  <input
                    type="search"
                    aria-label="Search jobs"
                    placeholder="Name, ID, or description"
                    value={searchQuery}
                    disabled={bulkAction !== null}
                    onChange={(e) => {
                      clearSelectionForViewChange();
                      setSearchQuery(e.target.value);
                    }}
                    className="min-w-0 flex-1 rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent/50 disabled:opacity-60"
                  />
                </label>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-text-tertiary">
                <span aria-live="polite">
                  {visibleJobs.length} of {jobs.length} jobs
                </span>
                {!selectionMode ? (
                  <button
                    type="button"
                    disabled={bulkAction !== null}
                    onClick={() => {
                      setSelectionMode(true);
                      setSelectedJobIds(new Set());
                      setBulkFeedback(null);
                    }}
                    className="rounded border border-border-default bg-bg-tertiary px-2 py-1 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60"
                  >
                    Select jobs
                  </button>
                ) : (
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <label className="flex cursor-pointer items-center gap-1.5 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-text-secondary">
                      <input
                        ref={selectVisibleRef}
                        type="checkbox"
                        aria-label="Select all visible jobs"
                        checked={allVisibleSelected}
                        disabled={bulkAction !== null || visibleJobs.length === 0}
                        onChange={toggleVisibleSelection}
                        className="h-3.5 w-3.5 accent-accent disabled:cursor-not-allowed"
                      />
                      Select visible
                    </label>
                    <span>{selectedVisibleJobs.length} selected</span>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={bulkAction !== null || selectedVisibleJobs.length === 0}
                      onClick={() =>
                        void runBulkAction(
                          'pause',
                          selectedVisibleJobs.map((j) => j.jobId),
                        )
                      }
                    >
                      Pause selected
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={bulkAction !== null || selectedVisibleJobs.length === 0}
                      onClick={() => setBatchDeleteIds(selectedVisibleJobs.map((j) => j.jobId))}
                    >
                      Delete selected ({selectedVisibleJobs.length})
                    </Button>
                    <button
                      type="button"
                      disabled={bulkAction !== null}
                      onClick={() => {
                        setSelectionMode(false);
                        clearSelectionForViewChange();
                      }}
                      className="rounded px-1.5 py-1 text-text-secondary hover:text-text-primary disabled:opacity-60"
                    >
                      Done
                    </button>
                  </div>
                )}
              </div>

              {bulkAction && (
                <div role="status" className="text-[11px] text-text-secondary">
                  {bulkAction === 'pause' ? 'Pausing' : 'Deleting'} selected jobs…
                </div>
              )}
              {bulkFeedback && (
                <div
                  role="status"
                  aria-live="polite"
                  className={`flex min-w-0 flex-col gap-1 rounded border px-2.5 py-2 text-[11px] ${
                    Object.keys(bulkFeedback.failed).length
                      ? 'border-warning/50 bg-warning/10 text-warning'
                      : 'border-success/50 bg-success/10 text-success'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {bulkFeedback.action === 'pause' ? 'Pause' : 'Delete'}:{' '}
                      {bulkFeedback.succeeded} succeeded, {Object.keys(bulkFeedback.failed).length}{' '}
                      failed
                      {bulkFeedback.alreadyDeleted > 0 &&
                        `, ${bulkFeedback.alreadyDeleted} already deleted`}
                    </span>
                    {Object.keys(bulkFeedback.failed).length > 0 && (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={bulkAction !== null}
                        onClick={() =>
                          void runBulkAction(bulkFeedback.action, Object.keys(bulkFeedback.failed))
                        }
                      >
                        Retry failed ({Object.keys(bulkFeedback.failed).length})
                      </Button>
                    )}
                  </div>
                  {Object.entries(bulkFeedback.failed).map(([id, failure]) => (
                    <div key={id} className="break-words">
                      {failure.name}: {failure.error}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div
            data-testid="jobs-list-scroll"
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
          >
            <div className="mx-auto w-full max-w-2xl min-w-0 p-4">
              {loading && jobs.length === 0 ? (
                <div className="rounded border border-border-muted bg-bg-secondary/40 px-3 py-6 text-center text-xs text-text-tertiary">
                  Loading…
                </div>
              ) : error ? (
                <div className="flex flex-col items-center gap-2 rounded border border-danger/50 bg-danger/10 px-3 py-6 text-center text-xs text-danger">
                  <span>{error}</span>
                  <Button variant="secondary" size="sm" onClick={() => void loadJobs()}>
                    Retry
                  </Button>
                </div>
              ) : visibleJobs.length > 0 ? (
                <div className="flex min-w-0 flex-col gap-2">
                  {visibleJobs.map((job) => (
                    <JobRow
                      key={job.jobId}
                      job={job}
                      kindLabel={labelFor(job.kind)}
                      onOpen={() => void openDetail(job.jobId)}
                      menuOpen={menuJobId === job.jobId}
                      onToggleMenu={() => setMenuJobId(menuJobId === job.jobId ? null : job.jobId)}
                      onRunNow={() => void handleRunNow(job)}
                      onTogglePaused={() => void handleTogglePaused(job)}
                      onDelete={() => setDeleteJobId(job.jobId)}
                      selectionMode={selectionMode}
                      selected={selectedJobIds.has(job.jobId)}
                      selectionDisabled={bulkAction !== null}
                      onToggleSelected={() => toggleSelectedJob(job.jobId)}
                    />
                  ))}
                </div>
              ) : (
                <div className="rounded border border-border-muted bg-bg-secondary/40 px-3 py-6 text-center text-xs text-text-tertiary">
                  {jobs.length === 0 ? 'No jobs yet' : 'No jobs match'}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl p-4">
            <NewJobForm submitting={submitting} onCreate={handleCreate} />
          </div>
        </div>
      )}

      {selectedJob && !dialogOpen && (
        <JobDetailDrawer
          job={selectedJob}
          kindLabel={labelFor(selectedJob.kind)}
          runs={runs}
          runsLoading={runsLoading}
          onLoadMoreRuns={() => setRunsLimit((n) => n + RUNS_PAGE)}
          onClose={() => setSelectedJobId(null)}
          onRunNow={() => void handleRunNow(selectedJob)}
          onTogglePaused={() => void handleTogglePaused(selectedJob)}
          onDelete={() => setDeleteJobId(selectedJob.jobId)}
          onChangeTarget={() => openChangeTarget(selectedJob)}
          onEdit={() => setEditJobId(selectedJob.jobId)}
          onToggleEntryEnabled={(entryId) => void handleToggleEntry(selectedJob, entryId)}
        />
      )}

      {/* Change target dialog */}
      <Modal
        open={changeTargetJobId !== null}
        onClose={() => setChangeTargetJobId(null)}
        title="Change target"
        size="sm"
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-text-tertiary">Target session</span>
            <select
              aria-label="Target session"
              value={newTargetId}
              onChange={(e) => setNewTargetId(e.target.value)}
              className={selectClass}
            >
              <option value="">无 target（积压）</option>
              {sessionOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Untitled'} · {s.id}
                </option>
              ))}
            </select>
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setChangeTargetJobId(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={() => void handleChangeTarget()}>
              Save
            </Button>
          </div>
        </div>
      </Modal>

      {/* Delete confirmation dialog */}
      <Modal
        open={deleteJobId !== null}
        onClose={() => setDeleteJobId(null)}
        title="Delete job"
        size="sm"
      >
        <div className="flex flex-col gap-3">
          <p className="text-xs text-text-secondary">
            Delete job <span className="text-text-primary">"{deleteJob?.name}"</span>? This cannot
            be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setDeleteJobId(null)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" onClick={() => void handleDelete()}>
              Delete
            </Button>
          </div>
        </div>
      </Modal>

      {/* Batch delete always confirms the exact selection before issuing per-job DELETEs. */}
      <Modal
        open={batchDeleteIds !== null}
        onClose={() => {
          if (bulkAction === null) setBatchDeleteIds(null);
        }}
        title="Delete selected jobs"
        size="sm"
      >
        <div className="flex flex-col gap-3">
          <p className="text-xs text-text-secondary">
            Delete{' '}
            <span className="font-semibold text-text-primary">{batchDeleteIds?.length ?? 0}</span>{' '}
            selected jobs? This cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setBatchDeleteIds(null)}
              disabled={bulkAction !== null}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              disabled={bulkAction !== null || (batchDeleteIds?.length ?? 0) === 0}
              onClick={() => {
                if (batchDeleteIds) void runBulkAction('delete', batchDeleteIds);
              }}
            >
              Delete {batchDeleteIds?.length ?? 0} jobs
            </Button>
          </div>
        </div>
      </Modal>

      {/* Edit job dialog */}
      <Modal
        open={editJobId !== null}
        onClose={() => setEditJobId(null)}
        title="Edit job"
        size="lg"
      >
        {editJob && (
          <NewJobForm
            key={editJob.jobId}
            mode="edit"
            initialJob={editJob}
            submitting={submitting}
            onSave={(patch) => void handleUpdate(editJob.jobId, patch)}
          />
        )}
      </Modal>
    </div>
  );
}
