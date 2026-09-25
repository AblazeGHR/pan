import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import {
  ScheduleListEditor,
  Field,
  MiniSwitch,
  inputClass,
  newEntryDraft,
  buildScheduleEntry,
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

export function NewJobForm({ jobs, onCreate }: { jobs: Job[]; onCreate: (job: Job) => void }) {
  const sessions = useSessionStore((s) => s.sessions);
  const [template, setTemplate] = useState<TemplateId>('scheduled');

  // ── 创建定时任务 模板字段 ──
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [targetSessionId, setTargetSessionId] = useState('');
  const [text, setText] = useState('');
  const [entries, setEntries] = useState<ScheduleEntryDraft[]>([newEntryDraft()]);

  // ── 无模板（全字段）字段 ──
  const [ffName, setFfName] = useState('');
  const [ffDescription, setFfDescription] = useState('');
  const [ffKind, setFfKind] = useState<JobKind>('scheduled_task');
  const [ffSourceType, setFfSourceType] = useState<JobSource['type']>('user');
  const [ffSourceSessionId, setFfSourceSessionId] = useState('');
  const [ffSourcePluginName, setFfSourcePluginName] = useState('');
  const [ffTargetSessionId, setFfTargetSessionId] = useState('');
  const [ffEntries, setFfEntries] = useState<ScheduleEntryDraft[]>([newEntryDraft()]);
  const [ffActionApi, setFfActionApi] = useState('assign');
  const [ffActionSessionId, setFfActionSessionId] = useState('');
  const [ffActionText, setFfActionText] = useState('');
  const [ffActionCommand, setFfActionCommand] = useState('');
  const [ffActionCwd, setFfActionCwd] = useState('');
  const [ffMaxRuns, setFfMaxRuns] = useState('');
  const [ffPaused, setFfPaused] = useState(false);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const defaultNameHint = `留空 name 将自动命名（当前 ${defaultJobName(jobs.length)}）`;

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
    onCreate(job);
  };

  const handleFullCreate = () => {
    const finalName = ffName.trim() || defaultJobName(jobs.length);

    let source: JobSource;
    if (ffSourceType === 'agent') {
      source = { type: 'agent', sessionId: ffSourceSessionId || undefined };
    } else if (ffSourceType === 'plugin') {
      source = { type: 'plugin', pluginName: ffSourcePluginName || undefined };
    } else {
      source = { type: ffSourceType };
    }

    let action: JobAction;
    if (ffActionApi === 'assign' || ffActionApi === 'send_session') {
      action = {
        api: ffActionApi,
        args: { sessionId: ffActionSessionId || null, text: ffActionText.trim() },
      };
    } else {
      action = {
        api: 'spawn_process',
        args: { command: ffActionCommand.trim(), cwd: ffActionCwd.trim() || undefined },
      };
    }

    const maxRuns = (() => {
      const raw = ffMaxRuns.trim();
      if (raw === '') return null;
      const n = Number(raw);
      return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
    })();

    const now = new Date();
    const job: Job = {
      jobId: `job_${randomHex(12)}`,
      name: finalName,
      description: ffDescription.trim(),
      kind: ffKind,
      status: ffKind === 'scheduled_task' ? 'scheduled' : 'pending',
      source,
      target: { sessionId: ffTargetSessionId || null },
      schedule: ffEntries.map(buildScheduleEntry),
      action,
      notificationState: 'pending',
      terminalEventId: null,
      mailbox: [],
      lastFireAt: null,
      runCount: 0,
      lastError: null,
      maxRuns,
      paused: ffPaused,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    onCreate(job);
  };

  const sourcePillClass = (active: boolean) =>
    `rounded border px-2.5 py-1 text-[11px] font-medium transition-colors ${
      active
        ? 'border-accent/50 bg-accent/10 text-accent'
        : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
    }`;

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
      ) : (
        <div className="flex flex-col gap-3">
          <Field label="Name">
            <input
              aria-label="Name"
              value={ffName}
              onChange={(e) => setFfName(e.target.value)}
              placeholder="留空将自动命名 job-N"
              className={inputClass}
            />
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
              className={inputClass}
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
            <Button variant="primary" size="sm" onClick={handleFullCreate}>
              <Plus size={12} />
              Create job
            </Button>
            <span className="text-[11px] text-text-tertiary">{defaultNameHint}</span>
          </div>
        </div>
      )}
    </div>
  );
}
