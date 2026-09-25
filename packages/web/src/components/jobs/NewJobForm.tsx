import { useMemo, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import {
  DEFAULT_SIMPLE,
  HOURS_RANGE,
  MINUTES_RANGE,
  MONTH_DAY_RANGE,
  nextCronFires,
  toNaiveIso,
  parseSimpleSchedule,
  simpleToCron,
} from '@/components/schedule/cronPreview';
import type { SimpleFreq, SimpleSpec } from '@/components/schedule/cronPreview';
import {
  defaultJobName,
  type Job,
  type JobKind,
  type MisfirePolicy,
  type ScheduleEntry,
  type ScheduleEntryKind,
} from '@/components/jobs/mockJobs';
import { Plus, Trash2 } from 'lucide-react';

const DEFAULT_TIMEZONE = 'Asia/Shanghai';
const PREVIEW_COUNT = 5;

type TemplateId = 'scheduled' | 'full';

const TEMPLATES: { id: TemplateId; label: string; hint: string }[] = [
  { id: 'scheduled', label: '创建定时任务', hint: '定时派发 assign 到目标会话' },
  { id: 'full', label: '无模板（全字段）', hint: '展示全部可写字段（骨架）' },
];

/** Simple mode builds a cron for you; advanced exposes once/interval/cron. */
type ScheduleMode = 'simple' | 'advanced';

const SCHEDULE_MODES: { mode: ScheduleMode; label: string; title: string }[] = [
  { mode: 'simple', label: '简单模式', title: '选择频率与时间，自动生成 cron' },
  { mode: 'advanced', label: '高级模式', title: '直接编辑 cron / 一次性 / 固定间隔' },
];

const KINDS: { kind: ScheduleEntryKind; label: string; hint: string }[] = [
  { kind: 'once', label: 'Once', hint: 'one-shot' },
  { kind: 'interval', label: 'Interval', hint: 'every N seconds' },
  { kind: 'cron', label: 'Cron', hint: '5-field' },
];

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

const CRON_PRESETS: { label: string; expr: string }[] = [
  { label: '每天 9:00', expr: '0 9 * * *' },
  { label: '每工作日 9:00', expr: '0 9 * * 1-5' },
  { label: '每小时', expr: '0 * * * *' },
  { label: '每 30 分钟', expr: '*/30 * * * *' },
];

const MISFIRE_POLICIES: { value: MisfirePolicy; label: string }[] = [
  { value: 'fire_now', label: 'Fire now (catch up once)' },
  { value: 'skip', label: 'Skip (do not catch up)' },
];

const JOB_KINDS: JobKind[] = [
  'background-process',
  'session-message',
  'session-broadcast',
  'main-lifecycle',
  'scheduled_task',
];

const SOURCE_TYPES: Job['source']['type'][] = ['agent', 'user', 'system', 'plugin'];

const ACTION_APIS = ['assign', 'send_session', 'spawn_process'];

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function toLocalInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function randomHex(n: number): string {
  let out = '';
  for (let i = 0; i < n; i++) out += Math.floor(Math.random() * 16).toString(16);
  return out;
}

// ── Schedule entry draft（schedule 是列表，每条 entry 独立编辑） ──

interface ScheduleEntryDraft {
  key: string;
  kind: ScheduleEntryKind;
  mode: ScheduleMode;
  /** `datetime-local` value (no seconds) for kind=once. */
  at: string;
  intervalSec: number;
  cron: string;
  timezone: string;
  misfirePolicy: MisfirePolicy;
  enabled: boolean;
}

function newEntryDraft(): ScheduleEntryDraft {
  const soon = new Date(Date.now() + 3600_000);
  soon.setSeconds(0, 0);
  return {
    key: Math.random().toString(36).slice(2),
    kind: 'cron',
    mode: 'simple',
    at: toLocalInput(soon),
    intervalSec: 3600,
    cron: simpleToCron(DEFAULT_SIMPLE),
    timezone: DEFAULT_TIMEZONE,
    misfirePolicy: 'fire_now',
    enabled: true,
  };
}

/** 把编辑态 entry 构建为 PLAN §1 的 ScheduleEntry，并估算 nextFireAt。 */
function buildScheduleEntry(d: ScheduleEntryDraft): ScheduleEntry {
  const timezone = d.timezone.trim() || DEFAULT_TIMEZONE;
  let nextFireAt: string | null = null;
  if (d.kind === 'once') {
    nextFireAt = d.at ? `${d.at}:00` : null;
  } else if (d.kind === 'interval') {
    const sec = Math.max(1, Math.round(d.intervalSec));
    nextFireAt = toNaiveIso(new Date(Date.now() + sec * 1000));
  } else {
    nextFireAt = nextCronFires(d.cron.trim(), 1)[0] ?? null;
  }
  return {
    id: `schx_${randomHex(6)}`,
    kind: d.kind,
    at: d.kind === 'once' ? (d.at ? `${d.at}:00` : null) : null,
    intervalSec: d.kind === 'interval' ? Math.max(1, Math.round(d.intervalSec)) : null,
    cron: d.kind === 'cron' ? d.cron.trim() : null,
    timezone,
    misfirePolicy: d.misfirePolicy,
    enabled: d.enabled,
    nextFireAt,
  };
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-text-tertiary">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  'w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50';

function ScheduleEntryEditor({
  entry,
  index,
  canRemove,
  onChange,
  onRemove,
}: {
  entry: ScheduleEntryDraft;
  index: number;
  canRemove: boolean;
  onChange: (e: ScheduleEntryDraft) => void;
  onRemove: () => void;
}) {
  const simpleSpec = useMemo(
    () =>
      parseSimpleSchedule({
        kind: entry.kind,
        intervalSec: entry.kind === 'interval' ? entry.intervalSec : null,
        cron: entry.kind === 'cron' ? entry.cron : null,
      }),
    [entry.kind, entry.intervalSec, entry.cron],
  );

  const preview = useMemo(() => {
    if (entry.kind === 'once') return entry.at ? [`${entry.at}:00`] : [];
    if (entry.kind === 'interval') {
      const sec = Math.max(1, Math.round(entry.intervalSec));
      return [toNaiveIso(new Date(Date.now() + sec * 1000))];
    }
    return nextCronFires(entry.cron.trim(), PREVIEW_COUNT);
  }, [entry.kind, entry.at, entry.intervalSec, entry.cron]);

  const applySimple = (patch: Partial<SimpleSpec>) => {
    const next = { ...(simpleSpec ?? DEFAULT_SIMPLE), ...patch };
    onChange({ ...entry, kind: 'cron', cron: simpleToCron(next) });
  };

  const switchMode = (mode: ScheduleMode) => {
    if (mode === entry.mode) return;
    if (mode === 'simple') {
      const spec = simpleSpec ?? DEFAULT_SIMPLE;
      onChange({ ...entry, mode, kind: 'cron', cron: simpleToCron(spec) });
    } else {
      onChange({ ...entry, mode });
    }
  };

  const toggleWeekday = (iso: number) => {
    if (!simpleSpec) return;
    const has = simpleSpec.weekdays.includes(iso);
    const weekdays = has
      ? simpleSpec.weekdays.filter((w) => w !== iso)
      : [...simpleSpec.weekdays, iso].sort((a, b) => a - b);
    applySimple({ weekdays });
  };

  return (
    <div className="flex flex-col gap-2 rounded border border-border-default bg-bg-primary p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <MiniSwitch
            label={`Schedule ${index + 1} enabled`}
            checked={entry.enabled}
            onChange={(v) => onChange({ ...entry, enabled: v })}
          />
          <span className="text-xs font-medium text-text-primary">Schedule {index + 1}</span>
        </div>
        <button
          type="button"
          onClick={onRemove}
          disabled={!canRemove}
          aria-label="Remove schedule entry"
          title="Remove schedule entry"
          className="rounded border border-transparent p-1 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-danger disabled:opacity-40 disabled:pointer-events-none"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-text-tertiary">Mode</span>
        <div className="flex gap-1">
          {SCHEDULE_MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              aria-pressed={entry.mode === m.mode}
              disabled={m.mode === 'simple' && !simpleSpec}
              title={m.mode === 'simple' && !simpleSpec ? '当前 cron 无法用简单模式表达' : m.title}
              onClick={() => switchMode(m.mode)}
              className={`rounded border px-2 py-0.5 text-[10px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none ${
                entry.mode === m.mode
                  ? 'border-accent/50 bg-accent/10 text-accent'
                  : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {entry.mode === 'simple' && simpleSpec && (
        <div className="flex flex-col gap-2">
          <Field label="重复频率">
            <select
              aria-label="Frequency"
              value={simpleSpec.freq}
              onChange={(e) => applySimple({ freq: e.target.value as SimpleFreq })}
              className={inputClass}
            >
              {SIMPLE_FREQS.map((f) => (
                <option key={f.freq} value={f.freq}>
                  {f.label}
                </option>
              ))}
            </select>
          </Field>

          {(simpleSpec.freq === 'minutes' || simpleSpec.freq === 'hours') && (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">
                {simpleSpec.freq === 'minutes' ? '间隔分钟数 (1-59)' : '间隔小时数 (1-23)'}
              </span>
              <input
                aria-label={simpleSpec.freq === 'minutes' ? 'Every N minutes' : 'Every N hours'}
                type="number"
                min={simpleSpec.freq === 'minutes' ? MINUTES_RANGE.min : HOURS_RANGE.min}
                max={simpleSpec.freq === 'minutes' ? MINUTES_RANGE.max : HOURS_RANGE.max}
                value={simpleSpec.n}
                onChange={(e) => applySimple({ n: Number(e.target.value) })}
                className={inputClass}
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
            <Field label="每月几号 (1-31)">
              <input
                aria-label="Day of month"
                type="number"
                min={MONTH_DAY_RANGE.min}
                max={MONTH_DAY_RANGE.max}
                value={simpleSpec.day}
                onChange={(e) => applySimple({ day: Number(e.target.value) })}
                className={inputClass}
              />
            </Field>
          )}

          {simpleSpec.freq !== 'minutes' && simpleSpec.freq !== 'hours' && (
            <Field label="触发时间">
              <input
                aria-label="Fire time"
                type="time"
                value={simpleSpec.time}
                onChange={(e) => applySimple({ time: e.target.value })}
                className={inputClass}
              />
            </Field>
          )}

          <div className="text-[11px] text-text-tertiary">
            cron: <code className="font-mono text-text-secondary">{simpleToCron(simpleSpec)}</code>
          </div>
        </div>
      )}

      {entry.mode === 'simple' && !simpleSpec && (
        <div className="rounded border border-border-muted bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
          当前 cron 无法用简单模式表达，请切换到高级模式编辑（原始表达式已保留）。
        </div>
      )}

      {entry.mode === 'advanced' && (
        <>
          <div className="flex gap-1">
            {KINDS.map((k) => (
              <button
                key={k.kind}
                type="button"
                onClick={() => onChange({ ...entry, kind: k.kind })}
                aria-pressed={entry.kind === k.kind}
                className={`flex-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors ${
                  entry.kind === k.kind
                    ? 'border-accent/50 bg-accent/10 text-accent'
                    : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                }`}
              >
                {k.label}
                <span className="ml-1 text-[10px] opacity-70">{k.hint}</span>
              </button>
            ))}
          </div>

          {entry.kind === 'once' && (
            <Field label="Fire at">
              <input
                aria-label="Fire at"
                type="datetime-local"
                value={entry.at}
                onChange={(e) => onChange({ ...entry, at: e.target.value })}
                className={inputClass}
              />
            </Field>
          )}

          {entry.kind === 'interval' && (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">Interval (seconds)</span>
              <input
                aria-label="Interval seconds"
                type="number"
                min={1}
                value={entry.intervalSec}
                onChange={(e) => onChange({ ...entry, intervalSec: Number(e.target.value) })}
                className={inputClass}
              />
              <div className="flex flex-wrap gap-1">
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.sec}
                    type="button"
                    onClick={() => onChange({ ...entry, intervalSec: p.sec })}
                    className="rounded border border-border-default bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {entry.kind === 'cron' && (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-text-tertiary">Cron (min hour dom month dow)</span>
              <input
                aria-label="Cron expression"
                value={entry.cron}
                onChange={(e) => onChange({ ...entry, cron: e.target.value })}
                placeholder="0 9 * * 1-5"
                className={`${inputClass} font-mono`}
              />
              <div className="flex flex-wrap gap-1">
                {CRON_PRESETS.map((p) => (
                  <button
                    key={p.expr}
                    type="button"
                    onClick={() => onChange({ ...entry, cron: p.expr })}
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

      <div className="flex flex-wrap gap-2">
        <Field label="Missed fire">
          <select
            aria-label="Missed fire"
            value={entry.misfirePolicy}
            onChange={(e) => onChange({ ...entry, misfirePolicy: e.target.value as MisfirePolicy })}
            className={inputClass}
          >
            {MISFIRE_POLICIES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Timezone">
          <input
            aria-label="Timezone"
            value={entry.timezone}
            onChange={(e) => onChange({ ...entry, timezone: e.target.value })}
            placeholder={DEFAULT_TIMEZONE}
            className={inputClass}
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
        <span className="text-text-tertiary">Next {preview.length} fires</span>
        {preview.length === 0 && <span className="text-text-tertiary">—</span>}
        {preview.map((t, i) => (
          <span key={`${t}-${i}`} className="font-mono text-text-secondary">
            {formatDateTime(t)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function NewJobForm({ jobs, onCreate }: { jobs: Job[]; onCreate: (job: Job) => void }) {
  const sessions = useSessionStore((s) => s.sessions);
  const [template, setTemplate] = useState<TemplateId>('scheduled');

  // ── 创建定时任务 模板字段 ──
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [targetSessionId, setTargetSessionId] = useState('');
  const [text, setText] = useState('');
  const [entries, setEntries] = useState<ScheduleEntryDraft[]>([newEntryDraft()]);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const handleCreate = () => {
    const finalName = name.trim() || defaultJobName(jobs.length);
    const schedule = entries.map(buildScheduleEntry);
    const now = new Date();
    const job: Job = {
      jobId: `job_${randomHex(12)}`,
      name: finalName,
      description: description.trim(),
      kind: 'scheduled_task',
      status: 'scheduled',
      source: { type: 'user' },
      target: { sessionId: targetSessionId || null },
      schedule,
      action: { api: 'assign', args: { sessionId: targetSessionId || null, text: text.trim() } },
      notificationState: 'pending',
      terminalEventId: null,
      mailbox: [],
      lastFireAt: null,
      runCount: 0,
      lastError: null,
      maxRuns: null,
      paused: false,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    onCreate(job);
  };

  const updateEntry = (key: string, next: ScheduleEntryDraft) =>
    setEntries((prev) => prev.map((e) => (e.key === key ? next : e)));
  const removeEntry = (key: string) =>
    setEntries((prev) => (prev.length > 1 ? prev.filter((e) => e.key !== key) : prev));
  const addEntry = () => setEntries((prev) => [...prev, newEntryDraft()]);

  return (
    <div className="flex flex-col gap-3">
      {/* Template selector */}
      <div className="flex gap-1.5">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-pressed={template === t.id}
            onClick={() => setTemplate(t.id)}
            title={t.hint}
            className={`flex-1 rounded border px-2.5 py-1.5 text-xs font-medium transition-colors ${
              template === t.id
                ? 'border-accent/50 bg-accent/10 text-accent'
                : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {template === 'scheduled' ? (
        <div className="flex flex-col gap-3">
          <Field label="Name">
            <input
              aria-label="Job name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="留空将自动命名 job-N"
              className={inputClass}
            />
          </Field>

          <Field label="Description">
            <input
              aria-label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="可选"
              className={inputClass}
            />
          </Field>

          <Field label="Target session">
            <select
              aria-label="Target session"
              value={targetSessionId}
              onChange={(e) => setTargetSessionId(e.target.value)}
              className={inputClass}
            >
              <option value="">无 target</option>
              {sessionOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Untitled'} · {s.id}
                </option>
              ))}
            </select>
          </Field>

          {/* Schedule list */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-text-tertiary">Schedule（可多条）</span>
              <button
                type="button"
                onClick={addEntry}
                className="inline-flex items-center gap-1 rounded border border-border-default bg-bg-tertiary px-2 py-0.5 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
              >
                <Plus size={12} />
                Add entry
              </button>
            </div>
            {entries.map((e, i) => (
              <ScheduleEntryEditor
                key={e.key}
                entry={e}
                index={i}
                canRemove={entries.length > 1}
                onChange={(next) => updateEntry(e.key, next)}
                onRemove={() => removeEntry(e.key)}
              />
            ))}
          </div>

          <Field label="派发正文（action.args.text）">
            <textarea
              aria-label="Task text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              placeholder="到点后派发给 session 的任务文本"
              className={`${inputClass} resize-y`}
            />
          </Field>

          <div className="flex items-center gap-2">
            <Button variant="primary" size="sm" onClick={handleCreate}>
              <Plus size={12} />
              Create job
            </Button>
            <span className="text-[11px] text-text-tertiary">
              留空 name 将自动命名（当前 {defaultJobName(jobs.length)}）
            </span>
          </div>
        </div>
      ) : (
        /* 无模板（全字段）骨架 — 只渲染分组，控件简化并标注「待细化」，不提交 */
        <div className="flex flex-col gap-3">
          <div className="rounded border border-border-muted bg-bg-secondary/40 px-2.5 py-2 text-[11px] text-text-tertiary">
            字段分组骨架，提交动作待后续话题定案。
          </div>

          <Field label="Name（基本信息）">
            <input aria-label="Name" placeholder="待细化" className={inputClass} disabled />
          </Field>
          <Field label="Description（基本信息）">
            <input aria-label="Description" placeholder="待细化" className={inputClass} disabled />
          </Field>

          <Field label="Kind">
            <select aria-label="Kind" className={inputClass} defaultValue="scheduled_task">
              {JOB_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </Field>

          <div className="flex flex-col gap-1">
            <span className="text-[11px] text-text-tertiary">Source（四类）</span>
            <div className="flex flex-wrap gap-1">
              {SOURCE_TYPES.map((t) => (
                <span
                  key={t}
                  className="rounded border border-border-default bg-bg-tertiary px-2 py-0.5 text-[11px] text-text-secondary"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>

          <Field label="Target">
            <select aria-label="Target" className={inputClass} defaultValue="">
              <option value="">无 target</option>
              {sessionOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Untitled'} · {s.id}
                </option>
              ))}
            </select>
          </Field>

          <div className="rounded border border-border-muted bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
            Schedule — 待细化（复用「创建定时任务」的列表编辑器）
          </div>

          <div className="flex flex-col gap-2">
            <Field label="Action.api">
              <select aria-label="Action api" className={inputClass} defaultValue="assign">
                {ACTION_APIS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Action.args（简化控件）">
              <input aria-label="Action args" placeholder="待细化" className={inputClass} disabled />
            </Field>
          </div>

          <div className="flex flex-col gap-2">
            <Field label="Max runs（限制，空 = ∞）">
              <input aria-label="Max runs" placeholder="待细化" className={inputClass} disabled />
            </Field>
            <div className="flex items-center gap-2">
              <MiniSwitch label="Paused" checked={false} onChange={() => {}} />
              <span className="text-[11px] text-text-tertiary">Paused（限制，待细化）</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
