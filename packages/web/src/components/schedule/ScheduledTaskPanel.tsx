import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useScheduleStore } from '@/stores/scheduleStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { fetchSchedulerNext, fetchTaskRuns } from '@/services/api';
import {
  DEFAULT_SIMPLE,
  HOURS_RANGE,
  MINUTES_RANGE,
  MONTH_DAY_RANGE,
  nextCronFires,
  toNaiveIso,
  parseSimpleSchedule,
  simpleToCron,
} from './cronPreview';
import type { SimpleFreq, SimpleSpec } from './cronPreview';
import type {
  MisfirePolicy,
  ScheduleKind,
  ScheduledTask,
  ScheduledTaskInput,
  TaskRun,
  TaskSchedule,
} from '@/types';
import { CalendarClock, Clock, Pause, Play, Plus, RotateCw, Timer, Trash2, X } from 'lucide-react';

const DEFAULT_TIMEZONE = 'Asia/Shanghai';
const PREVIEW_COUNT = 5;

/** Simple mode builds a cron for you; advanced exposes the raw 5 fields. */
type ScheduleMode = 'simple' | 'advanced';

const SCHEDULE_MODES: { mode: ScheduleMode; label: string; title: string }[] = [
  { mode: 'simple', label: '简单模式', title: '选择频率与时间，自动生成 cron' },
  { mode: 'advanced', label: '高级模式', title: '直接编辑 cron / 一次性 / 固定间隔' },
];

const KINDS: { kind: ScheduleKind; label: string; hint: string }[] = [
  { kind: 'once', label: 'Once', hint: 'one-shot' },
  { kind: 'interval', label: 'Interval', hint: 'every N seconds' },
  { kind: 'cron', label: 'Cron', hint: '5-field' },
];

/** One-click cron fills — the expressions the alarm UI is mostly used for. */
const CRON_PRESETS: { label: string; expr: string }[] = [
  { label: '每天 9:00', expr: '0 9 * * *' },
  { label: '每工作日 9:00', expr: '0 9 * * 1-5' },
  { label: '每小时', expr: '0 * * * *' },
  { label: '每 30 分钟', expr: '*/30 * * * *' },
  { label: '每周一 9:00', expr: '0 9 * * 1' },
];

/** Simple-mode frequencies (ISO weekdays: 1=Mon … 7=Sun). */
const SIMPLE_FREQS: { freq: SimpleFreq; label: string }[] = [
  { freq: 'minutes', label: '每 N 分钟' },
  { freq: 'hours', label: '每 N 小时' },
  { freq: 'daily', label: '每天' },
  { freq: 'weekly', label: '每周' },
  { freq: 'monthly', label: '每月' },
];

const WEEKDAYS: { iso: number; label: string }[] = [
  { iso: 1, label: '一' },
  { iso: 2, label: '二' },
  { iso: 3, label: '三' },
  { iso: 4, label: '四' },
  { iso: 5, label: '五' },
  { iso: 6, label: '六' },
  { iso: 7, label: '日' },
];

const MINUTE_PRESETS = [5, 10, 15, 30];
const HOUR_PRESETS = [1, 2, 4, 6, 12];

const INTERVAL_PRESETS: { label: string; sec: number }[] = [
  { label: '5 分钟', sec: 300 },
  { label: '30 分钟', sec: 1800 },
  { label: '1 小时', sec: 3600 },
  { label: '6 小时', sec: 21600 },
  { label: '1 天', sec: 86400 },
];

const MISFIRE_POLICIES: { value: MisfirePolicy; label: string }[] = [
  { value: 'fire_now', label: 'Fire now (catch up once)' },
  { value: 'skip', label: 'Skip (do not catch up)' },
];

