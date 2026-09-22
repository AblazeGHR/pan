// @vitest-environment jsdom
/**
 * Re-audit investigation suite — real useWebSocket event pipeline ordering.
 *
 * Baseline: main 591367a65f88e9d5270e8e99920ef5448d1d68c9.
 * Drives the actual registered handlers of useWebSocket (only the ws singleton
 * and the HTTP api module are mocked). Failing assertions document defects.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const wsMock = vi.hoisted(() => {
  const handlers: Record<string, Array<(e: unknown) => void>> = {};
  return {
    handlers,
    connect: vi.fn(),
    send: vi.fn(() => true),
    reconnect: vi.fn(),
    isConnectionFresh: vi.fn(() => true),
    on: vi.fn((type: string, h: (e: unknown) => void) => {
      (handlers[type] ??= []).push(h);
      return () => { handlers[type] = (handlers[type] ?? []).filter((x) => x !== h); };
    }),
    trigger: (type: string, e: unknown) => { for (const h of handlers[type] ?? []) h(e); },
  };
});

vi.mock('@/services/ws', () => ({
  wsClient: {
    connect: wsMock.connect, reconnect: wsMock.reconnect, on: wsMock.on,
    send: wsMock.send, isOpen: true, isConnectionFresh: wsMock.isConnectionFresh,
  },
}));

const apiMock = vi.hoisted(() => ({
  fetchSessionHistory: vi.fn(),
  fetchSessions: vi.fn(),
  listWorkers: vi.fn(),
  fetchSessionQueue: vi.fn(),
  updateUiSettings: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchSessionHistory: apiMock.fetchSessionHistory,
  fetchSessions: apiMock.fetchSessions,
  listWorkers: apiMock.listWorkers,
  fetchSessionQueue: apiMock.fetchSessionQueue,
  updateUiSettings: apiMock.updateUiSettings,
}));

import { useWebSocket } from '@/hooks/useWebSocket';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useQueueStore } from '@/stores/queueStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Session } from '@/types';

function mk(id: string): Session {
  return { id, name: id, alwaysThinkingEnabled: false, effort: '', history: [], workerStatus: 'idle', workerId: 'w1' };
}

const shape = () => useSessionStore.getState().currentMessages.map(
  (x) => `${x.role}:${x.content}`,
);

function resetStore() {
  useSessionStore.setState({
    sessions: [mk('A')], currentSessionId: 'A', currentMessages: [],
    hasMoreMessages: false, historyLoading: false, initialLoading: false, sessionsLoading: false,
    historyLoadEnd: 0, historyWindowStarts: { A: 0 }, _loadSeq: 0, _sessionWsTouchedSeq: {},
    _sessionLocalTouchedSeq: {}, _historyRefreshSeq: {}, _historyPageSeq: {}, _selectionSeq: {},
    liveStreamBuffers: {}, terminalWatermarks: {}, sessionUnread: {}, _pendingQueueIds: {},
    _deliveredQueueIds: {}, _sessionEventPatches: {}, sessionSettingMutations: {},
    sessionTranscripts: {},
  });
}

describe('reaudit · ordered event pipeline', () => {
  beforeEach(() => {
    for (const k of Object.keys(wsMock.handlers)) delete wsMock.handlers[k];
    wsMock.send.mockClear();
    apiMock.fetchSessions.mockReset().mockRejectedValue(new Error('not mocked'));
    apiMock.listWorkers.mockReset().mockRejectedValue(new Error('not mocked'));
    apiMock.fetchSessionHistory.mockReset();
    apiMock.fetchSessionQueue.mockReset().mockResolvedValue([]);
    apiMock.updateUiSettings.mockReset().mockResolvedValue({});
    resetStore();
    useUIStore.setState({ terminalInteractions: [], toastQueue: [] });
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useQueueStore.setState({ agentQueues: {}, agentQueueLoadSeq: {} });
    useWorkerStore.setState({ workers: {} });
  });
  afterEach(() => { vi.useRealTimers(); });

  const assistantDelta = (text: string, itemId: string, turnId: string, taskSeq: number) => ({
    type: 'worker.stream' as const, sessionId: 'A', workerId: 'w1', generation: 0, taskSeq,
    event: {
      type: 'assistant', delta: true, stream_text: text, item_id: itemId, turn_id: turnId,
      message: { content: [{ type: 'text', text }] },
    },
  });

  // POSITIVE CONTROL (implementation holds): two sequential ordered turns land in
  // arrival order, and a repeated identical delta for the same item is idempotent.
  it('E1 · two ordered turns land in order and a repeated identical delta is idempotent', () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'running' });
      wsMock.trigger('worker.stream', assistantDelta('A', 'i1', 't1', 1));
      wsMock.trigger('worker.stream', assistantDelta('A', 'i1', 't1', 1));
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'done', result: 'A' });
      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 2, status: 'running' });
      wsMock.trigger('worker.stream', assistantDelta('B', 'i2', 't2', 2));
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 2, status: 'done', result: 'B' });
    });

    const assistants = useSessionStore.getState().currentMessages
      .filter((x) => x.role === 'assistant')
      .map((x) => x.content);
    expect(assistants).toEqual(['A', 'B']);
  });

  // DEFECT E2 (F-B). A tool event that arrives before the assistant text is
  // reordered after it once worker.result rebuilds the live projection.
  it('E2 · a tool block streamed before the assistant text keeps its position after result', () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'running' });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1,
        event: { type: 'codex.item.completed', item_id: 'tool-1', item: { id: 'tool-1', type: 'Command', command: 'echo hi' } },
      });
    });
    expect(shape()).toEqual(['tool:Command({"command":"echo hi"})']);

    act(() => {
      wsMock.trigger('worker.stream', assistantDelta('Hello world', 'item-2', 't1', 1));
    });
    expect(shape()).toEqual([
      'tool:Command({"command":"echo hi"})',
      'assistant:Hello world',
    ]);

    act(() => {
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'done', result: 'Hello world' });
    });

    const rows = useSessionStore.getState().currentMessages
      .filter((x) => x.role !== 'system')
      .map((x) => `${x.role}:${x.content}`);
    expect(rows).toEqual([
      'tool:Command({"command":"echo hi"})',
      'assistant:Hello world',
    ]);
    // observed: ['assistant:Hello world', 'tool:Command({"command":"echo hi"})']
  });

  // DEFECT E3 (F-B, user-visible). After result, the canonical history page is
  // merged in; the leftover live tool row is not matched (nativeItemId vs
  // messageId) and survives as a duplicate.
  it('E3 · a history refresh after result does not duplicate the tool row', async () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'running' });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1,
        event: { type: 'codex.item.completed', item_id: 'tool-1', item: { id: 'tool-1', type: 'Command', command: 'echo hi' } },
      });
      wsMock.trigger('worker.stream', assistantDelta('Hello world', 'item-2', 't1', 1));
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'done', result: 'Hello world' });
    });
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        { role: 'user', content: 'u0', messageId: 'm-0' },
        { role: 'tool', content: 'Command({"command":"echo hi"})', messageId: 'm-1' },
        { role: 'assistant', content: 'Hello world', messageId: 'm-2' },
      ],
      total: 3, hasMore: false, start: 0, historyEpoch: 'e1', historyRevision: 3,
    });

    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    const rows = useSessionStore.getState().currentMessages
      .filter((x) => x.role !== 'system')
      .map((x) => `${x.role}:${x.content}`);
    expect(rows).toEqual([
      'user:u0',
      'tool:Command({"command":"echo hi"})',
      'assistant:Hello world',
    ]);
    // observed: 4 rows, the tool line present twice
    expect(rows.filter((x) => x.startsWith('tool:'))).toHaveLength(1);
  });

  // DEFECT E4 (priority: positions after consecutive DONE rounds). Two turns
  // through the real registered handlers: turn 1 streams a tool block then the
  // final assistant text; turn 2 streams a second assistant reply. Both rounds
  // end in worker.result. Every row must keep its arrival position and none may
  // be lost when turn 2 reconciles.
  it('E4 · two consecutive DONE rounds keep every block and its position', () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'running' });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1,
        event: { type: 'codex.item.completed', item_id: 'tool-1', item: { id: 'tool-1', type: 'Command', command: 'echo hi' } },
      });
      wsMock.trigger('worker.stream', assistantDelta('Hello world', 'item-2', 't1', 1));
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 1, status: 'done', result: 'Hello world' });

      wsMock.trigger('worker.status', { type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 2, status: 'running' });
      wsMock.trigger('worker.stream', assistantDelta('Second answer', 'item-3', 't2', 2));
      wsMock.trigger('worker.result', { type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0, taskSeq: 2, status: 'done', result: 'Second answer' });
    });

    const rows = useSessionStore.getState().currentMessages
      .filter((x) => x.role !== 'system')
      .map((x) => `${x.role}:${x.content}`);
    expect(rows).toEqual([
      'tool:Command({"command":"echo hi"})',
      'assistant:Hello world',
      'assistant:Second answer',
    ]);
    expect(rows).toHaveLength(3);
  });
});
