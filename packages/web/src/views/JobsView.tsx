import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ListChecks, MoreHorizontal, Pause, Play, Plus, Trash2 } from 'lucide-react';
import { NewJobForm } from '@/components/jobs/NewJobForm';
import { JobDetailDrawer } from '@/components/jobs/JobDetailDrawer';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import {
  mockJobs,
  type Job,
  type JobKind,
  type JobRun,
  type JobSource,
  type JobStatus,
} from '@/components/jobs/mockJobs';

type Tab = 'list' | 'create';

type StatusFilter = 'all' | 'active' | 'scheduled' | 'failed' | 'undeliverable';

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'scheduled', label: 'Scheduled' },
  { key: 'failed', label: 'Failed' },
  { key: 'undeliverable', label: 'Undeliverable' },
];

const KIND_OPTIONS: { key: 'all' | JobKind; label: string }[] = [
  { key: 'all', label: 'All kinds' },
  { key: 'background-process', label: 'background-process' },
  { key: 'session-message', label: 'session-message' },
  { key: 'session-broadcast', label: 'session-broadcast' },
  { key: 'main-lifecycle', label: 'main-lifecycle' },
  { key: 'scheduled_task', label: 'scheduled_task' },
];

const selectClass =
  'w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50';

/** 状态 → 徽标配色（沿用主题 token）。 */
function statusClass(status: JobStatus): string {
  switch (status) {
    case 'running':
      return 'border-accent/50 bg-accent/10 text-accent';
    case 'scheduled':
      return 'border-warning/50 bg-warning/10 text-warning';
    case 'completed':
      return 'border-success/50 bg-success/10 text-success';
    case 'failed':
      return 'border-danger/50 bg-danger/10 text-danger';
    case 'cancelled':
      return 'border-border-default bg-bg-tertiary text-text-tertiary';
    default:
      return 'border-border-default bg-bg-tertiary text-text-secondary';
  }
}

function sourceSummary(source: JobSource): string {
  if (source.type === 'plugin') return `plugin:${source.pluginName ?? '?'}`;
  if (source.sessionId) return `${source.type}:${source.sessionId.slice(0, 8)}…`;
  return source.type;
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Naive local ISO（仓库时间戳约定，无时区后缀）。 */
function naiveIso(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
}

function randomHex(n: number): string {
  let out = '';
  for (let i = 0; i < n; i++) out += Math.floor(Math.random() * 16).toString(16);
  return out;
}

/** 下次触发 = 各 enabled entry 中最早的 nextFireAt。 */
function nextFireAt(job: Job): string | null {
  let min: string | null = null;
  for (const e of job.schedule) {
    if (e.enabled && e.nextFireAt && (min === null || e.nextFireAt < min)) min = e.nextFireAt;
  }
  return min;
}

/** 最近结果摘要：lastDelivery（含 partial）> lastError > 状态。 */
function lastResultSummary(job: Job): string {
  if (job.lastDelivery) {
    const { status, results, errors } = job.lastDelivery;
    const ok = results?.length ?? 0;
    const bad = errors?.length ?? 0;
    return `last ${status}${bad > 0 ? ` · ${ok} ok / ${bad} err` : ''}`;
  }
  if (job.lastError) return `last error: ${job.lastError}`;
  return `last ${job.status}`;
}

/** 活跃优先排序档位：running → starting → scheduled → pending → 终态。 */
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
    default:
      return 4; // completed / failed / cancelled
  }
}

/** 活跃优先；同组内按 updatedAt 倒序（新在前）。 */
function compareJobs(a: Job, b: Job): number {
  const ra = activeRank(a.status);
  const rb = activeRank(b.status);
  if (ra !== rb) return ra - rb;
  return b.updatedAt.localeCompare(a.updatedAt);
}

/** 状态 chips 匹配（与 kind 下拉 AND 叠加）。 */
function matchesStatusFilter(job: Job, filter: StatusFilter): boolean {
  switch (filter) {
    case 'active':
      return job.status === 'pending' || job.status === 'starting' || job.status === 'running';
    case 'scheduled':
      return job.status === 'scheduled';
    case 'failed':
      return job.status === 'failed';
    case 'undeliverable':
      return job.notificationState === 'undeliverable';
    default:
      return true;
  }
}

const menuItemClass =
  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20';

