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
    useWorkerStore.setState({ workers: {}, currentWorkerId: null });
    useSessionStore.setState({ currentSessionId: null });
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
    useSessionStore.setState({ currentSessionId: 'ses_1' });
    useWorkerStore.getState().updateWorker('ses_1', 'worker-5', 'idle');
    mockKillSessionWorker.mockResolvedValue({ status: 'offline', workerId: 'worker-5' });

    await useWorkerStore.getState().killCurrent('ses_1');

    expect(useWorkerStore.getState().currentWorker).toBeNull();
    expect(mockKillSessionWorker).toHaveBeenCalledWith('ses_1');
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
