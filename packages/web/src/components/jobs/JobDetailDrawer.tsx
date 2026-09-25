import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, Copy, Pause, Pencil, Play, Target, Trash2, X } from 'lucide-react';
import { scheduleEntries, type Job, type JobRunRecord, type JobSource, type JobTimestamp } from '@/types/jobs';

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** epoch 秒（number）或本地朴素 ISO（string）→ "YYYY-MM-DD HH:mm"。 */
function formatDateTime(value?: JobTimestamp | null): string {
  if (value === null || value === undefined || value === '') return '—';
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 360) / 10}h`;
  return `${Math.round(sec / 8640) / 10}d`;
}

/** 状态 → 徽标配色（覆盖 job status 与 run status 两类字符串）。 */
function statusColor(status: string): string {
  switch (status) {
    case 'running':
    case 'starting':
      return 'border-accent/50 bg-accent/10 text-accent';
    case 'scheduled':
    case 'partial':
    case 'undeliverable':
      return 'border-warning/50 bg-warning/10 text-warning';
    case 'completed':
    case 'delivered':
    case 'dispatched':
      return 'border-success/50 bg-success/10 text-success';
    case 'failed':
    case 'error':
      return 'border-danger/50 bg-danger/10 text-danger';
    case 'cancelled':
    case 'expired':
    case 'skipped':
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

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-medium ${statusColor(status)}`}
    >
      {status}
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
        {title}
      </h3>
      {children}
    </section>
  );
}

function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="shrink-0 text-text-tertiary">{label}</span>
      <span className="min-w-0 text-right text-text-primary">{children}</span>
    </div>
  );
}

