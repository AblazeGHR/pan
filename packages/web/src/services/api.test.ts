// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { steerSessionWorker, uploadSessionAttachment } from './api';

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
});
