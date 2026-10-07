// ── Data types (matching backend API responses) ──

import type { AttachmentLocation } from './attachment';

export type { AttachmentLocation } from './attachment';

export interface Message {
  role: string;
  content: string;
  /** Stable canonical history identity. Absent on legacy rows. */
  messageId?: string;
  /** Client receipt identity used to reconcile an optimistic row to Pan history. */
  clientMessageId?: string;
  /** True while this (last) assistant entry is still being streamed by a
   *  running worker; actions that would fork or mutate history guard on it. */
  streaming?: boolean;
  /** Stable provider block identity for compound messages. */
  blockId?: string;
  /** Stable provider turn identity when the adapter exposes one. */
  turnId?: string;
  /** Server-canonical parts; content remains the adapter/legacy fallback. */
  parts?: MessagePart[];
  /** 本地 ISO-8601 发送/回复时间（后端写入历史时刻打点）；旧数据缺失则不显示时间。 */
  ts?: string;
  /** Transient native Codex identity used to merge live Codex messages. */
  nativeItemId?: string;
  /** Queue item(s) whose local CLI hand-off produced this user message. */
  queueItemIds?: string[];
  /** Stable server receipt keys shared by queue delivery events and history. */
  deliveryKeys?: string[];
  /**
   * Durable row origin written by the worker history receipt
   * (backend `worker.SOURCE_TYPES`): `user` = 前端发送、`agent` = MCP 编排注入、
   * `report` = 报告/通知通道、`system_prompt` = 系统提示词注入、
   * `automation` = 定时/后台 Job。旧数据缺失该字段。
   */
  source?: string;
  /** 派发方的 Session id（`agent` 来源行的调用方身份）；旧数据缺失。 */
  sourceSessionId?: string;
  /** 任务行携带的 taskId（正式派发或继承的活动任务号）；旧数据缺失。 */
  taskId?: string;
  /** `assign` = 正式派发幂等键；`active` = 仅继承活动 taskId 的后续消息。 */
  taskIdSource?: 'assign' | 'active';
}

export type MessagePart =
  | { type: 'text'; text: string }
  | {
      type: 'attachment';
      attachmentId: string;
      displayName: string;
      mimeType?: string;
      size?: number;
      source?: 'upload' | 'server_file';
      /** Legacy line fields accepted while older parts are reconstructed. */
      line?: number;
      endLine?: number;
      location?: AttachmentLocation;
    };

/** Session-scoped opaque attachment metadata. href/path are server output only. */
export interface AttachmentRef {
  attachmentId: string;
  displayName: string;
  mimeType?: string;
  size?: number;
  source?: 'upload' | 'server_file';
  href?: string;
  /** Compatibility-only server path; never sent back as authority. */
  path?: string;
}

/** MCP-only capability flags (backend `pan_access`, camelCase over HTTP). */
export interface PanAccess {
  /** MCP callers may only act on sessions they manage. */
  restrictToManaged?: boolean;
  /** MCP callers may claim sessions that have no manager yet. */
  canClaimUnmanaged?: boolean;
  /** Sessions created through MCP are auto-claimed by the creator. */
  autoClaimCreated?: boolean;
}

export interface Session {
  id: string;
  name: string;
  adapter?: string;
  cliSessionId?: string | null;
  model?: string | null;
  permissionMode?: string | null;
  alwaysThinkingEnabled: boolean;
  effort: string;
  maxThinkingTokens?: number;
  /** Explicit Codex override; absent means Codex/model default. */
  modelContextWindow?: number | null;
  /** Explicit Codex override; absent means Codex/model default. */
  modelAutoCompactTokenLimit?: number | null;
  workdir?: string;
  /** Calculated system prompt used for the current session, when available. */
  systemPrompt?: string | null;
  workerStatus?: string | null;
  workerId?: string | null;
  workerGeneration?: number | null;
  workerTaskId?: string | null;
  workerTaskSeq?: number | null;
  workerExecutionSeq?: number | null;
  /**
   * Unread done count for this Session: materialized ``generation - read``
   * (backend `_persist_terminal_state` increments the generation after the
   * existing terminal dedup; error/cancelled/zombie and child report arrivals
   * do not count). 0/undefined renders no badge. Display/filter only — acks
   * always carry {@link unreadDoneGeneration}, never this count.
   */
  unreadDoneCount?: number;
  /** Monotonic count of real done terminal states (ack request boundary). */
  unreadDoneGeneration?: number;
  /** Highest done generation acknowledged as read (server-side monotonic). */
  unreadDoneReadGeneration?: number;
  /** Pending queue rows per the queue panel's public projection (0 hides). */
  queuePendingCount?: number;
  /** True only when the queue is non-empty and every pending row is locked. */
  queueAllLocked?: boolean;
  /** Durable queue revision matching the queue events/snapshot. */
  queueRevision?: number;
  /** Independent durable pause for this Session's entire queue. */
  queuePaused?: boolean;
  /** Last Worker state confirmed through an explicit Pan lifecycle action. */
  lastLegalWorkerState?: string | null;
  /** Id of the managing (parent) session; absent/null means unmanaged. */
  managedBy?: string | null;
  /** True when the managing session has blocked outbound operations to this session. */
  readonlySession?: boolean;
  /** Ids of sessions this session manages (claims as a meta-agent). */
  managed?: string[];
  /**
   * Durable workspace memberships. The backend permits multiple memberships;
   * a missing or empty array means the session is ungrouped. Old sessions may
   * omit the field entirely.
   */
  workspaceIds?: string[];
  /** Existing custom Session order; pin state uses its own storage. */
  order?: number | null;
  /** Shared pin state, independent of the user-defined Session order. */
  pinned?: boolean;
  /** Position among all pinned Sessions, or null when unpinned. */
  pinOrder?: number | null;
  /** Monotonic revision for the shared pin-state snapshot. */
  pinRevision?: number;
  /** Managed-session report subscriptions (ids this session gets reports from). */
  reportSubscriptions?: string[];
  /** Whether the direct manager subscribes to this Session's completion reports. */
  reportsToManager?: boolean | null;
  /** QQ inbox subscriptions, each formatted "user:<uin>" or "group:<uin>". */
  qqSubscriptions?: string[];
  notificationSettings?: { browser: boolean; system: boolean };
  /** QQ, system, or browser msgBridge is enabled for this Session. */
  msgBridgeEnabled?: boolean;
  /** MCP capability flags; only present on the full (non-summary) endpoint. */
  panAccess?: PanAccess;
  /** Whether MCP was ever enabled for this session (mcp_servers non-empty). */
  mcpEnabled?: boolean;
  /** True when the session template locks MCP on/off (always/never mode). */
  mcpLocked?: boolean | null;
  /** Why MCP is locked: "always" / "never"; null when unlocked. */
  mcpLockReason?: 'always' | 'never' | null;
  /** Names of MCP servers currently enabled for this session. */
  mcpServers?: string[];
  history: Message[];
  /** Latest formal assign task context used by subsequent agent_send messages. */
  activeTaskId?: string | null;
  historyTruncated?: boolean;
  /** Null/undefined means the cold summary cannot know the total yet. */
  historyTotal?: number | null;
  /** Server history identity scope. Old sessions/clients may omit it. */
  historyEpoch?: string | null;
  /** Persisted Session-level canonical history revision across epoch changes. */
  historyRevision?: number;
  /** Absolute start offset of the currently loaded history window. */
  historyStart?: number;
  /** Raw bounded display preview (summary=1 endpoint, truncated ~200 chars). */
  lastMessage?: string;
  /** Monotonic backend summary projection version. */
  summaryRevision?: number;
  lastUserPreview?: string;
  lastAssistantPreview?: string;
  lastDisplayPreview?: string;
  /** Explicit worker execution mode for this session: "stream" / "oneshot" / null(unset). */
  outputMode?: string | null;
  lastResult?: Record<string, unknown> | null;
  totalUsage?: Record<string, number> | null;
  createdAt?: string;
  updatedAt?: string;
}

