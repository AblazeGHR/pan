/**
 * Job 统一 API（`/api/jobs/*`）的前端类型。
 *
 * 契约源：`packages/jobs/api.py` + `packages/jobs`/`packages.core.background_jobs`
 * 的 `job_public_view` 出口视图。字段按真实视图对齐：
 * - `kind` 用连字符形式（`scheduled-task`）；
 * - `source` / `target` 为嵌套对象；
 * - 积压字段为 `undeliveredFires`（不用 mailbox / notificationState）；
 * - `createdAt` / `updatedAt` 为 **epoch 秒（number）**；schedule/fire 时间戳为本地朴素 ISO。
 */

export type JobKind =
  | 'background-process'
  | 'session-message'
  | 'session-broadcast'
  | 'main-lifecycle'
  | 'scheduled-task';

export type JobStatus =
  | 'pending'
  | 'scheduled'
  | 'starting'
  | 'running'
  | 'completed'
  | 'failed'
  | 'timed_out'
  | 'cancelled';

export type SourceType = 'agent' | 'user' | 'system' | 'plugin';

/** 创建者身份；fire 时动作以该身份执行。 */
export interface JobSource {
  type: SourceType;
  sessionId?: string;
  pluginName?: string;
}

/** 输出/通知对象；与 source 正交，可为空（target 缺失 ⇒ 积压态）。 */
export interface JobTarget {
  sessionId: string | null;
  sessionIds?: string[];
}

export type ScheduleEntryKind = 'once' | 'interval' | 'cron';
export type MisfirePolicy = 'fire_now' | 'skip';

/** job 记录里的 schedule entry（服务端出口形状）。 */
export interface ScheduleEntry {
  id: string;
  kind: ScheduleEntryKind;
  at?: string | null;
  anchor?: string | null;
  cron?: string | null;
  intervalSec?: number | null;
  interval_sec?: number | null;
  timezone?: string | null;
  misfirePolicy: MisfirePolicy;
  enabled: boolean;
  nextFireAt: string | null;
  lastFireAt?: string | null;
}

/** 客户端 → 服务端的 schedule spec（POST/PATCH 接受；控制键由服务端定）。 */
export interface JobScheduleSpec {
  kind: ScheduleEntryKind;
  at?: string | null;
  anchor?: string | null;
  cron?: string | null;
  intervalSec?: number | null;
  timezone?: string | null;
  misfirePolicy?: MisfirePolicy;
  enabled?: boolean;
}

/** target 缺失时积压的未投递派发（上限 20）。 */
export interface UndeliveredFire {
  entryId?: string | null;
  fireAt?: string | null;
  dispatchKey?: string | null;
  text?: string | null;
  error?: string | null;
}

/** 最近一次动作返回（broadcast 时为 {status, results[], errors[]} 汇总）。 */
export interface JobDelivery {
  status: string;
  results?: unknown[];
  errors?: unknown[];
}

/** epoch 秒（新记录）或本地朴素 ISO（兼容旧读路径）。 */
export type JobTimestamp = number | string;

export interface Job {
  jobId: string;
  taskId?: string | null;
  kind: JobKind;
  status: JobStatus;
  name: string;
  description: string;
  source: JobSource;
  target: JobTarget;
  text?: string;
  enabled?: boolean;
  paused: boolean;
  /** Legacy Job kinds may expose one schedule object or omit/null this field. */
  schedule?: unknown;
  misfirePolicy?: MisfirePolicy;
  nextFireAt?: string | null;
  lastFireAt?: string | null;
  lastStatus?: string | null;
  lastError?: string | null;
  lastDelivery?: JobDelivery;
  runCount: number;
  maxRuns?: number | null;
  undeliveredFires?: UndeliveredFire[];
  /** 进程类 job 专有。 */
  logPath?: string | null;
  createdAt: JobTimestamp;
  updatedAt: JobTimestamp;
}

/** Return only iterable schedule-entry arrays; legacy scalar/object values stay untouched. */
export function scheduleEntries(schedule: unknown): ScheduleEntry[] {
  if (!Array.isArray(schedule)) return [];
  return schedule.filter(
    (entry): entry is ScheduleEntry => typeof entry === 'object' && entry !== null && !Array.isArray(entry),
  );
}

/** `GET /api/jobs/kinds` 的 kind 元数据（GUI 徽标/筛选用中文 label）。 */
export interface JobKindMeta {
  kind: JobKind;
  label: string;
  hasSchedule: boolean;
  hasProcess: boolean;
}

/** 一行 runs.jsonl 记录（snake_case，最新在前）。 */
export interface JobRunRecord {
  run_id?: string;
  task_id?: string | null;
  fire_at?: string | null;
  actual_at?: string | null;
  dispatch_key?: string | null;
  status?: string;
  session_id?: string | null;
  worker_id?: string | null;
  error?: string | null;
}

/** `POST /api/jobs` 请求体（本期仅 scheduled-task）。 */
export interface JobCreateInput {
  kind: 'scheduled-task';
  name?: string;
  description?: string;
  target: { sessionId: string };
  text: string;
  schedule: JobScheduleSpec[];
  misfirePolicy?: MisfirePolicy;
  maxRuns?: number | null;
  enabled?: boolean;
}

/** `PATCH /api/jobs/{id}` 请求体（可显式清空 target；text 非空校验）。 */
export interface JobPatchInput {
  name?: string;
  description?: string;
  /** 派发正文；后端要求非空字符串。 */
  text?: string;
  enabled?: boolean;
  paused?: boolean;
  target?: { sessionId: string | null } | null;
  schedule?: JobScheduleSpec[];
}
