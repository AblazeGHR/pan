// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiRequestError,
  createSession,
  fetchSessionHistory,
  fetchHistorySearch,
  importCbcSession,
  importCodexSession,
  importKimiSession,
  importOpencodeSession,
  reimportSession,
  steerSessionWorker,
  uploadSessionAttachment,
  fetchJobs,
  fetchCompletedJobRetentionSettings,
  updateCompletedJobRetentionSettings,
  invokePluginPanel,
  pluginPanelFailure,
  PluginPanelError,
} from './api';

class FakeXMLHttpRequest {
  static instances: FakeXMLHttpRequest[] = [];

  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  status = 0;
  statusText = '';
  responseText = '';
  aborted = false;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn(() => { FakeXMLHttpRequest.instances.push(this); });

  abort() {
    this.aborted = true;
    this.onabort?.();
  }
}

describe('uploadSessionAttachment', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeXMLHttpRequest.instances = [];
  });

  it('aborts the real XHR and rejects when its signal is cancelled', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest);
    const controller = new AbortController();
    const file = new File(['body'], 'cancel.txt', { type: 'text/plain' });
    const upload = uploadSessionAttachment('s1', file, undefined, controller.signal);
    const xhr = FakeXMLHttpRequest.instances[0]!;

    controller.abort();

    await expect(upload).rejects.toThrow('附件上传已取消');
    expect(xhr.aborted).toBe(true);
  });

  it('keeps the stable server reference and reports completion progress', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest);
    const progress: Array<[number, number]> = [];
    const file = new File(['body'], '说明.md', { type: 'text/markdown' });
    const upload = uploadSessionAttachment('s1', file, (loaded, total) => {
      progress.push([loaded, total]);
    });
    const xhr = FakeXMLHttpRequest.instances[0]!;
    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      ok: true,
      filename: '说明.md',
      displayName: '说明.md',
      attachmentId: 'upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
      storageFilename: 'upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      path: 'D:\\attachments\\upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
      size: file.size,
    });
    xhr.onload?.();

    await expect(upload).resolves.toMatchObject({
      attachmentId: 'upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
      storageFilename: 'upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
    });
    expect(progress.at(-1)).toEqual([file.size, file.size]);
  });
});

describe('reimportSession adapter routes', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ['cbc', '/api/cbc/sessions/import'],
    ['kimi', '/api/kimi/sessions/import'],
    ['opencode', '/api/opencode/sessions/import'],
    ['codex', '/api/adapters/codex/sessions/import'],
    ['claude', '/api/adapters/claude/sessions/import'],
  ])('uses the supported %s history-import route', async (adapter, expectedPath) => {
    let requestUrl = '';
    let requestBody: Record<string, unknown> = {};
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(url);
      requestBody = JSON.parse(init?.body as string);
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({ id: 'ses-existing', workspaceIds: ['ws-original'] }),
      };
    }));

    await reimportSession('ses-existing', adapter, 'native-session', 'C:/work');

    expect(requestUrl.endsWith(expectedPath)).toBe(true);
    expect(requestBody).toEqual({ session_id: 'native-session', cwd: 'C:/work' });
  });
});

describe('reimport confirmation API', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('sends force only with the explicit Session-bound confirmation', async () => {
    const fetchMock = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => ({ id: 'ses-confirm' }) }));
    vi.stubGlobal('fetch', fetchMock);
    await reimportSession('ses-confirm', 'codex', 'native', 'C:/private', 'confirmed-snapshot');
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string)).toEqual({
      session_id: 'native', cwd: 'C:/private', force: true, panSessionId: 'ses-confirm',
      confirmationToken: 'confirmed-snapshot',
    });
  });
  it('retains the structured summary on a business error instead of losing it in a generic Error', async () => {
    const conflict = { sessionId: 'ses-confirm', count: 1, confirmationToken: 'snapshot' };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({
      error: 'origin mismatch', errorCode: 'reimport_origin_conflict', conflict,
    }) })));
    await expect(reimportSession('ses-confirm', 'codex', 'native')).rejects.toMatchObject({
      name: 'ReimportError', code: 'reimport_origin_conflict', conflict,
    });
  });
});

