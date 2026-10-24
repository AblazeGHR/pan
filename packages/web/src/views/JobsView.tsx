import { useAutomationAuth } from '@/stores/automationStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  ListChecks,
  MoreHorizontal,
  Pause,
  Pin,
  PinOff,
  Play,
  Plus,
  Settings2,
  Trash2,
} from 'lucide-react';
import { NewJobForm } from '@/components/jobs/NewJobForm';
import { UnifiedJobForm } from '@/components/jobs/UnifiedJobForm';
import { JobTemplateEditor } from '@/components/jobs/JobTemplateEditor';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { JobDetailDrawer } from '@/components/jobs/JobDetailDrawer';
import { JobRetentionSettings } from '@/components/jobs/JobRetentionSettings';
import { entryToSpec } from '@/components/jobs/ScheduleListEditor';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useJobStore } from '@/stores/jobStore';
import { wsClient } from '@/services/ws';
import { scheduleEntries } from '@/types/jobs';
import {
  cancelJob,
  deleteJob as deleteJobApi,
  fetchJob,
  fetchJobKinds,
  fetchJobRuns,
  patchJob,
  pinJob,
  runJobNow,
  createActionJob,
  createJob,
  updateActionJob,
  fetchActionRegistry,
  mutateActionJob,
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
import type { Action } from '@/types/actions';

function canCancelJob(job: Job): boolean {
  return (isUnifiedJob(job) || job.kind !== 'scheduled-task') && ['pending', 'scheduled', 'starting', 'running', 'restarting'].includes(job.status);
}
function isUnifiedJob(job: Job): boolean { return Boolean(job.unified && !job.unified.compatibility); }
function timestampMs(value: unknown): number | null {
  const result = typeof value === 'number' ? value * 1000 : typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
  return Number.isFinite(result) ? result : null;
}

type Tab = 'list' | 'legacy' | 'create' | 'settings';

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
    case 'restarting':
    case 'cleaning':
    case 'cleanup_pending':
    case 'deleting':
    case 'unknown':
    case 'uncertain':
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

function targetSummary(job: Job): string {
  if (job.action?.api === 'resume_legal_running') {
    return 'dynamic: legal running Sessions without a live Worker';
  }
  if (job.action?.api === 'send_qq') {
    const { targetType, targetId, botUin } = job.action.args;
    return `QQ ${targetType} ${targetId} · ${botUin ? `bot ${botUin}` : 'default bot'}`;
  }
  return job.target.sessionId ?? 'no target';
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
  return isUnifiedJob(job) ? `runs ${job.runCount ?? 0}`
    : job.kind === 'background-process' ? `legacy ${job.status}` : `deliveries ${job.runCount ?? 0}`;
}

/** 活跃优先排序档位：running → starting → scheduled → pending → completed → failed → timed_out → cancelled。 */
function activeRank(status: JobStatus): number {
  switch (status) {
    case 'running':
      return 0;
    case 'starting':
    case 'cleaning':
    case 'cleanup_pending':
    case 'deleting':
    case 'restarting':
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
  return timestampMs(job.updatedAt) ?? 0;
}

/** 置顶优先；每组保留活跃优先和 updatedAt 倒序（新在前）。 */
function compareJobs(a: Job, b: Job, usePins = true): number {
  if (usePins && !!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
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
  pinUnavailable,
  onTogglePinned,
  onTogglePaused,
  onDelete,
  onCancel,
  cancelling,
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
  pinUnavailable: boolean;
  onTogglePinned: () => void;
  onTogglePaused: () => void;
  onDelete: () => void;
  onCancel: () => void;
  cancelling: boolean;
  selectionMode: boolean;
  selected: boolean;
  selectionDisabled: boolean;
  onToggleSelected: () => void;
}) {
  const next = nextFireAt(job);
  const backlog = job.undeliveredFires?.length ?? 0;
  const isScheduledTask = job.kind === 'scheduled-task';
  const hasTarget = !!job.target.sessionId;
  const canRunNow = job.action?.api === 'shell'
    || Boolean(isUnifiedJob(job) && job.enabled)
    || job.action?.api === 'resume_legal_running'
    || job.action?.api === 'send_qq'
    || hasTarget;
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
        <div className="min-w-0 flex-1 truncate text-sm text-text-primary" title={job.name}>
          {job.name}
        </div>
        {job.label && <span title={job.label} className="min-w-0 max-w-[28%] truncate rounded border border-accent/50 bg-accent/10 px-1.5 py-px text-[11px] font-medium text-accent">{job.label}</span>}
        {canCancelJob(job) && <button type="button" disabled={cancelling || selectionDisabled} onClick={(e) => { e.stopPropagation(); onCancel(); }} className="shrink-0 rounded border border-warning/50 px-1.5 py-1 text-[11px] text-warning disabled:opacity-50">{cancelling ? 'Cancelling…' : 'Cancel job'}</button>}
        {!pinUnavailable && job.pinned && <span title="Pinned" aria-label="Pinned" className="shrink-0 text-accent"><Pin size={14} /></span>}
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
                <button role="menuitem" type="button" disabled={pinUnavailable}
                  className={`${menuItemClass} disabled:cursor-not-allowed disabled:opacity-50`}
                  onClick={(e) => { e.stopPropagation(); onToggleMenu(); onTogglePinned(); }}>
                  {job.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                  {pinUnavailable ? 'Pin unavailable' : job.pinned ? 'Unpin' : 'Pin'}
                </button>
                {isScheduledTask && (
                  <button
                    role="menuitem"
                    type="button"
                    disabled={!canRunNow}
                    title={canRunNow ? undefined : '无 target，无法立即派发'}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!canRunNow) return;
                      onToggleMenu();
                      onRunNow();
                    }}
                    className={`${menuItemClass} disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent`}
                  >
                    <Play size={13} />
                    Run now
                  </button>
                )}
                {isScheduledTask && (
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
                )}
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
        <span
          className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-medium ${statusColor(job.status)}`}
        >
          {job.status}
        </span>
        <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1.5 py-px text-[10px] text-text-secondary">
          {kindLabel}
        </span>

        <span>
          {sourceSummary(job)} → {targetSummary(job)}
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
  const authorizationToken = useAutomationAuth((state) => state.token);
  const navigate = useNavigate();
  const showToast = useUIStore((s) => s.showToast);
  const toastRef = useRef(showToast); toastRef.current = showToast;
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);

  const [sort, setSort] = useState(() => {
    try { const value = JSON.parse(localStorage.getItem('pan:jobsSort') || 'null');
      if (['active', 'updated', 'created', 'kind'].includes(value?.key) && ['asc', 'desc'].includes(value?.direction)) return value as { key: string; direction: string };
    } catch { /* storage unavailable */ }
    return { key: 'active', direction: 'desc' };
  });
  useEffect(() => { try { localStorage.setItem('pan:jobsSort', JSON.stringify(sort)); } catch { /* storage unavailable */ } }, [sort]);
  const cancelRequests = useRef(new Set<string>());
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<Tab>('list');
  const [legacyCreateOpen, setLegacyCreateOpen] = useState(false);
  const dirtyEditor = useRef(false);
  const editorDirtyChange = useCallback((dirty: boolean) => { dirtyEditor.current = dirty; }, []);
  const requestTab = (next: Tab) => {
    if (dirtyEditor.current && !window.confirm('Discard unsaved Job configuration?')) return;
    dirtyEditor.current = false; setTab(next);
  };
  const [laneFilters, setLaneFilters] = useState<Record<'list' | 'legacy', {
    status: StatusFilter; kind: 'all' | JobKind; query: string;
  }>>({ list: { status: 'all', kind: 'all', query: '' }, legacy: { status: 'all', kind: 'all', query: '' } });
  const lane = tab === 'legacy' ? 'legacy' : 'list';
  const { status: statusFilter, kind: kindFilter, query: searchQuery } = laneFilters[lane];
  const setStatusFilter = (status: StatusFilter) => setLaneFilters((previous) => ({ ...previous, [lane]: { ...previous[lane], status } }));
  const setKindFilter = (kind: 'all' | JobKind) => setLaneFilters((previous) => ({ ...previous, [lane]: { ...previous[lane], kind } }));
  const setSearchQuery = (query: string) => setLaneFilters((previous) => ({ ...previous, [lane]: { ...previous[lane], query } }));
  // The unified job store owns the list (shared with the nav running badge);
  // this page only owns view state on top of it.
  const allJobs = useJobStore((s) => s.jobs);
  // /api/jobs is a mixed read facade. Compatibility projections never confer
  // Action execution ownership on a legacy record.
  const jobs = useMemo(() => allJobs.filter((job) =>
    tab === 'legacy' ? !isUnifiedJob(job) : isUnifiedJob(job)), [allJobs, tab]);
  const loading = useJobStore((s) => s.loading);
  const error = useJobStore((s) => s.error);
  const pinMetadataUnavailable = useJobStore((s) => s.pinMetadataUnavailable);
  const loadJobs = useJobStore((s) => s.loadJobs);
  const upsertJob = useJobStore((s) => s.upsertJob);
  const removeJob = useJobStore((s) => s.removeJob);
  useEffect(() => {
    let changed = false;
    for (const id of cancelRequests.current) {
      const current = allJobs.find((job) => job.jobId === id);
      if (!current || !canCancelJob(current)) { cancelRequests.current.delete(id); changed = true; }
    }
    if (changed) setCancellingIds(new Set(cancelRequests.current));
  }, [allJobs]);
  const [kindMetas, setKindMetas] = useState<JobKindMeta[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
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
  const [editSnapshot, setEditSnapshot] = useState<Job | null>(null);

  const [runs, setRuns] = useState<JobRunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsLimit, setRunsLimit] = useState(RUNS_PAGE);

  // Fresh authoritative list whenever the page is entered. The store is also
  // hydrated globally on socket open, so this is a refresh — not the only load
  // — and it keeps working when the socket never connected.
  useEffect(() => {
    void loadJobs();
  }, [loadJobs, authorizationToken]);

  useEffect(() => {
    void fetchJobKinds()
      .then(setKindMetas)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (tab !== 'create' && editJobId === null) return;
    let active = true;
    void fetchActionRegistry().then((registry) => { if (active) setActions(registry.actions || []); })
      .catch((e: unknown) => { if (active) toastRef.current(errMsg(e), 'error'); });
    return () => { active = false; };
  }, [tab, editJobId, authorizationToken]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // WS：job.created/updated/deleted 由 useWebSocket 统一写入 jobStore（导航徽标
  // 共用同一份列表）；本页只保留 fired/partial_failed 的 toast 通知。
  useEffect(() => {
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
      offFired();
      offPartial();
    };
  }, [showToast]);

  // Deletions now arrive through the shared store (global WS wiring, this
  // page's own delete, or another client). Reconcile page-local selection
  // state against the list so a removed job cannot leave a stale selection,
  // bulk id or feedback entry behind.
  const knownJobIdsRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    const previous = knownJobIdsRef.current;
    const current = new Set(allJobs.map((job) => job.jobId));
    knownJobIdsRef.current = current;
    if (!previous) return;
    for (const jobId of previous) {
      if (current.has(jobId)) continue;
      if (bulkActiveIdsRef.current.has(jobId)) bulkDeletedIdsRef.current.add(jobId);
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
    }
  }, [allJobs]);

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

  const pinUnavailable = pinMetadataUnavailable || jobs.some((job) => job.pinMetadataStatus === 'unavailable');

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
      .sort((a, b) => {
        if (sort.key === 'active') return compareJobs(a, b, !pinUnavailable) || a.jobId.localeCompare(b.jobId);
        if (!pinUnavailable && !!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
        const av = sort.key === 'kind' ? a.kind || null : timestampMs(sort.key === 'created' ? a.createdAt : a.updatedAt);
        const bv = sort.key === 'kind' ? b.kind || null : timestampMs(sort.key === 'created' ? b.createdAt : b.updatedAt);
        if (av === null || bv === null) return av === bv ? a.jobId.localeCompare(b.jobId) : av === null ? 1 : -1;
        const result = typeof av === 'string' && typeof bv === 'string' ? av.localeCompare(bv) : Number(av) - Number(bv);
        return result * (sort.direction === 'asc' ? 1 : -1) || a.jobId.localeCompare(b.jobId);
      });
  }, [jobs, statusFilter, kindFilter, searchQuery, pinUnavailable, sort]);

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
  // An open draft and its CAS revision are one snapshot. A WS refresh may update
  // the list, but cannot promote the revision attached to unsaved input.
  const editJob = editSnapshot?.jobId === editJobId ? editSnapshot : null;
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
    const candidates = visibleJobs.filter((job) =>
      requested.has(job.jobId) && (action !== 'pause' || job.kind === 'scheduled-task'));
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
          toPause.map((job) => isUnifiedJob(job) && job.unified && job.configRevision != null
            ? updateActionJob(job.jobId, { ...job.unified.config, enabled: false }, job.configRevision)
            : patchJob(job.jobId, { paused: true })),
        );
        results.forEach((result, index) => {
          const job = toPause[index];
          if (!job) return;
          if (result.status === 'fulfilled') {
            succeeded += 1;
            succeededIds.add(job.jobId);
            upsertJob(result.value);
          } else if (!bulkDeletedIdsRef.current.has(job.jobId)) {
            failed[job.jobId] = { name: job.name, error: errMsg(result.reason) };
          }
        });
      } else {
        const results = await Promise.allSettled(candidates.map((job) => isUnifiedJob(job) ? mutateActionJob(job, 'delete') : deleteJobApi(job.jobId)));
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
          succeededIds.forEach((id) => removeJob(id));
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
        upsertJob(await fetchJob(jobId));
      } catch {
        // keep the list copy if the detail fetch fails
      }
    },
    [upsertJob],
  );

  const handleUnifiedCreate = async (config: import('@/types/jobs').UnifiedJobConfig) => {
    setSubmitting(true);
    try {
      const job = await createActionJob(config);
      upsertJob(job);
      dirtyEditor.current = false;
      setTab('list');
      showToast(`Created unified Job "${job.name}"`);
    } catch (e) { showToast(errMsg(e), 'error'); throw e; }
    finally { setSubmitting(false); }
  };

  const handleLegacyCreate = async (input: JobCreateInput) => {
    setSubmitting(true);
    try {
      const job = await createJob(input);
      upsertJob(job); setLegacyCreateOpen(false); setTab('legacy');
      showToast(`Created legacy Job "${job.name}"`);
    } catch (e) { showToast(errMsg(e), 'error'); }
    finally { setSubmitting(false); }
  };

  const handleUpdate = async (jobId: string, patch: JobPatchInput) => {
    setSubmitting(true);
    try {
      const job = await patchJob(jobId, patch);
      upsertJob(job);
      setEditJobId(null);
      showToast(`Saved job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleUnifiedUpdate = async (job: Job, config: import('@/types/jobs').UnifiedJobConfig) => {
    setSubmitting(true);
    try {
      if (job.configRevision == null) throw new Error('Configuration revision missing; reload before saving');
      const revision = job.configRevision;
      const updated = await updateActionJob(job.jobId, config, revision);
      upsertJob(updated); dirtyEditor.current = false; setEditJobId(null); showToast(`Saved unified Job "${updated.name}"`);
    } catch (e) { showToast(errMsg(e), 'error'); throw e; }
    finally { setSubmitting(false); }
  };

  const handleRunNow = async (job: Job) => {
    if (bulkActionRef.current) return;
    try {
      if (isUnifiedJob(job)) { await mutateActionJob(job, 'run-now'); upsertJob(await fetchJob(job.jobId)); }
      else upsertJob(await runJobNow(job.jobId));
      showToast(`Ran job "${job.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
    }
  };

  const handleAttemptRecovery = async (job: Job, operation: 'retry' | 'confirm-recovery') => {
    if (!window.confirm('Confirm another Attempt of the same Run? Reconcile all possible prior effects first. Unknown process identity remains blocked.')) return;
    try { await mutateActionJob(job, operation); upsertJob(await fetchJob(job.jobId)); }
    catch (e) { showToast(errMsg(e), 'error'); }
  };

  const handleTogglePinned = async (job: Job) => {
    if (bulkActionRef.current || pinUnavailable) return;
    try {
      upsertJob(await pinJob(job.jobId, !job.pinned));
    } catch (e) {
      showToast(`${errMsg(e)}. Refresh to read the current pin state.`, 'error');
      void loadJobs();
    }
  };

  const handleTogglePaused = async (job: Job) => {
    if (bulkActionRef.current) return;
    const next = !job.paused;
    try {
      if (isUnifiedJob(job)) {
        if (job.configRevision == null || !job.unified) throw new Error('Reload configuration revision before confirming');
        upsertJob(await updateActionJob(job.jobId, { ...job.unified.config, enabled: !job.enabled }, job.configRevision));
        return;
      }
      upsertJob(await patchJob(job.jobId, { paused: next }));
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
      if (job.unified && !job.unified.compatibility) {
        if (job.configRevision == null) throw new Error('Reload configuration revision before confirming');
        upsertJob(await updateActionJob(job.jobId, { ...job.unified.config, schedule: specs }, job.configRevision));
      } else upsertJob(await patchJob(job.jobId, { schedule: specs }));
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
      upsertJob(job);
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

  const handleCancel = async (job: Job) => {
    if (!canCancelJob(job) || cancelRequests.current.has(job.jobId)) return;
    cancelRequests.current.add(job.jobId);
    setCancellingIds(new Set(cancelRequests.current));
    let accepted = false;
    try {
      if (isUnifiedJob(job)) await mutateActionJob(job, 'cancel');
      else await cancelJob(job.jobId);
      accepted = true;
      showToast('Cancellation requested');
      try { upsertJob(await fetchJob(job.jobId)); }
      catch { showToast('Cancellation accepted; current status unavailable. Refresh to confirm.', 'warning'); }
    } catch (e) { showToast(errMsg(e), 'error'); }
    finally { if (!accepted) { cancelRequests.current.delete(job.jobId); setCancellingIds(new Set(cancelRequests.current)); } }
  };

  const handleDelete = async () => {
    if (!deleteJob || bulkActionRef.current) return;
    try {
      if (isUnifiedJob(deleteJob)) await mutateActionJob(deleteJob, 'delete');
      else await deleteJobApi(deleteJob.jobId);
      removeJob(deleteJob.jobId);
      if (selectedJobId === deleteJob.jobId) setSelectedJobId(null);
      setDeleteJobId(null);
      showToast(`Deleted job "${deleteJob.name}"`);
    } catch (e) {
      showToast(errMsg(e), 'error');
      // Production DELETE may durably enter cleanup_pending before refusing.
      // Reload its facts without retrying the destructive operation.
      try { upsertJob(await fetchJob(deleteJob.jobId)); } catch { /* retain current record */ }
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
            requestTab('list');
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
          Unified Jobs
        </button>
        <button type="button" aria-pressed={tab === 'legacy'} disabled={bulkAction !== null}
          onClick={() => { requestTab('legacy'); setSelectionMode(false); clearSelectionForViewChange(); setSelectedJobId(null); }}
          className={`rounded border px-2.5 py-1 text-xs ${tab === 'legacy' ? 'border-accent/50 text-accent' : 'border-border-default text-text-secondary'}`}>
          Legacy Jobs / 旧 Job
        </button>
        <button
          type="button"
          aria-pressed={tab === 'create'}
          disabled={bulkAction !== null}
          onClick={() => {
            requestTab('create');
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
        <button
          type="button"
          aria-pressed={tab === 'settings'}
          disabled={bulkAction !== null}
          onClick={() => {
            requestTab('settings');
            setSelectionMode(false);
            clearSelectionForViewChange();
          }}
          className={`inline-flex items-center gap-1 rounded border px-2.5 py-1 text-xs font-medium transition-colors ${
            tab === 'settings'
              ? 'border-accent/50 bg-accent/10 text-accent'
              : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
          }`}
        >
          <Settings2 size={12} />
          Settings
        </button>
      </div>

      {(tab === 'list' || tab === 'legacy') && <p className="shrink-0 px-4 py-2 text-xs text-text-secondary" role="note">
        {tab === 'legacy'
          ? '旧 Job · background_jobs registry / legacy scheduler · 旧 runner 与原始 Job 状态。与新 Job 可共用存储目录；不迁移、不自动转换。删除走原安全取消/清理路径；退出未确认时保留 cleanup_pending，须显式再次核对。'
          : '统一新 Job · Action-reference registry records / Action runtime · Run / Attempt 状态。存储目录可与旧 Job 共用；此处仅展示新执行语义。'}
      </p>}
      {tab === 'legacy' && <div className="shrink-0 px-4 pb-2">
        <Button size="sm" onClick={() => setLegacyCreateOpen(true)} disabled={bulkAction !== null}>New legacy Job</Button>
      </div>}
      {tab === 'list' || tab === 'legacy' ? (
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

              {pinUnavailable && (
                <div role="status" className="rounded border border-warning/50 px-3 py-2 text-xs text-warning">
                  Pin metadata unavailable. Pin states are unknown and pin actions are disabled.
                  Jobs remain usable; restore the metadata and refresh to read the current pin states.
                </div>
              )}
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

              <div className="flex flex-wrap items-center gap-2 text-xs">
                <label>Sort by <select aria-label="Sort jobs" value={sort.key} disabled={bulkAction !== null} onChange={(e) => setSort({ ...sort, key: e.target.value })} className="rounded border border-border-default bg-bg-tertiary p-1.5">
                  <option value="active">Active first</option><option value="updated">Updated time</option><option value="created">Created time</option><option value="kind">Kind</option>
                </select></label>
                <button type="button" disabled={sort.key === 'active' || bulkAction !== null} onClick={() => setSort({ ...sort, direction: sort.direction === 'asc' ? 'desc' : 'asc' })} className="rounded border border-border-default px-2 py-1.5 disabled:opacity-50">{sort.direction === 'asc' ? 'Ascending' : 'Descending'}</button>
                <span className="text-[11px] text-text-tertiary">Pinned jobs first</span>
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
                      disabled={bulkAction !== null || !selectedVisibleJobs.some(
                        (job) => job.kind === 'scheduled-task',
                      )}
                      onClick={() =>
                        void runBulkAction(
                          'pause',
                          selectedVisibleJobs.map((j) => j.jobId),
                        )
                      }
                    >
                      Pause scheduled tasks
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
                      pinUnavailable={pinUnavailable}
                      onTogglePinned={() => void handleTogglePinned(job)}
                      onTogglePaused={() => void handleTogglePaused(job)}
                      onCancel={() => void handleCancel(job)}
                      cancelling={cancellingIds.has(job.jobId)}
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
      ) : tab === 'create' ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl p-4">
            <JobUserAuthorization />
            <JobTemplateEditor actions={actions} submitting={submitting} onCreate={handleUnifiedCreate} onInstantiated={(job) => { dirtyEditor.current = false; upsertJob(job); setTab('list'); }} onDirtyChange={editorDirtyChange} />
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl p-4">
            <JobRetentionSettings />
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
          onCancel={() => void handleCancel(selectedJob)}
          cancelling={cancellingIds.has(selectedJob.jobId)}
          onDelete={() => setDeleteJobId(selectedJob.jobId)}
          onChangeTarget={() => openChangeTarget(selectedJob)}
          onEdit={() => { setEditSnapshot(structuredClone(selectedJob)); setEditJobId(selectedJob.jobId); }}
          onToggleEntryEnabled={(entryId) => void handleToggleEntry(selectedJob, entryId)}
          onRetry={isUnifiedJob(selectedJob) ? () => void handleAttemptRecovery(selectedJob, 'retry') : undefined}
          onConfirmRecovery={isUnifiedJob(selectedJob) ? () => void handleAttemptRecovery(selectedJob, 'confirm-recovery') : undefined}
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

      <Modal open={legacyCreateOpen} onClose={() => { if (!submitting) setLegacyCreateOpen(false); }} title="New legacy Job" size="lg">
        <p className="mb-3 text-xs text-text-secondary">创建旧 registry 记录，使用旧 runner / scheduler；不会转换为新 Action Job。</p>
        {legacyCreateOpen && <NewJobForm kindMetas={kindMetas} submitting={submitting} onCreate={(input) => void handleLegacyCreate(input)} />}
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
        onClose={() => { if (!dirtyEditor.current || window.confirm('Discard unsaved Job configuration?')) { dirtyEditor.current = false; setEditJobId(null); } }}
        title="Edit job"
        size="lg"
      >
        {editJob && isUnifiedJob(editJob) && <JobUserAuthorization />}
        {editJob && (editJob.unified && !editJob.unified.compatibility ? <UnifiedJobForm
          key={editJob.jobId}
          initial={editJob.unified.config}
          actions={actions}
          expectedRevision={editJob.configRevision}
          onDirtyChange={editorDirtyChange}
          submitting={submitting}
          onSubmit={(config) => handleUnifiedUpdate(editJob, config)}
        /> : <NewJobForm
          key={editJob.jobId}
          mode="edit"
          initialJob={editJob}
          kindMetas={kindMetas}
          submitting={submitting}
          onSave={(patch) => void handleUpdate(editJob.jobId, patch)}
        />)}
      </Modal>
    </div>
  );
}
