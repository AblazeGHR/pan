// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchSessionHistory } from './api';
import {
  cancelHistoryPrefetch,
  nextHistoryPrefetchBefore,
  peekHistoryPrefetch,
  prefetchHistoryPage,
  syncHistoryPrefetch,
  takeHistoryPrefetch,
  type HistoryPrefetchContext,
} from './historyPagePrefetch';
import type { ApiSessionHistoryResponse } from '@/types';

vi.mock('./api', () => ({ fetchSessionHistory: vi.fn() }));
const context: HistoryPrefetchContext = {
  sessionId: 'a',
  selection: 1,
  serverEpoch: 'server',
  historyEpoch: 'history',
  historyRevision: 4,
  limit: 50,
};
function page(before: number): ApiSessionHistoryResponse {
  const start = Math.max(0, before - 50);
  return {
    history: Array.from({ length: before - start }, (_, i) => ({
      role: 'user',
      content: `row ${start + i}`,
    })),
    total: 500,
    start,
    hasMore: start > 0,
    historyEpoch: 'history',
    historyRevision: 4,
  };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(fetchSessionHistory).mockReset();
  syncHistoryPrefetch(null);
  syncHistoryPrefetch(context);
});
afterEach(() => {
  syncHistoryPrefetch(null);
  vi.useRealTimers();
});

it('keeps three contiguous raw pages and consumes each once', async () => {
  vi.mocked(fetchSessionHistory).mockImplementation(async (_sid, before) => page(before!));
  for (const before of [450, 400, 350]) {
    expect(nextHistoryPrefetchBefore(context, 450)).toBe(before);
    expect(await prefetchHistoryPage(context, before)).toBe(true);
  }
  expect(nextHistoryPrefetchBefore(context, 450)).toBeNull();
  expect(await prefetchHistoryPage(context, 300)).toBe(false);
  expect(takeHistoryPrefetch(context, 450)?.start).toBe(400);
  expect(takeHistoryPrefetch(context, 450)).toBeNull();
  expect(nextHistoryPrefetchBefore(context, 400)).toBe(300);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(3);
});

it.each(['sessionId', 'selection', 'serverEpoch', 'historyEpoch', 'historyRevision'] as const)(
  'rejects late responses after %s changes even if fetch ignores abort',
  async (field) => {
    let resolve!: (value: ApiSessionHistoryResponse) => void;
    vi.mocked(fetchSessionHistory).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const pending = prefetchHistoryPage(context, 450);
    const signal = vi.mocked(fetchSessionHistory).mock.calls[0]![3]!;
    expect(await prefetchHistoryPage(context, 400)).toBe(false);
    syncHistoryPrefetch({
      ...context,
      [field]: typeof context[field] === 'number' ? 99 : 'changed',
    });
    expect(signal.aborted).toBe(true);
    resolve(page(450));
    expect(await pending).toBe(false);
    expect(takeHistoryPrefetch(context, 450)).toBeNull();
  },
);

it('foreground cancellation preserves completed pages but discards the pending page', async () => {
  vi.mocked(fetchSessionHistory).mockResolvedValueOnce(page(450));
  await prefetchHistoryPage(context, 450);
  let resolve!: (value: ApiSessionHistoryResponse) => void;
  vi.mocked(fetchSessionHistory).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const pending = prefetchHistoryPage(context, 400);
  cancelHistoryPrefetch();
  resolve(page(400));
  expect(await pending).toBe(false);
  expect(peekHistoryPrefetch(context, 450)).toBe(true);
  expect(peekHistoryPrefetch(context, 400)).toBe(false);
});

it.each(['epoch', 'revision', 'range', 'count', 'oversize', 'error'])(
  'falls back without retaining an invalid %s page',
  async (kind) => {
    const response = page(450);
    if (kind === 'epoch') response.historyEpoch = 'replaced';
    if (kind === 'revision') response.historyRevision = 5;
    if (kind === 'range') response.start = 399;
    if (kind === 'count') response.history.pop();
    if (kind === 'oversize') response.history[0]!.content = 'x'.repeat(500_001);
    if (kind === 'error') vi.mocked(fetchSessionHistory).mockRejectedValue(new Error('offline'));
    else vi.mocked(fetchSessionHistory).mockResolvedValue(response);
    expect(await prefetchHistoryPage(context, 450)).toBe(false);
    expect(takeHistoryPrefetch(context, 450)).toBeNull();
  },
);

it('expires cached pages after one minute', async () => {
  vi.mocked(fetchSessionHistory).mockResolvedValue(page(450));
  await prefetchHistoryPage(context, 450);
  vi.advanceTimersByTime(60_000);
  expect(takeHistoryPrefetch(context, 450)).toBeNull();
  expect(nextHistoryPrefetchBefore(context, 450)).toBe(450);
});

it('reclaims pages that a larger foreground navigation has already passed', async () => {
  vi.mocked(fetchSessionHistory).mockImplementation(async (_sid, before) => page(before!));
  for (const before of [450, 400, 350]) await prefetchHistoryPage(context, before);
  expect(nextHistoryPrefetchBefore(context, 200)).toBe(200);
  expect(peekHistoryPrefetch(context, 450)).toBe(false);
  expect(await prefetchHistoryPage(context, 200)).toBe(true);
});
