// ── Data types (matching backend API responses) ──

import type { AttachmentLocation } from './attachment';

export type { AttachmentLocation } from './attachment';

export interface Message {
  role: string;
  content: string;
  /** Stable canonical history identity. Absent on legacy rows. */
  messageId?: string;
  /** Stable provider block identity for compound messages. */
  blockId?: string;
  /** Stable provider turn identity when the adapter exposes one. */
  turnId?: string;
  /** Server-canonical parts; content remains the adapter/legacy fallback. */
  parts?: MessagePart[];
  /** Transient native Codex identity used to merge live Codex messages. */
  nativeItemId?: string;
  /** Queue item(s) whose local CLI hand-off produced this user message. */
  queueItemIds?: string[];
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
  /** Last Worker state confirmed through an explicit Pan lifecycle action. */
  lastLegalWorkerState?: string | null;
  /** Id of the managing (parent) session; absent/null means unmanaged. */
  managedBy?: string | null;
  /** True when the managing session has blocked outbound operations to this session. */
  readonlySession?: boolean;
  /** Ids of sessions this session manages (claims as a meta-agent). */
  managed?: string[];
  /**
   * Durable workspace memberships. Product rule is SINGLE membership: the UI
   * only ever writes at most one id here; an empty/missing array means the
   * session is ungrouped. Old sessions may omit the field entirely.
   */
  workspaceIds?: string[];
  /** Managed-session report subscriptions (ids this session gets reports from). */
  reportSubscriptions?: string[];
  /** QQ inbox subscriptions, each formatted "user:<uin>" or "group:<uin>". */
  qqSubscriptions?: string[];
  notificationSettings?: { browser: boolean; system: boolean };
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
  notification?: {
    title?: string;
    body?: string;
    browser?: boolean;
    system?: Record<string, unknown> | null;
  };
  cancelled?: boolean;
  taskSeq?: number;
  /** Durable task identity carried by worker.stream for late-frame isolation. */
  taskId?: string | null;
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

// ── Session template types ──

export interface SessionTemplate {
  name: string;
  adapter?: string;
  model?: string | null;
  mcpServers?: string[];
  /** Absolute path of the plugin dir whose manifest.json defined this template. */
  sourceManifest?: string;
  /** Short readable manifest label, e.g. "packages/mcp/manifest.json". */
  sourceManifestLabel?: string;
  system_prompt_preview?: string;
}

export interface ApiSessionTemplatesResponse {
  sessionTemplates?: SessionTemplate[];
  total?: number;
  error?: string;
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
  phase?: 'requested' | 'stopping' | 'stopped' | 'starting' | 'ready' | 'failed' | 'timed_out';
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

/** 编辑中的队列项（先从队列取出，避免被自动 flush 发出）。 */
export interface QueuedEdit {
  id: string;
  /** 编辑框当前值（持久化，刷新恢复编辑态）。 */
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
}

// ── Agent queue (backend session.queue_pending, normalized) ──

export type AgentQueueKind = 'task' | 'report' | 'qq';
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
  };
}

export interface ApiSessionQueueResponse {
  items: AgentQueueItem[];
  queueRevision?: number;
  error?: string;
  ok?: boolean;
}

// ── UI types ──

export interface ToastMessage {
  id: string;
  message: string;
  type: 'info' | 'error';
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
