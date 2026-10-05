// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useIdleHistoryPrefetch } from './useIdleHistoryPrefetch';
import { fetchSessionHistory } from '@/services/api';
import { beginForegroundRequest, noteForegroundActivity } from '@/services/foregroundActivity';
import { getHistoryPrefetchContext, useSessionStore } from '@/stores/sessionStore';
import { syncHistoryPrefetch } from '@/services/historyPagePrefetch';
import type { ApiSessionHistoryResponse } from '@/types';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(),
}));
vi.mock('@/services/ws', () => ({ wsClient: { onAll: vi.fn(() => () => {}) } }));
function page(before: number): ApiSessionHistoryResponse {
  return {
    history: Array.from({ length: 50 }, (_, i) => ({
      role: 'user',
      messageId: `row-${before - 50 + i}`,
      content: `row ${before - 50 + i}`,
    })),
    total: 500,
    start: before - 50,
    hasMore: true,
    historyEpoch: 'history',
    historyRevision: 4,
  };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  vi.mocked(fetchSessionHistory)
    .mockReset()
    .mockImplementation(async (_sid, before) => page(before!));
  useSessionStore.setState({
    currentSessionId: 'a',
    sessions: [
      {
        id: 'a',
        name: 'A',
        effort: '',
        alwaysThinkingEnabled: false,
        history: [],
        historyEpoch: 'history',
        historyRevision: 4,
        workerStatus: 'idle',
      },
    ],
    sessionTranscripts: {},
    _selectionSeq: { a: 1 },
    _historyPageSeq: {},
    serverEpoch: 'server',
    initialLoading: false,
    sessionsLoading: false,
    historyLoading: false,
    hasMoreMessages: true,
    historyLoadEnd: 450,
    currentMessages: [],
  });
  useSessionStore.getState().applyHistoryPage('a', page(500));
  syncHistoryPrefetch(null);
  noteForegroundActivity();
});
afterEach(() => {
  cleanup();
  syncHistoryPrefetch(null);
  vi.restoreAllMocks();
  vi.useRealTimers();
});
async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

it('prefetches only after quiet time without changing messages or scroll boundary, then uses the ordinary page merger', async () => {
  const original = useSessionStore.getState().currentMessages;
  renderHook(useIdleHistoryPrefetch);
  await wait(1900);
  expect(fetchSessionHistory).not.toHaveBeenCalled();
  await wait(1100);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(3);
  expect(useSessionStore.getState().currentMessages).toBe(original);
  expect(useSessionStore.getState().historyLoadEnd).toBe(450);
  expect(useSessionStore.getState().hasPrefetchedOlderMessages()).toBe(true);
  await act(async () => {
    await useSessionStore.getState().loadOlderMessages();
  });
  expect(fetchSessionHistory).toHaveBeenCalledTimes(3);
  expect(useSessionStore.getState().historyLoadEnd).toBe(400);
  expect(useSessionStore.getState().currentMessages.map((m) => m.messageId)).toEqual(
    Array.from({ length: 100 }, (_, i) => `row-${400 + i}`),
  );
});

it('aborts immediately for foreground work and cannot retain an ignored-abort response', async () => {
  let resolve!: (value: ApiSessionHistoryResponse) => void;
  vi.mocked(fetchSessionHistory).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  renderHook(useIdleHistoryPrefetch);
  await wait(2100);
  const signal = vi.mocked(fetchSessionHistory).mock.calls[0]![3]!;
  let finish!: () => void;
  act(() => {
    finish = beginForegroundRequest();
  });
  expect(signal.aborted).toBe(true);
  await act(async () => {
    resolve(page(450));
  });
  await wait(5000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(1);
  expect(useSessionStore.getState().hasPrefetchedOlderMessages()).toBe(false);
  vi.mocked(fetchSessionHistory).mockImplementation(async (_sid, before) => page(before!));
  act(finish);
  await wait(3000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(4);
});

it('yields to workers and Session changes, with no hot retry after an error', async () => {
  useSessionStore.setState({
    sessions: useSessionStore.getState().sessions.map((s) => ({ ...s, workerStatus: 'running' })),
  });
  renderHook(useIdleHistoryPrefetch);
  await wait(5000);
  expect(fetchSessionHistory).not.toHaveBeenCalled();
  act(() =>
    useSessionStore.setState({
      sessions: useSessionStore.getState().sessions.map((s) => ({ ...s, workerStatus: 'idle' })),
    }),
  );
  vi.mocked(fetchSessionHistory).mockRejectedValue(new Error('offline'));
  await wait(5000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(1);
  act(noteForegroundActivity);
  await wait(5000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(1);
  act(() => useSessionStore.setState({ currentSessionId: null }));
  expect(getHistoryPrefetchContext()).toBeNull();
  await wait(5000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(1);
});

it('does not prefetch in a hidden tab', async () => {
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  renderHook(useIdleHistoryPrefetch);
  await wait(5000);
  expect(fetchSessionHistory).not.toHaveBeenCalled();
});

it('can resume speculation after a failed page is loaded by the user', async () => {
  vi.mocked(fetchSessionHistory).mockRejectedValueOnce(new Error('offline'));
  renderHook(useIdleHistoryPrefetch);
  await wait(3000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(1);
  await act(async () => {
    await useSessionStore.getState().loadOlderMessages();
  });
  expect(useSessionStore.getState().historyLoadEnd).toBe(400);
  await wait(3000);
  expect(useSessionStore.getState().hasPrefetchedOlderMessages()).toBe(true);
});

it('invalidates an old window when the summary advertises a replacement with a reset revision', async () => {
  renderHook(useIdleHistoryPrefetch);
  await wait(3000);
  expect(useSessionStore.getState().hasPrefetchedOlderMessages()).toBe(true);
  act(() =>
    useSessionStore.setState({
      sessions: useSessionStore
        .getState()
        .sessions.map((s) => ({ ...s, historyEpoch: 'replacement', historyRevision: 0 })),
    }),
  );
  expect(getHistoryPrefetchContext()).toBeNull();
  expect(useSessionStore.getState().hasPrefetchedOlderMessages()).toBe(false);
  await wait(5000);
  expect(fetchSessionHistory).toHaveBeenCalledTimes(3);
});