/** Durable named container grouping Sessions (sidebar Workspace rail). */
export interface Workspace {
  id: string;
  name: string;
  /** Independent display order; null = never explicitly ordered (sorts last). */
  order: number | null;
  /**
   * Absolute server directories shared by every Session in this Workspace.
   * Metadata only; adding/removing never touches the disk. Legacy workspaces
   * may omit the field entirely.
   */
  dirs?: string[];
  createdAt?: string;
  updatedAt?: string;
  /** Server-computed member count (present on workspace endpoints). */
  sessionCount?: number;
  /** Server-computed member session ids. */
  sessionIds?: string[];
}

export interface ApiWorkspacesResponse {
  workspaces: Workspace[];
  error?: string;
}

export interface ApiWorkspaceResponse {
  ok?: boolean;
  workspace: Workspace;
  error?: { code?: string; message?: string };
}

export interface ApiWorkspaceOrderResponse {
  ok?: boolean;
  order?: string[];
  error?: { code?: string; message?: string };
}

export interface SessionUsageView {
  ok?: boolean;
  sessionId: string;
  adapter: string;
  input: number | null;
  output: number | null;
  cache: { read: number | null; write: number | null; total: number | null };
  total: { tokens: number | null; credit: number | null };
  /** Account-scoped Codex quota projection; never part of raw/total usage. */
  codexQuota?: CodexQuotaProjection | null;
  /** Stable projection provenance; raw payloads are intentionally excluded. */
  source?: Record<string, unknown> | string | null;
  updatedAt?: string | null;
  error?: { code?: string | number; message?: string };
}

export interface CodexQuotaProjection {
  ok?: boolean;
  provider?: string;
  profileKey?: string;
  sessionId?: string | null;
  workerId?: string | null;
  observedAt?: string | null;
  receivedAt?: string | null;
  updatedAt?: string | null;
  stale?: boolean;
  cacheMode?: 'live' | 'persisted';
  source?: Record<string, unknown> | string | null;
  windows?: Record<string, Record<string, unknown>>;
  rawSnapshots?: Record<string, unknown>;
  raw?: Record<string, unknown> | null;
  refreshError?: string;
  credentialStatus?: string;
  error?: { code?: string | number; message?: string };
}

export interface WorkerEventContent {
  type: string;
  text?: string;
  thinking?: string;
  /** kimi: 思考块用 `type: 'think'` + `think` 字段（cbc 用 thinking）。 */
  think?: string;
  name?: string;
  input?: Record<string, unknown>;
}

