// @vitest-environment jsdom
// Durable Illegal survives stale snapshots and converges after successful recovery.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, render, screen, cleanup } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { WorkerDot } from '@/components/worker/WorkerDot';
import type { Session } from '@/types';

const harness = vi.hoisted(() => ({
  fetchImpl: null as null | (() => Promise<Session[]>),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: vi.fn(() => {
      const impl = harness.fetchImpl;
      if (!impl) return Promise.resolve([] as Session[]);
      return impl();
    }),
  };
});

function mk(
  id: string,
  extra: Partial<Session> = {},
): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    illegalOffline: false,
    legalOffline: true,
    ...extra,
  } as Session;
}

/** Post-restart failed execution awaiting a valid successor. */
function pendingRecoverySession(id = 's1'): Session {
  return mk(id, { workerStatus: null, lastLegalWorkerState: 'running', illegalOffline: true, legalOffline: false });
}

function dotClass(): string {
  const dot = document.body.querySelector('span[class*="rounded-full"]');
  return dot?.className ?? '';
}

function renderDotFor(session: Session): void {
  cleanup();
  render(
    <WorkerDot status={session.workerStatus} illegalOffline={session.illegalOffline} />,
  );
}

function storeSession(id = 's1'): Session {
  const found = useSessionStore.getState().sessions.find((s) => s.id === id);
  if (!found) throw new Error(`session ${id} missing`);
  return found;
}

beforeEach(() => {
  harness.fetchImpl = null;
  useSessionStore.setState({
    sessions: [pendingRecoverySession()],
    currentSessionId: null,
    currentMessages: [],
    serverEpoch: 'epoch-restart',
    liveStreamBuffers: {},
    terminalWatermarks: {},
    _sessionWsTouchedSeq: {},
    _sessionEventPatches: {},
  });
  useUIStore.setState({ activeWorkspaceId: 'all' });
});

afterEach(cleanup);

describe('restart worker indicator convergence', () => {
  it('converges the mismatch red to green when the authoritative snapshot reports idle/idle', async () => {
    // Pre-convergence: legal running + no live worker → deliberate mismatch red.
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-danger');

    // The backend synchronized the legal ledger to the idle runtime (recovery
    // choice or task completion); the next snapshot carries idle/idle.
    harness.fetchImpl = () => Promise.resolve([
      mk('s1', {
        workerStatus: 'idle',
        lastLegalWorkerState: 'idle',
        summaryRevision: 7,
      }),
    ]);

    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });

    expect(storeSession()).toMatchObject({
      workerStatus: 'idle',
      lastLegalWorkerState: 'idle',
    });
    // The stale red must not survive the authoritative convergence.
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-success');
    expect(dotClass()).not.toContain('bg-danger');
  });

  it('keeps a genuine durable Illegal red across refreshes', async () => {
    harness.fetchImpl = () => Promise.resolve([pendingRecoverySession()]);
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    // An unresolved durable Illegal stays red independently of the old ledger.
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-danger');
    expect(screen.queryByLabelText(/Illegal offline/)).toBeTruthy();
  });

  it('keeps a real runtime error red regardless of the legal ledger', () => {
    renderDotFor(mk('s1', { workerStatus: 'error', lastLegalWorkerState: null }));
    expect(dotClass()).toContain('bg-danger');
    // Pure runtime error has no mismatch aria label — distinct semantics.
    expect(screen.queryByLabelText(/Illegal offline/)).toBeNull();
  });

  it('keeps a green running dot when legal running matches the live runtime', () => {
    renderDotFor(mk('s1', { workerStatus: 'running', lastLegalWorkerState: 'running' }));
    expect(dotClass()).toContain('bg-accent');
    expect(dotClass()).not.toContain('bg-danger');
  });
});

