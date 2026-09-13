// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  deleteSessionQueueItem,
  enqueueSessionMessage,
  fetchSessionQueue,
  reorderSessionQueue,
  updateSessionQueueItem,
} from '@/services/api';
import {
  installMockBackend,
  isMockMode,
  mockUploadSessionAttachment,
  resetMockData,
} from './mockBackend';

describe('frontend mock backend', () => {
  let originalFetch: typeof window.fetch;

  beforeEach(() => {
    originalFetch = window.fetch;
    if (typeof window.fetch !== 'function') {
      window.fetch = vi.fn(async () => new Response('{}'));
    }
    resetMockData();
    installMockBackend();
  });

  afterEach(() => {
    window.fetch = originalFetch;
    resetMockData();
    window.history.replaceState({}, '', '/');
  });

  it('only enables the URL switch for ?mock=1', () => {
    window.history.pushState({}, '', '/?mock=0');
    expect(isMockMode()).toBe(false);
    window.history.pushState({}, '', '/?mock=1');
    expect(isMockMode()).toBe(true);
  });

  it('simulates upload progress and returns a safe attachment reference', async () => {
    const progress: Array<[number, number]> = [];
    const file = new File(['demo'], '说明.md', { type: 'text/markdown' });

    const result = await mockUploadSessionAttachment('mock-alpha', file, (loaded, total) => {
      progress.push([loaded, total]);
    });

    expect(progress[0]).toEqual([0, file.size]);
    expect(progress.at(-1)).toEqual([file.size, file.size]);
    expect(result).toMatchObject({
      ok: true,
      filename: '说明.md',
      attachmentId: expect.stringMatching(/^upload_[a-z0-9]{32}\.md$/),
      displayName: '说明.md',
      path: 'D:\\mock-uploads\\mock-alpha\\说明.md',
      size: file.size,
    });
    expect(result.href).toMatch(/^\/api\/attachments\/upload_[a-z0-9]{32}\.md\?session_id=mock-alpha$/);
  });

  it('serves the existing queue API contract for mock send and queue actions', async () => {
    const first = await enqueueSessionMessage('mock-alpha', 'first message', 'client-1');
    const duplicate = await enqueueSessionMessage('mock-alpha', 'changed duplicate', 'client-1');
    const second = await enqueueSessionMessage('mock-alpha', 'second message', 'client-2');

    expect(first.item).toMatchObject({ text: 'first message', kind: 'task', source: 'user' });
    expect(duplicate).toMatchObject({ duplicate: true, item: { id: first.item.id, text: 'first message' } });
    expect(second.queueRevision).toBe(2);

    const initial = await fetchSessionQueue('mock-alpha');
    expect(initial.map((item) => item.text)).toEqual(['first message', 'second message']);
    expect(initial.queueRevision).toBe(2);

    const reordered = await reorderSessionQueue(
      'mock-alpha',
      [second.item.id, first.item.id],
      initial.queueRevision,
    );
    expect(reordered.map((item) => item.id)).toEqual([second.item.id, first.item.id]);

    const edited = await updateSessionQueueItem(
      'mock-alpha',
      first.item.id,
      'edited message',
      reordered.queueRevision,
    );
    expect(edited.item).toMatchObject({ id: first.item.id, text: 'edited message' });

    await deleteSessionQueueItem('mock-alpha', second.item.id);
    const final = await fetchSessionQueue('mock-alpha');
    expect(final.map((item) => item.text)).toEqual(['edited message']);
    expect(final[0]?.meta?.dispatchState).toBe('queued');
  });
});
