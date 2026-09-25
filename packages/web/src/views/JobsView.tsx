import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ListChecks, Plus } from 'lucide-react';
import {
  mockJobs,
  type Job,
  type JobSource,
  type JobStatus,
} from '@/components/jobs/mockJobs';

type Tab = 'list' | 'create';

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

function JobRow({ job }: { job: Job }) {
  const next = nextFireAt(job);
  return (
    <div className="flex flex-col gap-1.5 rounded border border-border-default bg-bg-primary px-3 py-2.5">
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
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-text-tertiary">
        <span>
          {sourceSummary(job.source)} → {job.target.sessionId ? job.target.sessionId : 'no target'}
        </span>
        {next && <span>next {formatDateTime(next)}</span>}
        <span>runs {job.runCount}</span>
        <span>{lastResultSummary(job)}</span>
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
          undeliverable — target missing · {job.mailbox.length} note(s) backlogged
        </div>
      )}
    </div>
  );
}

/**
 * JobsView（P3 最低范围骨架）。本轮只做「列表 / 创建新 Job」两个可切换的
 * 小标签：列表渲染 mock 种子数据；创建标签为占位。
 */
export default function JobsView() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('list');

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
            <div className="flex flex-col gap-2">
              <div className="text-[11px] text-text-tertiary">
                {mockJobs.length} job{mockJobs.length === 1 ? '' : 's'} (mock)
              </div>
              {mockJobs.map((job) => (
                <JobRow key={job.jobId} job={job} />
              ))}
            </div>
          ) : (
            <div className="rounded border border-border-muted bg-bg-secondary/40 px-3 py-6 text-center text-xs text-text-tertiary">
              创建表单待设计（后续增量补齐模板化快捷创建）。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
