import type {
  Session,
  ApiSessionsResponse,
  ApiSessionResponse,
  ApiSessionHistoryResponse,
  ApiGenericResponse,
  AdapterConfig,
  ApiConfigResponse,
  ApiAdaptersResponse,
  ApiBatchDeleteResponse,
  SessionTemplate,
  ApiSessionTemplatesResponse,
  CbcProject,
  CbcSessionItem,
  KimiWorkspace,
  KimiSessionItem,
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
  ApiQqSubscribeResponse,
  QqContact,
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

export async function createSession(
  name: string,
  workdir?: string | null,
  adapter?: string,
  sessionTemplate?: string,
): Promise<Session> {
  const body: Record<string, string> = { name, adapter: adapter || 'cbc' };
  if (workdir) body.workdir = workdir;
  if (sessionTemplate) body.sessionTemplate = sessionTemplate;
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
): Promise<ApiQqSubscribeResponse> {
  const data = await request<ApiQqSubscribeResponse>(`${BASE}/qq/subscribe`, {
    method: 'POST',
    body: JSON.stringify({
      sessionId,
      target_type: targetType,
      target_id: targetId,
    }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function qqUnsubscribe(
  sessionId: string,
  targetType: 'user' | 'group',
  targetId: string,
): Promise<ApiQqSubscribeResponse> {
  const data = await request<ApiQqSubscribeResponse>(`${BASE}/qq/unsubscribe`, {
    method: 'POST',
    body: JSON.stringify({
      sessionId,
      target_type: targetType,
      target_id: targetId,
    }),
  });
  if (data.error) throw new Error(data.error);
  return data;
}

export async function fetchQqContacts(): Promise<QqContact[]> {
  const data = await request<ApiQqContactsResponse>(`${BASE}/qq/contacts`);
  if (data.ok === false) {
    throw new Error(data.error?.message || 'Failed to load QQ contacts');
  }
  return data.contacts || [];
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
    permissionModes: data.permissionModes || [],
    defaultPermissionMode: data.defaultPermissionMode || '',
    supportedSettings: data.supportedSettings || [
      'model',
      'permissionMode',
      'thinking',
      'effort',
    ],
  };
}

export async function fetchAdapters(): Promise<ApiAdaptersResponse> {
  return request<ApiAdaptersResponse>(`${BASE}/adapters`);
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
