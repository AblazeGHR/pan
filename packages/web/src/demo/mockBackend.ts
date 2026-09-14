/**
 * Mock/no-backend demo layer (URL: ?mock=1).
 *
 * Patches window.fetch for `/api/*` requests with an in-memory session DB so
 * the REAL React app (Sidebar / SessionList / SessionItem / stores) runs
 * unchanged without the Pan server. Drag interactions mutate this DB via the
 * normal store actions; nothing here talks to 8768/8767.
 */

import type { AgentQueueItem, Session } from '@/types';
import type { ServerFileAttachmentResponse, SessionAttachmentUploadResponse } from '@/services/api';

export function isMockMode(): boolean {
  try {
    return new URLSearchParams(window.location.search).get('mock') === '1';
  } catch {
    return false;
  }
}

// ── Mock DB (seeded once, then persisted so reloads keep demo state) ──

const MOCK_DB_KEY = 'pan:mockSessions';

const now = Date.now();
const min = 60_000;

function mkSession(partial: Partial<Session> & { id: string; name: string }): Session {
  return {
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 3,
    lastMessage: 'mock preview — 无后端演示数据',
    ...partial,
  };
}

function seedSessions(): Session[] {
  return [
    mkSession({
      id: 'mock-alpha',
      name: 'Alpha 主控',
      workerStatus: 'running',
      model: 'mock-model-x',
      workdir: 'D:/project/alpha',
      updatedAt: new Date(now - 2 * min).toISOString(),
      history: [
        { role: 'user', content: 'mock 用户消息' },
        { role: 'assistant', content: 'mock 回复：这是无后端演示数据。' },
        {
          role: 'assistant',
          content: '可拖动这份附件到下方输入框中间： [接口说明.md](/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=mock-alpha)',
        },
      ],
    }),
    mkSession({
      id: 'mock-bravo',
      name: 'Bravo 执行器',
      workerStatus: 'idle',
      model: 'mock-model-y',
      workdir: 'D:/project/bravo',
      updatedAt: new Date(now - 20 * min).toISOString(),
    }),
    mkSession({
      id: 'mock-charlie',
      name: 'Charlie 巡检',
      workerStatus: 'idle',
      workdir: 'D:/project/charlie',
      updatedAt: new Date(now - 3 * 60 * min).toISOString(),
    }),
    mkSession({
      id: 'mock-delta',
      name: 'Delta 报表',
      workerStatus: null,
      workdir: 'D:/project/alpha',
      updatedAt: new Date(now - 26 * 60 * min).toISOString(),
    }),
    mkSession({
      id: 'mock-echo',
      name: 'Echo 文档',
      workerStatus: 'held',
      workdir: 'D:/project/echo',
      managedBy: 'mock-bravo',
      updatedAt: new Date(now - 30 * 60 * min).toISOString(),
    }),
    mkSession({
      id: 'mock-foxtrot',
      name: 'Foxtrot 实验',
      workerStatus: null,
      updatedAt: new Date(now - 50 * 60 * min).toISOString(),
    }),
  ];
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(MOCK_DB_KEY);
    if (raw) {
      const arr: unknown = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length > 0) {
        return arr as Session[];
      }
    }
  } catch {
    // fall through to seeding
  }
  const seeded = seedSessions();
  try {
    localStorage.setItem(MOCK_DB_KEY, JSON.stringify(seeded));
  } catch {
    // storage unavailable — keep in-memory only
  }
  return seeded;
}

export const mockSessions: Session[] = loadSessions();

// The queue is intentionally kept in this frontend-only mock layer.  It has
// the same response shape as the real queue API so the existing queue store
// and SendQueuePanel can exercise the complete "Send -> queued" flow without
// turning the demo into a second backend implementation.
const mockQueues: Record<string, AgentQueueItem[]> = {};
const mockQueueRevisions: Record<string, number> = {};
const mockQueueClientIds: Record<string, Map<string, string>> = {};
let mockQueueSequence = 0;