export interface WorkerEvent {
  type: string;
  /** 本地 ISO-8601 完成时刻（最终 assistant 消息一次性携带，delta 不带）。 */
  ts?: string;
  /** Native app-server incremental event; UI merges it into one message. */
  delta?: boolean;
  /** Cumulative text for sidebar previews while `delta` is true. */
  stream_text?: string;
  /** Completed canonical event should replace any in-flight delta message. */
  final?: boolean;
  /** Tool/output delta targets the currently displayed item instead of appending. */
  replace?: boolean;
  /** Native Codex item id; lets the UI update the right interleaved item. */
  item_id?: string;
  /** Native Codex turn id; canonical identity for the assistant reply. */
  turn_id?: string;
  /** Native Codex terminal interaction process id and prompt/input bytes. */
  process_id?: string;
  stdin?: string;
  /** Native Codex server request metadata (approval/user-input bridge). */
  method?: string;
  request_id?: string | number;
  params?: Record<string, unknown>;
  /** kimi: stream-json 事件以 role 标识（assistant/thinking/result/meta），
   *  纯文本 assistant 事件没有 type 字段。 */
  role?: string;
  subtype?: string;
  message?: {
    content?: WorkerEventContent[];
  };
  /** kimi: content 可为纯字符串或块数组（cbc 走 message.content）。 */
  content?: string | WorkerEventContent[];
  /** kimi: `type:'content.part'` 事件的增量块。 */
  part?: { type?: string } & Record<string, unknown>;
  /** kimi: tool_calls（function call）。 */
  tool_calls?: Array<{ function?: { name?: string; arguments?: string } }>;
  session_id?: string;
  model?: string;
  is_error?: boolean;
  cancelled?: boolean;
  turn_status?: string;
  error?: unknown;
  error_text?: string;
  result?: string;
  cliSessionId?: string;
  /** Durable acknowledgement correlation for browser send-queue items. */
  clientMessageId?: string;
  /** Native Codex thread status (`active` may carry waiting flags). */
  native_status?: {
    type?: string;
    activeFlags?: string[];
    message?: string;
    error?: string;
  };
  /** Raw native item carried by the generic Codex item fallback. */
  item?: Record<string, unknown>;
  /** Native Codex thread/turn token usage snapshot. */
  token_usage?: Record<string, unknown>;
  /** Native Codex account rate-limit snapshot. */
  rate_limits?: Record<string, unknown>;
  /** Native Codex aggregate plan for the current turn. */
  plan?: Array<Record<string, unknown>>;
  explanation?: string | null;
  /** Native Codex aggregate diff for the current turn. */
  diff?: string;
  /** Native Codex MCP startup status notification. */
  mcp_status?: Record<string, unknown>;
  /** Native Codex model reroute notification. */
  model_rerouted?: Record<string, unknown>;
}

export interface ApprovalRequest {
  sessionId: string;
  workerId: string;
  requestId: string | number;
  method: string;
  params: Record<string, unknown>;
}

export interface UserInputQuestion {
  id: string;
  header?: string;
  question?: string;
  isOther?: boolean;
  isSecret?: boolean;
  options?: Array<{ label: string; description?: string }>;
}

export interface UserInputRequest {
  sessionId: string;
  workerId: string;
  requestId: string | number;
  method: string;
  questions: UserInputQuestion[];
}

export interface ElicitationRequest {
  sessionId: string;
  workerId: string;
  requestId: string | number;
  method: string;
  params: Record<string, unknown>;
}

/** Native Codex terminal interaction emitted when a command needs stdin. */
export interface TerminalInteraction {
  sessionId: string;
  workerId: string;
  itemId: string;
  processId: string;
  stdin: string;
  params: Record<string, unknown>;
}

export interface StreamEvent {
  type: string;
  /** Legacy/source cursor; new clients prefer sourceCursorStart/End. */
  eventEpoch?: string;
  eventSeq?: number;
  /** Per-connection contiguous transport cursor. */
  deliveryEpoch?: string;
  deliverySeq?: number;
  /** Global source cursor range represented by this frame. */
  sourceCursorStart?: number;
  sourceCursorEnd?: number;
  /** Server process epoch; aliases eventEpoch for old payloads. */
  serverEpoch?: string;
  historyEpoch?: string;
  historyRevision?: number;
  /** Durable result/history coverage boundary. */
  terminalCoverage?: {
    historyEpoch?: string;
    historyRevision?: number;
    messageIds?: string[];
  };
  snapshotId?: string;
  boundary?: 'authoritative' | string;
  reason?: string;
  sessions?: Session[];
  sessionsTruncated?: boolean;
  workers?: Array<Record<string, unknown>>;
  details?: Record<string, Record<string, unknown>>;
  resultCursors?: Record<string, number>;
  resultsAvailableFrom?: Record<string, number>;
  sessionId?: string;
  queueRevision?: number;
  agentReportsPaused?: boolean;
  queuePaused?: boolean;
  workerId?: string;
  /** Monotonic runtime generation, used to ignore late lifecycle events. */
  generation?: number;
  /** Physical browser WebSocket generation for client handshake idempotency. */
  connectionGeneration?: number;
  /** Identity of a native-interaction replay handshake batch. */
  replayGeneration?: number;
  replayRequestId?: string;
  event?: WorkerEvent;
  message?: string;
  status?: string;
  result?: string;
  notification?: {
    title?: string;
    body?: string;
    browser?: boolean;
    system?: Record<string, unknown> | null;
  };
  cancelled?: boolean;
  taskSeq?: number;
  executionSeq?: number;
  /** Durable task identity carried by worker.stream for late-frame isolation. */
  taskId?: string | null;
  /** Stable backend identity for this terminal task result. */
  terminalKey?: string;
  /** Monotonic per-Session cursor for terminal result replay and ordering. */
  resultCursor?: number;
  name?: string;
  newName?: string;
  /** Safe session fields included by session lifecycle events when available. */
  session?: Partial<Session>;
  cliSessionId?: string;
  /** 任务来源标记（worker.status 事件透传）：agent=meta-agent 编排注入、
   *  report=订阅报告、user=前端发送、system_prompt=系统提示词注入。 */
  source?: string;
  /** User messages durably handed to the local CLI and removed from pending. */
  messages?: Message[];
  /** Queue ids included in a successful local CLI hand-off. */
  queueItemIds?: string[];
  /** Raw or normalized queue item carried by queue update notifications. */
  item?: Record<string, unknown>;
  /** Stable canonical message/block identity when the event carries one. */
  messageId?: string;
  blockId?: string;
  /** True when the server replays a still-pending interactive prompt after WS reconnect. */
  replayed?: boolean;
  /** Workspace events: the workspace whose membership/metadata changed. */
  workspaceId?: string;
  /** session.workspaceUpdated: the session's complete membership snapshot. */
  workspaceIds?: string[];
  /** workspace.membershipUpdated: the workspace's complete member id snapshot. */
  sessionIds?: string[];
  /** Shared Session pin snapshot revision. */
  pinRevision?: number;
  /** session.rewind.progress: rewind job identity. */
  jobId?: string;
  /** session.rewind.progress: starting/resuming/rewinding-files/truncating/completed/failed. */
  stage?: string;
  /** session.rewind.progress: branch session created on completion. */
  newSessionId?: string;
  /** session.rewind.progress: failure detail (stage=failed). */
  error?: string | null;
  /** session.rewind.progress: capability boundary note from the backend. */
  limitation?: string;
}