interface Draft {
  id: string | null;
  name: string;
  targetSessionId: string;
  text: string;
  kind: ScheduleKind;
  /** `datetime-local` value (no seconds) for kind=once. */
  at: string;
  intervalSec: number;
  cron: string;
  timezone: string;
  /** Empty string = unlimited. */
  maxRuns: string;
  misfirePolicy: MisfirePolicy;
  enabled: boolean;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function toLocalInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function emptyDraft(): Draft {
  const soon = new Date(Date.now() + 3600_000);
  soon.setSeconds(0, 0);
  return {
    id: null,
    name: '',
    targetSessionId: '',
    text: '',
    // Simple mode is the default view, so a fresh draft must already be
    // something it can represent: daily at 09:00.
    kind: 'cron',
    at: toLocalInput(soon),
    intervalSec: 3600,
    cron: simpleToCron(DEFAULT_SIMPLE),
    timezone: DEFAULT_TIMEZONE,
    maxRuns: '',
    misfirePolicy: 'fire_now',
    enabled: true,
  };
}

/** Naive ISO → "YYYY-MM-DD HH:mm" (backend timestamps carry no zone). */
function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 360) / 10}h`;
  return `${Math.round(sec / 8640) / 10}d`;
}

function scheduleSummary(schedule: TaskSchedule): string {
  if (schedule.kind === 'once') return `Once · ${formatDateTime(schedule.at)}`;
  if (schedule.kind === 'interval') return `Every ${formatDuration(schedule.intervalSec ?? 0)}`;
  return `Cron · ${schedule.cron ?? '—'}`;
}

function buildSchedule(draft: Draft): TaskSchedule {
  const timezone = draft.timezone.trim() || DEFAULT_TIMEZONE;
  if (draft.kind === 'once') {
    return { kind: 'once', at: draft.at ? `${draft.at}:00` : null, intervalSec: null, cron: null, timezone };
  }
  if (draft.kind === 'interval') {
    return {
      kind: 'interval',
      intervalSec: Math.max(1, Math.round(draft.intervalSec)),
      at: null,
      cron: null,
      timezone,
    };
  }
  return { kind: 'cron', cron: draft.cron.trim(), at: null, intervalSec: null, timezone };
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="border-b border-border-muted pb-1">
      <div className="text-xs font-semibold text-text-primary">{title}</div>
      <div className="text-[11px] text-text-tertiary">{subtitle}</div>
    </div>
  );
}

/** Full-width switch (mirrors ManageModal's SwitchRow) — used in the form. */
function SwitchRow({
  label,
  desc,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  desc: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="w-full flex items-start justify-between gap-3 rounded px-2.5 py-2 text-left transition-colors hover:bg-bg-tertiary disabled:opacity-60 disabled:pointer-events-none"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        <span className="block text-[11px] text-text-tertiary mt-0.5">{desc}</span>
      </span>
      <span
        className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-hover'
        }`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[14px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  );
}

