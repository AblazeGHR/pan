// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Message, Session } from '@/types';

function mk(id: string, name: string, extra?: Partial<Session>): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

function msg(role: string, content: string): Message {
  return { role, content };
}

// Deferred fetchSessions so tests can interleave WS updates / second refreshes
// while an HTTP load is in flight.
let pendingFetches: Array<(sessions: Session[]) => void> = [];
const api = vi.hoisted(() => ({ fetchSessionHistory: vi.fn() }));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: vi.fn(
      () =>
        new Promise<Session[]>((resolve) => {
          pendingFetches.push(resolve);
        }),
    ),
    fetchSessionHistory: api.fetchSessionHistory,
  };
});

function resolveNextFetch(sessions: Session[]) {
  const resolve = pendingFetches.shift();
  expect(resolve).toBeTruthy();
  resolve?.(sessions);
}

function resolveFetchAt(index: number, sessions: Session[]) {
  const resolve = pendingFetches[index];
  expect(resolve).toBeTruthy();
  resolve?.(sessions);
}

describe('sessionStore refresh staleness guards', () => {
  beforeEach(() => {
    pendingFetches = [];
    api.fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      sessionsLoading: false,
      currentSessionId: null,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      sessionTranscripts: {},
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
    });
  });

  it('keeps live-rendered messages when the server snapshot is a stale prefix', async () => {
    // Current session already shows streamed blocks the backend hasn't saved.
    const live = [msg('user', 'u1'), msg('assistant', 'a1'), msg('assistant', 'a2')];
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [live[0]!, live[1]!], workerStatus: 'running' })],
      currentSessionId: 'A',
      currentMessages: live,
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      // Server lags: its history is a prefix of what we already show locally.
      resolveNextFetch([
        mk('A', 'A', { history: [live[0]!, live[1]!], historyTotal: 2, workerStatus: 'running' }),
      ]);
      await promise!;
    });

    // currentMessages must NOT be clobbered by the stale prefix snapshot.
    expect(useSessionStore.getState().currentMessages).toEqual(live);
  });

  it('applies a fresh history page when it has content we do not have locally', async () => {
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [msg('user', 'u1')] })],
      currentSessionId: 'A',
      currentMessages: [msg('user', 'u1')],
    });

    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [msg('user', 'u1'), msg('assistant', 'a1')],
      total: 2,
      hasMore: false,
      start: 0,
      historyEpoch: 'epoch-current',
      historyRevision: 2,
    });
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().refreshCurrentSessionHistory();
    });
    await act(async () => {
      await promise!;
    });

    const { currentMessages } = useSessionStore.getState();
    expect(currentMessages).toHaveLength(2);
    expect(currentMessages[1]?.content).toBe('a1');
  });

  it('isolates a background history page from the selected transcript and pagination', async () => {
    const aHistory = [msg('user', 'A durable'), msg('assistant', 'A answer')];
    const aOptimistic = msg('user', 'A optimistic');
    const bTail = msg('assistant', 'B tail');
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: aHistory, historyTotal: 2 }),
        mk('B', 'B', { history: [], historyTotal: 100, historyStart: 0 }),
      ],
      currentSessionId: 'A',
      currentMessages: [...aHistory, aOptimistic],
      historyWindowStarts: { A: 0 },
      historyLoadEnd: 0,
      hasMoreMessages: false,
      sessionTranscripts: {},
    });

    // summaryBackfillCompleted/loadSessions is in flight while a background
    // Session B receives an authoritative page.
    let listRefresh: Promise<void>;
    act(() => {
      listRefresh = useSessionStore.getState().loadSessions();
      useSessionStore.getState().applyHistoryPage('B', {
        history: [bTail],
        start: 50,
        total: 100,
        hasMore: true,
        historyEpoch: 'B-epoch',
        historyRevision: 1,
      });
    });
    await act(async () => {
      resolveNextFetch([
        mk('A', 'A', { history: [], historyTotal: 2 }),
        mk('B', 'B', { history: [], historyTotal: 100 }),
      ]);
      await listRefresh!;
    });

    const afterBackgroundPage = useSessionStore.getState();
    expect(afterBackgroundPage.currentSessionId).toBe('A');
    expect(afterBackgroundPage.currentMessages.map((row) => row.content)).toEqual([
      'A durable', 'A answer', 'A optimistic',
    ]);
    // These are A's pagination fields. B's page must not overwrite them.
    expect(afterBackgroundPage.historyLoadEnd).toBe(0);
    expect(afterBackgroundPage.hasMoreMessages).toBe(false);
    expect(afterBackgroundPage.historyWindowStarts.A).toBe(0);
    expect(afterBackgroundPage.historyWindowStarts.B).toBe(50);
    expect(afterBackgroundPage.sessionTranscripts.B?.runtime).toEqual([]);

    // A→B must not reveal A's runtime rows that were adopted by B's page.
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [bTail],
      start: 50,
      total: 100,
      hasMore: true,
      historyEpoch: 'B-epoch',
      historyRevision: 1,
    });
    await act(async () => {
      await useSessionStore.getState().selectSession('B');
    });
    expect(useSessionStore.getState().currentMessages.map((row) => row.content)).toEqual([
      'B tail',
    ]);
  });

  it('keeps a selected transcript isolated while background terminal recovery is delayed', async () => {
    const aHistory = [msg('user', 'A durable')];
    const bFinal = msg('assistant', 'B final');
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: aHistory, historyTotal: 1 }),
        mk('B', 'B', {
          history: [],
          historyTotal: 0,
          historyEpoch: 'B-epoch',
          historyRevision: 0,
        }),
      ],
      currentSessionId: 'A',
      currentMessages: aHistory.slice(),
      historyWindowStarts: { A: 7 },
      historyLoadEnd: 7,
      hasMoreMessages: true,
      historyLoading: true,
      initialLoading: true,
      sessionTranscripts: {},
    });
    act(() => {
      useSessionStore.getState().appendLocalMessage('A', {
        role: 'user',
        content: 'A optimistic',
      });
      useSessionStore.getState().applyLiveStream('A', [
        { role: 'assistant', content: 'A live' },
      ], {
        serverEpoch: 'server',
        workerId: 'worker-A',
        generation: 1,
        taskSeq: 1,
      });
    });
    const beforeMessages = useSessionStore.getState().currentMessages.map((row) => ({
      role: row.role,
      content: row.content,
    }));
    const beforePaging = {
      historyLoadEnd: useSessionStore.getState().historyLoadEnd,
      hasMoreMessages: useSessionStore.getState().hasMoreMessages,
      historyLoading: useSessionStore.getState().historyLoading,
      initialLoading: useSessionStore.getState().initialLoading,
      historyWindowStarts: { ...useSessionStore.getState().historyWindowStarts },
    };

    let resolveRecovery!: (page: unknown) => void;
    api.fetchSessionHistory.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRecovery = resolve; }),
    );
    act(() => {
      // A background B terminal result schedules recoverSessionHistory(B).
      useSessionStore.getState().reconcileWorkerResult(
        'B',
        {
          status: 'done',
          result: bFinal.content,
          terminalCoverage: { historyEpoch: 'B-epoch', historyRevision: 1 },
        },
        { serverEpoch: 'server', workerId: 'worker-B', generation: 1, taskSeq: 1 },
      );
    });
    await act(async () => {
      for (let i = 0; i < 4; i += 1) await Promise.resolve();
    });
    expect(resolveRecovery).toBeTypeOf('function');

    resolveRecovery({
      history: [bFinal],
      start: 0,
      total: 1,
      hasMore: false,
      historyEpoch: 'B-epoch',
      historyRevision: 1,
    });
    await act(async () => {
      for (let i = 0; i < 4; i += 1) await Promise.resolve();
    });

    const afterRecovery = useSessionStore.getState();
    expect(afterRecovery.currentMessages.map((row) => ({
      role: row.role,
      content: row.content,
    }))).toEqual(beforeMessages);
    expect({
      historyLoadEnd: afterRecovery.historyLoadEnd,
      hasMoreMessages: afterRecovery.hasMoreMessages,
      historyLoading: afterRecovery.historyLoading,
      initialLoading: afterRecovery.initialLoading,
      selectedHistoryWindowStart: afterRecovery.historyWindowStarts.A,
    }).toEqual({
      historyLoadEnd: beforePaging.historyLoadEnd,
      hasMoreMessages: beforePaging.hasMoreMessages,
      historyLoading: beforePaging.historyLoading,
      initialLoading: beforePaging.initialLoading,
      selectedHistoryWindowStart: beforePaging.historyWindowStarts.A,
    });
    expect(afterRecovery.historyWindowStarts.B).toBe(0);
    expect(afterRecovery.sessionTranscripts.B?.runtime.map((row) => row.content))
      .not.toContain('A optimistic');
    expect(afterRecovery.sessionTranscripts.B?.runtime.map((row) => row.content))
      .not.toContain('A live');

    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [bFinal],
      start: 0,
      total: 1,
      hasMore: false,
      historyEpoch: 'B-epoch',
      historyRevision: 1,
    });
    await act(async () => {
      await useSessionStore.getState().selectSession('B');
    });
    expect(useSessionStore.getState().currentMessages.map((row) => row.content))
      .toEqual(['B final']);
  });

  it('drops an older background recovery response by the per-session sequence', async () => {
    const aRow = msg('user', 'A current');
    const bFresh = msg('assistant', 'B fresh');
    const bStale = msg('assistant', 'B stale');
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: [aRow], historyTotal: 1 }),
        mk('B', 'B', { history: [], historyTotal: 0 }),
      ],
      currentSessionId: 'A',
      currentMessages: [aRow],
      historyWindowStarts: { A: 0 },
      historyLoadEnd: 0,
      hasMoreMessages: false,
      sessionTranscripts: {},
    });

    const pending: Array<(page: unknown) => void> = [];
    api.fetchSessionHistory.mockImplementation(
      () => new Promise((resolve) => { pending.push(resolve); }),
    );
    let older!: Promise<void>;
    let newer!: Promise<void>;
    act(() => {
      older = useSessionStore.getState().recoverSessionHistory('B');
      newer = useSessionStore.getState().recoverSessionHistory('B');
    });
    expect(pending).toHaveLength(2);

    pending[1]!({
      history: [bFresh],
      start: 0,
      total: 1,
      hasMore: false,
      historyEpoch: 'B-new',
      historyRevision: 2,
    });
    await act(async () => { await newer!; });

    // The older request deliberately carries a higher revision to prove that
    // recover's request sequence, not a current-session restriction, owns the
    // response ordering boundary.
    pending[0]!({
      history: [bStale],
      start: 0,
      total: 1,
      hasMore: false,
      historyEpoch: 'B-old',
      historyRevision: 3,
    });
    await act(async () => { await older!; });

    const b = useSessionStore.getState().sessions.find((session) => session.id === 'B')!;
    expect(b.history.map((row) => row.content)).toEqual(['B fresh']);
    expect(b.historyEpoch).toBe('B-new');
    expect(b.historyRevision).toBe(2);
    expect(useSessionStore.getState().currentMessages).toEqual([aRow]);
  });

  it('does not clear selected loading flags when a background page is rejected', () => {
    const aRow = msg('user', 'A current');
    const bRow = msg('assistant', 'B already loaded');
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: [aRow], historyTotal: 1 }),
        mk('B', 'B', {
          history: [bRow],
          historyTotal: 1,
          historyEpoch: 'B-epoch',
          historyRevision: 5,
        }),
      ],
      currentSessionId: 'A',
      currentMessages: [aRow],
      historyWindowStarts: { A: 9, B: 0 },
      historyLoadEnd: 9,
      hasMoreMessages: true,
      historyLoading: true,
      initialLoading: true,
      sessionTranscripts: {},
    });

    act(() => {
      // Same object/offset/revision: B's page is a rejected no-op, but it is
      // still a background operation and must not settle A's global flags.
      useSessionStore.getState().applyHistoryPage('B', {
        history: [bRow],
        start: 0,
        total: 1,
        hasMore: false,
        historyEpoch: 'B-epoch',
        historyRevision: 5,
      });
    });

    const state = useSessionStore.getState();
    expect(state.currentMessages).toEqual([aRow]);
    expect(state.historyLoadEnd).toBe(9);
    expect(state.hasMoreMessages).toBe(true);
    expect(state.historyLoading).toBe(true);
    expect(state.initialLoading).toBe(true);
    expect(state.historyWindowStarts).toEqual({ A: 9, B: 0 });
  });

  it('does not use selected currentMessages for a same-shaped background page', () => {
    const aRow = { role: 'assistant', content: 'same text', queueItemIds: ['A'] } as Message;
    const bRow = { role: 'assistant', content: 'same text', queueItemIds: ['B'] } as Message;
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: [aRow], historyTotal: 1 }),
        mk('B', 'B', { history: [], historyTotal: 0 }),
      ],
      currentSessionId: 'A',
      currentMessages: [aRow],
      historyWindowStarts: { A: 0 },
      sessionTranscripts: {},
    });

    act(() => {
      useSessionStore.getState().applyHistoryPage('B', {
        history: [bRow],
        start: 0,
        total: 1,
        hasMore: false,
        historyEpoch: 'B-epoch',
        historyRevision: 1,
      });
    });

    expect(useSessionStore.getState().sessions.find((session) => session.id === 'B')?.history)
      .toEqual([bRow]);
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'B')?.history[0])
      .toBe(bRow);
  });

  it('does not revert workerStatus freshened by WS while a fetch is in flight', async () => {
    useSessionStore.setState({
      sessions: [mk('A', 'A', { workerStatus: 'offline' })],
      currentSessionId: null,
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });

    // WS event freshens workerStatus AFTER the fetch started.
    act(() => {
      useSessionStore.getState().updateSession('A', {
        workerStatus: 'running',
        workerId: 'w1',
      });
    });

    // Stale snapshot arrives (it predates the WS update).
    await act(async () => {
      resolveNextFetch([mk('A', 'A', { workerStatus: 'offline', history: [] })]);
      await promise!;
    });

    const s = useSessionStore.getState().sessions[0]!;
    expect(s.workerStatus).toBe('running'); // WS state preserved, not reverted
    expect(s.workerId).toBe('w1');
  });

  it('preserves explicit WS nulls instead of restoring a stale workerId', async () => {
    useSessionStore.setState({
      sessions: [mk('A', 'A', { workerStatus: 'idle', workerId: 'w1' })],
      currentSessionId: null,
    });
    act(() => {
      useSessionStore.getState().updateSession('A', {
        workerStatus: null,
        workerId: null,
      });
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      resolveNextFetch([mk('A', 'A', { workerStatus: 'idle', history: [] })]);
      await promise!;
    });

    const s = useSessionStore.getState().sessions[0]!;
    expect(s.workerStatus).toBeNull();
    expect(s.workerId).toBeNull();
  });

  it('keeps idle set right before a refresh over a transient done snapshot', async () => {
    // Mirrors the worker.result path: handleWorkerUpdate sets idle, then a
    // (debounced) refresh starts; its snapshot lands in the backend's transient
    // "done" window and must not override the local idle.
    useSessionStore.setState({
      sessions: [mk('A', 'A', { workerStatus: 'running' })],
      currentSessionId: null,
    });
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'idle' });
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      resolveNextFetch([mk('A', 'A', { workerStatus: 'done', history: [] })]);
      await promise!;
    });

    expect(useSessionStore.getState().sessions[0]?.workerStatus).toBe('idle');
  });

  it('discards an older in-flight refresh superseded by a newer one', async () => {
    useSessionStore.setState({ sessions: [], currentSessionId: null });

    let p1: Promise<void>;
    let p2: Promise<void>;
    act(() => {
      p1 = useSessionStore.getState().loadSessions();
      p2 = useSessionStore.getState().loadSessions();
    });

    // Newer refresh (2nd fetch) resolves first with the real list.
    await act(async () => {
      resolveFetchAt(1, [mk('B', 'B')]);
      await p2!;
    });
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(['B']);

    // The older, stale response resolves afterwards and must be discarded.
    await act(async () => {
      resolveFetchAt(0, [mk('A', 'A')]);
      await p1!;
    });
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(['B']);
  });

  it('carries the last-known workerId across a summary refresh while the worker is alive', async () => {
    // summary=1 omits workerId (server.py `_session_summary`) — a session whose
    // worker is alive (workerStatus present) must keep resolving its workerId
    // after a plain list refresh.
    useSessionStore.setState({
      sessions: [mk('A', 'A', { workerStatus: 'idle', workerId: 'w1' })],
      currentSessionId: null,
    });
    // Touch an unrelated session so the assertion cannot pass merely because
    // nothing was ever updated. 'A' itself is untouched, so the snapshot is
    // authoritative for it.
    act(() => {
      useSessionStore.getState().updateSession('B', { workerStatus: 'idle' });
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      // Snapshot mirrors summary=1: workerStatus present, workerId absent.
      resolveNextFetch([mk('A', 'A', { workerStatus: 'idle', history: [] })]);
      await promise!;
    });

    expect(useSessionStore.getState().sessions[0]?.workerId).toBe('w1');
  });

  it('drops workerId once the server stops reporting a live worker', async () => {
    // After a kill/crash the summary flips workerStatus to null — the stale
    // workerId must not keep the worker-action buttons alive.
    useSessionStore.setState({
      sessions: [mk('A', 'A', { workerStatus: 'idle', workerId: 'w1' })],
      currentSessionId: null,
    });
    act(() => {
      useSessionStore.getState().updateSession('B', { workerStatus: 'idle' });
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      resolveNextFetch([mk('A', 'A', { workerStatus: null, history: [] })]);
      await promise!;
    });

    expect(useSessionStore.getState().sessions[0]?.workerId).toBeFalsy();
  });

  it('toggles sessionsLoading while the list fetch is in flight', async () => {
    useSessionStore.setState({ sessions: [], currentSessionId: null });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    expect(useSessionStore.getState().sessionsLoading).toBe(true);

    await act(async () => {
      resolveNextFetch([mk('A', 'A')]);
      await promise!;
    });
    expect(useSessionStore.getState().sessionsLoading).toBe(false);
  });

  it('does not let a superseded refresh clear the newer request`s sessionsLoading', async () => {
    useSessionStore.setState({ sessions: [], currentSessionId: null });

    let p1: Promise<void>;
    let p2: Promise<void>;
    act(() => {
      p1 = useSessionStore.getState().loadSessions();
      p2 = useSessionStore.getState().loadSessions();
    });
    expect(useSessionStore.getState().sessionsLoading).toBe(true);

    // Newer refresh resolves first → loading ends.
    await act(async () => {
      resolveFetchAt(1, [mk('B', 'B')]);
      await p2!;
    });
    expect(useSessionStore.getState().sessionsLoading).toBe(false);

    // The superseded response resolving afterwards must not flip it back on.
    await act(async () => {
      resolveFetchAt(0, [mk('A', 'A')]);
      await p1!;
    });
    expect(useSessionStore.getState().sessionsLoading).toBe(false);
  });

  it('drives customOrder from the authoritative server order in custom sort mode', async () => {
    // Custom sort + a server snapshot that reflects a drag reorder (real mode,
    // no pan:mockDemo flag): loadSessions must align customOrder with the
    // server order so a stale/partial local order can never override it.
    useSessionStore.setState({ sessions: [], currentSessionId: null });
    useUIStore.setState({ sortBy: 'custom', customOrder: ['A', 'B'] });
    try {
      let promise: Promise<void>;
      act(() => {
        promise = useSessionStore.getState().loadSessions();
      });
      await act(async () => {
        resolveNextFetch([mk('B', 'B'), mk('C', 'C'), mk('A', 'A')]);
        await promise!;
      });

      expect(useUIStore.getState().customOrder).toEqual(['B', 'C', 'A']);
    } finally {
      useUIStore.setState({ sortBy: 'recent', customOrder: [] });
    }
  });
});