describe('worker control business errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('rejects HTTP 200 responses that carry a worker error body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ error: 'Worker not found' }),
    })));

    await expect(steerSessionWorker('s1', 'continue')).rejects.toThrow('Worker not found');
  });

  it('keeps an unknown Steer outcome distinct from a rejection so the draft cannot be resent', async () => {
    const { SubmissionRejectedError } = await import('./api');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200,
      json: async () => ({ error: 'opaque provider detail', outcome: 'unknown' }) })));
    const error = await steerSessionWorker('s1', 'continue', 'steer:unknown').catch(error => error);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(SubmissionRejectedError);
  });

  it('uses rejected status even when its detail contains unknown or awaiting words', async () => {
    const { SubmissionRejectedError } = await import('./api');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200,
      json: async () => ({ error: 'Steer outcome unknown: awaiting receipt', outcome: 'rejected' }) })));
    await expect(steerSessionWorker('s1', 'change')).rejects.toBeInstanceOf(SubmissionRejectedError);
  });

  it('queue mutations reject HTTP 200 responses that carry an error body', async () => {
    const { enqueueSessionMessage, fetchSessionQueue, updateSessionQueueItem, deleteSessionQueueItem } =
      await import('./api');

    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ ok: false, error: { message: 'queue rejected' } }),
    })));
    await expect(enqueueSessionMessage('s1', 'hi', 'c1')).rejects.toThrow('queue rejected');

    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ error: 'session gone' }),
    })));
    await expect(fetchSessionQueue('s1')).rejects.toThrow('session gone');

    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ ok: false, error: 'revision conflict' }),
    })));
    await expect(updateSessionQueueItem('s1', 'q1', 'x', 1)).rejects.toThrow('revision conflict');
    await expect(deleteSessionQueueItem('s1', 'q1')).rejects.toThrow('revision conflict');
  });

  it('carries one edit token through queue lease, save, and release requests', async () => {
    const {
      acquireSessionQueueItemEdit,
      releaseSessionQueueItemEdit,
      updateSessionQueueItem,
    } = await import('./api');
    const bodies = [
      { ok: true, expiresAt: 4_000_000_000 },
      { ok: true, item: { id: 'q1', text: 'edited' }, queueRevision: 3 },
      { ok: true, released: true },
    ];
    const requests: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      requests.push(init ?? {});
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => bodies.shift(),
      };
    }));

    const lease = await acquireSessionQueueItemEdit('s1', 'q1', 'edit-token', 2);
    await updateSessionQueueItem('s1', 'q1', 'edited', 2, 'edit-token');
    await releaseSessionQueueItemEdit('s1', 'q1', 'edit-token');

    expect(lease.expiresAt).toBe(4_000_000_000);
    expect(requests.map((request) => request.method)).toEqual(['POST', 'PATCH', 'POST']);
    expect(JSON.parse(requests[0]?.body as string)).toEqual({
      editToken: 'edit-token', expectedRevision: 2, includeBody: false,
    });
    expect(JSON.parse(requests[1]?.body as string)).toEqual({
      text: 'edited', expectedRevision: 2, editToken: 'edit-token',
    });
    expect(JSON.parse(requests[2]?.body as string)).toEqual({ editToken: 'edit-token' });
  });
});