// ── Rewind ──

/** 1 = code + conversation, 2 = conversation only, 3 = code only. */
export type RewindScope = 1 | 2 | 3;

export interface ApiRewindResponse {
  ok?: boolean;
  error?: string | { code?: string; message?: string };
  jobId?: string;
  sessionId?: string;
  scope?: RewindScope;
  scopeLabel?: string;
  status?: string;
  stage?: string;
  limitation?: string;
}

export interface ApiRewindJobStatus {
  ok?: boolean;
  error?: string | { code?: string; message?: string } | null;
  jobId?: string;
  sessionId?: string;
  stage?: string;
  status?: string;
  scope?: RewindScope;
  newSessionId?: string | null;
  limitation?: string;
}

// ── API response types ──

export interface ApiSessionsResponse {
  sessions: Session[];
  error?: string;
}

export interface ApiSessionResponse extends Session {
  error?: string;
}

export interface ApiModelsResponse {
  models: string[];
  default: string;
}

export interface ApiCodexRefreshOfficialModelsResponse {
  ok: boolean;
  before: string[];
  after: string[];
  error?: string;
}

export interface ApiGenericResponse {
  error?: string;
  workerId?: string;
  sessionId?: string;
  status?: string;
  cliSessionId?: string;
  takeoverCommand?: string;
  name?: string;
  model?: string;
  takeoverPid?: number;
  reason?: string;
  /** Backend signals the change requires a worker restart/respawn to take effect. */
  requireRestart?: boolean;
}

// ── Manage / QQ postbox types ──

export interface ApiErrorInfo {
  code: string | number;
  message: string;
}

export interface ApiClaimResponse {
  ok?: boolean;
  managerId?: string;
  sessionId?: string;
  managed?: string[];
  error?: ApiErrorInfo;
}

/** POST /api/sessions/order — custom display order (drag & drop) response. */
export interface ApiSessionOrderResponse {
  ok?: boolean;
  /** Full session id order after the reorder (authoritative server order). */
  order?: string[];
  error?: { code?: string; message?: string };
}

export interface ApiSessionPinResponse {
  ok?: boolean;
  pinned?: boolean;
  pinOrder?: number | null;
  pinRevision?: number;
  sessionIds?: string[];
  error?: { code?: string; message?: string };
}

/** POST /api/sessions/{id}/unread-done/ack — generation-cursor acknowledgement. */
export interface ApiSessionUnreadAckResponse {
  ok?: boolean;
  /** Remaining unread done count after this acknowledgement (authoritative). */
  unreadDoneCount?: number;
  /** Done generation known to the server when the ack was applied. */
  unreadDoneGeneration?: number;
  /** Read cursor after the ack (monotonic; idempotent for equal cursors). */
  unreadDoneReadGeneration?: number;
  error?: { code?: string; message?: string };
}

export interface ApiReportSubscribeResponse {
  managerId?: string;
  sessionId?: string;
  subscribed?: boolean;
  reportSubscriptions?: string[];
  error?: string;
}

export interface ApiReadonlyResponse {
  ok?: boolean;
  managerId?: string;
  sessionId?: string;
  readonlySession?: boolean;
  error?: ApiErrorInfo;
}

export interface ApiSessionReportToManagerResponse {
  ok?: boolean;
  sessionId?: string;
  managerId?: string;
  reportsToManager?: boolean;
  error?: ApiErrorInfo;
}

export interface ApiSessionMsgBridgeResponse {
  ok?: boolean;
  sessionId?: string;
  msgBridgeEnabled?: boolean;
  qqSubscriptions?: string[];
  notificationSettings?: { browser: boolean; system: boolean };
  error?: ApiErrorInfo;
}

export interface QqContact {
  peerName: string;
  peerUin: string;
  /** 1 = private chat (user), 2 = group chat. */
  chatType: number;
}

export interface ApiQqContactsResponse {
  ok?: boolean;
  contacts?: QqContact[];
  error?: ApiErrorInfo;
}

/** A registered QQ channel (bot account), from GET /api/qq/channels. */
export interface QqChannelInfo {
  /** Channel name, e.g. "llonebot" / "llonebot2". */
  name: string;
  /** Bot QQ number; empty when the channel has no bot_uin configured. */
  bot_uin: string;
  connected: boolean;
}

export interface ApiQqChannelsResponse {
  ok?: boolean;
  channels?: QqChannelInfo[];
  error?: ApiErrorInfo;
}

export interface ApiQqSubscribeResponse {
  sessionId?: string;
  qqTarget?: string;
  subscribed?: boolean;
  qqSubscriptions?: string[];
  error?: string;
}

export interface ApiSessionHistoryResponse {
  history: Message[];
  total: number;
  hasMore: boolean;
  start: number;
  historyEpoch?: string;
  historyRevision?: number;
  error?: string;
}

export interface ApiHistorySearchVersion {
  sessionId: string;
  historyEpoch: string;
  historyRevision: number;
  historyTotal: number;
}

export type HistorySearchRole = 'user' | 'assistant' | 'tool' | 'thinking';

export interface ApiHistorySearchHit {
  sessionId: string;
  messageId: string;
  messageIndex: number;
  role: HistorySearchRole;
  snippet: string;
  historyEpoch: string;
  historyRevision: number;
  historyTotal: number;
  matchCount?: number;
  matchStart?: number;
  firstMatch?: number;
}