function decodePathPart(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function queueForSession(sessionId: string): AgentQueueItem[] {
  return mockQueues[sessionId] ?? [];
}

function queueRevisionForSession(sessionId: string): number {
  return mockQueueRevisions[sessionId] ?? 0;
}

function bumpQueueRevision(sessionId: string): number {
  const next = queueRevisionForSession(sessionId) + 1;
  mockQueueRevisions[sessionId] = next;
  return next;
}

function queueResponse(sessionId: string): { ok: true; items: AgentQueueItem[]; queueRevision: number } {
  return {
    ok: true,
    items: queueForSession(sessionId).slice(),
    queueRevision: queueRevisionForSession(sessionId),
  };
}

function clearMockQueues(): void {
  for (const key of Object.keys(mockQueues)) delete mockQueues[key];
  for (const key of Object.keys(mockQueueRevisions)) delete mockQueueRevisions[key];
  for (const key of Object.keys(mockQueueClientIds)) delete mockQueueClientIds[key];
  mockQueueSequence = 0;
}

function persistSessions(): void {
  try {
    localStorage.setItem(MOCK_DB_KEY, JSON.stringify(mockSessions));
  } catch {
    // no-op
  }
}

const findSession = (id: string) => mockSessions.find((s) => s.id === id);

/** Keep the mock DB in sync with local drag/management mutations so a reload
 *  (which re-fetches /api/sessions) reflects what the user just did. */
export function applyMockSessionUpdate(id: string, patch: Partial<Session>): void {
  const session = findSession(id);
  if (session) {
    Object.assign(session, patch);
    persistSessions();
  }
}

/** Reset the demo data to its seeded state (used by the DemoBadge reset). */
export function resetMockData(): void {
  try {
    localStorage.removeItem(MOCK_DB_KEY);
  } catch {
    // no-op
  }
  clearMockQueues();
}

/**
 * UI-only upload simulation used by the direct-file picker in mock mode.
 * Keeping this outside the fetch interceptor matters because the real upload
 * helper uses XMLHttpRequest and must remain untouched in normal mode.
 */
export async function mockUploadSessionAttachment(
  sessionId: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<SessionAttachmentUploadResponse> {
  const wait = (delay: number) => new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('附件上传已取消'));
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', cancel);
      resolve();
    }, delay);
    const cancel = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      reject(new Error('附件上传已取消'));
    };
    signal?.addEventListener('abort', cancel, { once: true });
  });
  const total = file.size;
  onProgress?.(0, total);
  await wait(80);
  onProgress?.(Math.floor(total * 0.55), total);
  await wait(100);
  onProgress?.(total, total);
  const tokenSource = `${sessionId}:${file.name}:${file.size}`;
  let token = '';
  for (let index = 0; index < tokenSource.length; index += 1) {
    token += (tokenSource.charCodeAt(index) % 36).toString(36);
  }
  const token32 = token.padEnd(32, '0').slice(0, 32);
  const extension = file.name.match(/\.[A-Za-z0-9]{1,32}$/)?.[0] || '';
  return {
    ok: true,
    filename: file.name,
    attachmentId: `upload_${token32}${extension}`,
    displayName: file.name,
    storageFilename: `upload_${token32}${extension}`,
    href: `/api/attachments/upload_${token32}${extension}?session_id=${encodeURIComponent(sessionId)}`,
    path: `D:\\mock-uploads\\${sessionId}\\${file.name}`,
    size: file.size,
  };
}

