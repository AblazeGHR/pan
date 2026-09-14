// @vitest-environment jsdom
// Regression coverage for T-030: a worker's terminal state is known to the
// server but the session/worker status indicator keeps showing the old state.
//
// The indicator (WorkerDot in SessionItem/TopBar/SessionList) is driven by
// `Session.workerStatus`, which is written by WS worker events. The backend
// also exposes the authoritative live status through the (debounced / on
// reconnect / on focus) `/api/sessions?summary=1` snapshot. These tests pin the
// merge rule between the two so a terminal event the client never received can
// still be corrected, without letting a stale snapshot revert fresher WS state.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

function mk(id: string, extra?: Partial<Session>): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

// Deferred fetchSessions so a test can interleave WS writes with an in-flight
// snapshot resolution.
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

function beginLoad(): Promise<void> {
  let promise!: Promise<void>;
  act(() => {
    promise = useSessionStore.getState().loadSessions();
  });
  return promise;
}

async function resolveLoad(promise: Promise<void>, sessions: Session[]): Promise<void> {
  await act(async () => {
    const resolve = pendingFetches.shift();
    expect(resolve).toBeTruthy();
    resolve?.(sessions);
    await promise;
  });
}

function statusOf(id: string): string | null | undefined {
  return useSessionStore.getState().sessions.find((s) => s.id === id)?.workerStatus;
}

describe('done indicator latency — snapshot vs WS worker status', () => {
  beforeEach(() => {
    pendingFetches = [];
    useSessionStore.setState({
      sessions: [],
      sessionsLoading: false,
      currentSessionId: null,
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
    });
  });

  it('corrects a stuck running status from the authoritative snapshot when the terminal event was missed', async () => {
    // The completion never reached this client (dropped socket / missed
    // broadcast). 'A' is locally stuck on its last WS write and is also the most
    // recently touched session — the case the old global-counter guard shielded
    // forever, so no refresh (including reconnect/focus recovery) could fix it.
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'running', workerId: 'w1' })],
    });
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'running', workerId: 'w1' });
    });

    const promise = beginLoad();
    await resolveLoad(promise, [mk('A', { workerStatus: 'idle', workerId: 'w1' })]);

    expect(statusOf('A')).toBe('idle');
  });

  it('settles running -> done: a summary reporting idle replaces the running dot', async () => {
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'running', workerId: 'w1' }), mk('B')],
    });
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'running' });
      // An unrelated session's traffic must not decide 'A's fate (the old guard
      // keyed on a global counter, so this used to flip the outcome).
      useSessionStore.getState().updateSession('B', { workerStatus: 'running' });
    });

    const promise = beginLoad();
    await resolveLoad(promise, [
      mk('A', { workerStatus: 'idle', workerId: 'w1' }),
      mk('B', { workerStatus: 'running' }),
    ]);

    expect(statusOf('A')).toBe('idle');
    expect(statusOf('B')).toBe('running');
  });

  it('does not let an older running snapshot revert a status settled while the fetch was in flight', async () => {
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'running', workerId: 'w1' })],
    });

    const promise = beginLoad();
    // worker.result lands while the request is in flight → strictly newer than
    // the response, which still describes the pre-completion state.
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'idle' });
    });
    await resolveLoad(promise, [mk('A', { workerStatus: 'running', workerId: 'w1' })]);

    expect(statusOf('A')).toBe('idle');
  });

  it('applies the snapshot per session: a freshened session is preserved while a stale one is corrected', async () => {
    // 'A' is stuck running (missed terminal event); 'B' is settled by a WS event
    // during the fetch. Both must resolve independently.
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'running' }), mk('B', { workerStatus: 'running' })],
    });
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'running' });
    });

    const promise = beginLoad();
    act(() => {
      useSessionStore.getState().updateSession('B', { workerStatus: 'idle' });
    });
    await resolveLoad(promise, [
      mk('A', { workerStatus: 'idle' }),
      mk('B', { workerStatus: 'running' }),
    ]);

    expect(statusOf('A')).toBe('idle');
    expect(statusOf('B')).toBe('idle');
  });

  it('does not resurrect a destroyed worker when the summary lags behind the crash', async () => {
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'idle', workerId: 'w1' })],
    });
    // worker.destroyed / worker.crashed write an explicit null.
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: null, workerId: null });
    });

    const promise = beginLoad();
    await resolveLoad(promise, [mk('A', { workerStatus: 'idle', workerId: 'w1' })]);

    expect(statusOf('A')).toBeNull();
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'A')?.workerId).toBeNull();
  });

  it('keeps a settled status over the backend transient "done" window', async () => {
    // The backend holds `w.status = "done"` only between the worker.result
    // broadcast and its reset to "idle"; a snapshot landing inside that window
    // must not turn the dot back into a terminal/offline-looking state.
    useSessionStore.setState({
      sessions: [mk('A', { workerStatus: 'running' })],
    });
    act(() => {
      useSessionStore.getState().updateSession('A', { workerStatus: 'idle' });
    });

    const promise = beginLoad();
    await resolveLoad(promise, [mk('A', { workerStatus: 'done' })]);

    expect(statusOf('A')).toBe('idle');
  });

  it('adopts the summary status after a session switch for sessions the client never touched', async () => {
    // A freshly loaded / switched-to session has no local WS history, so the
    // snapshot (not a stale null) decides its indicator.
    useSessionStore.setState({
      sessions: [mk('A'), mk('B', { workerStatus: 'idle', workerId: 'w2' })],
      currentSessionId: 'B',
    });

    const promise = beginLoad();
    await resolveLoad(promise, [
      mk('A', { workerStatus: 'running', workerId: 'w9' }),
      mk('B', { workerStatus: 'idle', workerId: 'w2' }),
    ]);

    expect(statusOf('A')).toBe('running');
    expect(statusOf('B')).toBe('idle');
    // Switching the selected session must not move one session's status onto
    // another.
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'B')?.workerId).toBe('w2');
  });
});
