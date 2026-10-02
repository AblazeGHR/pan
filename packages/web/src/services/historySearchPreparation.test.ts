// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { prepareHistorySearch } from './api';

afterEach(() => vi.unstubAllGlobals());

function transport(events: object[]) {
  const bytes = new TextEncoder().encode(events.map((event) => JSON.stringify(event)+'\n').join(''));
  const chunks = [bytes.slice(0, 17), bytes.slice(17, 63), bytes.slice(63)];
  const reader = { read: vi.fn(async () => chunks.length ? { value: chunks.shift(), done: false } : { done: true }),
    cancel: vi.fn(async () => undefined), releaseLock: vi.fn() };
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, body: { getReader: () => reader } })));
  return { reader, signal: new AbortController().signal };
}

const result = { hits: [], versions: [], limit: 50, hasMore: false, nextCursor: null, totalMatches: 7 };

it('decodes split NDJSON frames and requires a terminal result', async () => {
  const first = { result, completed: 0, total: 1, done: false, failedSessions: [] };
  const last = { ...first, completed: 1, done: true };
  const { reader, signal } = transport([first, last]);
  const report = vi.fn();
  await prepareHistorySearch('中', ['user'], 50, signal, report);
  expect(report.mock.calls.map(([event]) => event)).toEqual([first, last]);
  expect(reader.cancel).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledWith('/api/history/search/prepare', expect.objectContaining({ method: 'POST', signal }));
});

it('does not mistake an interrupted stream for a complete search', async () => {
  const { signal } = transport([{ result, completed: 0, total: 2, done: false, failedSessions: [] }]);
  await expect(prepareHistorySearch('q', ['tool'], 50, signal, vi.fn())).rejects.toThrow('incomplete');
});

it('propagates a controlled version error and stops a cancelled response', async () => {
  let context = transport([{ error: { status: 409, message: 'changed' } }]);
  await expect(prepareHistorySearch('q', ['user'], 50, context.signal, vi.fn())).rejects.toMatchObject({ status: 409 });
  context = transport([{ result, completed: 1, total: 1, done: true, failedSessions: [] }]);
  const controller = new AbortController();
  controller.abort();
  const report = vi.fn();
  await expect(prepareHistorySearch('q', ['user'], 50, controller.signal, report)).rejects.toMatchObject({ name: 'AbortError' });
  expect(report).not.toHaveBeenCalled();
});
