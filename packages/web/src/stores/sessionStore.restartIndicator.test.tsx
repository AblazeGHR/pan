// @vitest-environment jsdom
// Regression coverage for the Worker indicator across a Pan service restart.
//
// The dot's red has two distinct meanings that must both survive this suite:
//   1. legal-running mismatch ("Illegal state"): legal ledger says running but
//      the live Worker does not — a deliberate product state after a
//      preserve-running restart, resolved only by an explicit recovery choice.
//   2. a real runtime error status.
// The bug class covered here: after the authoritative runtime settles (e.g.
// legal synchronized to the idle runtime), a stale pre-restart HTTP snapshot,
// a late WS event, or a missing convergence refresh must not keep the stale
// mismatch red — nor may any guard mask a genuine error/illegal state.
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
    ...extra,
  } as Session;
}

/** Post-restart truth while recovery is pending: preserved legal running,
 *  no live Worker. Renders the mismatch red by design. */
function pendingRecoverySession(id = 's1'): Session {
  return mk(id, { workerStatus: null, lastLegalWorkerState: 'running' });
}

function dotClass(): string {
  const dot = document.body.querySelector('span[class*="rounded-full"]');
  return dot?.className ?? '';
}

function renderDotFor(session: Session): void {
  cleanup();
  render(
    <WorkerDot status={session.workerStatus} legalState={session.lastLegalWorkerState} />,
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

  it('keeps the deliberate preserve-running mismatch red across refreshes', async () => {
    harness.fetchImpl = () => Promise.resolve([pendingRecoverySession()]);
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    // preserve-running recovery leaves legal=running with no live Worker:
    // the red "Illegal state" indicator is the product semantics, not a bug.
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-danger');
    expect(screen.queryByLabelText(/Legal Worker state is running/)).toBeTruthy();
  });

  it('keeps a real runtime error red regardless of the legal ledger', () => {
    renderDotFor(mk('s1', { workerStatus: 'error', lastLegalWorkerState: null }));
    expect(dotClass()).toContain('bg-danger');
    // Pure runtime error has no mismatch aria label — distinct semantics.
    expect(screen.queryByLabelText(/Legal Worker state is running/)).toBeNull();
  });

  it('keeps a green running dot when legal running matches the live runtime', () => {
    renderDotFor(mk('s1', { workerStatus: 'running', lastLegalWorkerState: 'running' }));
    expect(dotClass()).toContain('bg-accent');
    expect(dotClass()).not.toContain('bg-danger');
  });
});

describe('stale cross-restart responses and events', () => {
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
      });
    });

    await act(async () => {
      resolveSnapshot([mk('s1', {
        workerStatus: 'running',
        lastLegalWorkerState: 'running',
      })]);
      await pending!;
    });

    // The WS write is newer than the snapshot: idle (green) must survive; a
    // legal running ledger with a live idle worker still renders the
    // transient mismatch red until the next authoritative snapshot.
    expect(storeSession().workerStatus).toBe('idle');
    renderDotFor(storeSession());
    expect(dotClass()).toContain('bg-danger');

    // The next snapshot carries the settled legal ledger → green.
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
