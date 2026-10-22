// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useWorkerStore } from '@/stores/workerStore';
import { useSessionStore } from '@/stores/sessionStore';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    listWorkers: vi.fn(),
    killSessionWorker: vi.fn(),
    interruptSessionWorker: vi.fn(),
    takeoverSessionWorker: vi.fn(),
    restartOrStartWorker: vi.fn(),
  };
});

import { listWorkers, killSessionWorker, restartOrStartWorker } from '@/services/api';

const mockListWorkers = vi.mocked(listWorkers);
const mockKillSessionWorker = vi.mocked(killSessionWorker);
const mockRestartOrStartWorker = vi.mocked(restartOrStartWorker);

describe('workerStore currentWorker resolution', () => {
  beforeEach(() => {
    mockListWorkers.mockReset();
    mockKillSessionWorker.mockReset();
    mockRestartOrStartWorker.mockReset();
    useWorkerStore.setState({ workers: {}, currentWorkerId: null, runtimeEpoch: 'epoch-1' });
    useSessionStore.setState({ currentSessionId: null, sessions: [] });
  });

  it('resolves currentWorker by workerId via refresh() (list keyed by sessionId, id matches workerId)', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    mockListWorkers.mockResolvedValue([
      { workerId: 'worker-5', sessionId: 'ses_1', status: 'idle' },
      { workerId: 'worker-9', sessionId: 'ses_2', status: 'running' },
    ]);

    await useWorkerStore.getState().refresh();

    expect(useWorkerStore.getState().currentWorker).toEqual({
      id: 'worker-5',
      sessionId: 'ses_1',
      status: 'idle',
    });
  });

  it('refresh() leaves currentWorker null when the current session has no worker', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    mockListWorkers.mockResolvedValue([
      { workerId: 'worker-9', sessionId: 'ses_2', status: 'running' },
    ]);

    await useWorkerStore.getState().refresh();

    expect(useWorkerStore.getState().currentWorker).toBeNull();
  });

  it('updateWorker keeps the worker resolvable for the currently selected session', () => {
    useSessionStore.setState({ currentSessionId: 'ses_1' });

    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle');

    const w = useWorkerStore.getState().currentWorker;
    expect(w?.id).toBe('worker-5');
    expect(w?.sessionId).toBe('ses_1');
    expect(w?.status).toBe('idle');
  });

  it('updateWorker for another session must not hijack the current worker', () => {
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle');

    useWorkerStore.getState().updateWorker('ses_2', 'worker-9', 'running');

    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-5');
  });

  it('syncToSession picks up the selected session worker after a session switch', () => {
    // No session selected yet — updateWorker must not set currentWorkerId.
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle');
    expect(useWorkerStore.getState().currentWorker).toBeNull();

    useWorkerStore.getState().syncToSession('ses_1');
    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-5');
  });

  it('killCurrent routes by sessionId and clears that session worker', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
      { id: 'ses_1', name: 'target', workerId: 'worker-5', workerGeneration: 10, workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
    ] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', 10);
    mockKillSessionWorker.mockResolvedValue({ status: 'offline', workerId: 'worker-5' });

    await useWorkerStore.getState().killCurrent('ses_1');

    expect(useWorkerStore.getState().currentWorker).toBeNull();
    expect(mockKillSessionWorker).toHaveBeenCalledWith('ses_1');
    expect(useSessionStore.getState().sessions[0]).toMatchObject({ workerId: null, workerStatus: null });
  });

  it.each(['status', 'usage'] as const)('clears both stores after a same-runtime %s update during Kill', async (update) => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
      { id: 'ses_1', name: 'target', workerId: 'worker-5', workerGeneration: 10, workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
    ] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', 10);
    let finish!: (value: { status: string }) => void;
    mockKillSessionWorker.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const killing = useWorkerStore.getState().killCurrent('ses_1');
    if (update === 'status') useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'running', 10);
    else useWorkerStore.getState().updateNativeUsage('ses_1', 'worker-5', { inputTokens: 123 });
    useSessionStore.getState().updateSession('ses_1', { workerStatus: 'running' });
    finish({ status: 'offline' });
    await killing;
    expect(useWorkerStore.getState().workers.ses_1).toBeUndefined();
    expect(useWorkerStore.getState().currentWorker).toBeNull();
    expect(useSessionStore.getState().sessions[0]).toMatchObject({ workerId: null, workerStatus: null });
  });

  it.each(['worker', 'session'] as const)('preserves both stores when the %s store gains an uncertain or successor identity', async (store) => {
    for (const change of ['generation', 'workerId', 'unknown', 'newly-known', 'still-unknown'] as const) {
      const initialGeneration = change === 'newly-known' || change === 'still-unknown' ? undefined : 10;
      useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null });
      useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
        { id: 'ses_1', name: 'target', workerId: 'worker-5', workerGeneration: initialGeneration, workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
      ] });
      useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', initialGeneration);
      let finish!: (value: { status: string }) => void;
      mockKillSessionWorker.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
      const killing = useWorkerStore.getState().killCurrent('ses_1');
      const id = change === 'workerId' ? 'worker-6' : 'worker-5';
      const generation = change === 'unknown' || change === 'still-unknown' ? undefined : change === 'workerId' ? 10 : 11;
      if (store === 'worker') {
        useWorkerStore.setState({ workers: { ses_1: { id, sessionId: 'ses_1', status: 'idle', generation } } });
      } else useSessionStore.getState().updateSession('ses_1', { workerId: id, workerGeneration: generation });
      const beforeWorker = useWorkerStore.getState().workers.ses_1;
      const beforeSession = useSessionStore.getState().sessions[0];
      finish({ status: 'offline' });
      await killing;
      expect(useWorkerStore.getState().workers.ses_1).toEqual(beforeWorker);
      expect(useSessionStore.getState().sessions[0]).toEqual(beforeSession);
    }
  });

  it('preserves both stores when the service epoch changes during Kill', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
      { id: 'ses_1', name: 'target', workerId: 'worker-5', workerGeneration: 10, workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
    ] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', 10);
    let finish!: (value: { status: string }) => void;
    mockKillSessionWorker.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const killing = useWorkerStore.getState().killCurrent('ses_1');
    useWorkerStore.setState({ runtimeEpoch: 'epoch-2' });
    finish({ status: 'offline' });
    await killing;
    expect(useWorkerStore.getState().workers.ses_1?.id).toBe('worker-5');
    expect(useSessionStore.getState().sessions[0]?.workerId).toBe('worker-5');
  });

  it.each(['worker', 'session'] as const)('preserves active entries when the %s generation stays unknown', async (store) => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
      { id: 'ses_1', name: 'target', workerId: 'worker-5', workerGeneration: store === 'session' ? undefined : 10,
        workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
    ] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', store === 'worker' ? undefined : 10);
    mockKillSessionWorker.mockResolvedValue({ status: 'offline', workerId: 'worker-5' });
    await useWorkerStore.getState().killCurrent('ses_1');
    expect(useWorkerStore.getState().workers.ses_1?.id).toBe('worker-5');
    expect(useSessionStore.getState().sessions[0]?.workerId).toBe('worker-5');
  });

  it('a rejected Kill preserves the runtime and Session projection', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [
      { id: 'ses_1', name: 'target', workerId: 'worker-5', workerStatus: 'idle', history: [], alwaysThinkingEnabled: false, effort: '' },
    ] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle');
    mockKillSessionWorker.mockRejectedValue(new Error('owner retained'));
    await expect(useWorkerStore.getState().killCurrent('ses_1')).rejects.toThrow('owner retained');
    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-5');
    expect(useSessionStore.getState().sessions[0]?.workerStatus).toBe('idle');
  });

  it('a late Kill response preserves a successor runtime', async () => {
    useSessionStore.setState({ currentSessionId: 'ses_1', sessions: [] });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle', 10);
    let finish!: (value: { status: string }) => void;
    mockKillSessionWorker.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const killing = useWorkerStore.getState().killCurrent('ses_1');
    useWorkerStore.getState().updateWorker('ses_1', 'worker-6', 'idle', 11);
    finish({ status: 'offline' });
    await killing;
    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-6');
  });

  it('restart routes by sessionId so a stale workerId cannot cause Worker not found', async () => {
    // updateWorker only promotes a worker to currentWorkerId for the selected
    // session, matching the production TopBar/SettingsPopover call path.
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    mockRestartOrStartWorker.mockResolvedValue({
      workerId: 'worker-new',
      sessionId: 'ses_1',
      status: 'idle',
    });

    await useWorkerStore.getState().restart('ses_1');

    expect(mockRestartOrStartWorker).toHaveBeenCalledWith('ses_1');
    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-new');
  });

  it('ignores a late destroyed event from an older worker generation', () => {
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-new', 'idle', 3);

    useWorkerStore.getState().updateWorker('ses_1', 'worker-old', null, 2, true);

    expect(useWorkerStore.getState().currentWorker?.id).toBe('worker-new');
    expect(useWorkerStore.getState().workers.ses_1?.generation).toBe(3);
  });
});
