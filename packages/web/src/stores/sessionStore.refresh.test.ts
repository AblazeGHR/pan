// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
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
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _touchSeq: 0,
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

  it('applies server history when it has content we do not have locally', async () => {
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [msg('user', 'u1')] })],
      currentSessionId: 'A',
      currentMessages: [msg('user', 'u1')],
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().loadSessions();
    });
    await act(async () => {
      resolveNextFetch([
        mk('A', 'A', {
          history: [msg('user', 'u1'), msg('assistant', 'a1')],
          historyTotal: 2,
        }),
      ]);
      await promise!;
    });

    const { currentMessages } = useSessionStore.getState();
    expect(currentMessages).toHaveLength(2);
    expect(currentMessages[1]?.content).toBe('a1');
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
});