/** Compact switch for the task rows. */
function MiniSwitch({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={checked ? 'Click to disable' : 'Click to enable'}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors disabled:opacity-60 disabled:pointer-events-none ${
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

function PreviewList({ label, times }: { label: string; times: string[] }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
      <span className="text-text-tertiary">{label}</span>
      {times.length === 0 && <span className="text-text-tertiary">—</span>}
      {times.map((t, i) => (
        <span key={`${t}-${i}`} className="font-mono text-text-secondary">
          {formatDateTime(t)}
        </span>
      ))}
    </div>
  );
}

/**
 * Scheduled-task manager: list + create/edit form + run history.
 * Structure mirrors ManageSessionsPanel (sections, per-item busy, and the
 * "call API → toast → reload" triple); mutations live in scheduleStore.
 */
export function ScheduledTaskPanel({ open = true }: { open?: boolean }) {
  const tasks = useScheduleStore((s) => s.tasks);
  const loaded = useScheduleStore((s) => s.loaded);
  const loading = useScheduleStore((s) => s.loading);
  const busyId = useScheduleStore((s) => s.busyId);
  const status = useScheduleStore((s) => s.status);
  const loadTasks = useScheduleStore((s) => s.loadTasks);
  const loadStatus = useScheduleStore((s) => s.loadStatus);
  const createTask = useScheduleStore((s) => s.createTask);
  const updateTask = useScheduleStore((s) => s.updateTask);
  const removeTask = useScheduleStore((s) => s.remove);
  const toggleEnabled = useScheduleStore((s) => s.toggleEnabled);
  const pauseTask = useScheduleStore((s) => s.pause);
  const resumeTask = useScheduleStore((s) => s.resume);
  const runNow = useScheduleStore((s) => s.runNow);

  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const showToast = useUIStore((s) => s.showToast);

  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<ScheduleMode>('simple');
  const [saving, setSaving] = useState(false);
  const [runsFor, setRunsFor] = useState<string | null>(null);
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [serverPreview, setServerPreview] = useState<string[]>([]);
  // Bumped after a save so the server-side preview re-fetches.
  const [previewTick, setPreviewTick] = useState(0);

  useEffect(() => {
    if (!open) return;
    void loadTasks();
    void loadStatus();
    void loadSessions();
  }, [open, loadTasks, loadStatus, loadSessions]);

  // GET /api/scheduler/next — authoritative fire times of the saved task.
  useEffect(() => {
    let alive = true;
    if (!open || !draft.id) {
      setServerPreview([]);
      return;
    }
    setServerPreview([]);
    fetchSchedulerNext(draft.id, PREVIEW_COUNT)
      .then((list) => {
        if (!alive) return;
        setServerPreview(list.map((n) => n.fireAt).filter((v): v is string => typeof v === 'string'));
      })
      .catch(() => {
        if (alive) setServerPreview([]);
      });
    return () => {
      alive = false;
    };
  }, [open, draft.id, previewTick]);

  useEffect(() => {
    let alive = true;
    if (!open || !runsFor) {
      setRuns([]);
      return;
    }
    setRunsLoading(true);
    fetchTaskRuns(runsFor)
      .then((list) => {
        if (alive) setRuns(list);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setRuns([]);
        showToast(e instanceof Error ? e.message : 'Failed to load run history', 'error');
      })
      .finally(() => {
        if (alive) setRunsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, runsFor, showToast]);

  // Reverse view of the current draft: `null` when its cron is more than
  // simple mode can express (→ advanced mode only, never auto-rewritten).
  const simpleSpec = useMemo(
    () =>
      parseSimpleSchedule({
        kind: draft.kind,
        intervalSec: draft.kind === 'interval' ? draft.intervalSec : null,
        cron: draft.kind === 'cron' ? draft.cron : null,
      }),
    [draft.kind, draft.intervalSec, draft.cron],
  );

  // Real-time draft preview: computed locally so it updates while typing
  // (a draft has no id, so GET /api/scheduler/next cannot answer for it).
  const localPreview = useMemo(() => {
    if (draft.kind === 'cron') return nextCronFires(draft.cron, PREVIEW_COUNT);
    if (draft.kind === 'once') return draft.at ? [`${draft.at}:00`] : [];
    const sec = Math.max(1, Math.round(draft.intervalSec));
    const base = new Date();
    base.setSeconds(0, 0);
    return Array.from({ length: PREVIEW_COUNT }, (_, i) =>
      toNaiveIso(new Date(base.getTime() + sec * 1000 * (i + 1))),
    );
  }, [draft.kind, draft.cron, draft.at, draft.intervalSec]);

  /** Write a simple-mode selection back as cron (the only storage format). */
  const applySimple = (patch: Partial<SimpleSpec>) => {
    const next = { ...(simpleSpec ?? DEFAULT_SIMPLE), ...patch };
    setDraft({ ...draft, kind: 'cron', cron: simpleToCron(next) });
  };

  const switchMode = (next: ScheduleMode) => {
    if (next === 'simple' && !simpleSpec) {
      showToast('当前 cron 无法用简单模式表达，已保留高级模式', 'error');
      return;
    }
    setMode(next);
  };

  const toggleWeekday = (iso: number) => {
    const current = new Set(simpleSpec?.weekdays ?? DEFAULT_SIMPLE.weekdays);
    if (current.has(iso)) current.delete(iso);
    else current.add(iso);
    const weekdays = [...current].sort((a, b) => a - b);
    // Never allow the empty set — it would generate a `*`-every-day cron.
    applySimple({ freq: 'weekly', weekdays: weekdays.length > 0 ? weekdays : [iso] });
  };

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const startEdit = (task: ScheduledTask) => {
    setDraft({
      id: task.id,
      name: task.name,
      targetSessionId: task.targetSessionId,
      text: task.text,
      kind: task.schedule.kind,
      at: task.schedule.at ? task.schedule.at.slice(0, 16) : emptyDraft().at,
      intervalSec: task.schedule.intervalSec ?? 3600,
      cron: task.schedule.cron ?? '0 9 * * *',
      timezone: task.schedule.timezone ?? DEFAULT_TIMEZONE,
      maxRuns: task.maxRuns === null || task.maxRuns === undefined ? '' : String(task.maxRuns),
      misfirePolicy: task.misfirePolicy,
      enabled: task.enabled,
    });
    // Existing tasks open in whichever mode can represent them: anything
    // simple-parsable round-trips back to the friendly editor, the rest keeps
    // its raw cron in advanced mode.
    setMode(parseSimpleSchedule(task.schedule) ? 'simple' : 'advanced');
    setEditing(true);
  };

  const resetForm = () => {
    setDraft(emptyDraft());
    setMode('simple');
    setEditing(false);
  };

  const handleSave = async () => {
    const name = draft.name.trim();
    if (!name) {
      showToast('Name is required', 'error');
      return;
    }
    if (!draft.targetSessionId) {
      showToast('Pick a target session', 'error');
      return;
    }
    if (!draft.text.trim()) {
      showToast('Task text is required', 'error');
      return;
    }
    const schedule = buildSchedule(draft);
    if (schedule.kind === 'once' && !schedule.at) {
      showToast('Pick a date and time', 'error');
      return;
    }
    if (schedule.kind === 'cron' && nextCronFires(schedule.cron ?? '', 1).length === 0) {
      showToast('Invalid cron expression', 'error');
      return;
    }
    let maxRuns: number | null = null;
    if (draft.maxRuns.trim() !== '') {
      const parsed = Number(draft.maxRuns);
      if (!Number.isInteger(parsed) || parsed < 1) {
        showToast('Max runs must be a positive whole number', 'error');
        return;
      }
      maxRuns = parsed;
    }
    const payload: ScheduledTaskInput = {
      name,
      targetSessionId: draft.targetSessionId,
      text: draft.text.trim(),
      schedule,
      enabled: draft.enabled,
      maxRuns,
      misfirePolicy: draft.misfirePolicy,
    };
    setSaving(true);
    const ok = draft.id ? await updateTask(draft.id, payload) : await createTask(payload);
    setSaving(false);
    if (ok) {
      setPreviewTick((t) => t + 1);
      resetForm();
    }
  };

  const handleDelete = async (task: ScheduledTask) => {
    if (!confirm(`Delete scheduled task "${task.name}"?`)) return;
    if (runsFor === task.id) setRunsFor(null);
    if (draft.id === task.id) resetForm();
    await removeTask(task.id);
  };

  return (
    <div className="flex flex-col gap-5">
      {/* ── Section 1: task list ── */}
      <section className="flex flex-col gap-2">
        <SectionHeader
          title="Scheduled tasks / 定时任务"
          subtitle="Each task dispatches its text to the target session's worker when it fires."
        />
        <div className="flex items-center gap-2 text-[11px] text-text-tertiary">
          <span>
            {tasks.length} task{tasks.length === 1 ? '' : 's'}
          </span>
          <span
            className={`rounded border px-1 py-px ${
              status?.running
                ? 'border-accent/50 bg-accent/10 text-accent'
                : 'border-border-default bg-bg-tertiary text-text-tertiary'
            }`}
          >
            engine {status?.running ? 'running' : 'stopped'}
          </span>
        </div>

        <div className="flex flex-col gap-1.5 rounded border border-border-muted bg-bg-primary p-1">
          {!loaded && loading && (
            <div className="py-4 text-center text-[11px] text-text-tertiary">Loading tasks…</div>
          )}
          {loaded && tasks.length === 0 && (
            <div className="py-4 text-center text-sm text-text-tertiary">
              No scheduled tasks yet — create one below.
            </div>
          )}
          {tasks.map((task) => {
            const busy = busyId === task.id;
            const target = sessions.find((s) => s.id === task.targetSessionId);
            return (
              <div
                key={task.id}
                className={`flex flex-col gap-1 rounded px-2.5 py-2 transition-colors hover:bg-bg-tertiary ${
                  busy ? 'pointer-events-none opacity-70' : ''
                }`}
              >
                <div className="flex items-center gap-2">
                  <MiniSwitch
                    label={`Enable ${task.name}`}
                    checked={task.enabled}
                    disabled={busyId !== null}
                    onChange={(v) => void toggleEnabled(task.id, v)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-text-primary truncate" title={task.name}>
                      {task.name}
                    </div>
                    <div className="text-[11px] text-text-tertiary truncate">
                      {scheduleSummary(task.schedule)}
                      {task.schedule.timezone ? ` · ${task.schedule.timezone}` : ''}
                    </div>
                  </div>
                  {task.paused && (
                    <span className="shrink-0 rounded border border-amber-500/50 bg-amber-500/10 px-1 py-px text-[10px] text-amber-400">
                      Paused
                    </span>
                  )}
                  {!task.enabled && (
                    <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-[10px] text-text-tertiary">
                      Off
                    </span>
                  )}
                </div>

                <div className="text-[11px] text-text-tertiary truncate">
                  → {target?.name || task.targetSessionId} · next {formatDateTime(task.nextFireAt)} ·
                  runs {task.runCount}
                  {task.maxRuns === null ? '' : `/${task.maxRuns}`}
                  {task.lastStatus ? ` · last ${task.lastStatus}` : ''}
                </div>
                <div className="text-[11px] text-text-secondary whitespace-pre-wrap break-words">
                  {task.text}
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {task.paused ? (
                    <button
                      type="button"
                      onClick={() => void resumeTask(task.id)}
                      disabled={busyId !== null}
                      title="Resume this task"
                      className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                    >
                      <Play size={11} />
                      Resume
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void pauseTask(task.id)}
                      disabled={busyId !== null}
                      title="Temporarily hold this task"
                      className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                    >
                      <Pause size={11} />
                      Pause
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void runNow(task.id)}
                    disabled={busyId !== null}
                    title="Dispatch this task right now"
                    className="inline-flex items-center gap-1 rounded border border-accent/50 bg-accent/10 px-2 py-1 text-[11px] font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-60 disabled:pointer-events-none"
                  >
                    <Clock size={11} />
                    Run now
                  </button>
                  <button
                    type="button"
                    onClick={() => setRunsFor(runsFor === task.id ? null : task.id)}
                    disabled={busyId !== null}
                    title="Show run history"
                    className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                  >
                    <RotateCw size={11} />
                    History
                  </button>
                  <button
                    type="button"
                    onClick={() => startEdit(task)}
                    disabled={busyId !== null}
                    title="Edit this task"
                    className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] font-medium text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary disabled:opacity-60 disabled:pointer-events-none"
                  >
                    <Timer size={11} />
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDelete(task)}
                    disabled={busyId !== null}
                    title="Delete this task"
                    className="inline-flex items-center gap-1 rounded border border-danger/50 bg-danger/10 px-2 py-1 text-[11px] font-medium text-danger transition-colors hover:bg-danger/20 disabled:opacity-60 disabled:pointer-events-none"
                  >
                    <Trash2 size={11} />
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── Section 2: run history ── */}
      {runsFor && (
        <section className="flex flex-col gap-2">
          <SectionHeader
            title="Run history / 执行历史"
            subtitle={`Recent dispatches of ${runsFor}.`}
          />
          <div className="flex flex-col gap-1 rounded border border-border-muted bg-bg-primary p-1">
            {runsLoading && (
              <div className="py-3 text-center text-[11px] text-text-tertiary">Loading…</div>
            )}
            {!runsLoading && runs.length === 0 && (
              <div className="py-3 text-center text-[11px] text-text-tertiary">No runs yet</div>
            )}
            {runs.map((run) => (
              <div
                key={run.runId || `${run.taskId}-${run.fireAt ?? ''}`}
                className="flex items-center gap-2 px-2.5 py-1.5 text-[11px]"
              >
                <span className="shrink-0 rounded border border-border-default bg-bg-tertiary px-1 py-px text-text-secondary">
                  {run.status}
                </span>
                <span className="font-mono text-text-secondary">{formatDateTime(run.fireAt)}</span>
                {run.actualAt && (
                  <span className="text-text-tertiary">→ {formatDateTime(run.actualAt)}</span>
                )}
                {run.error && (
                  <span className="min-w-0 truncate text-danger" title={run.error}>
                    {run.error}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Section 3: create / edit form ── */}
      <section className="flex flex-col gap-2">
        <SectionHeader
          title={editing ? 'Edit task / 编辑任务' : 'New task / 新建任务'}
          subtitle="Pick a session, the text to dispatch, and when it should fire."
        />

        <div className="flex flex-col gap-2 rounded border border-border-muted bg-bg-primary px-2.5 py-2">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-text-tertiary">Name</span>
            <input
              aria-label="Task name"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="每天 9 点跑数据"
              className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-text-tertiary">Target session</span>
            <select
              aria-label="Target session"
              value={draft.targetSessionId}
              onChange={(e) => setDraft({ ...draft, targetSessionId: e.target.value })}
              className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
            >
              <option value="">Select a session…</option>
              {sessionOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Untitled'} · {s.id}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-text-tertiary">Task text</span>
            <textarea
              aria-label="Task text"
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
              rows={3}
              placeholder="到点后派发给 session 的任务文本"
              className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50 resize-y"
            />
          </label>

          {/* Schedule — simple mode (default) or the raw cron editor */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-text-tertiary">Schedule</span>
              <div className="flex gap-1">
                {SCHEDULE_MODES.map((m) => (
                  <button
                    key={m.mode}
                    type="button"
                    aria-pressed={mode === m.mode}
                    disabled={m.mode === 'simple' && !simpleSpec}
                    title={
                      m.mode === 'simple' && !simpleSpec
                        ? '当前 cron 无法用简单模式表达'
                        : m.title
                    }
                    onClick={() => switchMode(m.mode)}
                    className={`rounded border px-2 py-0.5 text-[10px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none ${
                      mode === m.mode
                        ? 'border-accent/50 bg-accent/10 text-accent'
                        : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            {mode === 'simple' && simpleSpec && (
              <div className="flex flex-col gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[11px] text-text-tertiary">重复频率</span>
                  <select
                    aria-label="Frequency"
                    value={simpleSpec.freq}
                    onChange={(e) => applySimple({ freq: e.target.value as SimpleFreq })}
                    className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                  >
                    {SIMPLE_FREQS.map((f) => (
                      <option key={f.freq} value={f.freq}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </label>

                {(simpleSpec.freq === 'minutes' || simpleSpec.freq === 'hours') && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">
                      {simpleSpec.freq === 'minutes' ? '间隔分钟数 (1-59)' : '间隔小时数 (1-23)'}
                    </span>
                    <input
                      aria-label={simpleSpec.freq === 'minutes' ? 'Every N minutes' : 'Every N hours'}
                      type="number"
                      min={
                        simpleSpec.freq === 'minutes' ? MINUTES_RANGE.min : HOURS_RANGE.min
                      }
                      max={
                        simpleSpec.freq === 'minutes' ? MINUTES_RANGE.max : HOURS_RANGE.max
                      }
                      value={simpleSpec.n}
                      onChange={(e) => applySimple({ n: Number(e.target.value) })}
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                    />
                    <div className="flex flex-wrap gap-1">
                      {(simpleSpec.freq === 'minutes' ? MINUTE_PRESETS : HOUR_PRESETS).map((n) => (
                        <button
                          key={n}
                          type="button"
                          onClick={() => applySimple({ n })}
                          className="rounded border border-border-default bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
                        >
                          {n}
                          {simpleSpec.freq === 'minutes' ? ' 分钟' : ' 小时'}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {simpleSpec.freq === 'weekly' && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">星期（可多选）</span>
                    <div className="flex flex-wrap gap-1">
                      {WEEKDAYS.map((w) => {
                        const active = simpleSpec.weekdays.includes(w.iso);
                        return (
                          <button
                            key={w.iso}
                            type="button"
                            aria-label={`星期${w.label}`}
                            aria-pressed={active}
                            onClick={() => toggleWeekday(w.iso)}
                            className={`w-8 rounded border px-1 py-0.5 text-[11px] transition-colors ${
                              active
                                ? 'border-accent/50 bg-accent/10 text-accent'
                                : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                            }`}
                          >
                            {w.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {simpleSpec.freq === 'monthly' && (
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">每月几号 (1-31)</span>
                    <input
                      aria-label="Day of month"
                      type="number"
                      min={MONTH_DAY_RANGE.min}
                      max={MONTH_DAY_RANGE.max}
                      value={simpleSpec.day}
                      onChange={(e) => applySimple({ day: Number(e.target.value) })}
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                    />
                  </label>
                )}

                {simpleSpec.freq !== 'minutes' && simpleSpec.freq !== 'hours' && (
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">触发时间</span>
                    <input
                      aria-label="Fire time"
                      type="time"
                      value={simpleSpec.time}
                      onChange={(e) => applySimple({ time: e.target.value })}
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                    />
                  </label>
                )}

                <div className="text-[11px] text-text-tertiary">
                  cron:{' '}
                  <code className="font-mono text-text-secondary">{simpleToCron(simpleSpec)}</code>
                </div>
              </div>
            )}

            {mode === 'simple' && !simpleSpec && (
              <div className="rounded border border-border-muted bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
                当前 cron 无法用简单模式表达，请切换到高级模式编辑（原始表达式已保留）。
              </div>
            )}

            {mode === 'advanced' && (
              <>
                {/* Kind selector */}
                <div className="flex flex-col gap-1">
                  <div className="flex gap-1">
                    {KINDS.map((k) => (
                      <button
                        key={k.kind}
                        type="button"
                        onClick={() => setDraft({ ...draft, kind: k.kind })}
                        aria-pressed={draft.kind === k.kind}
                        className={`flex-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors ${
                          draft.kind === k.kind
                            ? 'border-accent/50 bg-accent/10 text-accent'
                            : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                        }`}
                      >
                        {k.label}
                        <span className="ml-1 text-[10px] opacity-70">{k.hint}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* once */}
                {draft.kind === 'once' && (
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">Fire at</span>
                    <input
                      aria-label="Fire at"
                      type="datetime-local"
                      value={draft.at}
                      onChange={(e) => setDraft({ ...draft, at: e.target.value })}
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                    />
                  </label>
                )}

                {/* interval */}
                {draft.kind === 'interval' && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">Interval (seconds)</span>
                    <input
                      aria-label="Interval seconds"
                      type="number"
                      min={1}
                      value={draft.intervalSec}
                      onChange={(e) => setDraft({ ...draft, intervalSec: Number(e.target.value) })}
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
                    />
                    <div className="flex flex-wrap gap-1">
                      {INTERVAL_PRESETS.map((p) => (
                        <button
                          key={p.sec}
                          type="button"
                          onClick={() => setDraft({ ...draft, intervalSec: p.sec })}
                          className="rounded border border-border-default bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* cron */}
                {draft.kind === 'cron' && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[11px] text-text-tertiary">
                      Cron (min hour dom month dow)
                    </span>
                    <input
                      aria-label="Cron expression"
                      value={draft.cron}
                      onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
                      placeholder="0 9 * * 1-5"
                      className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 font-mono text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
                    />
                    <div className="flex flex-wrap gap-1">
                      {CRON_PRESETS.map((p) => (
                        <button
                          key={p.expr}
                          type="button"
                          onClick={() => setDraft({ ...draft, cron: p.expr })}
                          title={p.expr}
                          className="rounded border border-border-default bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Next-fire preview */}
          <div className="flex flex-col gap-0.5 rounded bg-bg-tertiary px-2 py-1.5">
            <PreviewList
              label={draft.kind === 'once' ? 'Fire at' : `Next ${PREVIEW_COUNT} fires`}
              times={localPreview}
            />
            {draft.id && serverPreview.length > 0 && (
              <PreviewList label={`Next ${PREVIEW_COUNT} (server)`} times={serverPreview} />
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <label className="flex flex-1 min-w-24 flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">Max runs (blank = ∞)</span>
              <input
                aria-label="Max runs"
                value={draft.maxRuns}
                onChange={(e) => setDraft({ ...draft, maxRuns: e.target.value })}
                inputMode="numeric"
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
              />
            </label>
            <label className="flex flex-1 min-w-32 flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">Timezone</span>
              <input
                aria-label="Timezone"
                value={draft.timezone}
                onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
                placeholder={DEFAULT_TIMEZONE}
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
              />
            </label>
            <label className="flex flex-1 min-w-40 flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">Missed fire</span>
              <select
                aria-label="Missed fire"
                value={draft.misfirePolicy}
                onChange={(e) =>
                  setDraft({ ...draft, misfirePolicy: e.target.value as MisfirePolicy })
                }
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50"
              >
                {MISFIRE_POLICIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="rounded border border-border-muted bg-bg-primary p-1">
            <SwitchRow
              label="Enabled"
              desc="A disabled task is never scanned by the engine."
              checked={draft.enabled}
              disabled={saving}
              onChange={(v) => setDraft({ ...draft, enabled: v })}
            />
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleSave()}
              disabled={saving || busyId !== null}
            >
              {editing ? <Timer size={12} /> : <Plus size={12} />}
              {editing ? 'Save task' : 'Create task'}
            </Button>
            {editing && (
              <Button variant="ghost" size="sm" onClick={resetForm} disabled={saving}>
                <X size={12} />
                Cancel
              </Button>
            )}
            {!editing && tasks.length > 0 && (
              <span className="text-[11px] text-text-tertiary">
                <CalendarClock size={11} className="inline mr-1" />
                {tasks.filter((t) => t.enabled && !t.paused).length} active
              </span>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