describe('global history search API', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('encodes the global query, bounds pages to 100, and forwards cancellation', async () => {
    const controller = new AbortController();
    let requestUrl = '';
    let requestInit: RequestInit | undefined;
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      requestUrl = String(url);
      requestInit = init;
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({
          hits: [], versions: [], limit: 100, hasMore: false, nextCursor: null,
        }),
      };
    }));

    await fetchHistorySearch('needle & 雪', 250, 'next cursor', controller.signal);

    const url = new URL(requestUrl, 'http://localhost');
    expect(url.pathname).toBe('/api/history/search');
    expect(url.searchParams.get('q')).toBe('needle & 雪');
    expect(url.searchParams.get('limit')).toBe('100');
    expect(url.searchParams.get('cursor')).toBe('next cursor');
    expect(requestInit?.signal).toBe(controller.signal);
  });

  it.each([409, 503])('preserves HTTP %i so search can show the correct recovery state', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status,
      statusText: status === 409 ? 'Conflict' : 'Service Unavailable',
      json: async () => ({ detail: { code: 'history_search_error', message: 'unavailable' } }),
    })));

    await expect(fetchHistorySearch('needle')).rejects.toMatchObject({
      name: 'ApiRequestError',
      status,
    } satisfies Partial<ApiRequestError>);
  });

  it('encodes occurrence counting, empty roles, ordinal seek and stable-ID relocation', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => {
      urls.push(String(url));
      return { ok: true, status: 200, json: async () => ({ hits: [], versions: [], limit: 50, hasMore: false, nextCursor: null }) };
    }));
    await fetchHistorySearch('中', 50, undefined, undefined,
      { sessionId: 'a', roles: [], countMode: 'content', matchIndex: 0 });
    await fetchHistorySearch('中', 1, undefined, undefined,
      { sessionId: 'a', roles: ['tool', 'thinking'], countMode: 'content', messageId: 'pan:123' });
    const first = new URL(urls[0]!, 'http://localhost');
    expect(first.searchParams.get('roles')).toBe('');
    expect(first.searchParams.get('countMode')).toBe('content');
    expect(first.searchParams.get('matchIndex')).toBe('0');
    const next = new URL(urls[1]!, 'http://localhost');
    expect(next.searchParams.get('roles')).toBe('tool,thinking');
    expect(next.searchParams.get('messageId')).toBe('pan:123');
    expect(next.searchParams.get('sessionId')).toBe('a');
  });
});

describe('history page API for search jumps', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('opts in only explicit jump requests to the larger page cap', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => {
      urls.push(String(url));
      return {
        ok: true,
        status: 200,
        json: async () => ({ history: [], total: 0, hasMore: false, start: 0 }),
      };
    }));

    await fetchSessionHistory('session', 1000, 1000);
    await fetchSessionHistory('session', 1000, 1000, undefined, true);

    expect(new URL(urls[0]!, 'http://localhost').searchParams.has('searchJump')).toBe(false);
    expect(new URL(urls[1]!, 'http://localhost').searchParams.get('searchJump')).toBe('true');
  });
});

describe('canonical Jobs completed-retention API', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reads and writes the same Jobs API route with the rules payload', async () => {
    const rules = {
      completed: { enabled: false, days: null },
      failed: { enabled: true, days: 14 },
      timed_out: { enabled: false, days: null },
      cancelled: { enabled: false, days: null },
      logs: { enabled: false, days: null },
    };
    const configValidity = {
      completed: true,
      failed: true,
      timed_out: true,
      cancelled: true,
      logs: true,
    };
    const lastRuns = {
      completed: null,
      failed: null,
      timed_out: null,
      cancelled: null,
      logs: null,
    };
    const requests: Array<{ url: string; method: string; body: unknown }> = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      requests.push({
        url: String(url),
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({
          settings: rules.completed,
          rules,
          configValid: true,
          configValidity,
          lastRun: null,
          lastRuns,
        }),
      };
    }));

    await expect(fetchCompletedJobRetentionSettings()).resolves.toMatchObject({ rules });
    await updateCompletedJobRetentionSettings(rules);

    expect(requests).toEqual([
      { url: '/api/legacy/jobs/settings/completed-retention', method: 'GET', body: undefined },
      {
        url: '/api/legacy/jobs/settings/completed-retention',
        method: 'PUT',
        body: { rules },
      },
    ]);
  });
});

