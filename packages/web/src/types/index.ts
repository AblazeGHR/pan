// ── Data types (matching backend API responses) ──

export interface Message {
  role: string;
  content: string;
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
  workdir?: string;
  workerStatus?: string | null;
  workerId?: string | null;
  /** Id of the managing (parent) session; absent/null means unmanaged. */
  managedBy?: string | null;
  history: Message[];
  historyTruncated?: boolean;
  historyTotal?: number;
  lastResult?: Record<string, unknown> | null;
  totalUsage?: Record<string, number> | null;
  createdAt?: string;
  updatedAt?: string;
}

export interface WorkerEventContent {
  type: string;
  text?: string;
  thinking?: string;
  name?: string;
  input?: Record<string, unknown>;
}

export interface WorkerEvent {
  type: string;
  subtype?: string;
  message?: {
    content?: WorkerEventContent[];
  };
  session_id?: string;
  model?: string;
  is_error?: boolean;
  result?: string;
  cliSessionId?: string;
}

export interface StreamEvent {
  type: string;
  sessionId?: string;
  workerId?: string;
  event?: WorkerEvent;
  message?: string;
  status?: string;
  name?: string;
  cliSessionId?: string;
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
}

export interface ApiSessionHistoryResponse {
  history: Message[];
  total: number;
  hasMore: boolean;
  start: number;
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

// ── Adapter types ──

export interface PermissionMode {
  value: string;
  label: string;
}

export interface AdapterConfig {
  models: string[];
  defaultModel: string;
  effortValues: string[];
  permissionModes: PermissionMode[];
  defaultPermissionMode: string;
  supportedSettings: string[];
}

export interface ApiConfigResponse {
  adapter?: string;
  models: string[];
  defaultModel: string;
  effortValues: string[];
  permissionModes: PermissionMode[];
  defaultPermissionMode?: string;
  supportedSettings?: string[];
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

// ── Worker types ──

export interface WorkerItem {
  workerId: string;
  sessionId: string;
  status: string;
}

export interface ApiWorkerListResponse {
  workers: WorkerItem[];
}

// ── Multi-select types ──

export interface ApiBatchDeleteResponse {
  deleted: number;
  error?: string;
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
  model?: string;
  name?: string;
}