export interface ApiHistorySearchResponse {
  /** Optional nearest visible-window hit; ordinary page ordering is unchanged. */
  viewportHit?: ApiHistorySearchHit | null;
  matchingSessionIds?: string[];
  preparedIdentities?: number;
  hits: ApiHistorySearchHit[];
  versions: ApiHistorySearchVersion[];
  limit: number;
  hasMore: boolean;
  nextCursor: string | null;
  totalMatches?: number;
  totalMessages?: number;
  roles?: HistorySearchRole[];
}

// ── Session template types ──

export interface SessionTemplate {
  name: string;
  adapter?: string;
  model?: string | null;
  mcpMode?: 'always' | 'optional' | 'never';
  mcpServers?: string[];
  panAccess?: PanAccess;
  /** Absolute path of the plugin dir whose manifest.json defined this template. */
  sourceManifest?: string;
  /** Short readable manifest label, e.g. "packages/mcp/manifest.json". */
  sourceManifestLabel?: string;
  system_prompt_preview?: string;
}

export interface SessionTemplateManifestTarget {
  id: string;
  label: string;
  writable: boolean;
  reason?: string | null;
}

export interface ApiSessionTemplateTargetsResponse {
  manifestTargets?: SessionTemplateManifestTarget[];
  loaded?: boolean;
  total?: number;
  error?: string;
}

export interface SessionTemplateSaveInput {
  manifestId: string;
  baseTemplate?: string;
  name: string;
  adapter?: string | null;
  model?: string | null;
  permission_mode?: string | null;
  system_prompt?: string | string[];
  mcp_mode?: 'always' | 'optional' | 'never';
  mcp_servers?: string[];
  pan_access?: {
    restrict_to_managed?: boolean;
    can_claim_unmanaged?: boolean;
    auto_claim_created?: boolean;
  };
}

export interface ApiSessionTemplatesResponse {
  sessionTemplates?: SessionTemplate[];
  total?: number;
  error?: string;
}

export interface ApiSessionTemplateSaveResponse {
  ok?: boolean;
  error?: string;
  name?: string;
  manifestId?: string;
}

export interface ApiMcpServersResponse {
  servers?: McpServerInfo[];
  loaded?: boolean;
  error?: string;
}

// ── Adapter types ──

export interface PermissionMode {
  value: string;
  label: string;
}

export interface AdapterConfig {
  models: string[];
  defaultModel: string;
  effortValues: string[];
  /** Per-model reasoning effort values when the adapter exposes them. */
  modelEfforts?: Record<string, string[]>;
  permissionModes: PermissionMode[];
  defaultPermissionMode: string;
  supportedSettings: string[];
  /** Worker 对该 adapter 的可用驱动方式：["stream"] 或 ["stream","oneshot"]。 */
  executionModes?: string[];
}

export interface ApiConfigResponse {
  adapter?: string;
  models: string[];
  defaultModel: string;
  effortValues: string[];
  modelEfforts?: Record<string, string[]>;
  permissionModes: PermissionMode[];
  defaultPermissionMode?: string;
  supportedSettings?: string[];
  executionModes?: string[];
}

export type DataCatalogPolicyStatus =
  | 'data_retention_policy'
  | 'jobs_api_managed'
  | 'session_lifecycle_cleanup'
  | 'not_auto_cleanable';

export interface DataCatalogPath {
  label: string;
  path: string;
  exists: boolean;
  source: string;
  overridden: boolean;
  external: boolean;
}

export interface DataCatalogCategory {
  id: string;
  name: string;
  purpose: string;
  policyStatus: DataCatalogPolicyStatus;
  paths: DataCatalogPath[];
  note?: string;
}

export interface ApiDataCatalogResponse {
  categories: DataCatalogCategory[];
  notice: string;
  jobsRetention: {
    slot: string;
    status: 'reserved';
    message: string;
  };
}

export type DataRetentionPolicyId = 'sessions' | 'attachments' | 'qq_history' | 'qq_media' | 'pan_logs';

export interface ApiDataRetentionPolicy {
  enabled: boolean;
  days: number | null;
}

export interface ApiDataRetentionScanResult {
  scanned: number;
  deleted: number;
  skipped: number;
  skipReasons: Record<string, number>;
  error?: string | null;
  completedAt?: string | null;
  lastScanAt?: string | null;
}

export interface ApiDataRetentionResponse {
  policies: Record<DataRetentionPolicyId, ApiDataRetentionPolicy>;
  configKey: 'data_retention';
  lastScans: Record<DataRetentionPolicyId, ApiDataRetentionScanResult>;
}

export interface AdapterInfo {
  name: string;
  defaultModel: string;
  supportsResume: boolean;
  supportsFork: boolean;
}

export interface ApiAdaptersResponse {
  adapters: AdapterInfo[];
  default: string;
}

export interface CliDiagnostic {
  name: string;
  label: string;
  available: boolean;
  command: string[];
  missing: string[];
  hint: string;
  error?: string | null;
}

export interface ApiCliStatusResponse {
  adapters: CliDiagnostic[];
  available: string[];
  hasAvailable: boolean;
}

// ── Config hot-reload ──

export interface ApiConfigReloadAdapterEntry {
  name: string;
  modelsBefore?: number | null;
  modelsAfter?: number | null;
}

export interface ApiConfigReloadWorkerValues {
  timeout_sec: number;
  task_timeout_sec: number;
  idle_sec: number;
}

export interface ApiConfigReloadResponse {
  reloaded: boolean;
  error?: string;
  adapters?: ApiConfigReloadAdapterEntry[];
  worker?: {
    before: Partial<ApiConfigReloadWorkerValues>;
    after: Partial<ApiConfigReloadWorkerValues>;
  };
  memory?: {
    before: { enabled: boolean };
    after: { enabled: boolean };
  };
  plugin?: {
    before: string[];
    after: string[];
    applied: boolean;
    sessionTemplates?: number;
    mcpServers?: number;
    characters?: number;
    commandRoutes?: number;
    errors?: string[];
  };
  requiresRestart?: string[];
  errors?: string[];
}

