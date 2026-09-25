/**
 * Job 统一数据模型（PLAN_JOB_UNIFICATION §1）的前端 mock 层。
 *
 * 本轮（增量 1）只为 JobsView 提供可渲染的种子数据与 TS 类型，不接后端。
 * 字段以 PLAN §1 的 job schema 为准；`kind` 清单见 §2。
 */

export type JobKind =
  | 'background-process'
  | 'session-message'
  | 'session-broadcast'
  | 'main-lifecycle'
  | 'scheduled_task';

export type JobStatus =
  | 'pending'
  | 'scheduled'
  | 'starting'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** 创建者身份；fire 时动作以该身份执行。 */
export interface JobSource {
  type: 'agent' | 'user' | 'system' | 'plugin';
  sessionId?: string;
  pluginName?: string;
}

/** 输出/通知对象；与 source 正交，可为空（target 缺失）。 */
export interface JobTarget {
  sessionId: string | null;
}

export type ScheduleEntryKind = 'once' | 'interval' | 'cron';
export type MisfirePolicy = 'fire_now' | 'skip';

/** schedule 列表的单条 entry（PLAN §1：列表 + 模板）。 */
export interface ScheduleEntry {
  id: string;
  kind: ScheduleEntryKind;
  at?: string | null;
  intervalSec?: number | null;
  cron?: string | null;
  timezone?: string | null;
  anchor?: string | null;
  misfirePolicy: MisfirePolicy;
  enabled: boolean;
  nextFireAt: string | null;
}

/** 对 Pan 内部接口的调用模板（首批：assign / send_session / spawn_process）。 */
export interface JobAction {
  api: string;
  args: Record<string, unknown>;
}

/** 终态便条投递状态；`undeliverable` 为 target 缺失时的积压态。 */
export type NotificationState = 'pending' | 'delivered' | 'undeliverable';

/** 便条级 mailbox 记录（滚动上限）。 */
export interface MailboxNote {
  type: string;
  jobId: string;
  status: string;
  exitCode?: number | null;
  logPath?: string | null;
  fireAt?: string | null;
  entryId?: string | null;
}

/** 最近一次动作返回（broadcast 时为 {status, results[], errors[]} 汇总）。 */
export interface JobDelivery {
  status: string;
  results?: unknown[];
  errors?: unknown[];
}

/** 单条运行历史（对应 runs.jsonl 的摘要行）。 */
export interface JobRun {
  runId: string;
  fireAt: string;
  entryId?: string | null;
  status: string;
  error?: string | null;
}

