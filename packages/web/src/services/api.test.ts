// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createSession,
  importCbcSession,
  importCodexSession,
  importKimiSession,
  importOpencodeSession,
  reimportSession,
  steerSessionWorker,
  uploadSessionAttachment,
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
      editToken: 'edit-token', expectedRevision: 2,
    });
    expect(JSON.parse(requests[1]?.body as string)).toEqual({
      text: 'edited', expectedRevision: 2, editToken: 'edit-token',
    });
    expect(JSON.parse(requests[2]?.body as string)).toEqual({ editToken: 'edit-token' });
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
