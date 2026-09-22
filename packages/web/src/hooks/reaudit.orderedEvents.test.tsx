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
    sendInteractiveSync: vi.fn(() => true),
    sendAuthoritativeResync: vi.fn(
      (_payload: Record<string, unknown>, _mode?: 'initial' | 'recovery') => true,
    ),
    getConnectionGeneration: vi.fn(() => 1),
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
    send: wsMock.send, sendInteractiveSync: wsMock.sendInteractiveSync,
    sendAuthoritativeResync: wsMock.sendAuthoritativeResync,
    getConnectionGeneration: wsMock.getConnectionGeneration,
    isOpen: true, isConnectionFresh: wsMock.isConnectionFresh,
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
    liveStreamBuffers: {}, terminalWatermarks: {}, sessionUnread: {}, unscopedReplayPending: {},
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

  it('E1a · resync replay frames without task scope are quarantined per Session', () => {
    useSessionStore.setState({ sessions: [mk('A'), mk('B')] });
    renderHook(() => useWebSocket());
    const replay = (sessionId: string) => ({
      type: 'worker.stream', sessionId, workerId: 'w1', generation: 4,
      replayed: true,
      event: {
        type: 'assistant', item_id: `replay-${sessionId}`,
        message: { content: [{ type: 'text', text: `replayed-${sessionId}` }] },
      },
    });
    const live = (sessionId: string, taskSeq: number) => ({
      type: 'worker.stream', sessionId, workerId: 'w1', generation: 4,
      taskSeq, taskId: `task-${sessionId}-${taskSeq}`,
      event: {
        type: 'assistant', delta: true, replace: true,
        item_id: `live-${sessionId}`, turn_id: `turn-${sessionId}`,
        stream_text: `live-${sessionId}`,
        message: { content: [{ type: 'text', text: `live-${sessionId}` }] },
      },
    });

    act(() => {
      wsMock.trigger('resync.snapshot', {
        type: 'resync.snapshot', serverEpoch: 'server-4', eventSeq: 81512,
        sessions: [mk('A'), mk('B')],
        workers: [{ sessionId: 'A' }, { sessionId: 'B' }],
      });
      // The physical socket replays the same unscoped batch twice before the
      // first task-scoped live frame for either running Session.
      wsMock.trigger('worker.stream', replay('A'));
      wsMock.trigger('worker.stream', replay('A'));
      wsMock.trigger('worker.stream', replay('B'));
      wsMock.trigger('worker.stream', replay('B'));
      wsMock.trigger('worker.stream', live('A', 11));
      wsMock.trigger('worker.stream', live('B', 22));
    });

    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['live-A']);
    expect(useSessionStore.getState().liveStreamBuffers.B?.messages.map((m) => m.content))
      .toEqual(['live-B']);
  });

  it('does not permanently quarantine an unscoped live frame without replay marker', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('resync.snapshot', {
        type: 'resync.snapshot', serverEpoch: 'server-5', eventSeq: 81513,
        sessions: [mk('A')], workers: [{ sessionId: 'A' }],
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 4,
        event: {
          type: 'assistant', delta: true, replace: true,
          item_id: 'live-unscoped', stream_text: 'live-unscoped',
          message: { content: [{ type: 'text', text: 'live-unscoped' }] },
        },
      });
    });

    expect(useSessionStore.getState().currentMessages.map((m) => m.content))
      .toEqual(['live-unscoped']);
  });

  it('E1b · Codex delta and final tool envelope with one item renders once', () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.status', {
        type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-1', status: 'running',
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-1', event: {
          type: 'assistant', delta: true, replace: true,
          stream_text: 'Command({"command":"echo hi"})',
          item_id: 'exec-X', turn_id: 'T',
          message: { content: [{ type: 'text', text: 'Command({"command":"echo hi"})' }] },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-1', event: {
          type: 'assistant', final: true, replace: true,
          item_id: 'exec-X', turn_id: 'T',
          message: { content: [{ type: 'tool_use', name: 'Command', input: { command: 'echo hi' } }] },
        },
      });
    });

    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'tool', content: 'Command({"command":"echo hi"})', nativeItemId: 'exec-X' },
    ]);
  });

  it('E1c · repeated non-delta full item and same-item multi-block replay are idempotent', () => {
    renderHook(() => useWebSocket());
    const envelope = (event: Record<string, unknown>) => ({
      type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
      taskSeq: 1, taskId: 'task-1', event,
    });
    act(() => {
      wsMock.trigger('worker.stream', envelope({
        type: 'assistant', item_id: 'full-item',
        message: { content: [{ type: 'text', text: 'full item' }] },
      }));
      wsMock.trigger('worker.stream', envelope({
        type: 'assistant', item_id: 'full-item',
        message: { content: [{ type: 'text', text: 'full item' }] },
      }));
      const multi = {
        type: 'assistant', final: true, replace: true, item_id: 'multi-item',
        message: { content: [
          { type: 'text', text: 'block one' },
          { type: 'text', text: 'block two' },
        ] },
      };
      wsMock.trigger('worker.stream', envelope(multi));
      wsMock.trigger('worker.stream', envelope(multi));
    });

    const rows = useSessionStore.getState().currentMessages;
    expect(rows.map((m) => m.content)).toEqual(['full item', 'block one', 'block two']);
    expect(rows.filter((m) => m.nativeItemId === 'multi-item')).toHaveLength(2);
    expect(new Set(rows.filter((m) => m.nativeItemId === 'multi-item').map((m) => m.blockId)).size)
      .toBe(2);
  });

  it('E1d · consecutive tasks with identical text remain two ordered messages', () => {
    renderHook(() => useWebSocket());
    act(() => {
      for (const [taskSeq, itemId] of [[1, 'item-1'], [2, 'item-2']] as const) {
        wsMock.trigger('worker.status', {
          type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0,
          taskSeq, taskId: `task-${taskSeq}`, status: 'running',
        });
        wsMock.trigger('worker.stream', {
          type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
          taskSeq, taskId: `task-${taskSeq}`, event: {
            type: 'assistant', final: true, replace: true,
            item_id: itemId, turn_id: `turn-${taskSeq}`,
            message: { content: [{ type: 'text', text: 'same body' }] },
          },
        });
        wsMock.trigger('worker.result', {
          type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0,
          taskSeq, taskId: `task-${taskSeq}`, status: 'done', result: 'same body',
        });
      }
    });

    expect(useSessionStore.getState().currentMessages
      .filter((m) => m.role === 'assistant').map((m) => m.content))
      .toEqual(['same body', 'same body']);
  });

  it('E1e · full lifecycle keeps user→thinking/tool→assistant→DONE across A/B switching', async () => {
    const a = mk('A');
    const b = mk('B');
    a.history = [{ role: 'user', content: 'question A', messageId: 'a-user' }];
    b.history = [{ role: 'user', content: 'question B', messageId: 'b-user' }];
    a.historyTotal = 1;
    b.historyTotal = 1;
    useSessionStore.setState({
      sessions: [a, b], currentSessionId: 'A', currentMessages: a.history,
    });
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.status', {
        type: 'worker.status', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-A-1', status: 'running',
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-A-1', event: {
          type: 'assistant', delta: true, replace: true, item_id: 'think-A',
          message: { content: [{ type: 'thinking', thinking: 'plan A' }] },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-A-1', event: {
          type: 'codex.item.completed', item_id: 'tool-A',
          item: { id: 'tool-A', type: 'Command', command: 'echo A' },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-A-1', event: {
          type: 'assistant', final: true, replace: true, item_id: 'answer-A',
          message: { content: [{ type: 'text', text: 'answer A' }] },
        },
      });
      wsMock.trigger('worker.result', {
        type: 'worker.result', sessionId: 'A', workerId: 'w1', generation: 0,
        taskSeq: 1, taskId: 'task-A-1', status: 'done', result: 'answer A',
      });
      // B is allowed to stream while A is selected, but must not enter A's
      // viewport.  Its own transcript is retained for the later switch.
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'B', workerId: 'w2', generation: 0,
        taskSeq: 3, taskId: 'task-B-3', event: {
          type: 'assistant', delta: true, stream_text: 'answer B', item_id: 'answer-B',
          message: { content: [{ type: 'text', text: 'answer B' }] },
        },
      });
    });

    expect(useSessionStore.getState().currentMessages.map((m) => `${m.role}:${m.content}`))
      .toEqual([
        'user:question A', 'thinking:plan A', 'tool:Command({"command":"echo A"})',
        'assistant:answer A', 'system:[DONE] Task completed',
      ]);

    await act(async () => { await useSessionStore.getState().selectSession('B'); });
    expect(useSessionStore.getState().currentMessages.map((m) => m.content))
      .toEqual(['question B', 'answer B']);
    await act(async () => { await useSessionStore.getState().selectSession('A'); });
    expect(useSessionStore.getState().currentMessages.map((m) => `${m.role}:${m.content}`))
      .toEqual([
        'user:question A', 'thinking:plan A', 'tool:Command({"command":"echo A"})',
        'assistant:answer A', 'system:[DONE] Task completed',
      ]);
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
