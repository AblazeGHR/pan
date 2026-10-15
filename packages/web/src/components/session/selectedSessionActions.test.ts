// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import {
  describeActionResults,
  runSelectedSessionAction,
  snapshotSelectedSessions,
} from '@/utils/selectedSessionActions';
import {
  ackSessionUnreadDone,
  killSessionWorker,
  requestDelayedRestart,
  restartOrStartWorker,
  setSessionMsgBridge,
} from '@/services/api';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  ackSessionUnreadDone: vi.fn(),
  setSessionMsgBridge: vi.fn(),
  restartOrStartWorker: vi.fn(),
  killSessionWorker: vi.fn(),
  requestDelayedRestart: vi.fn(),
}));

function delayedIntent(sessionId: string, action: 'start' | 'restart') {
  return {
    sessionId,
    workerId: action === 'restart' ? `worker-${sessionId}` : null,
    generation: action === 'restart' ? 1 : null,
    revision: 1,
    action,
    status: 'pending' as const,
    error: null,
  };
}

describe('selected Session actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({
      sessions: [
        {
          id: 'a',
          name: 'A',
          unreadDoneGeneration: 4,
          unreadDoneReadGeneration: 1,
          unreadDoneCount: 3,
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
          workerId: 'worker-a',
          workerStatus: 'idle',
        },
        {
          id: 'b',
          name: 'B',
          unreadDoneGeneration: 2,
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
          workerId: 'worker-b',
          workerStatus: 'running',
        },
        {
          id: 'c',
          name: 'C',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
      ],
    });
    useWorkerStore.setState({ workers: {}, delayedRestarts: {} });
  });

  it('keeps click-time targets and observed generations, preserving later unread completions', async () => {
    const targets = snapshotSelectedSessions(new Set(['a']), useSessionStore.getState().sessions);
    useSessionStore.getState().updateSession('a', { unreadDoneGeneration: 5, unreadDoneCount: 4 });
    vi.mocked(ackSessionUnreadDone).mockResolvedValue({
      ok: true,
      unreadDoneGeneration: 5,
      unreadDoneReadGeneration: 4,
      unreadDoneCount: 1,
    });
    const result = await runSelectedSessionAction(targets, 'read');
    expect(ackSessionUnreadDone).toHaveBeenCalledTimes(1);
    expect(ackSessionUnreadDone).toHaveBeenCalledWith('a', 4);
    expect(result[0]?.error).toBeUndefined();
    expect(useSessionStore.getState().sessions[0]?.unreadDoneCount).toBe(1);
  });

  it('does not clear the badge on failure and reports target-level partial success', async () => {
    vi.mocked(ackSessionUnreadDone)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({
        ok: true,
        unreadDoneGeneration: 2,
        unreadDoneReadGeneration: 2,
        unreadDoneCount: 0,
      });
    const result = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'b']), useSessionStore.getState().sessions),
      'read',
    );
    expect(useSessionStore.getState().sessions[0]?.unreadDoneCount).toBe(3);
    expect(describeActionResults(result)).toBe('1 succeeded, 1 failed: A: offline');
  });

  it('uses the connection API for both Bell directions and retains validation failures', async () => {
    vi.mocked(setSessionMsgBridge).mockRejectedValueOnce(new Error('Default contact unavailable'));
    const targets = snapshotSelectedSessions(new Set(['a']), useSessionStore.getState().sessions);
    expect((await runSelectedSessionAction(targets, 'bell-on'))[0]?.error).toBe(
      'Default contact unavailable',
    );
    expect(setSessionMsgBridge).toHaveBeenCalledWith('a', true);
    useSessionStore.getState().updateSession('a', { msgBridgeEnabled: true });
    vi.mocked(setSessionMsgBridge).mockResolvedValue({ ok: true, msgBridgeEnabled: false });
    await runSelectedSessionAction(targets, 'bell-off');
    expect(setSessionMsgBridge).toHaveBeenLastCalledWith('a', false);
  });

  it('snapshots the click-time worker view used by Kill', () => {
    useWorkerStore.getState().updateWorker('c', 'worker-c', 'idle');
    const targets = snapshotSelectedSessions(
      new Set(['a', 'b', 'c']),
      useSessionStore.getState().sessions,
    );
    expect(targets.map((target) => target.hasWorker)).toEqual([true, true, true]);
    // A later worker change must not retarget the snapshot.
    useWorkerStore.getState().updateWorker('b', null, null);
    useSessionStore.getState().updateSession('a', { workerId: null, workerStatus: null });
    useSessionStore.getState().updateSession('b', { workerId: null, workerStatus: null });
    expect(targets.map((target) => target.hasWorker)).toEqual([true, true, true]);
    expect(
      snapshotSelectedSessions(new Set(['a', 'b', 'c']), useSessionStore.getState().sessions).map(
        (target) => target.hasWorker,
      ),
    ).toEqual([false, false, true]);
  });

  it('restart reuses the worker-store lifecycle action per target', async () => {
    vi.mocked(restartOrStartWorker).mockResolvedValue({
      workerId: 'worker-a-new',
      sessionId: 'a',
      status: 'idle',
    });
    const results = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'b']), useSessionStore.getState().sessions),
      'restart',
    );
    expect(restartOrStartWorker).toHaveBeenCalledTimes(2);
    expect(vi.mocked(restartOrStartWorker).mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
    expect(results.map((result) => result.detail)).toEqual([
      'Restarted or started',
      'Restarted or started',
    ]);
    expect(describeActionResults(results)).toBe(
      '2 succeeded, 0 failed: A: Restarted or started; B: Restarted or started',
    );
    expect(useWorkerStore.getState().workers.a?.id).toBe('worker-a-new');
  });

  it('delayed restart reuses the existing intent mechanism and reports start vs restart', async () => {
    vi.mocked(requestDelayedRestart).mockImplementation(async (sessionId: string) =>
      delayedIntent(sessionId, sessionId === 'a' ? 'restart' : 'start'),
    );
    const results = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'c']), useSessionStore.getState().sessions),
      'delayed-restart',
    );
    expect(requestDelayedRestart).toHaveBeenCalledTimes(2);
    expect(results.map((result) => result.detail)).toEqual([
      'Delayed restart scheduled',
      'Delayed start scheduled',
    ]);
    expect(useWorkerStore.getState().delayedRestarts.a?.status).toBe('pending');
    expect(useWorkerStore.getState().delayedRestarts.c?.action).toBe('start');
    expect(describeActionResults(results)).toBe(
      '2 succeeded, 0 failed: A: Delayed restart scheduled; C: Delayed start scheduled',
    );
  });

  it('delayed restart reports per-target failures and keeps successful intents applied', async () => {
    vi.mocked(requestDelayedRestart).mockImplementation(async (sessionId: string) => {
      if (sessionId === 'b') throw new Error('Worker is held (takeover mode); use Restart after finishing takeover');
      return delayedIntent(sessionId, 'restart');
    });
    const results = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'b']), useSessionStore.getState().sessions),
      'delayed-restart',
    );
    expect(describeActionResults(results)).toContain('1 succeeded, 1 failed');
    expect(describeActionResults(results)).toContain('B: Worker is held (takeover mode)');
    expect(useWorkerStore.getState().delayedRestarts.a?.status).toBe('pending');
    expect(useWorkerStore.getState().delayedRestarts.b).toBeUndefined();
  });

  it('kill only requests workers that existed at click time and reports the honest no-op', async () => {
    vi.mocked(killSessionWorker).mockResolvedValue({ workerId: 'worker-a', status: 'offline' });
    const results = await runSelectedSessionAction(
      snapshotSelectedSessions(new Set(['a', 'c']), useSessionStore.getState().sessions),
      'kill',
    );
    expect(killSessionWorker).toHaveBeenCalledTimes(1);
    expect(killSessionWorker).toHaveBeenCalledWith('a');
    expect(results.map((result) => result.detail)).toEqual([
      'Kill sent',
      'No worker (already offline)',
    ]);
    expect(describeActionResults(results)).toBe(
      '2 succeeded, 0 failed: A: Kill sent; C: No worker (already offline)',
    );
    expect(useWorkerStore.getState().workers.a).toBeUndefined();
  });

  it('kill reports partial failures per target and stays retryable', async () => {
    vi.mocked(killSessionWorker).mockImplementation(async (sessionId: string) => {
      if (sessionId === 'b') throw new Error('Kill rejected');
      return { workerId: `worker-${sessionId}`, status: 'offline' };
    });
    const targets = snapshotSelectedSessions(
      new Set(['a', 'b']),
      useSessionStore.getState().sessions,
    );
    const results = await runSelectedSessionAction(targets, 'kill');
    expect(describeActionResults(results)).toBe(
      '1 succeeded, 1 failed: B: Kill rejected; A: Kill sent',
    );
    // Retry after failure re-requests the failed target through the same path.
    vi.mocked(killSessionWorker).mockClear();
    vi.mocked(killSessionWorker).mockResolvedValue({ workerId: 'worker-b', status: 'offline' });
    const retried = await runSelectedSessionAction(targets, 'kill');
    expect(killSessionWorker).toHaveBeenCalledTimes(2);
    expect(describeActionResults(retried)).toBe(
      '2 succeeded, 0 failed: A: Kill sent; B: Kill sent',
    );
  });

  it('reports a vanished selection target instead of acting on it', async () => {
    const targets = snapshotSelectedSessions(new Set(['a']), useSessionStore.getState().sessions);
    useSessionStore.setState({ sessions: [] });
    const results = await runSelectedSessionAction(targets, 'kill');
    expect(describeActionResults(results)).toBe('0 succeeded, 1 failed: A: Session no longer exists');
    expect(killSessionWorker).not.toHaveBeenCalled();
  });
});