export interface Job {
  jobId: string;
  /** 人类可读名称；必填、不允许为空。缺省时由 defaultJobName 自动生成 job-N。 */
  name: string;
  /** 可选描述；允许为空字符串。 */
  description: string;
  kind: JobKind;
  status: JobStatus;
  source: JobSource;
  target: JobTarget;
  schedule: ScheduleEntry[];
  action: JobAction;
  notificationState: NotificationState;
  terminalEventId: string | null;
  mailbox: MailboxNote[];
  lastFireAt: string | null;
  runCount: number;
  lastError: string | null;
  maxRuns: number | null;
  paused: boolean;
  lastDelivery?: JobDelivery;
  /** 运行历史（runs.jsonl 摘要行），详情视图展示最近 N 条。 */
  runs?: JobRun[];
  /** 进程类 job 专有；非进程类无此字段。 */
  logPath?: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * 默认命名语义：name 不允许为空，创建时若用户不填，自动生成递增的 `job-N`。
 * `existingCount` 为当前已有 job 数量，返回下一个序号（existingCount + 1）。
 * 供后续创建表单复用。description 可空，无默认值。
 */
export function defaultJobName(existingCount: number): string {
  return `job-${existingCount + 1}`;
}

/** 5 条代表性种子数据，覆盖 PLAN §5 最低展示字段的各类形态。 */
export const mockJobs: Job[] = [
  {
    jobId: 'job_1a2b3c4d5e6f',
    name: '抓取脚本后台运行',
    description: '运行 scripts/crawl.py 抓取最新数据',
    kind: 'background-process',
    status: 'running',
    source: { type: 'agent', sessionId: 'sess_7f3a1b2c' },
    target: { sessionId: 'sess_7f3a1b2c' },
    schedule: [],
    action: { api: 'spawn_process', args: { command: 'python', script: 'scripts/crawl.py' } },
    notificationState: 'pending',
    terminalEventId: null,
    mailbox: [],
    lastFireAt: '2026-09-25T10:12:00',
    runCount: 1,
    lastError: null,
    maxRuns: null,
    paused: false,
    runs: [
      { runId: 'run_1a2b3c', fireAt: '2026-09-25T10:12:00', status: 'running' },
    ],
    logPath: 'data/jobs/job_1a2b3c4d5e6f.log',
    createdAt: '2026-09-25T10:12:00',
    updatedAt: '2026-09-25T10:12:01',
  },
  {
    jobId: 'job_2b3c4d5e6f7a',
    name: '每日复盘',
    description: '工作日 9:00 触发复盘任务',
    kind: 'scheduled_task',
    status: 'scheduled',
    source: { type: 'user' },
    target: { sessionId: 'sess_9c2d4e6f' },
    schedule: [
      {
        id: 'schx_1a2b3c',
        kind: 'cron',
        cron: '0 9 * * 1-5',
        timezone: 'Asia/Shanghai',
        misfirePolicy: 'fire_now',
        enabled: true,
        nextFireAt: '2026-09-26T09:00:00',
      },
      {
        id: 'schx_4d5e6f',
        kind: 'cron',
        cron: '0 18 * * 1-5',
        timezone: 'Asia/Shanghai',
        misfirePolicy: 'skip',
        enabled: false,
        nextFireAt: null,
      },
    ],
    action: { api: 'assign', args: { sessionId: 'sess_9c2d4e6f', text: '每日复盘' } },
    notificationState: 'pending',
    terminalEventId: null,
    mailbox: [],
    lastFireAt: '2026-09-24T09:00:00',
    runCount: 12,
    lastError: null,
    maxRuns: null,
    paused: false,
    runs: [
      { runId: 'run_2a2b3c', fireAt: '2026-09-24T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b3d', fireAt: '2026-09-23T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b3e', fireAt: '2026-09-22T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b3f', fireAt: '2026-09-19T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b40', fireAt: '2026-09-18T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b41', fireAt: '2026-09-17T09:00:00', entryId: 'schx_1a2b3c', status: 'error', error: 'worker busy' },
      { runId: 'run_2a2b42', fireAt: '2026-09-16T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
      { runId: 'run_2a2b43', fireAt: '2026-09-15T09:00:00', entryId: 'schx_1a2b3c', status: 'dispatched' },
    ],
    createdAt: '2026-09-10T08:00:00',
    updatedAt: '2026-09-24T09:00:02',
  },
  {
    jobId: 'job_3c4d5e6f7a8b',
    name: '广播通知',
    description: '',
    kind: 'session-broadcast',
    status: 'completed',
    source: { type: 'agent', sessionId: 'sess_1a2b3c4d' },
    target: { sessionId: 'sess_1a2b3c4d' },
    schedule: [],
    action: {
      api: 'send_session',
      args: { recipients: ['sess_5e6f7a8b', 'sess_9c0d1e2f', 'sess_3f4a5b6c'], text: '广播通知' },
    },
    notificationState: 'delivered',
    terminalEventId: 'evt_broadcast_01',
    mailbox: [],
    lastFireAt: '2026-09-25T09:30:00',
    runCount: 1,
    lastError: null,
    maxRuns: null,
    paused: false,
    lastDelivery: {
      status: 'partial',
      results: [
        { sessionId: 'sess_5e6f7a8b', ok: true },
        { sessionId: 'sess_9c0d1e2f', ok: true },
      ],
      errors: [{ sessionId: 'sess_3f4a5b6c', error: 'session not found' }],
    },
    runs: [
      { runId: 'run_3a2b3c', fireAt: '2026-09-25T09:30:00', status: 'partial' },
      { runId: 'run_3a2b3d', fireAt: '2026-09-24T09:30:00', status: 'dispatched' },
    ],
    createdAt: '2026-09-25T09:29:00',
    updatedAt: '2026-09-25T09:30:05',
  },
  {
    jobId: 'job_4d5e6f7a8b9c',
    name: 'job-6',
    description: '',
    kind: 'scheduled_task',
    status: 'scheduled',
    source: { type: 'user' },
    target: { sessionId: null },
    schedule: [
      {
        id: 'schx_7a8b9c',
        kind: 'cron',
        cron: '0 9 * * *',
        timezone: 'Asia/Shanghai',
        misfirePolicy: 'fire_now',
        enabled: true,
        nextFireAt: '2026-09-26T09:00:00',
      },
    ],
    action: { api: 'assign', args: { sessionId: null, text: '待定目标' } },
    notificationState: 'undeliverable',
    terminalEventId: null,
    mailbox: [
      {
        type: 'note',
        jobId: 'job_4d5e6f7a8b9c',
        status: 'completed',
        exitCode: 0,
        fireAt: '2026-09-24T09:00:00',
        entryId: 'schx_7a8b9c',
      },
      {
        type: 'note',
        jobId: 'job_4d5e6f7a8b9c',
        status: 'completed',
        exitCode: 0,
        fireAt: '2026-09-23T09:00:00',
        entryId: 'schx_7a8b9c',
      },
      {
        type: 'note',
        jobId: 'job_4d5e6f7a8b9c',
        status: 'completed',
        exitCode: 0,
        fireAt: '2026-09-22T09:00:00',
        entryId: 'schx_7a8b9c',
      },
    ],
    lastFireAt: '2026-09-24T09:00:00',
    runCount: 5,
    lastError: null,
    maxRuns: null,
    paused: false,
    runs: [
      { runId: 'run_4a2b3c', fireAt: '2026-09-24T09:00:00', entryId: 'schx_7a8b9c', status: 'completed' },
      { runId: 'run_4a2b3d', fireAt: '2026-09-23T09:00:00', entryId: 'schx_7a8b9c', status: 'completed' },
      { runId: 'run_4a2b3e', fireAt: '2026-09-22T09:00:00', entryId: 'schx_7a8b9c', status: 'completed' },
      { runId: 'run_4a2b3f', fireAt: '2026-09-21T09:00:00', entryId: 'schx_7a8b9c', status: 'completed' },
      { runId: 'run_4a2b40', fireAt: '2026-09-20T09:00:00', entryId: 'schx_7a8b9c', status: 'completed' },
    ],
    createdAt: '2026-09-20T08:00:00',
    updatedAt: '2026-09-24T09:00:02',
  },
  {
    jobId: 'job_5e6f7a8b9c0d',
    name: '任务完成通知',
    description: '完成后向目标会话发送通知',
    kind: 'session-message',
    status: 'completed',
    source: { type: 'system' },
    target: { sessionId: 'sess_2b3c4d5e' },
    schedule: [],
    action: { api: 'send_session', args: { sessionId: 'sess_2b3c4d5e', text: '任务完成通知' } },
    notificationState: 'delivered',
    terminalEventId: 'evt_msg_01',
    mailbox: [],
    lastFireAt: '2026-09-25T08:15:00',
    runCount: 1,
    lastError: null,
    maxRuns: null,
    paused: false,
    lastDelivery: {
      status: 'delivered',
      results: [{ sessionId: 'sess_2b3c4d5e', ok: true }],
      errors: [],
    },
    runs: [
      { runId: 'run_5a2b3c', fireAt: '2026-09-25T08:15:00', status: 'delivered' },
    ],
    createdAt: '2026-09-25T08:15:00',
    updatedAt: '2026-09-25T08:15:01',
  },
];
