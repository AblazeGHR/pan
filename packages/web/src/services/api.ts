import type {
  Session,
  ApiSessionsResponse,
  ApiSessionResponse,
  ApiSessionHistoryResponse,
  ApiGenericResponse,
  AdapterConfig,
  ApiConfigResponse,
  ApiConfigReloadResponse,
  ApiAdaptersResponse,
  ApiBatchDeleteResponse,
  SessionTemplate,
  ApiSessionTemplatesResponse,
  CbcProject,
  CbcSessionItem,
  KimiWorkspace,
  KimiSessionItem,
  OpencodeSessionItem,
  SettingsBody,
  WorkerItem,
  ApiWorkerListResponse,
  ApiFsListResponse,
  ApiFsReadResponse,
  ApiFsWriteResponse,
  ApiFsGenericResponse,
  FsEntry,
  ApiClaimResponse,
  ApiReportSubscribeResponse,
  ApiQqContactsResponse,
  ApiQqChannelsResponse,
  ApiQqSubscribeResponse,
  QqContact,
  QqChannelInfo,
  McpServerInfo,
  ApiMcpServersResponse,
  AgentQueueItem,
  ApiSessionQueueResponse,
} from '@/types';

const BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ── Sessions ──

export async function fetchSessions(summary = false): Promise<Session[]> {
  const url = summary ? `${BASE}/sessions?summary=1` : `${BASE}/sessions`;
  const data = await request<ApiSessionsResponse>(url);
  if (data.error) throw new Error(data.error);
  return data.sessions || [];
}

export async function fetchSession(id: string): Promise<Session> {
  const data = await request<ApiSessionResponse>(`${BASE}/sessions/${id}`);
  if (data.error) throw new Error(data.error);
  return data;
}

