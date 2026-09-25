import { useMemo, useState } from 'react';
import { Check, Plus } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  ScheduleListEditor,
  Field,
  MiniSwitch,
  inputClass,
  newEntryDraft,
  buildScheduleEntry,
  scheduleEntryToDraft,
  type ScheduleEntryDraft,
} from '@/components/jobs/ScheduleListEditor';
import {
  defaultJobName,
  type Job,
  type JobAction,
  type JobKind,
  type JobSource,
} from '@/components/jobs/mockJobs';

type TemplateId = 'scheduled' | 'full';

export type JobFormMode = 'create' | 'edit';

const TEMPLATES: { id: TemplateId; label: string; hint: string }[] = [
  { id: 'scheduled', label: '创建定时任务', hint: '定时派发 assign 到目标会话' },
  { id: 'full', label: '无模板（全字段）', hint: '展示全部可写字段' },
];

const JOB_KINDS: JobKind[] = [
  'background-process',
  'session-message',
  'session-broadcast',
  'main-lifecycle',
  'scheduled_task',
];

const SOURCE_TYPES: JobSource['type'][] = ['agent', 'user', 'system', 'plugin'];

const ACTION_APIS = ['assign', 'send_session', 'spawn_process'];

function randomHex(n: number): string {
  let out = '';
  for (let i = 0; i < n; i++) out += Math.floor(Math.random() * 16).toString(16);
  return out;
}

/**
 * Job 表单。create 模式带模板选择器（定时任务 / 全字段）；edit 模式直接展示
 * 全字段表单并预填 initialJob，kind 只读、name 必填。提交统一走 onSubmit。
 */