function MiniSwitch({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={checked ? 'Click to disable' : 'Click to enable'}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors ${
        checked ? 'bg-accent' : 'bg-bg-hover'
      }`}
    >
      <span
        className={`absolute top-[2px] left-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-[14px]' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

function scheduleKindLabel(kind: string): string {
  switch (kind) {
    case 'once':
      return 'Once';
    case 'interval':
      return 'Interval';
    case 'cron':
      return 'Cron';
    default:
      return kind;
  }
}

function RunRow({ run }: { run: JobRunRecord }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded border border-border-default bg-bg-primary px-2 py-1.5 text-[11px]">
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-text-tertiary">{formatDateTime(run.fire_at ?? null)}</span>
        {run.error && <span className="truncate text-danger">{run.error}</span>}
      </div>
      <StatusBadge status={run.status ?? 'unknown'} />
    </div>
  );
}

export function JobDetailDrawer({
  job,
  kindLabel,
  runs,
  runsLoading,
  onLoadMoreRuns,
  onClose,
  onRunNow,
  onTogglePaused,
  onDelete,
  onChangeTarget,
  onEdit,
  onToggleEntryEnabled,
}: {
  job: Job;
  kindLabel: string;
  runs: JobRunRecord[];
  runsLoading: boolean;
  onLoadMoreRuns: () => void;
  onClose: () => void;
  onRunNow: () => void;
  onTogglePaused: () => void;
  onDelete: () => void;
  onChangeTarget: () => void;
  onEdit: () => void;
  onToggleEntryEnabled: (entryId: string) => void;
}) {
  const [deliveryExpanded, setDeliveryExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const backlog = job.undeliveredFires ?? [];
  const isUndeliverable = job.lastStatus === 'undeliverable' || backlog.length > 0;
  const isScheduledTask = job.kind === 'scheduled-task';
  const hasTarget = !!job.target.sessionId;
  const schedule = scheduleEntries(job.schedule);
  const legacyScheduleText = !Array.isArray(job.schedule) && job.schedule != null
    ? typeof job.schedule === 'string' ? job.schedule : JSON.stringify(job.schedule)
    : null;

  const copyLogPath = async () => {
    try {
      await navigator.clipboard.writeText(job.logPath ?? '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable — ignore
    }
  };

  const delivery = job.lastDelivery;

  return createPortal(
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} />

      {/* Drawer panel */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Job detail"
        className="fixed inset-y-0 right-0 z-50 flex w-full md:max-w-[28rem] flex-col border-l border-border-default bg-bg-secondary shadow-xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-2 border-b border-border-default px-3 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="min-w-0 truncate text-sm font-semibold text-text-primary">
              {job.name}
            </h2>
            <StatusBadge status={job.status} />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            title="Close"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border-default bg-bg-tertiary text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex flex-col gap-4">
            {/* Undeliverable banner */}
            {isUndeliverable && (
              <div className="rounded border border-warning/50 bg-warning/10 px-2.5 py-2 text-[11px] text-warning">
                target missing — switch target to deliver {backlog.length} backlogged fire(s)
              </div>
            )}

            {/* Action group */}
            <div className="flex flex-wrap items-center gap-1.5">
              {isScheduledTask && (
                <button
                  type="button"
                  onClick={onRunNow}
                  disabled={!hasTarget}
                  title={hasTarget ? undefined : '无 target，无法立即派发'}
                  className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-bg-tertiary disabled:hover:text-text-secondary"
                >
                  <Play size={12} />
                  Run now
                </button>
              )}
              <button
                type="button"
                onClick={onTogglePaused}
                className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
              >
                {job.paused ? <Play size={12} /> : <Pause size={12} />}
                {job.paused ? 'Resume' : 'Pause'}
              </button>
              <button
                type="button"
                onClick={onChangeTarget}
                className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
              >
                <Target size={12} />
                Change target
              </button>
              <button
                type="button"
                onClick={onDelete}
                className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-danger transition-colors hover:bg-danger/10"
              >
                <Trash2 size={12} />
                Delete
              </button>
              <button
                type="button"
                onClick={onEdit}
                className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
              >
                <Pencil size={12} />
                Edit
              </button>
            </div>

            {/* 1. Overview */}
            <Section title="Overview">
              {job.description && (
                <div className="text-xs text-text-secondary">{job.description}</div>
              )}
              <div className="flex flex-col gap-1 rounded border border-border-default bg-bg-primary p-2.5">
                <KV label="Kind">{kindLabel}</KV>
                <KV label="Source">{sourceSummary(job.source)}</KV>
                <KV label="Target">
                  <span className="font-mono">{job.target.sessionId ?? 'no target'}</span>
                </KV>
                <KV label="Run count">{job.runCount}</KV>
                <KV label="Created">{formatDateTime(job.createdAt)}</KV>
                <KV label="Updated">{formatDateTime(job.updatedAt)}</KV>
                {job.paused && <KV label="Paused">Yes</KV>}
                {job.maxRuns != null && <KV label="Max runs">{job.maxRuns}</KV>}
              </div>
            </Section>

            {/* 2. Schedule entries */}
            <Section title="Schedule">
              {schedule.length === 0 ? (
                legacyScheduleText ? (
                  <pre className="overflow-x-auto whitespace-pre-wrap rounded border border-border-default bg-bg-primary p-2.5 text-[11px] text-text-secondary">
                    {legacyScheduleText}
                  </pre>
                ) : (
                  <div className="text-xs text-text-tertiary">—</div>
                )
              ) : (
                <div className="flex flex-col gap-1.5">
                  {schedule.map((e, index) => (
                    <div
                      key={e.id ?? index}
                      className="flex flex-col gap-1 rounded border border-border-default bg-bg-primary p-2.5 text-[11px]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="font-mono text-text-secondary">
                            {scheduleKindLabel(e.kind)}
                          </span>
                          <span className="font-mono text-text-tertiary">{e.id}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span
                            className={`text-[10px] font-medium ${
                              e.enabled ? 'text-success' : 'text-text-tertiary'
                            }`}
                          >
                            {e.enabled ? 'enabled' : 'disabled'}
                          </span>
                          {isScheduledTask && e.id && (
                            <MiniSwitch
                              label={`Schedule entry ${e.id} enabled`}
                              checked={e.enabled}
                              onChange={() => onToggleEntryEnabled(e.id)}
                            />
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col gap-0.5 text-text-secondary">
                        {e.kind === 'cron' && <span className="font-mono">{e.cron ?? '—'}</span>}
                        {e.kind === 'interval' && (
                          <span>every {formatDuration(e.intervalSec ?? e.interval_sec ?? 0)}</span>
                        )}
                        {e.kind === 'once' && <span>{formatDateTime(e.at ?? null)}</span>}
                        {e.timezone && <span className="text-text-tertiary">tz {e.timezone}</span>}
                        <span className="text-text-tertiary">misfire {e.misfirePolicy}</span>
                        <span className="text-text-tertiary">
                          next {formatDateTime(e.nextFireAt ?? null)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* 3. Last result */}
            <Section title="Last result">
              {delivery ? (
                <div className="flex flex-col gap-1.5 rounded border border-border-default bg-bg-primary p-2.5">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={delivery.status} />
                    {delivery.status === 'partial' && (
                      <button
                        type="button"
                        onClick={() => setDeliveryExpanded((v) => !v)}
                        className="text-[11px] text-accent hover:underline"
                      >
                        {deliveryExpanded ? 'Hide details' : 'Show results/errors'}
                      </button>
                    )}
                  </div>
                  {deliveryExpanded && (
                    <div className="flex flex-col gap-1 text-[11px]">
                      {(delivery.results ?? []).map((r, i) => (
                        <pre
                          key={`r${i}`}
                          className="overflow-x-auto whitespace-pre-wrap rounded bg-bg-tertiary px-2 py-1 font-mono text-text-secondary"
                        >
                          {JSON.stringify(r)}
                        </pre>
                      ))}
                      {(delivery.errors ?? []).map((r, i) => (
                        <pre
                          key={`e${i}`}
                          className="overflow-x-auto whitespace-pre-wrap rounded bg-bg-tertiary px-2 py-1 font-mono text-danger"
                        >
                          {JSON.stringify(r)}
                        </pre>
                      ))}
                    </div>
                  )}
                </div>
              ) : job.lastError ? (
                <div className="rounded border border-danger/50 bg-danger/10 px-2.5 py-2 text-xs text-danger">
                  {job.lastError}
                </div>
              ) : job.lastStatus ? (
                <div className="flex items-center gap-2">
                  <StatusBadge status={job.lastStatus} />
                </div>
              ) : (
                <div className="text-xs text-text-tertiary">—</div>
              )}
            </Section>

            {/* 4. Runs history */}
            <Section title="Runs">
              {runs.length === 0 ? (
                <div className="text-xs text-text-tertiary">
                  {runsLoading ? 'Loading…' : '—'}
                </div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {runs.map((r, i) => (
                    <RunRow key={r.run_id ?? `${r.fire_at}-${i}`} run={r} />
                  ))}
                  <button
                    type="button"
                    onClick={onLoadMoreRuns}
                    disabled={runsLoading}
                    className="rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
                  >
                    {runsLoading ? 'Loading…' : 'Load more'}
                  </button>
                </div>
              )}
            </Section>

            {/* 5. Backlog */}
            {backlog.length > 0 && (
              <Section title="Backlog">
                <div className="flex flex-col gap-1.5 rounded border border-warning/50 bg-warning/10 p-2.5">
                  <div className="text-xs font-medium text-warning">
                    {backlog.length} fire(s) backlogged — target missing
                  </div>
                  {backlog.map((note, i) => (
                    <div
                      key={`${note.entryId ?? 'e'}-${note.fireAt ?? i}`}
                      className="flex items-center justify-between gap-2 rounded border border-border-default bg-bg-primary px-2 py-1 text-[11px]"
                    >
                      <span className="font-mono text-text-tertiary">
                        {formatDateTime(note.fireAt ?? null)}
                      </span>
                      {note.entryId && (
                        <span className="font-mono text-text-tertiary">{note.entryId}</span>
                      )}
                      {note.error && <span className="truncate text-danger">{note.error}</span>}
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* 6. Log */}
            {job.logPath && (
              <Section title="Log">
                <div className="flex items-center gap-2 rounded border border-border-default bg-bg-primary p-2">
                  <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-secondary">
                    {job.logPath}
                  </code>
                  <button
                    type="button"
                    onClick={copyLogPath}
                    aria-label="Copy log path"
                    title="Copy log path"
                    className="shrink-0 rounded border border-border-default bg-bg-tertiary p-1 text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
                  >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                  </button>
                </div>
              </Section>
            )}
          </div>
        </div>
      </aside>
    </>,
    document.body,
  );
}