export async function fetchSessionHistory(
  id: string,
  before: number = 0,
  limit: number = 50,
): Promise<ApiSessionHistoryResponse> {
  const data = await request<ApiSessionHistoryResponse>(
    `${BASE}/sessions/${id}/history?before=${before}&limit=${limit}`,
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export interface CreateSessionSettings {
  model?: string;
  permissionMode?: string;
  alwaysThinkingEnabled?: boolean;
  effort?: string;
  outputMode?: string;
}

export async function createSession(
  name: string,
  workdir?: string | null,
  adapter?: string,
  sessionTemplate?: string,
  settings?: CreateSessionSettings,
): Promise<Session> {
  const body: Record<string, unknown> = { name, adapter: adapter || 'cbc' };
  if (workdir) body.workdir = workdir;
  if (sessionTemplate) body.sessionTemplate = sessionTemplate;
  // Per-adapter settings (backend _create_session applies them). Sent only
  // when provided so the server-side adapter default still applies otherwise.
  if (settings?.model) body.model = settings.model;
  if (settings?.permissionMode) body.permissionMode = settings.permissionMode;
  if (typeof settings?.alwaysThinkingEnabled === 'boolean')
    body.alwaysThinkingEnabled = settings.alwaysThinkingEnabled;
  if (settings?.effort) body.effort = settings.effort;
  if (settings?.outputMode) body.outputMode = settings.outputMode;
  const data = await request<ApiSessionResponse>(`${BASE}/sessions`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function fetchSessionTemplates(): Promise<SessionTemplate[]> {
  const data = await request<ApiSessionTemplatesResponse>(
    `${BASE}/session-templates`,
  );
  if (data.error) throw new Error(data.error);
  return data.sessionTemplates || [];
}

export async function fetchMcpServers(): Promise<McpServerInfo[]> {
  const data = await request<ApiMcpServersResponse>(`${BASE}/mcp/servers`);
  // `loaded: false` means the manifest isn't loaded yet — return empty rather
  // than throwing, so the modal can show an explanatory empty state.
  if (!data.loaded) return [];
  return data.servers || [];
}

export async function patchSession(
  id: string,
  settings: SettingsBody,
): Promise<Session> {
  const data = await request<ApiSessionResponse>(`${BASE}/sessions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(settings),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function deleteSession(id: string): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(`${BASE}/sessions/${id}`, {
    method: 'DELETE',
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function batchDeleteSessions(
  sessionIds: string[],
): Promise<ApiBatchDeleteResponse> {
  const data = await request<ApiBatchDeleteResponse>(
    `${BASE}/sessions/batch-delete`,
    {
      method: 'POST',
      body: JSON.stringify({ sessionIds }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function renameSession(
  id: string,
  name: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/sessions/${id}/rename`,
    {
      method: 'POST',
      body: JSON.stringify({ name }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function branchSession(
  id: string,
  name: string,
): Promise<Session> {
  const data = await request<ApiSessionResponse>(
    `${BASE}/sessions/${id}/branch`,
    {
      method: 'POST',
      body: JSON.stringify({ name }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── Agent queue (session.queue_pending, normalized) ──

export async function fetchSessionQueue(
  sessionId: string,
): Promise<AgentQueueItem[]> {
  const data = await request<ApiSessionQueueResponse>(
    `${BASE}/sessions/${sessionId}/queue`,
  );
  if (data.error) throw new Error(data.error);
  return data.items || [];
}

export async function deleteSessionQueueItem(
  sessionId: string,
  itemId: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse & { ok?: boolean }>(
    `${BASE}/sessions/${sessionId}/queue/${itemId}`,
    { method: 'DELETE' },
  );
  if (data.ok === false || data.error) {
    throw new Error(data.error || 'Delete failed');
  }
  return data;
}

export async function reorderSessionQueue(
  sessionId: string,
  order: string[],
): Promise<AgentQueueItem[]> {
  const data = await request<ApiSessionQueueResponse>(
    `${BASE}/sessions/${sessionId}/queue/order`,
    {
      method: 'PATCH',
      body: JSON.stringify({ order }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data.items || [];
}

// ── Session management (claim / unclaim) ──

export async function claimSession(
  managerId: string,
  sessionId: string,
): Promise<ApiClaimResponse> {
  const data = await request<ApiClaimResponse>(`${BASE}/claim`, {
    method: 'POST',
    body: JSON.stringify({ managerId, sessionId }),
  });
  if (data.ok === false) {
    throw new Error(data.error?.message || 'Claim failed');
  }
  return data;
}

export async function unclaimSession(
  managerId: string,
  sessionId: string,
): Promise<ApiClaimResponse> {
  const data = await request<ApiClaimResponse>(`${BASE}/unclaim`, {
    method: 'POST',
    body: JSON.stringify({ managerId, sessionId }),
  });
  if (data.ok === false) {
    throw new Error(data.error?.message || 'Unclaim failed');
  }
  return data;
}

// ── Report subscription (meta-agent subscribes to managed-session reports) ──

export async function reportSubscribe(
  managerId: string,
  sessionId: string,
): Promise<ApiReportSubscribeResponse> {
  const data = await request<ApiReportSubscribeResponse>(
    `${BASE}/report-subscribe`,
    {
      method: 'POST',
      body: JSON.stringify({ managerId, sessionId }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function reportUnsubscribe(
  managerId: string,
  sessionId: string,
): Promise<ApiReportSubscribeResponse> {
  const data = await request<ApiReportSubscribeResponse>(
    `${BASE}/report-unsubscribe`,
    {
      method: 'POST',
      body: JSON.stringify({ managerId, sessionId }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── QQ postbox (subscribe inbox reminders) ──

export async function qqSubscribe(
  sessionId: string,
  targetType: 'user' | 'group',
  targetId: string,
  botUin?: string,
): Promise<ApiQqSubscribeResponse> {
  const data = await request<ApiQqSubscribeResponse>(`${BASE}/qq/subscribe`, {
    method: 'POST',
    body: JSON.stringify({
      sessionId,
      target_type: targetType,
      target_id: targetId,
      ...(botUin ? { bot_uin: botUin } : {}),
    }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function qqUnsubscribe(
  sessionId: string,
  targetType: 'user' | 'group',
  targetId: string,
  botUin?: string,
): Promise<ApiQqSubscribeResponse> {
  const data = await request<ApiQqSubscribeResponse>(`${BASE}/qq/unsubscribe`, {
    method: 'POST',
    body: JSON.stringify({
      sessionId,
      target_type: targetType,
      target_id: targetId,
      ...(botUin ? { bot_uin: botUin } : {}),
    }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function fetchQqContacts(botUin?: string): Promise<QqContact[]> {
  const qs = botUin ? `?bot_uin=${encodeURIComponent(botUin)}` : '';
  const data = await request<ApiQqContactsResponse>(`${BASE}/qq/contacts${qs}`);
  if (data.ok === false) {
    throw new Error(data.error?.message || 'Failed to load QQ contacts');
  }
  return data.contacts || [];
}

export async function fetchQqChannels(): Promise<QqChannelInfo[]> {
  const data = await request<ApiQqChannelsResponse>(`${BASE}/qq/channels`);
  if (data.ok === false) {
    throw new Error(data.error?.message || 'Failed to load QQ channels');
  }
  return data.channels || [];
}

// ── Workers ──

export async function spawnWorker(
  sessionId: string,
  settings?: SettingsBody,
): Promise<ApiGenericResponse> {
  const body: Record<string, unknown> = { sessionId };
  if (settings) Object.assign(body, settings);
  const data = await request<ApiGenericResponse>(`${BASE}/spawn`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function killWorker(workerId: string): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(`${BASE}/kill/${workerId}`, {
    method: 'POST',
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function restartWorker(
  workerId: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/restart`,
    { method: 'POST' },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function workerSettings(
  workerId: string,
  settings: SettingsBody,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/settings`,
    {
      method: 'POST',
      body: JSON.stringify(settings),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function interruptWorker(
  workerId: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/interrupt`,
    { method: 'POST' },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function steerWorker(
  workerId: string,
  text: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/steer`,
    {
      method: 'POST',
      body: JSON.stringify({ text }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function sendWorkerControl(
  workerId: string,
  control: Record<string, unknown>,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/control`,
    {
      method: 'POST',
      body: JSON.stringify({ control }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function takeoverWorker(
  workerId: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/takeover`,
    { method: 'POST' },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function workerBranch(
  workerId: string,
  name: string,
): Promise<ApiGenericResponse> {
  const data = await request<ApiGenericResponse>(
    `${BASE}/worker/${workerId}/branch`,
    {
      method: 'POST',
      body: JSON.stringify({ name }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

export async function listWorkers(): Promise<WorkerItem[]> {
  const data = await request<ApiWorkerListResponse>(`${BASE}/list`);
  return data.workers || [];
}

// ── Configuration ──

export async function fetchAdapterConfig(
  adapter: string,
): Promise<AdapterConfig> {
  const data = await request<ApiConfigResponse>(
    `${BASE}/adapter/config?adapter=${encodeURIComponent(adapter)}`,
  );
  return {
    models: data.models || [],
    defaultModel: data.defaultModel || 'deepseek-v4-flash',
    effortValues: data.effortValues || [],
    modelEfforts: data.modelEfforts || {},
    permissionModes: data.permissionModes || [],
    defaultPermissionMode: data.defaultPermissionMode || '',
    supportedSettings: data.supportedSettings || [
      'model',
      'permissionMode',
      'thinking',
      'effort',
    ],
    executionModes: data.executionModes || ['stream'],
  };
}

export async function fetchAdapters(): Promise<ApiAdaptersResponse> {
  return request<ApiAdaptersResponse>(`${BASE}/adapters`);
}

// ── Config hot-reload ──

/**
 * Force a config.json hot-reload without restarting the server.
 * scope "adapters": invalidate all adapters' model-list caches;
 * scope "worker": re-read worker lifecycle timeouts;
 * scope "plugin": reload the plugin_manifests list (add/remove manifests);
 * scope "memory": re-read the memory.enabled injection switch;
 * scope "all": everything above (server default).
 */
export async function reloadConfig(
  scope: 'adapters' | 'worker' | 'plugin' | 'memory' | 'all',
): Promise<ApiConfigReloadResponse> {
  const data = await request<ApiConfigReloadResponse>(
    `${BASE}/config/reload`,
    {
      method: 'POST',
      body: JSON.stringify({ scope }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── Import: cbc ──

export async function fetchCbcProjects(): Promise<CbcProject[]> {
  const data = await request<{ projects: CbcProject[] }>(
    `${BASE}/cbc/projects`,
  );
  return data.projects || [];
}

export async function fetchCbcSessions(
  projectDir: string,
): Promise<CbcSessionItem[]> {
  const data = await request<{ sessions: CbcSessionItem[] }>(
    `${BASE}/cbc/sessions?project_dir=${encodeURIComponent(projectDir)}`,
  );
  return data.sessions || [];
}

export async function importCbcSession(
  sessionId: string,
  projectDir: string,
): Promise<Session> {
  const data = await request<ApiSessionResponse>(
    `${BASE}/cbc/sessions/import`,
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, project_dir: projectDir }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── Import: kimi ──

export async function fetchKimiWorkspaces(): Promise<KimiWorkspace[]> {
  const data = await request<{ workspaces: KimiWorkspace[] }>(
    `${BASE}/kimi/workspaces`,
  );
  return data.workspaces || [];
}

export async function fetchKimiSessions(
  cwd: string,
): Promise<KimiSessionItem[]> {
  const data = await request<{ sessions: KimiSessionItem[] }>(
    `${BASE}/kimi/sessions?cwd=${encodeURIComponent(cwd)}`,
  );
  return data.sessions || [];
}

export async function importKimiSession(
  sessionId: string,
  cwd: string,
): Promise<Session> {
  const data = await request<ApiSessionResponse>(
    `${BASE}/kimi/sessions/import`,
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, cwd }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── Import: opencode ──

export async function fetchOpencodeSessions(
  cwd: string,
): Promise<OpencodeSessionItem[]> {
  const data = await request<{ sessions: OpencodeSessionItem[] }>(
    `${BASE}/opencode/sessions?cwd=${encodeURIComponent(cwd)}`,
  );
  return data.sessions || [];
}

export async function importOpencodeSession(
  sessionId: string,
  cwd: string,
): Promise<Session> {
  const data = await request<ApiSessionResponse>(
    `${BASE}/opencode/sessions/import`,
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, cwd }),
    },
  );
  if (data.error) throw new Error(data.error);
  return data;
}

// ── Reimport ──

export async function reimportSession(
  _id: string,
  adapter: string,
  cliSessionId: string,
  workdir?: string,
): Promise<Session> {
  const url =
    adapter === 'kimi'
      ? `${BASE}/kimi/sessions/import`
      : adapter === 'opencode'
        ? `${BASE}/opencode/sessions/import`
        : `${BASE}/cbc/sessions/import`;
  const body: Record<string, string> = { session_id: cliSessionId };
  if (workdir) body.cwd = workdir;
  const data = await request<ApiSessionResponse>(url, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

// ── File-system operations ──

export async function listFiles(
  sessionId: string,
  path: string = '',
  includeHidden: boolean = false,
): Promise<FsEntry[]> {
  const params = new URLSearchParams({ session_id: sessionId, path });
  if (includeHidden) params.set('include_hidden', 'true');
  const data = await request<ApiFsListResponse>(
    `${BASE}/fs/list?${params.toString()}`,
  );
  if (data.error) throw new Error(data.error);
  return data.entries || [];
}

export async function readFile(
  sessionId: string,
  path: string,
): Promise<string> {
  const params = new URLSearchParams({ session_id: sessionId, path });
  const data = await request<ApiFsReadResponse>(
    `${BASE}/fs/read?${params.toString()}`,
  );
  if (data.error) throw new Error(data.error);
  return data.content;
}

export async function writeFile(
  sessionId: string,
  path: string,
  content: string,
): Promise<ApiFsWriteResponse> {
  const data = await request<ApiFsWriteResponse>(`${BASE}/fs/write`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, path, content }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function renameFs(
  sessionId: string,
  from: string,
  to: string,
): Promise<ApiFsGenericResponse> {
  const data = await request<ApiFsGenericResponse>(`${BASE}/fs/rename`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, from, to }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function deleteFs(
  sessionId: string,
  path: string,
): Promise<ApiFsGenericResponse> {
  const data = await request<ApiFsGenericResponse>(`${BASE}/fs/delete`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, path }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

// ── App settings (config.json ui) ──

export async function fetchUiSettings(): Promise<Record<string, unknown>> {
  const data = await request<Record<string, unknown>>(`${BASE}/settings/ui`);
  return data || {};
}

export async function updateUiSettings(
  patch: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const data = await request<Record<string, unknown>>(`${BASE}/settings/ui`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  });
  if (data.error) throw new Error(String(data.error));
  return data;
}