export function NewJobForm({
  mode = 'create',
  jobs,
  initialJob,
  onSubmit,
}: {
  mode?: JobFormMode;
  jobs: Job[];
  initialJob?: Job;
  onSubmit: (job: Job) => void;
}) {
  const editing = mode === 'edit' && !!initialJob;
  const sessions = useSessionStore((s) => s.sessions);
  const showToast = useUIStore((s) => s.showToast);
  const [template, setTemplate] = useState<TemplateId>('scheduled');
  const [nameError, setNameError] = useState('');

  // ── 创建定时任务 模板字段 ──
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [targetSessionId, setTargetSessionId] = useState('');
  const [text, setText] = useState('');
  const [entries, setEntries] = useState<ScheduleEntryDraft[]>([newEntryDraft()]);

  // ── 全字段字段（create / edit 共用；edit 用 initialJob 预填） ──
  const [ffName, setFfName] = useState(initialJob?.name ?? '');
  const [ffDescription, setFfDescription] = useState(initialJob?.description ?? '');
  const [ffKind, setFfKind] = useState<JobKind>(initialJob?.kind ?? 'scheduled_task');
  const [ffSourceType, setFfSourceType] = useState<JobSource['type']>(
    initialJob?.source.type ?? 'user',
  );
  const [ffSourceSessionId, setFfSourceSessionId] = useState(initialJob?.source.sessionId ?? '');
  const [ffSourcePluginName, setFfSourcePluginName] = useState(initialJob?.source.pluginName ?? '');
  const [ffTargetSessionId, setFfTargetSessionId] = useState(initialJob?.target.sessionId ?? '');
  const [ffEntries, setFfEntries] = useState<ScheduleEntryDraft[]>(() =>
    initialJob && initialJob.schedule.length > 0
      ? initialJob.schedule.map(scheduleEntryToDraft)
      : [newEntryDraft()],
  );
  const [ffActionApi, setFfActionApi] = useState(initialJob?.action.api ?? 'assign');
  const ffArgs: Record<string, unknown> = initialJob?.action.args ?? {};
  const [ffActionSessionId, setFfActionSessionId] = useState(
    ffArgs.sessionId != null ? String(ffArgs.sessionId) : '',
  );
  const [ffActionText, setFfActionText] = useState(ffArgs.text != null ? String(ffArgs.text) : '');
  const [ffActionCommand, setFfActionCommand] = useState(
    ffArgs.command != null ? String(ffArgs.command) : '',
  );
  const [ffActionCwd, setFfActionCwd] = useState(ffArgs.cwd != null ? String(ffArgs.cwd) : '');
  const [ffMaxRuns, setFfMaxRuns] = useState(
    initialJob?.maxRuns != null ? String(initialJob.maxRuns) : '',
  );
  const [ffPaused, setFfPaused] = useState(initialJob?.paused ?? false);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const defaultNameHint = `留空 name 将自动命名（当前 ${defaultJobName(jobs.length)}）`;

  const buildSource = (): JobSource => {
    if (ffSourceType === 'agent') {
      return { type: 'agent', sessionId: ffSourceSessionId || undefined };
    }
    if (ffSourceType === 'plugin') {
      return { type: 'plugin', pluginName: ffSourcePluginName || undefined };
    }
    return { type: ffSourceType };
  };

  const buildAction = (): JobAction => {
    if (ffActionApi === 'assign' || ffActionApi === 'send_session') {
      return {
        api: ffActionApi,
        args: { sessionId: ffActionSessionId || null, text: ffActionText.trim() },
      };
    }
    return {
      api: 'spawn_process',
      args: { command: ffActionCommand.trim(), cwd: ffActionCwd.trim() || undefined },
    };
  };

  const parseMaxRuns = (): number | null => {
    const raw = ffMaxRuns.trim();
    if (raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
  };

  const handleCreate = () => {
    const finalName = name.trim() || defaultJobName(jobs.length);
    const now = new Date();
    const job: Job = {
      jobId: `job_${randomHex(12)}`,
      name: finalName,
      description: description.trim(),
      kind: 'scheduled_task',
      status: 'scheduled',
      source: { type: 'user' },
      target: { sessionId: targetSessionId || null },
      schedule: entries.map(buildScheduleEntry),
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
    onSubmit(job);
  };

  const handleFullCreate = () => {
    const finalName = ffName.trim() || defaultJobName(jobs.length);
    const now = new Date();
    const job: Job = {
      jobId: `job_${randomHex(12)}`,
      name: finalName,
      description: ffDescription.trim(),
      kind: ffKind,
      status: ffKind === 'scheduled_task' ? 'scheduled' : 'pending',
      source: buildSource(),
      target: { sessionId: ffTargetSessionId || null },
      schedule: ffEntries.map(buildScheduleEntry),
      action: buildAction(),
      notificationState: 'pending',
      terminalEventId: null,
      mailbox: [],
      lastFireAt: null,
      runCount: 0,
      lastError: null,
      maxRuns: parseMaxRuns(),
      paused: ffPaused,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    onSubmit(job);
  };

  const handleSave = () => {
    if (!initialJob) return;
    const trimmed = ffName.trim();
    if (!trimmed) {
      setNameError('Name is required');
      showToast('Name is required', 'error');
      return;
    }
    setNameError('');
    const updated: Job = {
      ...initialJob,
      name: trimmed,
      description: ffDescription.trim(),
      source: buildSource(),
      target: { sessionId: ffTargetSessionId || null },
      schedule: ffEntries.map(buildScheduleEntry),
      action: buildAction(),
      maxRuns: parseMaxRuns(),
      paused: ffPaused,
      updatedAt: new Date().toISOString(),
    };
    onSubmit(updated);
  };

  const sourcePillClass = (active: boolean) =>
    `rounded border px-2.5 py-1 text-[11px] font-medium transition-colors ${
      active
        ? 'border-accent/50 bg-accent/10 text-accent'
        : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
    }`;

  const templateSelector = (
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
  );

  const scheduledForm = (
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

      <ScheduleListEditor entries={entries} onChange={setEntries} />

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
        <span className="text-[11px] text-text-tertiary">{defaultNameHint}</span>
      </div>
    </div>
  );

  const fullForm = (
    <div className="flex flex-col gap-3">
      <Field label={editing ? 'Name *' : 'Name'}>
        <input
          aria-label="Name"
          value={ffName}
          onChange={(e) => {
            setFfName(e.target.value);
            if (nameError) setNameError('');
          }}
          placeholder={editing ? '必填' : '留空将自动命名 job-N'}
          className={inputClass}
        />
        {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
      </Field>

      <Field label="Description">
        <input
          aria-label="Description"
          value={ffDescription}
          onChange={(e) => setFfDescription(e.target.value)}
          placeholder="可选"
          className={inputClass}
        />
      </Field>

      <Field label="Kind">
        <select
          aria-label="Kind"
          value={ffKind}
          onChange={(e) => setFfKind(e.target.value as JobKind)}
          disabled={editing}
          title={editing ? 'kind 编辑时只读' : undefined}
          className={`${inputClass} disabled:opacity-60`}
        >
          {JOB_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Field>

      {/* Source */}
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] text-text-tertiary">Source</span>
        <div className="flex flex-wrap gap-1">
          {SOURCE_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={ffSourceType === t}
              onClick={() => setFfSourceType(t)}
              className={sourcePillClass(ffSourceType === t)}
            >
              {t}
            </button>
          ))}
        </div>
        {ffSourceType === 'agent' && (
          <Field label="Source session (agent)">
            <select
              aria-label="Source session"
              value={ffSourceSessionId}
              onChange={(e) => setFfSourceSessionId(e.target.value)}
              className={inputClass}
            >
              <option value="">（未指定）</option>
              {sessionOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Untitled'} · {s.id}
                </option>
              ))}
            </select>
          </Field>
        )}
        {ffSourceType === 'plugin' && (
          <Field label="Plugin name">
            <input
              aria-label="Plugin name"
              value={ffSourcePluginName}
              onChange={(e) => setFfSourcePluginName(e.target.value)}
              placeholder="plugin name"
              className={inputClass}
            />
          </Field>
        )}
      </div>

      <Field label="Target">
        <select
          aria-label="Target"
          value={ffTargetSessionId}
          onChange={(e) => setFfTargetSessionId(e.target.value)}
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

      <ScheduleListEditor entries={ffEntries} onChange={setFfEntries} />

      {/* Action */}
      <div className="flex flex-col gap-2">
        <Field label="Action.api">
          <select
            aria-label="Action api"
            value={ffActionApi}
            onChange={(e) => setFfActionApi(e.target.value)}
            className={inputClass}
          >
            {ACTION_APIS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </Field>
        {(ffActionApi === 'assign' || ffActionApi === 'send_session') && (
          <>
            <Field label="args.sessionId">
              <input
                aria-label="Action sessionId"
                value={ffActionSessionId}
                onChange={(e) => setFfActionSessionId(e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="args.text">
              <textarea
                aria-label="Action text"
                value={ffActionText}
                onChange={(e) => setFfActionText(e.target.value)}
                rows={2}
                className={`${inputClass} resize-y`}
              />
            </Field>
          </>
        )}
        {ffActionApi === 'spawn_process' && (
          <>
            <Field label="命令/argv">
              <input
                aria-label="Command"
                value={ffActionCommand}
                onChange={(e) => setFfActionCommand(e.target.value)}
                placeholder="python script.py"
                className={`${inputClass} font-mono`}
              />
            </Field>
            <Field label="cwd">
              <input
                aria-label="Working directory"
                value={ffActionCwd}
                onChange={(e) => setFfActionCwd(e.target.value)}
                className={inputClass}
              />
            </Field>
          </>
        )}
      </div>

      {/* Limits */}
      <div className="flex flex-col gap-2">
        <Field label="Max runs (空 = ∞)">
          <input
            aria-label="Max runs"
            value={ffMaxRuns}
            onChange={(e) => setFfMaxRuns(e.target.value)}
            inputMode="numeric"
            className={inputClass}
          />
        </Field>
        <div className="flex items-center gap-2">
          <MiniSwitch label="Paused" checked={ffPaused} onChange={setFfPaused} />
          <span className="text-[11px] text-text-tertiary">Paused</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          onClick={editing ? handleSave : handleFullCreate}
        >
          {editing ? <Check size={12} /> : <Plus size={12} />}
          {editing ? 'Save' : 'Create job'}
        </Button>
        {!editing && <span className="text-[11px] text-text-tertiary">{defaultNameHint}</span>}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {editing ? (
        fullForm
      ) : (
        <>
          {templateSelector}
          {template === 'scheduled' ? scheduledForm : fullForm}
        </>
      )}
    </div>
  );
}
