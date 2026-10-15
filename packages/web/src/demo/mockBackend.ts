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
const mockAgentReportsPaused: Record<string, boolean> = {};
const mockQueueClientIds: Record<string, Map<string, string>> = {};
let mockQueueSequence = 0;

// Explicit task reports recorded by the mock, mirroring the server's
// deterministic (session, task, message) idempotency so a browser retry can be
// observed as exactly one delivery.
const mockManualReports: Array<{
  /** Server-identical deterministic report key. */
  key: string;
  sessionId: string;
  taskId: string | null;
  messageId: string;
  at: number;
}> = [];

// Every manual-report attempt the mock handled, with its server-side outcome.
// `delivered`/`replayed` prove what a lost response would have confirmed;
// `rejected`/`dropped` are the failure simulations.  Exposed on window for
// isolated browser tests (the mock short-circuits fetch in-page, so the
// network layer cannot observe these requests).
const mockManualReportAttempts: Array<{
  sessionId: string;
  taskId: string | null;
  taskSeq: number | null;
  resultCursor: number | null;
  messageId: string;
  outcome: 'delivered' | 'replayed' | 'rejected' | 'dropped' | 'dropped_after_delivery';
}> = [];

declare global {
  interface Window {
    /** Test hook for the mock manual-report route: 'reject' returns a definite
     *  server failure, 'drop' fails the transport before recording anything
     *  (unconfirmed outcome), 'lose_receipt' records the delivery and then
     *  fails the transport (the already-sent-but-unconfirmed case). */
    __panMockManualReport?: { mode?: 'reject' | 'drop' | 'lose_receipt' };
    /** Test-observable log of manual-report attempts and deliveries. */
    __panMockManualReportLog?: {
      attempts: typeof mockManualReportAttempts;
      deliveries: typeof mockManualReports;
    };
  }
}

if (typeof window !== 'undefined') {
  window.__panMockManualReportLog = {
    attempts: mockManualReportAttempts,
    deliveries: mockManualReports,
  };
}

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

function withMockQueueLock(
  item: AgentQueueItem,
  manual?: boolean,
  automatic?: boolean,
): AgentQueueItem {
  const lockManual = manual ?? item.meta?.lockManual ?? false;
  const lockAutoReport = automatic ?? item.meta?.lockAutoReport ?? false;
  return {
    ...item,
    meta: { ...item.meta, lockManual, lockAutoReport, locked: lockManual || lockAutoReport },
  };
}

function queueRevisionForSession(sessionId: string): number {
  return mockQueueRevisions[sessionId] ?? 0;
}

function bumpQueueRevision(sessionId: string): number {
  const next = queueRevisionForSession(sessionId) + 1;
  mockQueueRevisions[sessionId] = next;
  return next;
}

function queueResponse(sessionId: string): {
  ok: true;
  items: AgentQueueItem[];
  queueRevision: number;
  agentReportsPaused: boolean;
} {
  return {
    ok: true,
    items: queueForSession(sessionId).slice(),
    queueRevision: queueRevisionForSession(sessionId),
    agentReportsPaused: mockAgentReportsPaused[sessionId] ?? false,
  };
}