// PUT /api/settings/worker — save + hot-apply worker lifecycle timeouts.
// Same {before, after} shape as the ``worker`` entry of ApiConfigReloadResponse.
export interface ApiWorkerSettingsUpdateResponse {
  error?: string;
  before: Partial<ApiConfigReloadWorkerValues>;
  after: Partial<ApiConfigReloadWorkerValues>;
}

// ── Remote tunnel (cloudflared via the internal Python launcher) ──

export interface ApiRemoteStatusResponse {
  available: boolean;
  enabled: boolean;
  provider?: string;
  quickTunnel?: boolean;
  protocol?: string;
  port?: number;
  running: boolean;
}

export interface ApiRemoteRestartResponse {
  ok: boolean;
  error?: string;
  killed?: number[];
  restarted?: boolean;
}

// Main Pan service restart (detached scripts/restart_pan.ps1 supervisor).
export interface ApiMainRestartStatusResponse {
  available: boolean;
  pending: boolean;
  platform: string;
  port?: number;
  reason?: string;
  requestId?: string;
  jobId?: string;
  operation?: 'restart' | 'exit';
  phase?: 'requested' | 'stopping_workers' | 'stopping' | 'stopped' | 'starting' | 'ready' | 'failed' | 'timed_out';
  jobStatus?: string;
  root?: string;
  oldPid?: number | null;
  oldPidCreatedAt?: number | null;
  newPid?: number | null;
  newPidCreatedAt?: number | null;
  error?: string | null;
  createdAt?: number;
  updatedAt?: number;
}

export interface ApiMainRestartResponse {
  ok: boolean;
  status: 'scheduled' | 'disabled' | 'busy' | 'error';
  accepted?: boolean;
  phase?: ApiMainRestartStatusResponse['phase'];
  jobId?: string;
  message?: string;
  error?: string;
  pending?: boolean;
  requestId?: string;
}

// Main Pan service stop-only exit (detached scripts/exit_pan.ps1 supervisor).
export interface ApiMainExitStatusResponse {
  available: boolean;
  pending: boolean;
  stage?:
    'idle' | 'scheduled' | 'stopping_workers' | 'stopping_service' | 'offline' | 'error' | string;
  platform: string;
  port?: number;
  reason?: string;
  error?: string | null;
  requestId?: string;
  jobId?: string;
  phase?:
    'requested' | 'stopping_workers' | 'stopping_service' | 'offline' | 'failed' | 'timed_out';
  jobStatus?: string;
  root?: string;
  oldPid?: number | null;
  oldPidCreatedAt?: number | null;
  createdAt?: number;
  updatedAt?: number;
}

export interface ApiMainExitResponse {
  ok: boolean;
  status: 'scheduled' | 'disabled' | 'busy' | 'error';
  message?: string;
  error?: string;
  pending?: boolean;
  requestId?: string;
  accepted?: boolean;
  phase?: ApiMainExitStatusResponse['phase'];
  jobId?: string;
}

export type ApiSessionExitStrategy = 'ask' | 'offline' | 'preserve-running';
export type ApiStartupPreference = 'ask' | 'wake-running' | 'sync-actual' | 'preserve-running';

export interface ApiSessionLifecyclePreferences {
  exitStrategy: ApiSessionExitStrategy;
  startupPreference: ApiStartupPreference;
}

export interface ApiSessionLegalStateSyncResult {
  sessionId: string;
  status: 'updated' | 'error';
  legalWorkerState?: string;
  runtimeWorkerStatus?: string;
  error?: string;
}

export type ApiStartupRecoveryChoice = 'restart' | 'preserve-running' | 'sync-actual';

export interface ApiStartupRecoveryCandidate {
  id: string;
  name: string;
  adapter: string;
  workdir?: string;
  updatedAt?: string;
  lastLegalWorkerState: 'running';
}

export interface ApiStartupRecoveryRecord {
  generation: string;
  state: 'initializing' | 'no_candidates' | 'pending' | 'processing' | 'failed' | 'completed';
  candidateSnapshot: ApiStartupRecoveryCandidate[];
  decision: ApiStartupRecoveryChoice | null;
  autoPreference?: ApiStartupPreference | null;
  decisionId?: string | null;
  attempts: number;
  results: Array<Record<string, unknown>>;
  error?: string | null;
}

export interface ApiStartupRecoveryClaimResponse {
  ok: boolean;
  claimed: boolean;
  state: ApiStartupRecoveryRecord['state'];
  decision?: ApiStartupRecoveryChoice | null;
  attempts?: number;
  error?: string | null;
  results?: Array<Record<string, unknown>>;
  candidates?: ApiStartupRecoveryCandidate[];
}

export interface ApiHealthResponse {
  status: string;
  version?: string;
}

// ── Import types ──

export interface CbcProject {
  project_dir: string;
  session_count: number;
  resumable_count?: number;
  path_hint: string;
  drive: string;
  short_label: string;
}

export interface CbcSessionItem {
  session_id: string;
  project_dir: string;
  title: string;
  message_count: number;
  first_timestamp: string;
  last_timestamp: string;
  model: string;
  forked_from: string | null;
}

export interface KimiWorkspace {
  workspace_id: string;
  name: string;
  root: string;
  session_count: number;
}

export interface KimiSessionItem {
  session_id: string;
  workspace_id: string;
  title: string;
  workDir: string;
  message_count: number;
  model: string;
  updatedAt: string;
}

export interface OpencodeSessionItem {
  session_id: string;
  title: string;
  workDir: string;
  createdAt: string;
  updatedAt: string;
  message_count: number;
  model: string;
}

export interface CodexSessionItem {
  session_id: string;
  title: string;
  workDir: string;
  createdAt: string;
  updatedAt: string;
  message_count: number;
  model: string;
}

export interface ApiCbcProjectsResponse {
  projects: CbcProject[];
}

