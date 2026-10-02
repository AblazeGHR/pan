import { useEffect, useMemo, useState } from 'react';
import { Check, Plus, Search } from 'lucide-react';
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
import type {
  BackgroundProcessCreateInput,
  Job,
  JobCreateInput,
  JobKind,
  JobKindMeta,
  JobPatchInput,
  MisfirePolicy,
  ScheduledTaskCreateInput,
  SessionBroadcastCreateInput,
  SessionMessageCreateInput,
  SessionMessageSchedule,
} from '@/types/jobs';
import { scheduleEntries } from '@/types/jobs';
import { fetchQqChannels, fetchQqContacts } from '@/services/api';
import type { QqChannelInfo, QqContact } from '@/types';

export type JobFormMode = 'create' | 'edit';
type TemplateId = 'scheduled' | 'scheduled_qq' | 'resume_legal_running' | 'custom';
type MessageScheduleKind = SessionMessageSchedule['type'];

interface QqJobContact {
  contact: QqContact;
  botUin: string;
  botName: string;
  connected: boolean;
}

interface QqJobTarget {
  targetType: 'user' | 'group';
  targetId: string;
  botUin: string;
  botName: string;
  peerName: string;
}

const TEMPLATES: { id: TemplateId; label: string; hint: string }[] = [
  { id: 'scheduled', label: '创建定时任务', hint: '按计划执行 Session action 或 shell 命令' },
  { id: 'scheduled_qq', label: 'Scheduled QQ message', hint: 'Send text to a selected QQ contact on a schedule' },
  { id: 'resume_legal_running', label: '唤醒所有合法 running 的 Session', hint: '每次触发时重新筛选合法状态为 running 且没有活 Worker 的 Session，并发送“继续”' },
  { id: 'custom', label: '自定义', hint: '选择可创建 Job 类型与适用字段' },
];

const CREATABLE_KINDS: { kind: Exclude<JobKind, 'main-lifecycle'>; label: string; hint: string }[] = [
  { kind: 'scheduled-task', label: '定时任务', hint: 'assign、send_session、send_qq 或计划 shell 命令' },
  { kind: 'session-message', label: '定时消息', hint: '按计划向一个 Session 发送文本' },
  { kind: 'session-broadcast', label: '群发消息', hint: '按计划向多个 Session 发送文本' },
  { kind: 'background-process', label: '后台进程', hint: '立即运行 argv 命令，不经 shell' },
];

const MISFIRE_POLICIES: { value: MisfirePolicy; label: string }[] = [
  { value: 'fire_now', label: 'Fire now (catch up once)' },
  { value: 'skip', label: 'Skip (do not catch up)' },
];

const WEEKDAYS = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
];

