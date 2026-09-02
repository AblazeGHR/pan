/**
 * Mock/no-backend demo layer (URL: ?mock=1).
 *
 * Patches window.fetch for `/api/*` requests with an in-memory session DB so
 * the REAL React app (Sidebar / SessionList / SessionItem / stores) runs
 * unchanged without the Pan server. Drag interactions mutate this DB via the
 * normal store actions; nothing here talks to 8768/8767.
 */

import type { Session } from '@/types';

export function isMockMode(): boolean {
  try {
    return (
      new URLSearchParams(window.location.search).has('mock') ||
      localStorage.getItem('pan:mockDemo') === '1'
    );
  } catch {
    return false;
  }
}

// ── In-memory mock DB ──

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

const mockSessions: Session[] = [
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

const findSession = (id: string) => mockSessions.find((s) => s.id === id);

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