function clearMockQueues(): void {
  for (const key of Object.keys(mockQueues)) delete mockQueues[key];
  for (const key of Object.keys(mockQueueRevisions)) delete mockQueueRevisions[key];
  for (const key of Object.keys(mockAgentReportsPaused)) delete mockAgentReportsPaused[key];
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

export function applyMockPinnedOrder(sessionIds: string[], pinRevision: number): void {
  const orderById = new Map(sessionIds.map((id, index) => [id, index]));
  for (const session of mockSessions) {
    const pinOrder = orderById.get(session.id);
    session.pinned = pinOrder !== undefined;
    session.pinOrder = pinOrder ?? null;
    session.pinRevision = pinRevision;
  }
  persistSessions();
}

/** Reset the demo data to its seeded state (used by the DemoBadge reset). */
export function resetMockData(): void {
  try {
    localStorage.removeItem(MOCK_DB_KEY);
  } catch {
    // no-op
  }
  clearMockQueues();
  mockManualReports.length = 0;
  mockManualReportAttempts.length = 0;
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
    // Mirror the real list read: the summary carries no history rows.  Clients
    // load the transcript through the history endpoint, exactly like against a
    // real backend.
    return {
      sessions: mockSessions.map((session) => ({
        ...session,
        // `undefined` disappears in the JSON response, mirroring the real list
        // read where the summary carries no history rows.
        history: undefined,
      })),
    };
  }
  if (method === 'POST' && path === '/api/sessions/pins/order') {
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    const requested = Array.isArray(values.sessionIds)
      ? values.sessionIds.filter((id): id is string => typeof id === 'string')
      : [];
    const current = mockSessions
      .filter((session) => session.pinned)
      .sort((a, b) => (a.pinOrder ?? Number.MAX_SAFE_INTEGER) - (b.pinOrder ?? Number.MAX_SAFE_INTEGER))
      .map((session) => session.id);
    if (requested.some((id) => !current.includes(id)) || new Set(requested).size !== requested.length) {
      return { ok: false, error: { message: 'Invalid pinned Session order' } };
    }
    const selected = new Set(requested);
    const slots = current.flatMap((id, index) => selected.has(id) ? [index] : []);
    const ordered = current.slice();
    slots.forEach((slot, index) => { ordered[slot] = requested[index]!; });
    const revision = Math.max(0, ...mockSessions.map((session) => session.pinRevision ?? 0)) + 1;
    applyMockPinnedOrder(ordered, revision);
    return { ok: true, pinRevision: revision, sessionIds: ordered };
  }
  const pinMatch = path.match(/^\/api\/sessions\/([^/]+)\/pin$/);
  if (pinMatch && method === 'POST') {
    const sessionId = decodePathPart(pinMatch[1]!);
    const session = findSession(sessionId);
    if (!session) return { ok: false, error: { message: 'Session not found' } };
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    const pinned = values.pinned === true;
    const ordered = mockSessions
      .filter((item) => item.pinned && item.id !== sessionId)
      .sort((a, b) => (a.pinOrder ?? Number.MAX_SAFE_INTEGER) - (b.pinOrder ?? Number.MAX_SAFE_INTEGER))
      .map((item) => item.id);
    if (pinned) ordered.push(sessionId);
    const revision = Math.max(0, ...mockSessions.map((item) => item.pinRevision ?? 0)) + 1;
    applyMockPinnedOrder(ordered, revision);
    return {
      ok: true,
      pinned,
      pinOrder: pinned ? ordered.indexOf(sessionId) : null,
      pinRevision: revision,
      sessionIds: ordered,
    };
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
    return {
      history,
      hasMore: false,
      start: 0,
      // The stored history may model a truncated client window while the
      // session's own total/revision describe the full durable history.
      total: session?.historyTotal ?? history.length,
      // Same identity fields the real history page carries: the client window
      // needs the epoch/revision to prove terminal boundaries.
      historyEpoch: session?.historyEpoch ?? null,
      historyRevision: session?.historyRevision ?? 0,
    };
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
      if (values.locked !== undefined && typeof values.locked !== 'boolean') {
        return { ok: false, error: 'locked must be a boolean' };
      }
      const locked = values.locked === true;
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
        meta: { dispatchState: 'queued', revision,
          locked, lockManual: locked, lockAutoReport: false },
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

  const queueReportsPausedMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue\/reports-paused$/);
  if (queueReportsPausedMatch && method === 'PATCH') {
    const sessionId = decodePathPart(queueReportsPausedMatch[1]!);
    if (!findSession(sessionId)) return { ok: false, error: 'not found' };
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    if (typeof values.paused !== 'boolean') return { ok: false, error: 'paused must be a boolean' };
    mockAgentReportsPaused[sessionId] = values.paused;
    mockQueues[sessionId] = queueForSession(sessionId).map((item) =>
      item.kind === 'report' && item.source === 'report'
        ? withMockQueueLock(item, undefined, values.paused as boolean)
        : item);
    bumpQueueRevision(sessionId);
    return queueResponse(sessionId);
  }

  const queueLocksMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue\/locks$/);
  if (queueLocksMatch && method === 'PATCH') {
    const sessionId = decodePathPart(queueLocksMatch[1]!);
    if (!findSession(sessionId)) return { ok: false, error: 'not found' };
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    if (typeof values.locked !== 'boolean') return { ok: false, error: 'locked must be a boolean' };
    mockQueues[sessionId] = queueForSession(sessionId).map((item) =>
      withMockQueueLock(item, values.locked as boolean, values.locked ? undefined : false));
    bumpQueueRevision(sessionId);
    return queueResponse(sessionId);
  }

  const queueItemLockMatch = path.match(/^\/api\/sessions\/([^/]+)\/queue\/([^/]+)\/lock$/);
  if (queueItemLockMatch && method === 'PATCH') {
    const sessionId = decodePathPart(queueItemLockMatch[1]!);
    const itemId = decodePathPart(queueItemLockMatch[2]!);
    if (!findSession(sessionId)) return { ok: false, error: 'not found' };
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    if (typeof values.locked !== 'boolean') return { ok: false, error: 'locked must be a boolean' };
    const current = queueForSession(sessionId);
    if (!current.some((item) => item.id === itemId)) return { ok: false, error: 'not found' };
    mockQueues[sessionId] = current.map((item) => item.id === itemId
      ? withMockQueueLock(item, values.locked as boolean, values.locked ? undefined : false)
      : item);
    bumpQueueRevision(sessionId);
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

  // ── Explicit task report (mirrors POST /api/sessions/{id}/manual-report) ──
  const manualReportMatch = path.match(/^\/api\/sessions\/([^/]+)\/manual-report$/);
  if (manualReportMatch && method === 'POST') {
    const sessionId = decodePathPart(manualReportMatch[1]!);
    const session = findSession(sessionId);
    if (!session) {
      return { ok: false, error: { code: 'session_not_found', message: 'Session not found' } };
    }
    const values = body && typeof body === 'object' ? body as Record<string, unknown> : {};
    const messageId = values.messageId;
    const taskId = values.taskId;
    const taskSeq = values.taskSeq;
    const resultCursor = values.resultCursor ?? null;
    if (typeof messageId !== 'string' || !messageId) {
      return { ok: false, error: { code: 'invalid_message_id', message: 'messageId is required' } };
    }
    if (taskId !== null && (typeof taskId !== 'string' || !taskId)) {
      return { ok: false, error: { code: 'invalid_task_id', message: 'taskId must be a string or null' } };
    }
    if (taskSeq !== null && (!Number.isInteger(taskSeq) || (taskSeq as number) < 0)) {
      return { ok: false, error: { code: 'invalid_task_seq', message: 'taskSeq must be a non-negative integer or null' } };
    }
    if (resultCursor !== null && (!Number.isInteger(resultCursor) || (resultCursor as number) < 0)) {
      return { ok: false, error: { code: 'invalid_result_cursor', message: 'resultCursor must be a non-negative integer or null' } };
    }
    const deliveredTaskId = taskId as string | null;
    const deliveredTaskSeq = taskSeq === null ? null : taskSeq as number;
    const deliveredResultCursor = resultCursor === null ? null : resultCursor as number;
    const attempt = {
      sessionId,
      taskId: deliveredTaskId,
      taskSeq: deliveredTaskSeq,
      resultCursor: deliveredResultCursor,
      messageId,
      outcome: 'dropped' as typeof mockManualReportAttempts[number]['outcome'],
    };
    mockManualReportAttempts.push(attempt);
    const hook = window.__panMockManualReport;
    if (hook?.mode === 'reject') {
      attempt.outcome = 'rejected';
      return { ok: false, retryable: true, error: { code: 'persist_failed', message: 'mock 汇报失败' } };
    }
    if (hook?.mode === 'drop') {
      // The outcome is unknown: nothing was recorded before the transport
      // failed, so the caller cannot tell send from non-send.
      attempt.outcome = 'dropped';
      throw new TypeError('Failed to fetch');
    }
    // Resolve the run's durable terminal the way the server does, never by
    // "newest": resultCursor is exact-first (legacy rows without the field
    // fall back to a taskSeq-equal cursor), then taskSeq, then a taskId's
    // latest run.  No identity at all is a definite refusal.
    const candidates = (session.terminalResults ?? []).filter(
      (terminal): terminal is NonNullable<typeof terminal> => !!terminal,
    );
    let run: (typeof candidates)[number] | undefined;
    if (deliveredResultCursor !== null) {
      const exact = candidates.filter((terminal) => terminal.resultCursor === deliveredResultCursor);
      const legacy = exact.length === 0
        ? candidates.filter((terminal) => terminal.resultCursor === undefined
          && terminal.taskSeq === deliveredResultCursor)
        : [];
      const match = exact.length === 1 ? exact[0] : legacy.length === 1 ? legacy[0] : undefined;
      if (!match) {
        attempt.outcome = 'rejected';
        return { ok: false, error: { code: 'no_task_result', message: '该运行的终态结果不可用，无法汇报' } };
      }
      const matchTaskId = typeof match.taskId === 'string' && match.taskId ? match.taskId : null;
      // Compare taskId strictly INCLUDING null: a client saying "plain run"
      // (taskId null) that resolves a task run describes a different run.
      const conflict = deliveredTaskId !== matchTaskId
        || (deliveredTaskSeq !== null && typeof match.taskSeq === 'number'
          && match.taskSeq !== deliveredTaskSeq);
      if (conflict) {
        attempt.outcome = 'rejected';
        return { ok: false, error: { code: 'run_mismatch', message: '运行身份与 resultCursor 不一致，无法汇报' } };
      }
      run = match;
    } else if (deliveredTaskSeq !== null || deliveredTaskId !== null) {
      const runs = candidates.filter((terminal) =>
        (typeof terminal.taskId === 'string' && terminal.taskId ? terminal.taskId : null) === deliveredTaskId
        && (deliveredTaskSeq === null || terminal.taskSeq === deliveredTaskSeq));
      run = runs[runs.length - 1];
    } else {
      attempt.outcome = 'rejected';
      return { ok: false, error: { code: 'no_run_identity', message: '缺少运行身份（taskSeq/resultCursor），无法汇报' } };
    }
    if (!run) {
      attempt.outcome = 'rejected';
      return { ok: false, error: { code: 'no_task_result', message: '该运行的终态结果不可用，无法汇报' } };
    }
    // Mirror the server's durable tail-block proof: within an unchanged history
    // epoch the run's final row offset is total - (revision - boundary).
    const history = session.history ?? [];
    const boundary = typeof run.historyRevision === 'number'
      && run.historyEpoch === session.historyEpoch
      ? history.length - ((session.historyRevision ?? 0) - run.historyRevision)
      : -1;
    let tailBlockId: string | null = null;
    if (boundary >= 0 && boundary <= history.length) {
      for (let index = Math.min(boundary, history.length - 1); index >= 0; index -= 1) {
        const row = history[index];
        if (row?.role === 'assistant' && typeof row.messageId === 'string') {
          tailBlockId = row.messageId;
          break;
        }
      }
    }
    if (tailBlockId === null) {
      attempt.outcome = 'rejected';
      return { ok: false, error: { code: 'block_unproven', message: '无法证明该消息属于该任务的末块，暂不能汇报' } };
    }
    if (tailBlockId !== messageId) {
      attempt.outcome = 'rejected';
      return { ok: false, error: { code: 'block_mismatch', message: '提交的消息不是该任务的末块，无法汇报' } };
    }
    // Dedup is keyed on the resolved run identity only: a client message id
    // can never turn one run into several deliveries, and a cursor-only plain
    // run keys by its durable cursor exactly like the server does.
    const resolvedTaskId = typeof run.taskId === 'string' && run.taskId ? run.taskId : null;
    const runKey = run.taskSeq ?? (typeof run.resultCursor === 'number' ? `rc:${run.resultCursor}` : '-');
    const key = `manual:${sessionId}:${resolvedTaskId ?? '-'}:${runKey}`;
    const alreadyReported = mockManualReports.some((report) => report.key === key);
    if (!alreadyReported) {
      mockManualReports.push({ key, sessionId, taskId: resolvedTaskId, messageId, at: Date.now() });
    }
    attempt.outcome = alreadyReported ? 'replayed' : 'delivered';
    if (hook?.mode === 'lose_receipt') {
      // Delivered, but the response never reaches the caller: a retry must be
      // deduplicated by identity instead of sending a second report.
      attempt.outcome = 'dropped_after_delivery';
      throw new TypeError('Failed to fetch');
    }
    return {
      ok: true,
      sessionId,
      managerId: session.managedBy ?? 'mock-manager',
      queueItemId: `mock-q-${mockManualReports.length}`,
      alreadyReported,
    };
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
  if (path === '/api/data/catalog' && method === 'GET') {
    return {
      categories: [],
      notice: 'Demo mode does not expose filesystem paths; connect to a Pan backend to view its registered storage locations.',
      jobsRetention: {
        slot: 'jobs-retention-control',
        status: 'reserved',
        message: 'Jobs retention controls will follow the Jobs API field contract.',
      },
    };
  }
  if (path === '/api/settings/data-retention' && method === 'GET') {
    const policies = {
      sessions: { enabled: false, days: null },
      attachments: { enabled: false, days: null },
      qq_history: { enabled: false, days: null },
      qq_media: { enabled: false, days: null },
      pan_logs: { enabled: false, days: null },
    };
    const lastScans = Object.fromEntries(Object.keys(policies).map((key) => [key, {
      scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null,
    }]));
    return { policies, configKey: 'data_retention', lastScans };
  }
  if (path === '/api/settings/data-retention' && method === 'PUT') {
    const submitted = body && typeof body === 'object' && !Array.isArray(body)
      ? body as { policies?: Record<string, { enabled: boolean; days: number | null }> }
      : {};
    return {
      policies: submitted.policies ?? {
        sessions: { enabled: false, days: null },
        attachments: { enabled: false, days: null },
        qq_history: { enabled: false, days: null },
        qq_media: { enabled: false, days: null },
        pan_logs: { enabled: false, days: null },
      },
      configKey: 'data_retention',
      lastScans: {
        sessions: { scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null },
        attachments: { scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null },
        qq_history: { scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null },
        qq_media: { scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null },
        pan_logs: { scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null },
      },
    };
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
