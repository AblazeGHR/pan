import { useMemo, useState } from 'react';
import { Check, Plus } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import {
  ScheduleListEditor,
  Field,
  MiniSwitch,
  inputClass,
  newEntryDraft,
  buildScheduleSpec,
  scheduleEntryToDraft,
  type ScheduleEntryDraft,
} from '@/components/jobs/ScheduleListEditor';
import { scheduleEntries, type Job, type JobCreateInput, type JobPatchInput, type MisfirePolicy } from '@/types/jobs';

export type JobFormMode = 'create' | 'edit';

type TemplateId = 'scheduled' | 'custom';

const TEMPLATES: { id: TemplateId; label: string; hint: string }[] = [
  { id: 'scheduled', label: '创建定时任务', hint: '定时派发到目标会话' },
  { id: 'custom', label: '自定义', hint: '全部可写字段（含限制项）' },
];

const MISFIRE_POLICIES: { value: MisfirePolicy; label: string }[] = [
  { value: 'fire_now', label: 'Fire now (catch up once)' },
  { value: 'skip', label: 'Skip (do not catch up)' },
];

/**
 * Job 表单（真实 /api/jobs 契约）。
 * - create：两个模板都创建 `scheduled-task`（POST 仅支持该 kind）；kind 不可选。
 * - edit：暴露 PATCH 支持的子集（name / description / text / target / schedule）；
 *   target 可清空为无 target；name 与 text 必填（后端 PATCH text 非空校验）。
 */
export function NewJobForm({
  mode = 'create',
  initialJob,
  submitting = false,
  onCreate,
  onSave,
}: {
  mode?: JobFormMode;
  initialJob?: Job;
  submitting?: boolean;
  onCreate?: (input: JobCreateInput) => void;
  onSave?: (patch: JobPatchInput) => void;
}) {
  const editing = mode === 'edit' && !!initialJob;
  const sessions = useSessionStore((s) => s.sessions);
  const [template, setTemplate] = useState<TemplateId>('scheduled');

  const [name, setName] = useState(initialJob?.name ?? '');
  const [description, setDescription] = useState(initialJob?.description ?? '');
  const [targetSessionId, setTargetSessionId] = useState(initialJob?.target.sessionId ?? '');
  const [text, setText] = useState(initialJob?.text ?? '');
  const [entries, setEntries] = useState<ScheduleEntryDraft[]>(() =>
    initialJob && scheduleEntries(initialJob.schedule).length > 0
      ? scheduleEntries(initialJob.schedule).map(scheduleEntryToDraft)
      : [newEntryDraft()],
  );
  const [maxRuns, setMaxRuns] = useState(
    initialJob?.maxRuns != null ? String(initialJob.maxRuns) : '',
  );
  const [misfirePolicy, setMisfirePolicy] = useState<MisfirePolicy>(
    initialJob?.misfirePolicy ?? 'fire_now',
  );
  const [enabled, setEnabled] = useState(initialJob?.enabled ?? true);

  const [nameError, setNameError] = useState('');
  const [textError, setTextError] = useState('');
  const [targetError, setTargetError] = useState('');

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );

  const parseMaxRuns = (): number | null => {
    const raw = maxRuns.trim();
    if (raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
  };

  const handleCreate = () => {
    const sid = targetSessionId.trim();
    const trimmedText = text.trim();
    let bad = false;
    if (!sid) {
      setTargetError('Target session is required');
      bad = true;
    }
    if (!trimmedText) {
      setTextError('Text is required');
      bad = true;
    }
    if (bad) return;
    onCreate?.({
      kind: 'scheduled-task',
      name: name.trim() || undefined,
      description: description.trim(),
      target: { sessionId: sid },
      text: trimmedText,
      schedule: entries.map(buildScheduleSpec),
      misfirePolicy,
      maxRuns: parseMaxRuns(),
      enabled,
    });
  };

  const handleSave = () => {
    if (!initialJob) return;
    const trimmedName = name.trim();
    const trimmedText = text.trim();
    let bad = false;
    if (!trimmedName) {
      setNameError('Name is required');
      bad = true;
    }
    if (!trimmedText) {
      setTextError('Text is required');
      bad = true;
    }
    if (bad) return;
    const patch: JobPatchInput = {
      name: trimmedName,
      description: description.trim(),
      text: trimmedText,
      target: { sessionId: targetSessionId.trim() || null },
    };
    if (initialJob.kind === 'scheduled-task') {
      patch.schedule = entries.map(buildScheduleSpec);
    }
    onSave?.(patch);
  };

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

  return (
    <div className="flex flex-col gap-3">
      {!editing && templateSelector}

      <Field label={editing ? 'Name *' : 'Name'}>
        <input
          aria-label="Name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (nameError) setNameError('');
          }}
          placeholder={editing ? '必填' : '留空则后端自动命名 job-N'}
          className={inputClass}
        />
        {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
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

      <Field label={editing ? 'Target session' : 'Target session *'}>
        <select
          aria-label="Target session"
          value={targetSessionId}
          onChange={(e) => {
            setTargetSessionId(e.target.value);
            if (targetError) setTargetError('');
          }}
          className={inputClass}
        >
          <option value="">{editing ? '无 target（积压）' : '选择会话…'}</option>
          {sessionOptions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name || 'Untitled'} · {s.id}
            </option>
          ))}
        </select>
        {targetError && <span className="text-[11px] text-danger">{targetError}</span>}
      </Field>

      {/* schedule 仅 scheduled-task 可编辑（PATCH schedule 限该 kind） */}
      {(!editing || initialJob?.kind === 'scheduled-task') && (
        <ScheduleListEditor entries={entries} onChange={setEntries} />
      )}

      {/* 派发正文：创建与编辑都必填（PATCH text 亦非空校验） */}
      <Field label="派发正文 *">
        <textarea
          aria-label="Task text"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (textError) setTextError('');
          }}
          rows={3}
          placeholder="到点后派发给 session 的任务文本"
          className={`${inputClass} resize-y`}
        />
        {textError && <span className="text-[11px] text-danger">{textError}</span>}
      </Field>

      {/* 自定义模板：限制项 */}
      {!editing && template === 'custom' && (
        <div className="flex flex-col gap-2">
          <Field label="Max runs (空 = ∞)">
            <input
              aria-label="Max runs"
              value={maxRuns}
              onChange={(e) => setMaxRuns(e.target.value)}
              inputMode="numeric"
              className={inputClass}
            />
          </Field>
          <Field label="Missed fire (job 级默认)">
            <select
              aria-label="Missed fire"
              value={misfirePolicy}
              onChange={(e) => setMisfirePolicy(e.target.value as MisfirePolicy)}
              className={inputClass}
            >
              {MISFIRE_POLICIES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex items-center gap-2">
            <MiniSwitch label="Enabled" checked={enabled} onChange={setEnabled} />
            <span className="text-[11px] text-text-tertiary">Enabled</span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={submitting}
          onClick={editing ? handleSave : handleCreate}
        >
          {editing ? <Check size={12} /> : <Plus size={12} />}
          {editing ? 'Save' : 'Create job'}
        </Button>
        {submitting && <span className="text-[11px] text-text-tertiary">Submitting…</span>}
      </div>
    </div>
  );
}