/** Mock-only server-file registration; it never writes a real file or path. */
export async function mockRegisterServerFileAttachment(
  sessionId: string,
  path: string,
): Promise<ServerFileAttachmentResponse> {
  const displayName = path.split(/[\\/]/).pop() || path;
  const token = Array.from(`${sessionId}:${path}`).reduce(
    (value, char) => `${value}${char.charCodeAt(0).toString(36)}`, '',
  ).padEnd(32, '0').slice(0, 32);
  const attachmentId = `att_${token}`;
  return {
    ok: true,
    attachmentId,
    displayName,
    source: 'server_file',
    href: `/api/attachments/ref/${encodeURIComponent(attachmentId)}?session_id=${encodeURIComponent(sessionId)}`,
    path,
    size: 0,
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const cliAdapters = ['cbc', 'kimi', 'opencode', 'codex'].map((name) => ({
  name,
  label: name.toUpperCase(),
  available: true,
  command: [name],
  missing: [],
  hint: '',
  error: null,
}));

/** Route (method, path) → response. Paths have no query string. */
function handleMockRequest(method: string, path: string, body: unknown): unknown {
  // ── Sessions ──
  if (method === 'GET' && path === '/api/sessions') {
    return { sessions: mockSessions };
  }
  const sessionMatch = path.match(/^\/api\/sessions\/([^/]+)$/);
  if (sessionMatch && method === 'GET') {
    return findSession(sessionMatch[1]!) ?? { error: 'not found' };
  }
  if (sessionMatch && method === 'PATCH') {
    const session = findSession(sessionMatch[1]!);
    if (session && body && typeof body === 'object') {
      Object.assign(session, body as Record<string, unknown>);
    }
    return session ?? { error: 'not found' };
  }
  const historyMatch = path.match(/^\/api\/sessions\/([^/]+)\/history$/);
  if (historyMatch && method === 'GET') {
    const session = findSession(historyMatch[1]!);
    const history = session?.history ?? [];
    return { history, hasMore: false, start: 0, total: history.length };
  }

  // ── Agent queue (frontend-only response-compatible mock) ──
  const queueMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue$/);
  if (queueMatch) {
    const sessionId = decodePathPart(queueMatch[1]!);
    if (!findSession(sessionId)) return { ok: false, error: 'not found' };
    if (method === 'GET') return queueResponse(sessionId);
    if (method === 'POST') {
      const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
      const text = typeof values.text === 'string' ? values.text : '';
      const parts = Array.isArray(values.parts) ? values.parts : undefined;
      if (!text.trim()) return { ok: false, error: '消息不能为空' };
      const clientMessageId = typeof values.clientMessageId === 'string'
        ? values.clientMessageId
        : '';
      const knownId = clientMessageId ? mockQueueClientIds[sessionId]?.get(clientMessageId) : undefined;
      if (knownId) {
        const existing = queueForSession(sessionId).find((item) => item.id === knownId);
        if (existing) {
          return {
            ok: true,
            item: existing,
            queueRevision: queueRevisionForSession(sessionId),
            duplicate: true,
          };
        }
      }
      const revision = bumpQueueRevision(sessionId);
      const id = `mock-q-${++mockQueueSequence}`;
      const item: AgentQueueItem = {
        id,
        queueItemId: id,
        kind: 'task',
        text,
        ...(parts ? { parts: parts as AgentQueueItem['parts'] } : {}),
        createdAt: Date.now(),
        source: 'user',
        meta: { dispatchState: 'queued', revision },
      };
      mockQueues[sessionId] = [...queueForSession(sessionId), item];
      if (clientMessageId) {
        const ids = mockQueueClientIds[sessionId] ?? new Map<string, string>();
        ids.set(clientMessageId, id);
        mockQueueClientIds[sessionId] = ids;
      }
      return { ...queueResponse(sessionId), item };
    }
  }

  const queueOrderMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue\/order$/);
  if (queueOrderMatch && method === 'PATCH') {
    const sessionId = decodePathPart(queueOrderMatch[1]!);
    if (!findSession(sessionId)) return { ok: false, error: 'not found' };
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    const orderedIds = Array.isArray(values.orderedIds)
      ? values.orderedIds.filter((id): id is string => typeof id === 'string')
      : [];
    const current = queueForSession(sessionId);
    const byId = new Map(current.map((item) => [item.id, item]));
    const reordered = orderedIds
      .map((id) => byId.get(id))
      .filter((item): item is AgentQueueItem => !!item);
    const included = new Set(reordered.map((item) => item.id));
    const remaining = current.filter((item) => !included.has(item.id));
    const revision = bumpQueueRevision(sessionId);
    mockQueues[sessionId] = [...reordered, ...remaining].map((item) => ({
      ...item,
      meta: { ...item.meta, revision },
    }));
    return queueResponse(sessionId);
  }

  const queueItemMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue\/([^/]+)(\/retry)?$/);
  if (queueItemMatch) {
    const sessionId = decodePathPart(queueItemMatch[1]!);
    const itemId = decodePathPart(queueItemMatch[2]!);
    const current = queueForSession(sessionId);
    const index = current.findIndex((item) => item.id === itemId);
    if (!findSession(sessionId) || index < 0) return { ok: false, error: 'not found' };
    if (queueItemMatch[3] && method === 'POST') {
      const revision = bumpQueueRevision(sessionId);
      const item = {
        ...current[index]!,
        meta: { ...current[index]!.meta, dispatchState: 'queued' as const, revision },
      };
      mockQueues[sessionId] = current.map((candidate, candidateIndex) => candidateIndex === index ? item : candidate);
      return { ...queueResponse(sessionId), item };
    }
    if (method === 'PATCH') {
      const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
      const text = typeof values.text === 'string' ? values.text : current[index]!.text;
      const revision = bumpQueueRevision(sessionId);
      const item = {
        ...current[index]!,
        text,
        meta: { ...current[index]!.meta, revision },
      };
      mockQueues[sessionId] = current.map((candidate, candidateIndex) => candidateIndex === index ? item : candidate);
      return { ...queueResponse(sessionId), item };
    }
    if (method === 'DELETE') {
      bumpQueueRevision(sessionId);
      mockQueues[sessionId] = current.filter((_, candidateIndex) => candidateIndex !== index);
      return queueResponse(sessionId);
    }
  }
  if (path === '/api/sessions/batch-delete' && method === 'POST') {
    return { deleted: 0 };
  }

  // ── Infra stubs (keep banners/settings/modals quiet) ──
  if (path === '/api/cli/status') {
    return { adapters: cliAdapters, available: cliAdapters.map((a) => a.name), hasAvailable: true };
  }
  if (path === '/api/health') {
    return { status: 'mock', version: 'demo' };
  }
  if (path === '/api/list') {
    return { workers: [] };
  }
  if (path === '/api/settings/ui' && method === 'GET') {
    return {};
  }
  if (path === '/api/settings/ui' && method === 'PUT') {
    return body ?? {};
  }
  if (path === '/api/adapters') {
    return {
      adapters: [
        { name: 'cbc', defaultModel: 'mock-model-x', supportsResume: true, supportsFork: false },
      ],
      default: 'cbc',
    };
  }
  if (path === '/api/claim' || path === '/api/unclaim') {
    return { ok: true };
  }
  if (path === '/api/session-templates') {
    return { sessionTemplates: [] };
  }

  // Generic success for anything else the UI probes.
  return { ok: true };
}

/** Install the fetch interceptor. Idempotent; call once at startup. */
export function installMockBackend(): void {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0] ?? '';
    if (!path.startsWith('/api/')) {
      return originalFetch(input, init);
    }
    const method = (init?.method ?? 'GET').toUpperCase();
    let body: unknown = null;
    if (init?.body && typeof init.body === 'string') {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    // Simulate a little latency so spinners/transitions are visible.
    await new Promise((resolve) => setTimeout(resolve, 40));
    return jsonResponse(handleMockRequest(method, path, body));
  };
}