describe('workspace assignment for Session creation/import requests', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends workspaceIds when creating a new Session', async () => {
    let body: Record<string, unknown> = {};
    vi.stubGlobal('fetch', vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      body = JSON.parse(init?.body as string);
      return { ok: true, status: 200, statusText: 'OK', json: async () => ({ id: 'created' }) };
    }));

    await createSession('New', null, 'cbc', undefined, { workspaceIds: ['ws-active'] });

    expect(body.workspaceIds).toEqual(['ws-active']);
  });

  it('sends an empty workspaceIds array for all and ungrouped creation scopes', async () => {
    let body: Record<string, unknown> = {};
    vi.stubGlobal('fetch', vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      body = JSON.parse(init?.body as string);
      return { ok: true, status: 200, statusText: 'OK', json: async () => ({ id: 'created' }) };
    }));

    await createSession('New', null, 'cbc', undefined, { workspaceIds: [] });

    expect(body.workspaceIds).toEqual([]);
  });

  it.each([
    ['cbc', (workspaceIds: string[]) => importCbcSession('native-cbc', 'C:/project', workspaceIds), 'project_dir'],
    ['kimi', (workspaceIds: string[]) => importKimiSession('native-kimi', 'C:/project', workspaceIds), 'cwd'],
    ['opencode', (workspaceIds: string[]) => importOpencodeSession('native-opencode', 'C:/project', workspaceIds), 'cwd'],
    ['codex', (workspaceIds: string[]) => importCodexSession('native-codex', 'C:/project', workspaceIds), 'cwd'],
  ])('includes the selected Workspace in %s import payloads', async (_adapter, invoke, pathKey) => {
    let body: Record<string, unknown> = {};
    vi.stubGlobal('fetch', vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      body = JSON.parse(init?.body as string);
      return { ok: true, status: 200, statusText: 'OK', json: async () => ({ id: 'imported' }) };
    }));

    await invoke(['ws-active']);

    expect(body.workspaceIds).toEqual(['ws-active']);
    expect(body[pathKey as string]).toBe('C:/project');
  });
});


describe('Jobs pin metadata envelope', () => {
  afterEach(() => { vi.unstubAllGlobals(); });
  it('reports unknown metadata even when the list is empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, jobs: [], pinMetadataStatus: 'unavailable' }), { status: 200 })));
    const status = vi.fn();
    expect(await fetchJobs(status)).toEqual([]);
    expect(status).toHaveBeenCalledWith('unavailable');
  });
  it('reports restored available metadata on the next read', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, jobs: [], pinMetadataStatus: 'available' }), { status: 200 })));
    const status = vi.fn();
    expect(await fetchJobs(status)).toEqual([]);
    expect(status).toHaveBeenCalledWith('available');
  });
});

describe('plugin panel structured failures', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('keeps a host-provided code and leaves plain text uncoded', () => {
    const coded = pluginPanelFailure(JSON.stringify({ ok: false, error: { code: 'cli_not_found', message: 'missing' } }));
    expect(coded).toBeInstanceOf(PluginPanelError);
    expect(coded.code).toBe('cli_not_found');
    expect(coded.message).toBe('missing');
    expect(pluginPanelFailure('plain failure').code).toBeUndefined();
    expect(pluginPanelFailure(JSON.stringify({ ok: false, error: { code: 'cli_failed', message: 'boom' } })).code)
      .toBe('cli_failed');
  });

  it('throws the coded failure from an isError action response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      isError: true,
      content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code: 'cli_not_found', message: 'missing' } }) }],
    }), { status: 200 })));
    await expect(invokePluginPanel('cc-switch-bridge', 'accounts', 'list', { app: 'codex' }))
      .rejects.toMatchObject({ code: 'cli_not_found', message: 'missing' });
  });
});