export interface ApiCbcSessionsResponse {
  sessions: CbcSessionItem[];
  total?: number;
  shown?: number;
}

export interface ApiKimiWorkspacesResponse {
  workspaces: KimiWorkspace[];
}

export interface ApiKimiSessionsResponse {
  sessions: KimiSessionItem[];
}

export interface ApiOpencodeSessionsResponse {
  sessions: OpencodeSessionItem[];
  total?: number;
}

// ── Worker types ──

export interface WorkerItem {
  workerId: string;
  sessionId: string;
  status: string;
  generation?: number;
}

export interface ApiWorkerListResponse {
  workers: WorkerItem[];
}

// ── Multi-select types ──

export interface ApiBatchDeleteResponse {
  deleted: number;
  error?: string;
}

// ── Send queue types ──

export interface QueuedMessage {
  id: string; // 唯一标识（重排/编辑/删除的 key）
  text: string; // 原文（渲染时单行截断，存全文）
  createdAt: number; // 入队时间戳
  status: 'pending'; // 首版恒 pending，预留扩展
}

/** Edit overlay for one queued item; the server lease keeps Worker hand-off paused. */
export interface QueuedEdit {
  id: string;
  /** Draft shown in the main composer while its server lease is held. */
  text: string;
  /** 编辑前的原文（Esc 取消 / 保存为空时恢复）。 */
  originalText: string;
  /** 原队列位置（Enter 保存后插回原位置）。 */
  index: number;
  createdAt: number;
  /** Monotonic identity for one edit transaction; never sent to the server. */
  editToken?: number;
  /** Keep editing locked while its PATCH and authoritative refresh settle. */
  saving?: boolean;
  /** Opaque server lease identity that prevents Worker hand-off while editing. */
  serverToken?: string;
  acquiring?: boolean;
  releasing?: boolean;
  cancelRequested?: boolean;
  leaseExpiresAt?: number;
  /** Body/revision captured under the edit lease, independent of the row preview. */
  revision?: number;
  bodyFormat?: 'text' | 'json' | 'parts';
  /** Keep the draft available for copying when the lease or item is invalid. */
  error?: string;
  /** Item kind captured at startEdit; gates attachment editing (task only). */
  kind?: 'task' | 'report' | 'qq' | 'wechat';
  /** Attachment parts already on the item (server-authoritative order/metadata;
   *  shown read-only, they are preserved server-side on save). */
  originalAttachments?: Array<{ attachmentId: string; displayName: string }>;
  /** New attachments added during this edit transaction; appended on save. */
  attachments?: QueuedEditAttachment[];
}

/** One newly added queued-edit attachment; mirrors PendingAttachment shape. */
export interface QueuedEditAttachment {
  /** Local composer occurrence identity; never sent to the server. */
  occurrenceId: string;
  /** Compatibility alias used by chip selectors. */
  id: string;
  /** Opaque server attachment id once uploaded/registered. */
  attachmentId?: string;
  displayName: string;
  href?: string;
  path?: string;
  mimeType?: string;
  source?: 'upload' | 'server_file';
  status: 'uploading' | 'registering' | 'ready' | 'error';
  loadedBytes?: number;
  totalBytes?: number;
  error?: string;
  /** Client File kept for upload/retry; never serialized. */
  file?: File;
  /** Dedupe key for repeated picks of the same client file. */
  fileKey?: string;
}

// ── Agent queue (backend session.queue_pending, normalized) ──

export type AgentQueueKind = 'task' | 'report' | 'qq' | 'wechat';
export type QueueDispatchState =
  | 'queued'
  | 'reserved'
  | 'writing'
  | 'sent_to_cli'
  | 'write_failed'
  | 'unknown_after_crash'
  | 'deleted';

/** 后端落盘队列 queue_pending 的归一化条目（task/report/qq 异构 → 统一形状）。 */
export interface AgentQueueItem {
  /** 服务端生成并持久化的 queueItemId。 */
  id: string;
  queueItemId: string;
  /** Legacy read compatibility; normalized status is in meta. */
  status?: string;
  kind: AgentQueueKind;
  text: string;
  parts?: MessagePart[];
  createdAt: number | string;
  source?: string;
  meta?: {
    seq?: number;
    taskId?: string;
    status?: string;
    workerId?: string;
    qqTarget?: string;
    time?: string;
    /** queued=仍待本地 CLI 交接；reserved/writing 只在恢复事件中短暂存在。 */
    dispatchState?: QueueDispatchState;
    revision?: number;
    locked?: boolean;
    lockManual?: boolean;
    lockAutoReport?: boolean;
  };
}

export interface ApiSessionQueueResponse {
  items: AgentQueueItem[];
  queueRevision?: number;
  agentReportsPaused?: boolean;
  queuePaused?: boolean;
  error?: string | { code?: string; message?: string };
  ok?: boolean;
}

// ── UI types ──

export interface ToastMessage {
  id: string;
  message: string;
  type: 'info' | 'error' | 'warning';
}

export interface SyncedSettings {
  model: string;
  permissionMode: string;
  alwaysThinkingEnabled: boolean;
  effort: string;
}

export interface SettingsBody {
  model?: string;
  permissionMode?: string;
  alwaysThinkingEnabled?: boolean;
  effort?: string;
  /** Partial patch — only the given flags are updated server-side. */
  panAccess?: PanAccess;
  /** Names of MCP servers to enable (empty array clears them). */
  mcpServers?: string[];
  /** Force past the session template's always/never MCP lock (user confirmed). */
  forceMcp?: boolean;
  /** Worker execution mode; empty string clears (→ adapter default). */
  outputMode?: string;
  /** Codex-only positive integer override; null removes the persisted key. */
  modelContextWindow?: number | null;
  /** Codex-only positive integer override; null removes the persisted key. */
  modelAutoCompactTokenLimit?: number | null;
  notificationSettings?: { browser?: boolean; system?: boolean };
}