describe('stale cross-restart responses and events', () => {
  it('accepts newer durable legality even when a crash patch has no execution cursor', () => {
    useSessionStore.setState({ sessions: [mk('s1', {
      workerId: 'w1', workerGeneration: 0, workerExecutionSeq: 1,
      workerTaskSeq: 1, workerStatus: 'running', summaryRevision: 4,
      legalOffline: false, illegalOffline: false,
    })] });
    act(() => useSessionStore.getState().updateSession('s1', {
      workerId: 'w1', workerGeneration: 0, workerExecutionSeq: null,
      summaryRevision: 5, legalOffline: false, illegalOffline: true,
    }, true));
    expect(storeSession().illegalOffline).toBe(true);
    // A delayed pre-crash snapshot cannot erase the durable Illegal fact.
    act(() => useSessionStore.getState().updateSession('s1', {
      summaryRevision: 4, legalOffline: true, illegalOffline: false,
    }));
    expect(storeSession().illegalOffline).toBe(true);
  });

  it('discards a mid-shutdown snapshot served before the server epoch change', async () => {
    // Request issued while the old process was still serving: its snapshot
    // still shows the pre-restart running Worker.
    let resolveStale!: (value: Session[]) => void;
    harness.fetchImpl = () => new Promise<Session[]>((resolve) => {
      resolveStale = resolve;
    });
    let pending: Promise<void>;
    act(() => {
      pending = useSessionStore.getState().loadSessions();
    });

    // The Pan restart completes and the client accepts the new server epoch.
    act(() => {
      useSessionStore.getState().acceptServerEpoch('epoch-next');
    });

    // The old process's response lands after the epoch change.
    await act(async () => {
      resolveStale([mk('s1', {
        workerStatus: 'running',
        lastLegalWorkerState: 'running',
      })]);
      await pending!;
    });

    // The stale mid-shutdown state must not overwrite the post-restart truth.
    expect(storeSession()).toMatchObject({
      workerStatus: null,
      lastLegalWorkerState: 'running',
    });

    // A normal post-restart snapshot still applies.
    harness.fetchImpl = () => Promise.resolve([
      mk('s1', { workerStatus: 'idle', lastLegalWorkerState: 'idle' }),
    ]);
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    expect(storeSession()).toMatchObject({
      workerStatus: 'idle',
      lastLegalWorkerState: 'idle',
    });
  });

  it('does not let a delayed snapshot revert a WS idle that arrived while it was in flight', async () => {
    useSessionStore.setState({
      sessions: [mk('s1', { workerStatus: 'running', lastLegalWorkerState: 'running' })],
    });
    let resolveSnapshot!: (value: Session[]) => void;
    harness.fetchImpl = () => new Promise<Session[]>((resolve) => {
      resolveSnapshot = resolve;
    });
    let pending: Promise<void>;
    act(() => {
      pending = useSessionStore.getState().loadSessions();
    });

    // Task completion settles the Worker idle before the snapshot returns.
    act(() => {
      useSessionStore.getState().applyWorkerStatus('s1', 'idle', {
        serverEpoch: 'epoch-restart',
        workerId: 'w1',
        generation: 3,
        taskSeq: 9,
        legalOffline: true,
        illegalOffline: false,
      });
    });

    await act(async () => {
      resolveSnapshot([mk('s1', {
        workerStatus: 'running',
        lastLegalWorkerState: 'running',
      })]);
      await pending!;
    });

    // The WS write is newer than the snapshot. Old restore-intent metadata
    // cannot turn a safely completed execution into an Illegal condition.
    expect(storeSession().workerStatus).toBe('idle');
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-success');
    expect(storeSession().illegalOffline).toBe(false);

    // The next snapshot carries the settled legal ledger as well.
    harness.fetchImpl = () => Promise.resolve([
      mk('s1', { workerStatus: 'idle', lastLegalWorkerState: 'idle', summaryRevision: 9 }),
    ]);
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-success');
    expect(dotClass()).not.toContain('bg-danger');
  });
});