function localInputAfterHour(): string {
  const d = new Date(Date.now() + 3600_000);
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return String(d.getFullYear()) + '-' + pad(d.getMonth() + 1) + '-'
    + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function localInputFromIso(value?: string): string {
  if (!value) return localInputAfterHour();
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return localInputAfterHour();
  const pad = (n: number) => String(n).padStart(2, '0');
  return String(d.getFullYear()) + '-' + pad(d.getMonth() + 1) + '-'
    + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function normalizeMessageSchedule(job?: Job): SessionMessageSchedule {
  const raw = job?.schedule as Partial<SessionMessageSchedule> | null | undefined;
  if (raw && typeof raw === 'object' && ['once', 'interval', 'weekly'].includes(String(raw.type))) {
    return raw as SessionMessageSchedule;
  }
  return { type: 'once', delaySeconds: 3600 };
}

/**
 * Create all user-creatable Job kinds. Source identity is assigned by the
 * server. Edit controls match each kind's PATCH support; process argv/cwd stay
 * immutable after launch.
 */
export function NewJobForm({
  mode = 'create',
  initialJob,
  submitting = false,
  kindMetas = [],
  onCreate,
  onSave,
}: {
  mode?: JobFormMode;
  initialJob?: Job;
  submitting?: boolean;
  kindMetas?: JobKindMeta[];
  onCreate?: (input: JobCreateInput) => void;
  onSave?: (patch: JobPatchInput) => void;
}) {
  const editing = mode === 'edit' && !!initialJob;
  const sessions = useSessionStore((s) => s.sessions);
  const [template, setTemplate] = useState<TemplateId>('scheduled');
  const [kind, setKind] = useState<JobKind>(initialJob?.kind ?? 'scheduled-task');
  const initialAction = initialJob?.action;
  const [actionApi, setActionApi] = useState<'assign' | 'send_session' | 'send_qq' | 'shell' | 'resume_legal_running'>(
    initialAction?.api ?? 'assign',
  );

  const [name, setName] = useState(initialJob?.name ?? '');
  const [description, setDescription] = useState(initialJob?.description ?? '');
  const [targetSessionId, setTargetSessionId] = useState(initialJob?.target.sessionId ?? '');
  const [targetSessionIds, setTargetSessionIds] = useState<string[]>(
    initialJob?.target.sessionIds
      ? [...initialJob.target.sessionIds]
      : initialJob?.target.sessionId ? [initialJob.target.sessionId] : [],
  );
  const [text, setText] = useState(initialJob?.text ?? '');
  const initialQqArgs = initialAction?.api === 'send_qq' ? initialAction.args : null;
  const [qqTarget, setQqTarget] = useState<QqJobTarget | null>(() => initialQqArgs ? ({
    targetType: initialQqArgs.targetType,
    targetId: initialQqArgs.targetId,
    botUin: initialQqArgs.botUin ?? '',
    botName: initialQqArgs.botUin ? `Bot ${initialQqArgs.botUin}` : 'Default channel',
    peerName: '',
  }) : null);
  const [qqContacts, setQqContacts] = useState<QqJobContact[]>([]);
  const [qqLoading, setQqLoading] = useState(false);
  const [qqSearch, setQqSearch] = useState('');
  const [qqLoadError, setQqLoadError] = useState('');
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
  const [paused, setPaused] = useState(initialJob?.paused ?? false);

  const shellArgs = initialAction?.api === 'shell' ? initialAction.args : null;
  const [shellCommand, setShellCommand] = useState(shellArgs?.command ?? initialJob?.shellCommand ?? '');
  const [cwd, setCwd] = useState(shellArgs?.cwd ?? initialJob?.cwd ?? '');
  const [executable, setExecutable] = useState(initialJob?.argv?.[0] ?? '');
  const [argumentsList, setArgumentsList] = useState<string[]>(
    initialJob?.argv?.slice(1) ?? [],
  );
  const [label, setLabel] = useState(initialJob?.label ?? '');

  const savedMessageSchedule = normalizeMessageSchedule(initialJob);
  const [messageScheduleKind, setMessageScheduleKind] = useState<MessageScheduleKind>(
    savedMessageSchedule.type,
  );
  const [onceMode, setOnceMode] = useState<'at' | 'delay'>(
    savedMessageSchedule.delaySeconds != null ? 'delay' : 'at',
  );
  const [onceAt, setOnceAt] = useState(localInputFromIso(savedMessageSchedule.at));
  const [delaySeconds, setDelaySeconds] = useState(
    String(savedMessageSchedule.delaySeconds ?? 3600),
  );
  const [intervalSeconds, setIntervalSeconds] = useState(
    String(savedMessageSchedule.intervalSeconds ?? 3600),
  );
  const [weekday, setWeekday] = useState(savedMessageSchedule.weekday ?? 0);
  const [messageTime, setMessageTime] = useState(savedMessageSchedule.time ?? '09:00');
  const [timezone, setTimezone] = useState(savedMessageSchedule.timezone ?? 'Asia/Shanghai');

  const [nameError, setNameError] = useState('');
  const [textError, setTextError] = useState('');
  const [targetError, setTargetError] = useState('');
  const [commandError, setCommandError] = useState('');
  const [cwdError, setCwdError] = useState('');
  const [formError, setFormError] = useState('');

  useEffect(() => {
    if (kind !== 'scheduled-task' || actionApi !== 'send_qq') return undefined;
    let cancelled = false;
    setQqLoading(true);
    setQqLoadError('');
    setQqSearch('');
    const load = async () => {
      let channels: QqChannelInfo[] = [];
      let channelsError = '';
      try {
        channels = await fetchQqChannels();
      } catch (error) {
        channelsError = error instanceof Error ? error.message : 'Failed to load QQ channels';
      }
      if (cancelled) return;
      const targets = channels.length > 0
        ? channels
        : [{ name: '', bot_uin: '', connected: false }];
      const perBot = await Promise.all(targets.map(async (bot) => {
        try {
          const contacts = await fetchQqContacts(bot.bot_uin || undefined);
          return contacts.map((contact): QqJobContact => ({
            contact,
            botUin: bot.bot_uin,
            botName: bot.name,
            connected: bot.connected,
          }));
        } catch {
          return [];
        }
      }));
      if (cancelled) return;
      const valid = perBot.flat().filter((item) =>
        /^[1-9][0-9]{4,19}$/.test(item.contact.peerUin)
        && (item.contact.chatType === 1 || item.contact.chatType === 2));
      setQqContacts(valid);
      setQqLoadError(valid.length === 0
        ? channelsError || 'No valid QQ contacts are available for the configured bots.'
        : '');
      setQqLoading(false);
    };
    void load();
    return () => { cancelled = true; };
  }, [kind, actionApi]);

  const sessionOptions = useMemo(
    () => sessions.filter((s) => !s.id.startsWith('__pending_')),
    [sessions],
  );
  const filteredQqContacts = useMemo(() => {
    const query = qqSearch.trim().toLowerCase();
    if (!query) return qqContacts;
    return qqContacts.filter(({ contact }) =>
      (contact.peerName || '').toLowerCase().includes(query)
      || contact.peerUin.includes(query));
  }, [qqContacts, qqSearch]);
  const selectedQqContact = qqTarget
    ? qqContacts.find((item) => item.contact.peerUin === qqTarget.targetId
      && (item.contact.chatType === 2 ? 'group' : 'user') === qqTarget.targetType
      && item.botUin === qqTarget.botUin)
    : undefined;
  const createableKindMetas = kindMetas.filter((meta) => meta.creatable);
  const kindOptions = createableKindMetas.length > 0
    ? CREATABLE_KINDS.filter((option) =>
      createableKindMetas.some((meta) => meta.kind === option.kind))
    : CREATABLE_KINDS;
  const currentKindMeta = kindMetas.find((meta) => meta.kind === kind);

  const parseMaxRuns = (): number | null | undefined => {
    const raw = maxRuns.trim();
    if (raw === '') return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : undefined;
  };

  const buildMessageSchedule = (): SessionMessageSchedule | null => {
    if (messageScheduleKind === 'once') {
      if (onceMode === 'delay') {
        const delay = Number(delaySeconds);
        return Number.isFinite(delay) && delay > 0
          ? { type: 'once', delaySeconds: delay }
          : null;
      }
      const instant = new Date(onceAt);
      return onceAt && !Number.isNaN(instant.getTime())
        ? { type: 'once', at: instant.toISOString() }
        : null;
    }
    if (messageScheduleKind === 'interval') {
      const interval = Number(intervalSeconds);
      return Number.isFinite(interval) && interval > 0
        ? { type: 'interval', intervalSeconds: interval }
        : null;
    }
    if (!messageTime.trim() || !timezone.trim()) return null;
    return { type: 'weekly', weekday, time: messageTime, timezone: timezone.trim() };
  };

  const handleCreate = () => {
    setFormError('');
    const common = {
      name: name.trim() || undefined,
      description: description.trim(),
    };
    if (kind === 'scheduled-task') {
      const max = parseMaxRuns();
      if (max === undefined) {
        setFormError('Max runs must be a positive whole number or empty.');
        return;
      }
      if (actionApi === 'shell') {
        if (!shellCommand.trim()) {
          setCommandError('Shell command is required');
          return;
        }
        if (!cwd.trim()) {
          setCwdError('Working directory is required');
          return;
        }
        const input: ScheduledTaskCreateInput = {
          ...common,
          kind,
          target: { sessionId: targetSessionId.trim() || null },
          action: { api: 'shell', args: { command: shellCommand, cwd: cwd.trim() } },
          schedule: entries.map(buildScheduleSpec),
          misfirePolicy,
          maxRuns: max,
          enabled,
          paused,
        };
        onCreate?.(input);
        return;
      }
      if (actionApi === 'resume_legal_running') {
        const input: ScheduledTaskCreateInput = {
          ...common,
          kind,
          target: { sessionId: null },
          action: { api: 'resume_legal_running' },
          text: '继续',
          schedule: entries.map(buildScheduleSpec),
          misfirePolicy,
          maxRuns: max,
          enabled,
          paused,
        };
        onCreate?.(input);
        return;
      }
      if (actionApi === 'send_qq') {
        if (!qqTarget) {
          setTargetError('Choose a QQ contact');
          return;
        }
        if (!text.trim()) {
          setTextError('Message text is required');
          return;
        }
        const input: ScheduledTaskCreateInput = {
          ...common,
          kind,
          target: { sessionId: null },
          action: {
            api: 'send_qq',
            args: {
              targetType: qqTarget.targetType,
              targetId: qqTarget.targetId,
              ...(qqTarget.botUin ? { botUin: qqTarget.botUin } : {}),
            },
          },
          text: text.trim(),
          schedule: entries.map(buildScheduleSpec),
          misfirePolicy,
          maxRuns: max,
          enabled,
          paused,
        };
        onCreate?.(input);
        return;
      }
      const sid = targetSessionId.trim();
      if (!sid) {
        setTargetError('Target session is required');
        return;
      }
      if (!text.trim()) {
        setTextError('Text is required');
        return;
      }
      const input: ScheduledTaskCreateInput = {
        ...common,
        kind,
        target: { sessionId: sid },
        action: { api: actionApi },
        text: text.trim(),
        schedule: entries.map(buildScheduleSpec),
        misfirePolicy,
        maxRuns: max,
        enabled,
        paused,
      };
      onCreate?.(input);
      return;
    }

    if (kind === 'session-message' || kind === 'session-broadcast') {
      const schedule = buildMessageSchedule();
      if (!schedule) {
        setFormError('Complete the selected message schedule fields.');
        return;
      }
      if (!text.trim()) {
        setTextError('Message text is required');
        return;
      }
      if (kind === 'session-message') {
        if (!targetSessionId.trim()) {
          setTargetError('Target session is required');
          return;
        }
        const input: SessionMessageCreateInput = {
          ...common,
          kind,
          target: { sessionId: targetSessionId.trim() },
          text: text.trim(),
          schedule,
        };
        onCreate?.(input);
        return;
      }
      if (targetSessionIds.length === 0) {
        setTargetError('Select at least one target session');
        return;
      }
      const input: SessionBroadcastCreateInput = {
        ...common,
        kind,
        target: { sessionId: targetSessionIds[0], sessionIds: targetSessionIds },
        text: text.trim(),
        schedule,
      };
      onCreate?.(input);
      return;
    }

    if (kind === 'background-process') {
      const sid = targetSessionId.trim();
      if (!sid) {
        setTargetError('Notification target session is required');
        return;
      }
      if (!executable.trim()) {
        setCommandError('Executable is required');
        return;
      }
      if (!cwd.trim()) {
        setCwdError('Working directory is required');
        return;
      }
      if (argumentsList.some((argument) => !argument.trim())) {
        setFormError('Remove empty argument rows or fill them in.');
        return;
      }
      const input: BackgroundProcessCreateInput = {
        ...common,
        kind,
        target: { sessionId: sid },
        argv: [executable, ...argumentsList],
        cwd: cwd.trim(),
        label: label.trim() || undefined,
      };
      onCreate?.(input);
      return;
    }
    setFormError('This Job kind cannot be created from this form.');
  };

  const handleSave = () => {
    if (!initialJob) return;
    setFormError('');
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError('Name is required');
      return;
    }
    const patch: JobPatchInput = {
      name: trimmedName,
      description: description.trim(),
    };
    if (initialJob.kind === 'scheduled-task') {
      const max = parseMaxRuns();
      if (max === undefined) {
        setFormError('Max runs must be a positive whole number or empty.');
        return;
      }
      if (actionApi === 'send_qq') {
        if (!qqTarget) {
          setTargetError('Choose a QQ contact');
          return;
        }
        patch.action = {
          api: 'send_qq',
          args: {
            targetType: qqTarget.targetType,
            targetId: qqTarget.targetId,
            ...(qqTarget.botUin ? { botUin: qqTarget.botUin } : {}),
          },
        };
      } else {
        patch.action = actionApi === 'shell'
          ? { api: 'shell', args: { command: shellCommand, cwd: cwd.trim() } }
          : { api: actionApi };
      }
      if (actionApi === 'resume_legal_running') {
        patch.text = '继续';
        patch.target = { sessionId: null };
      } else if (actionApi === 'send_qq') {
        if (!text.trim()) {
          setTextError('Message text is required');
          return;
        }
        patch.text = text.trim();
        patch.target = { sessionId: null };
      } else if (actionApi === 'shell') {
        if (!shellCommand.trim() || !cwd.trim()) {
          setFormError('Shell command and working directory are required.');
          return;
        }
      } else {
        if (!text.trim()) {
          setTextError('Text is required');
          return;
        }
        patch.text = text.trim();
      }
      if (actionApi !== 'resume_legal_running' && actionApi !== 'send_qq') {
        patch.target = { sessionId: targetSessionId.trim() || null };
      }
      patch.schedule = entries.map(buildScheduleSpec);
      patch.maxRuns = max;
      patch.misfirePolicy = misfirePolicy;
      patch.enabled = enabled;
      patch.paused = paused;
    } else if (initialJob.kind === 'session-message'
      || initialJob.kind === 'session-broadcast') {
      const schedule = buildMessageSchedule();
      if (!schedule) {
        setFormError('Complete the selected message schedule fields.');
        return;
      }
      if (!text.trim()) {
        setTextError('Message text is required');
        return;
      }
      patch.text = text.trim();
      patch.schedule = schedule;
      if (initialJob.kind === 'session-message') {
        if (!targetSessionId.trim()) {
          setTargetError('Target session is required');
          return;
        }
        patch.target = { sessionId: targetSessionId.trim() };
      } else {
        if (targetSessionIds.length === 0) {
          setTargetError('Select at least one target session');
          return;
        }
        patch.target = {
          sessionId: targetSessionIds[0],
          sessionIds: targetSessionIds,
        };
      }
    } else if (initialJob.kind === 'background-process') {
      patch.target = { sessionId: targetSessionId.trim() || null };
    } else if (initialJob.kind === 'main-lifecycle') {
      // Lifecycle Jobs are system-owned: the API permits only metadata edits.
    } else {
      setFormError('This Job kind has no editable fields in this form.');
      return;
    }
    onSave?.(patch);
  };

  const kindTargetSelect = (
    <Field label={kind === 'scheduled-task' && actionApi === 'shell'
      ? 'Notification target (optional)'
      : kind === 'background-process' ? 'Notification target *' : 'Target session *'}>
      <select
        aria-label="Target session"
        value={targetSessionId}
        onChange={(e) => {
          setTargetSessionId(e.target.value);
          if (targetError) setTargetError('');
        }}
        className={inputClass}
      >
        <option value="">
          {kind === 'scheduled-task' && actionApi === 'shell'
            ? 'No Session notification'
            : editing ? 'No target' : 'Select a Session…'}
        </option>
        {sessionOptions.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name || 'Untitled'} · {s.id}
          </option>
        ))}
      </select>
      {targetError && <span className="text-[11px] text-danger">{targetError}</span>}
    </Field>
  );

  const qqTargetPicker = (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] text-text-tertiary">QQ contact *</span>
      <label className="relative block">
        <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary" />
        <input
          aria-label="Search QQ contacts"
          value={qqSearch}
          onChange={(event) => setQqSearch(event.target.value)}
          placeholder="Search recent, friends, and groups"
          className={inputClass + ' pl-7'}
        />
      </label>
      {qqTarget && (
        <div className="rounded border border-accent/40 bg-accent/5 px-2.5 py-2 text-[11px] text-text-secondary">
          Selected: <span className="font-medium text-text-primary">
            {selectedQqContact?.contact.peerName || qqTarget.peerName || qqTarget.targetId}
          </span>
          {' · '}{qqTarget.targetType === 'group' ? 'Group' : 'User'} {qqTarget.targetId}
          {' · '}{qqTarget.botUin ? `Bot ${qqTarget.botUin}` : 'Default bot'}
        </div>
      )}
      <div className="max-h-52 overflow-y-auto rounded border border-border-default bg-bg-tertiary p-1.5">
        {qqLoading ? (
          <div className="py-4 text-center text-xs text-text-tertiary">Loading QQ contacts…</div>
        ) : filteredQqContacts.length === 0 ? (
          <div className="py-4 text-center text-xs text-text-tertiary">
            {qqLoadError || (qqSearch ? 'No contacts match this search.' : 'No valid QQ contacts available.')}
          </div>
        ) : filteredQqContacts.map((item) => {
          const targetType = item.contact.chatType === 2 ? 'group' : 'user';
          const selected = qqTarget?.targetType === targetType
            && qqTarget.targetId === item.contact.peerUin
            && qqTarget.botUin === item.botUin;
          return (
            <button
              key={`${item.botUin || item.botName}: ${targetType}:${item.contact.peerUin}`}
              type="button"
              aria-pressed={selected}
              onClick={() => {
                setQqTarget({
                  targetType,
                  targetId: item.contact.peerUin,
                  botUin: item.botUin,
                  botName: item.botName,
                  peerName: item.contact.peerName,
                });
                if (targetError) setTargetError('');
              }}
              className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${selected
                ? 'bg-accent/15 text-accent'
                : 'text-text-primary hover:bg-bg-hover'}`}
            >
              <span className="min-w-0 flex-1 truncate">
                {item.contact.peerName || item.contact.peerUin}
                <span className="ml-1 text-[10px] text-text-tertiary">{item.contact.peerUin}</span>
              </span>
              <span className="shrink-0 rounded border border-border-default bg-bg-primary px-1 py-px text-[10px] text-text-secondary">
                {item.contact.chatType === 2 ? 'group' : 'user'}
              </span>
              <span className="shrink-0 rounded border border-border-default bg-bg-primary px-1 py-px text-[10px] text-text-secondary">
                {item.botUin || item.botName || 'default'}
              </span>
              <span className={`shrink-0 text-[10px] ${item.connected ? 'text-success' : 'text-text-tertiary'}`}>
                {item.connected ? 'online' : 'offline'}
              </span>
            </button>
          );
        })}
      </div>
      {targetError && <span className="text-[11px] text-danger">{targetError}</span>}
    </div>
  );

  const messageTargetSelect = (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-text-tertiary">Target Sessions *</span>
      <div className="max-h-36 overflow-y-auto rounded border border-border-default bg-bg-tertiary p-2">
        {sessionOptions.map((s) => {
          const checked = targetSessionIds.includes(s.id);
          return (
            <label key={s.id} className="flex items-center gap-2 py-0.5 text-xs text-text-primary">
              <input
                type="checkbox"
                checked={checked}
                onChange={() => setTargetSessionIds((current) => checked
                  ? current.filter((id) => id !== s.id)
                  : [...current, s.id])}
                className="accent-accent"
              />
              <span className="truncate">{s.name || 'Untitled'} · {s.id}</span>
            </label>
          );
        })}
      </div>
      {targetError && <span className="text-[11px] text-danger">{targetError}</span>}
    </div>
  );

  const messageScheduleEditor = (
    <div className="flex flex-col gap-2 rounded border border-border-default bg-bg-primary p-2.5">
      <Field label="Schedule type">
        <select
          aria-label="Message schedule type"
          value={messageScheduleKind}
          onChange={(e) => setMessageScheduleKind(e.target.value as MessageScheduleKind)}
          className={inputClass}
        >
          <option value="once">Once</option>
          <option value="interval">Interval</option>
          <option value="weekly">Weekly</option>
        </select>
      </Field>
      {messageScheduleKind === 'once' && (
        <>
          <Field label="Run once">
            <select
              aria-label="Once schedule mode"
              value={onceMode}
              onChange={(e) => setOnceMode(e.target.value as 'at' | 'delay')}
              className={inputClass}
            >
              <option value="at">At a local date and time</option>
              <option value="delay">After a delay</option>
            </select>
          </Field>
          {onceMode === 'at' ? (
            <Field label="Run at">
              <input
                aria-label="Run at"
                type="datetime-local"
                value={onceAt}
                onChange={(e) => setOnceAt(e.target.value)}
                className={inputClass}
              />
            </Field>
          ) : (
            <Field label="Delay (seconds)">
              <input
                aria-label="Delay seconds"
                type="number"
                min={1}
                value={delaySeconds}
                onChange={(e) => setDelaySeconds(e.target.value)}
                className={inputClass}
              />
            </Field>
          )}
        </>
      )}
      {messageScheduleKind === 'interval' && (
        <Field label="Interval (seconds)">
          <input
            aria-label="Message interval seconds"
            type="number"
            min={1}
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(e.target.value)}
            className={inputClass}
          />
        </Field>
      )}
      {messageScheduleKind === 'weekly' && (
        <>
          <Field label="Weekday">
            <select
              aria-label="Weekday"
              value={weekday}
              onChange={(e) => setWeekday(Number(e.target.value))}
              className={inputClass}
            >
              {WEEKDAYS.map((day) => (
                <option key={day.value} value={day.value}>{day.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Local time">
            <input
              aria-label="Weekly time"
              type="time"
              value={messageTime}
              onChange={(e) => setMessageTime(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Timezone (IANA)">
            <input
              aria-label="Message timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className={inputClass}
            />
          </Field>
        </>
      )}
    </div>
  );

  const sourceNote = (
    <div className="rounded border border-border-default bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
      Source identity is assigned by the server and cannot be set here.
    </div>
  );

  const templateSelector = !editing && (
    <div className="grid grid-cols-2 gap-1.5 md:grid-cols-4">
      {TEMPLATES.map((item) => (
        <button
          key={item.id}
          type="button"
          aria-pressed={template === item.id}
          title={item.hint}
          onClick={() => {
            setTemplate(item.id);
            if (item.id === 'scheduled') {
              setKind('scheduled-task');
              setActionApi('assign');
            } else if (item.id === 'scheduled_qq') {
              setKind('scheduled-task');
              setActionApi('send_qq');
              setTargetSessionId('');
            } else if (item.id === 'resume_legal_running') {
              setKind('scheduled-task');
              setActionApi('resume_legal_running');
              setTargetSessionId('');
              setText('继续');
            }
          }}
          className={'flex-1 rounded border px-2.5 py-1.5 text-xs font-medium transition-colors '
            + (template === item.id
              ? 'border-accent/50 bg-accent/10 text-accent'
              : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary')}
        >
          {item.label}
        </button>
      ))}
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {templateSelector}

      {editing ? (
        <Field label="Job kind (fixed)">
          <input aria-label="Job kind" value={initialJob?.kind ?? ''} readOnly className={inputClass} />
        </Field>
      ) : template === 'custom' ? (
        <Field label="Createable Job kind">
          <select
            aria-label="Createable Job kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as JobKind)}
            className={inputClass}
          >
            {kindOptions.map((option) => {
              const meta = kindMetas.find((item) => item.kind === option.kind);
              return (
                <option key={option.kind} value={option.kind}>
                  {meta?.label ?? option.label}
                </option>
              );
            })}
          </select>
          <span className="text-[11px] text-text-tertiary">
            {kindOptions.find((option) => option.kind === kind)?.hint}
            {currentKindMeta
              ? ' · ' + (currentKindMeta.createMode === 'immediate'
                ? 'Runs immediately' : 'Uses a schedule')
              : ''}
            . System-owned main-lifecycle Jobs are not user-createable.
          </span>
          {currentKindMeta?.createFields?.length ? (
            <span className="text-[10px] text-text-tertiary">
              Writable fields: {currentKindMeta.createFields.join(', ')}
            </span>
          ) : null}
        </Field>
      ) : null}

      <Field label={editing ? 'Name *' : 'Name'}>
        <input
          aria-label="Name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (nameError) setNameError('');
          }}
          placeholder={editing ? 'Required' : 'Blank uses server-generated job-N'}
          className={inputClass}
        />
        {nameError && <span className="text-[11px] text-danger">{nameError}</span>}
      </Field>

      <Field label="Description">
        <input
          aria-label="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional"
          className={inputClass}
        />
      </Field>

      {sourceNote}

      {kind === 'scheduled-task' && (
        <>
          <Field label="Execution mode">
            <select
              aria-label="Execution mode"
              value={actionApi}
              onChange={(e) => setActionApi(e.target.value as typeof actionApi)}
              className={inputClass}
            >
              <option value="assign">Assign task to Session</option>
              <option value="send_session">Send Session message</option>
              <option value="send_qq">Scheduled QQ message</option>
              <option value="resume_legal_running">唤醒所有合法 running 的 Session</option>
              <option value="shell">Run shell command on Pan server</option>
            </select>
          </Field>
          {actionApi === 'send_qq' ? (
            <>
              {qqTargetPicker}
              <Field label="QQ message text *">
                <textarea
                  aria-label="QQ message text"
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    if (textError) setTextError('');
                  }}
                  rows={3}
                  placeholder="Text sent to the selected QQ contact"
                  className={inputClass + ' resize-y'}
                />
                {textError && <span className="text-[11px] text-danger">{textError}</span>}
              </Field>
            </>
          ) : actionApi === 'resume_legal_running' ? (
            <div className="rounded border border-border-default bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-secondary">
              每次 Job 触发时，服务端都会重新扫描持久合法状态为 running 且当前没有活 Worker 的 Session，
              再通过正常消息投递发送正文“继续”。创建时不会固定 Session ID，也不会运行 shell 命令。
            </div>
          ) : actionApi === 'shell' ? (
            <>
              <Field label="Shell command *">
                <textarea
                  aria-label="Shell command"
                  value={shellCommand}
                  onChange={(e) => {
                    setShellCommand(e.target.value);
                    if (commandError) setCommandError('');
                  }}
                  rows={3}
                  placeholder="Command executed by the Pan server shell at each fire"
                  className={inputClass + ' resize-y font-mono'}
                />
                {commandError && <span className="text-[11px] text-danger">{commandError}</span>}
              </Field>
              <Field label="Working directory *">
                <input
                  aria-label="Working directory"
                  value={cwd}
                  onChange={(e) => {
                    setCwd(e.target.value);
                    if (cwdError) setCwdError('');
                  }}
                  placeholder="Absolute path inside the Pan project directory"
                  className={inputClass}
                />
                {cwdError && <span className="text-[11px] text-danger">{cwdError}</span>}
              </Field>
              {kindTargetSelect}
              <div className="rounded border border-warning/50 bg-warning/10 px-2.5 py-2 text-[11px] text-warning">
                This command runs on the Pan server under its service account. It is never sent as Session text.
                Output, exit code, and failure state appear in the Job details and Runs.
              </div>
            </>
          ) : (
            <>
              {kindTargetSelect}
              <Field label={actionApi === 'assign' ? 'Task text *' : 'Session message *'}>
                <textarea
                  aria-label="Task text"
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    if (textError) setTextError('');
                  }}
                  rows={3}
                  placeholder={actionApi === 'assign'
                    ? 'Task text sent to the selected Session'
                    : 'Message text sent to the selected Session'}
                  className={inputClass + ' resize-y'}
                />
                {textError && <span className="text-[11px] text-danger">{textError}</span>}
              </Field>
            </>
          )}

          <ScheduleListEditor entries={entries} onChange={setEntries} />

          <div className="flex flex-col gap-2 rounded border border-border-default bg-bg-primary p-2.5">
            <Field label="Max runs (blank = unlimited)">
              <input
                aria-label="Max runs"
                type="number"
                min={1}
                step={1}
                value={maxRuns}
                onChange={(e) => setMaxRuns(e.target.value)}
                inputMode="numeric"
                className={inputClass}
              />
            </Field>
            <Field label="Default missed-fire policy">
              <select
                aria-label="Default missed-fire policy"
                value={misfirePolicy}
                onChange={(e) => {
                  const policy = e.target.value as MisfirePolicy;
                  setMisfirePolicy(policy);
                  setEntries((current) => current.map((entry) => ({
                    ...entry,
                    misfirePolicy: policy,
                  })));
                }}
                className={inputClass}
              >
                {MISFIRE_POLICIES.map((policy) => (
                  <option key={policy.value} value={policy.value}>{policy.label}</option>
                ))}
              </select>
            </Field>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-[11px] text-text-secondary">
                <MiniSwitch label="Job enabled" checked={enabled} onChange={setEnabled} />
                Enabled
              </label>
              <label className="flex items-center gap-2 text-[11px] text-text-secondary">
                <MiniSwitch label="Job paused" checked={paused} onChange={setPaused} />
                Paused
              </label>
            </div>
          </div>
        </>
      )}

      {(kind === 'session-message' || kind === 'session-broadcast') && (
        <>
          {kind === 'session-message' ? kindTargetSelect : messageTargetSelect}
          <Field label="Message text *">
            <textarea
              aria-label="Message text"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                if (textError) setTextError('');
              }}
              rows={3}
              className={inputClass + ' resize-y'}
            />
            {textError && <span className="text-[11px] text-danger">{textError}</span>}
          </Field>
          {messageScheduleEditor}
          <div className="text-[11px] text-text-tertiary">
            Source is server-assigned. These message Jobs do not expose maxRuns, pause, or per-entry schedule controls.
          </div>
        </>
      )}

      {kind === 'background-process' && (
        <>
          {editing ? (
            <>
              <Field label="Executable and arguments (immutable after launch)">
                <textarea
                  aria-label="Process argv"
                  value={JSON.stringify(initialJob?.argv ?? [])}
                  readOnly
                  rows={2}
                  className={inputClass + ' resize-y font-mono'}
                />
              </Field>
              <Field label="Working directory (immutable after launch)">
                <input aria-label="Process working directory" value={initialJob?.cwd ?? ''} readOnly className={inputClass} />
              </Field>
              {kindTargetSelect}
              <div className="text-[11px] text-text-tertiary">
                Edit supports name, description, and notification target. Recreate the Job to change argv or cwd.
              </div>
            </>
          ) : (
            <>
              {kindTargetSelect}
              <Field label="Executable *">
                <input
                  aria-label="Executable"
                  value={executable}
                  onChange={(e) => {
                    setExecutable(e.target.value);
                    if (commandError) setCommandError('');
                  }}
                  placeholder="Executable path or command name"
                  className={inputClass}
                />
                {commandError && <span className="text-[11px] text-danger">{commandError}</span>}
              </Field>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-text-tertiary">Arguments</span>
                {argumentsList.map((argument, index) => (
                  <div key={index} className="flex gap-1">
                    <input
                      aria-label={'Argument ' + (index + 1)}
                      value={argument}
                      onChange={(e) => setArgumentsList((current) =>
                        current.map((item, itemIndex) => itemIndex === index ? e.target.value : item))}
                      className={inputClass}
                    />
                    <button
                      type="button"
                      aria-label={'Remove argument ' + (index + 1)}
                      onClick={() => setArgumentsList((current) =>
                        current.filter((_, itemIndex) => itemIndex !== index))}
                      className="rounded border border-border-default px-2 text-xs text-text-secondary"
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setArgumentsList((current) => [...current, ''])}
                  className="self-start rounded border border-border-default bg-bg-tertiary px-2 py-1 text-[11px] text-text-secondary"
                >
                  Add argument
                </button>
              </div>
              <Field label="Working directory *">
                <input
                  aria-label="Working directory"
                  value={cwd}
                  onChange={(e) => {
                    setCwd(e.target.value);
                    if (cwdError) setCwdError('');
                  }}
                  placeholder="Absolute path inside the Pan project directory"
                  className={inputClass}
                />
                {cwdError && <span className="text-[11px] text-danger">{cwdError}</span>}
              </Field>
              <Field label="Label">
                <input aria-label="Process label" value={label}
                  onChange={(e) => setLabel(e.target.value)} className={inputClass} />
              </Field>
              <div className="rounded border border-border-default bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
                Runs immediately through argv without shell interpretation. A Session is required for terminal notifications.
              </div>
            </>
          )}
        </>
      )}

      {kind === 'main-lifecycle' && editing && (
        <div className="rounded border border-border-default bg-bg-tertiary px-2.5 py-2 text-[11px] text-text-tertiary">
          System-managed lifecycle Jobs can only edit name and description. Their source, target,
          action, schedule, and process controls are internal.
        </div>
      )}

      {formError && <span className="text-[11px] text-danger">{formError}</span>}
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