/** A single MCP server declared in the manifest (no secrets exposed). */
export interface McpServerInfo {
  name: string;
  command?: string | null;
  cwd?: string | null;
}

// ── File-system types ──

export interface FsEntry {
  name: string;
  type: 'file' | 'dir';
  size: number;
  modified: string;
}

export interface FileNode extends FsEntry {
  path: string;
  children?: FileNode[];
  expanded?: boolean;
}

export interface ApiFsListResponse {
  entries: FsEntry[];
  error?: string;
}

export interface ApiFsReadResponse {
  content: string;
  size: number;
  error?: string;
}

export interface ApiFsWriteResponse {
  path: string;
  size: number;
  error?: string;
}

export interface ApiFsGenericResponse {
  error?: string;
  from?: string;
  to?: string;
  path?: string;
  deleted?: boolean;
}

// ── Scheduler (alarm-style scheduled tasks) ──

/** How a task's next fire time is computed. */
export type ScheduleKind = 'once' | 'interval' | 'cron';

/** What the engine does with a fire time that was missed (Pan was asleep). */
export type MisfirePolicy = 'fire_now' | 'skip';

/** Outcome of one scheduled dispatch. */
export type TaskRunStatus = 'dispatched' | 'error' | 'skipped' | 'expired' | 'unknown';

/**
 * Fire-time rule for a scheduled task. All keys that are not used by the
 * selected `kind` stay null so the backend can switch kinds without leftovers.
 */
export interface TaskSchedule {
  kind: ScheduleKind;
  /** kind=once — local naive ISO datetime, e.g. "2026-09-16T09:00:00". */
  at?: string | null;
  /** kind=interval — seconds between fires (>0). */
  intervalSec?: number | null;
  /** kind=interval — anchor datetime; defaults to the task's createdAt. */
  anchor?: string | null;
  /** kind=cron — 5-field expression, e.g. "0 9 * * 1-5". */
  cron?: string | null;
  /** IANA zone name; cron is evaluated against wall-clock time in this zone. */
  timezone?: string | null;
}

/** A scheduled task as returned by `/api/scheduler` (camelCase). */
export interface ScheduledTask {
  id: string;
  name: string;
  /** Session the task text is dispatched to when it fires. */
  targetSessionId: string;
  /** Task text handed to the session's worker. */
  text: string;
  /** Master switch; a disabled task is never scanned. */
  enabled: boolean;
  /** Temporary hold: fires are skipped but the schedule keeps advancing. */
  paused: boolean;
  schedule: TaskSchedule;
  nextFireAt: string | null;
  lastFireAt: string | null;
  lastStatus: TaskRunStatus | null;
  lastError?: string | null;
  runCount: number;
  /** null = unlimited; reaching it auto-disables the task. */
  maxRuns: number | null;
  misfirePolicy: MisfirePolicy;
  createdAt?: string;
  updatedAt?: string;
}

/** Payload of POST /api/scheduler/tasks. */
export interface ScheduledTaskInput {
  name: string;
  targetSessionId: string;
  text: string;
  schedule: TaskSchedule;
  enabled?: boolean;
  maxRuns?: number | null;
  misfirePolicy?: MisfirePolicy;
}

/** Payload of PATCH /api/scheduler/tasks/{id} — any subset of editable fields. */
export type ScheduledTaskPatch = Partial<
  Pick<
    ScheduledTask,
    'name' | 'targetSessionId' | 'text' | 'enabled' | 'maxRuns' | 'misfirePolicy' | 'schedule'
  >
>;

/** One row of `/api/scheduler/tasks/{id}/runs`. */
export interface TaskRun {
  runId: string;
  taskId: string;
  fireAt: string | null;
  actualAt: string | null;
  dispatchKey?: string | null;
  status: TaskRunStatus;
  sessionId?: string | null;
  workerId?: string | null;
  error?: string | null;
}

/** One entry of GET /api/scheduler/next. */
export interface SchedulerNextFire {
  taskId: string;
  fireAt: string | null;
}

/** Engine health from GET /api/scheduler/status. */
export interface SchedulerStatus {
  running: boolean;
  tickSec?: number | null;
  dueScanned?: number | null;
  lastTickAt?: string | null;
}

/** Scheduler endpoints answer `{"ok":false,"error":{"code","message"}}` on
 *  failure; older handlers may still answer a bare `{"error":"string"}`. */
export type SchedulerApiError = ApiErrorInfo | string;

export interface ApiScheduledTasksResponse {
  ok?: boolean;
  tasks?: ScheduledTask[];
  error?: SchedulerApiError;
}

export interface ApiScheduledTaskResponse {
  ok?: boolean;
  task?: ScheduledTask;
  error?: SchedulerApiError;
}

export interface ApiTaskRunsResponse {
  ok?: boolean;
  runs?: TaskRun[];
  error?: SchedulerApiError;
}

export interface ApiSchedulerNextResponse {
  ok?: boolean;
  next?: SchedulerNextFire[];
  error?: SchedulerApiError;
}

export interface ApiSchedulerStatusResponse extends SchedulerStatus {
  ok?: boolean;
  error?: SchedulerApiError;
}

export interface ApiSchedulerActionResponse {
  ok?: boolean;
  error?: SchedulerApiError;
}

// ── Worker info for store ──

export interface WorkerInfo {
  id: string;
  sessionId?: string;
  status: 'idle' | 'running' | 'held' | 'offline';
  generation?: number;
  model?: string;
  name?: string;
  nativeStatus?: {
    type?: string;
    activeFlags?: string[];
    message?: string;
    error?: string;
  };
  /** Latest live Codex token usage snapshot; persisted totals live on Session. */
  nativeUsage?: Record<string, unknown>;
  /** Latest live Codex account rate-limit snapshot. */
  nativeRateLimits?: Record<string, unknown>;
}