function JobRow({
  job,
  onOpen,
  menuOpen,
  onToggleMenu,
  onRunNow,
  onTogglePaused,
  onDelete,
}: {
  job: Job;
  onOpen: () => void;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onRunNow: () => void;
  onTogglePaused: () => void;
  onDelete: () => void;
}) {
  const next = nextFireAt(job);
  return (
    <div
      onClick={onOpen}
      className="flex flex-col gap-1.5 rounded border border-border-default bg-bg-primary px-3 py-2.5 transition-colors hover:bg-bg-secondary/50 cursor-pointer"
    >
      <div className="flex items-center gap-2">
        <span
          className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-medium ${statusClass(job.status)}`}
        >
          {job.status}
        </span>
        <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1.5 py-px font-mono text-[10px] text-text-secondary">
          {job.kind}
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
            onClick={(e) => {
              e.stopPropagation();
              onToggleMenu();
            }}
            title="Job actions"
            aria-label="Job actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="shrink-0 rounded border border-transparent p-1 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-primary"
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
                <button
                  role="menuitem"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleMenu();
                    onRunNow();
                  }}
                  className={menuItemClass}
                >
                  <Play size={13} />
                  Run now
                </button>
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
          {sourceSummary(job.source)} → {job.target.sessionId ? job.target.sessionId : 'no target'}
        </span>
        {next && <span>next {formatDateTime(next)}</span>}
        <span>runs {job.runCount}</span>
        <span>{lastResultSummary(job)}</span>
        {job.mailbox.length > 0 && (
          <span className="rounded border border-warning/50 bg-warning/10 px-1 py-px text-[10px] text-warning">
            {job.mailbox.length} backlogged
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

      {job.notificationState === 'undeliverable' && (
        <div className="rounded border border-danger/50 bg-danger/10 px-2 py-1 text-[11px] text-danger">
          undeliverable — target missing
        </div>
      )}
    </div>
  );
}

/**
 * JobsView（P3 GUI）。「列表 / 创建新 Job」两个标签；列表支持筛选、排序、
 * 行内 `⋯` 动作菜单与详情抽屉（drawer）。全部动作为 mock，仅改本地列表 state。
 */
export default function JobsView() {
  const navigate = useNavigate();
  const showToast = useUIStore((s) => s.showToast);
  const sessions = useSessionStore((s) => s.sessions);
  const [tab, setTab] = useState<Tab>('list');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [kindFilter, setKindFilter] = useState<'all' | JobKind>('all');
  const [jobs, setJobs] = useState<Job[]>(mockJobs);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [menuJobId, setMenuJobId] = useState<string | null>(null);
  const [changeTargetJobId, setChangeTargetJobId] = useState<string | null>(null);
  const [newTargetId, setNewTargetId] = useState('');
  const [deleteJobId, setDeleteJobId] = useState<string | null>(null);

  const visibleJobs = useMemo(() => {
    return jobs
      .filter((job) => matchesStatusFilter(job, statusFilter))
      .filter((job) => kindFilter === 'all' || job.kind === kindFilter)
      .sort(compareJobs);
  }, [jobs, statusFilter, kindFilter]);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const selectedJob = jobs.find((j) => j.jobId === selectedJobId) ?? null;
  const changeTargetJob = jobs.find((j) => j.jobId === changeTargetJobId) ?? null;
  const deleteJob = jobs.find((j) => j.jobId === deleteJobId) ?? null;

  const updateJob = (jobId: string, fn: (j: Job) => Job) =>
    setJobs((prev) => prev.map((j) => (j.jobId === jobId ? fn(j) : j)));

  const handleCreate = (job: Job) => {
    setJobs((prev) => [...prev, job]);
    setTab('list');
    showToast(`Created job "${job.name}"`);
  };

  const handleRunNow = (job: Job) => {
    const iso = naiveIso(new Date());
    const run: JobRun = { runId: `run_${randomHex(6)}`, fireAt: iso, status: 'dispatched' };
    updateJob(job.jobId, (j) => ({
      ...j,
      runs: [...(j.runs ?? []), run],
      runCount: j.runCount + 1,
      lastFireAt: iso,
    }));
    showToast(`Ran job "${job.name}"`);
  };

  const handleTogglePaused = (job: Job) => {
    const next = !job.paused;
    updateJob(job.jobId, (j) => ({ ...j, paused: next }));
    showToast(next ? `Paused job "${job.name}"` : `Resumed job "${job.name}"`);
  };

  const handleToggleEntry = (jobId: string, entryId: string) => {
    updateJob(jobId, (j) => ({
      ...j,
      schedule: j.schedule.map((e) =>
        e.id === entryId
          ? {
              ...e,
              enabled: !e.enabled,
              nextFireAt: e.enabled ? null : naiveIso(new Date(Date.now() + 86400_000)),
            }
          : e,
      ),
    }));
  };

  const openChangeTarget = (job: Job) => {
    setNewTargetId(job.target.sessionId ?? '');
    setChangeTargetJobId(job.jobId);
  };

  const handleChangeTarget = () => {
    if (!changeTargetJob) return;
    const hadBacklog = changeTargetJob.notificationState === 'undeliverable';
    const backlogCount = changeTargetJob.mailbox.length;
    updateJob(changeTargetJob.jobId, (j) => ({
      ...j,
      target: { sessionId: newTargetId || null },
      notificationState: j.notificationState === 'undeliverable' ? 'pending' : j.notificationState,
      mailbox: j.notificationState === 'undeliverable' ? [] : j.mailbox,
    }));
    setChangeTargetJobId(null);
    showToast(
      hadBacklog
        ? `Target updated; ${backlogCount} backlogged note(s) requeued`
        : `Target updated for "${changeTargetJob.name}"`,
    );
  };

  const handleDelete = () => {
    if (!deleteJob) return;
    setJobs((prev) => prev.filter((j) => j.jobId !== deleteJob.jobId));
    if (selectedJobId === deleteJob.jobId) setSelectedJobId(null);
    setDeleteJobId(null);
    showToast(`Deleted job "${deleteJob.name}"`);
  };

  return (
    <div className="flex flex-col h-full min-h-0 bg-bg-primary">
      {/* Header — pl-10 clears the fixed mobile hamburger button */}
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
          onClick={() => setTab('list')}
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
          onClick={() => setTab('create')}
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

      {/* Body — scrollable, centered column on desktop */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl p-4">
          {tab === 'list' ? (
            <div className="flex flex-col gap-3">
              {/* Filters — status chips + kind dropdown, AND 叠加 */}
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  {STATUS_FILTERS.map((f) => (
                    <button
                      key={f.key}
                      type="button"
                      aria-pressed={statusFilter === f.key}
                      onClick={() => setStatusFilter(f.key)}
                      className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                        statusFilter === f.key
                          ? 'border-accent/50 bg-accent/10 text-accent'
                          : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <label className="shrink-0 text-[11px] text-text-tertiary" htmlFor="job-kind-filter">
                    Kind
                  </label>
                  <select
                    id="job-kind-filter"
                    value={kindFilter}
                    onChange={(e) => setKindFilter(e.target.value as 'all' | JobKind)}
                    className="flex-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary outline-none focus:border-accent/50"
                  >
                    {KIND_OPTIONS.map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Count */}
              <div className="text-[11px] text-text-tertiary">
                {visibleJobs.length} of {jobs.length} jobs
              </div>

              {/* List / empty state */}
              {visibleJobs.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {visibleJobs.map((job) => (
                    <JobRow
                      key={job.jobId}
                      job={job}
                      onOpen={() => setSelectedJobId(job.jobId)}
                      menuOpen={menuJobId === job.jobId}
                      onToggleMenu={() =>
                        setMenuJobId(menuJobId === job.jobId ? null : job.jobId)
                      }
                      onRunNow={() => handleRunNow(job)}
                      onTogglePaused={() => handleTogglePaused(job)}
                      onDelete={() => setDeleteJobId(job.jobId)}
                    />
                  ))}
                </div>
              ) : (
                <div className="rounded border border-border-muted bg-bg-secondary/40 px-3 py-6 text-center text-xs text-text-tertiary">
                  No jobs match
                </div>
              )}
            </div>
          ) : (
            <NewJobForm jobs={jobs} onCreate={handleCreate} />
          )}
        </div>
      </div>

      {selectedJob && (
        <JobDetailDrawer
          job={selectedJob}
          onClose={() => setSelectedJobId(null)}
          onRunNow={() => handleRunNow(selectedJob)}
          onTogglePaused={() => handleTogglePaused(selectedJob)}
          onDelete={() => setDeleteJobId(selectedJob.jobId)}
          onChangeTarget={() => openChangeTarget(selectedJob)}
          onToggleEntryEnabled={(entryId) => handleToggleEntry(selectedJob.jobId, entryId)}
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
              <option value="">无 target</option>
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
            <Button variant="primary" size="sm" onClick={handleChangeTarget}>
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
            <Button variant="danger" size="sm" onClick={handleDelete}>
              Delete
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
