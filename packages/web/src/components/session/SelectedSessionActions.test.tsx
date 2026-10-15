// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { SelectedSessionActions } from './SelectedSessionActions';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';
import {
  fetchSessions,
  killSessionWorker,
  requestDelayedRestart,
  restartOrStartWorker,
} from '@/services/api';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessions: vi.fn(),
  restartOrStartWorker: vi.fn(),
  killSessionWorker: vi.fn(),
  requestDelayedRestart: vi.fn(),
}));

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [], ...extra };
}

const withWorker = (id: string, name: string, status = 'idle'): Session =>
  mk(id, name, { workerId: `worker-${id}`, workerStatus: status });

function seedStores(selected: string[]) {
  useSessionStore.setState({
    sessions: [withWorker('a', 'A'), withWorker('b', 'B', 'running'), mk('c', 'C')],
    multiSelectMode: true,
    selectedIds: new Set(selected),
  });
  useWorkerStore.setState({ workers: {}, delayedRestarts: {} });
  useUIStore.setState({ toastQueue: [] });
}

function actionsButton(): HTMLButtonElement {
  return document.querySelector('button[aria-haspopup="menu"]') as HTMLButtonElement;
}

function openMenu() {
  fireEvent.click(actionsButton());
}

function menuItem(name: string): HTMLButtonElement {
  const item = [...document.querySelectorAll('button[role="menuitem"]')].find(
    (element) => element.textContent?.trim() === name,
  );
  if (!item) throw new Error(`Menu item ${name} not found`);
  return item as HTMLButtonElement;
}

function menuExists(): boolean {
  return document.querySelectorAll('button[role="menuitem"]').length > 0;
}

function toastMessages(): string[] {
  return useUIStore.getState().toastQueue.map((toast) => toast.message);
}

describe('SelectedSessionActions lifecycle menu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // The refresh after an action re-reads the current durable sessions so
    // later snapshots (retries) keep observing the same workers.
    vi.mocked(fetchSessions).mockImplementation(async () =>
      useSessionStore.getState().sessions.map((session) => ({ ...session })),
    );
    vi.mocked(restartOrStartWorker).mockImplementation(async (sessionId: string) => ({
      workerId: `worker-${sessionId}-new`,
      sessionId,
      status: 'idle',
    }));
    vi.mocked(killSessionWorker).mockResolvedValue({
      workerId: 'worker-x',
      status: 'offline',
    });
    vi.mocked(requestDelayedRestart).mockImplementation(async (sessionId: string) => ({
      sessionId,
      workerId: `worker-${sessionId}`,
      generation: 1,
      revision: 1,
      action: 'restart' as const,
      status: 'pending' as const,
      error: null,
    }));
    seedStores(['a', 'b']);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('offers Restart, Delayed restart and Kill under the single-Session names', () => {
    seedStores(['a']);
    render(<SelectedSessionActions />);
    openMenu();
    expect(menuItem('Restart')).toBeTruthy();
    expect(menuItem('Delayed restart')).toBeTruthy();
    expect(menuItem('Kill')).toBeTruthy();
  });

  it('disables Kill until a target has a live worker, with an explaining title', () => {
    seedStores(['c']);
    const first = render(<SelectedSessionActions />);
    openMenu();
    const kill = menuItem('Kill');
    expect(kill.disabled).toBe(true);
    expect(kill.title).toBe('No selected Session has a live worker to kill');
    first.unmount();

    seedStores(['a', 'c']);
    render(<SelectedSessionActions />);
    openMenu();
    expect(menuItem('Kill').disabled).toBe(false);
    expect(menuItem('Kill').title).toBe('Kill the live workers of the selected Sessions');
  });

  it('kill asks for confirmation, then requests every worker-backed target', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Kill'));
    });
    expect(confirmSpy).toHaveBeenCalledWith('Kill workers for 2 selected session(s)?');
    expect(killSessionWorker).not.toHaveBeenCalled();
    expect(menuItem('Kill')).toBeTruthy(); // dismissed confirmation keeps the menu open

    confirmSpy.mockReturnValue(true);
    await act(async () => {
      fireEvent.click(menuItem('Kill'));
    });
    expect(vi.mocked(killSessionWorker).mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
    expect(toastMessages()).toContain('2 succeeded, 0 failed: A: Kill sent; B: Kill sent');
    expect(menuExists()).toBe(false);
    expect(fetchSessions).toHaveBeenCalled();
  });

  it('kill reports a worker-less target as an honest no-op instead of requesting it', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    seedStores(['a', 'c']);
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Kill'));
    });
    expect(vi.mocked(killSessionWorker).mock.calls.map(([id]) => id)).toEqual(['a']);
    expect(toastMessages()).toContain(
      '2 succeeded, 0 failed: A: Kill sent; C: No worker (already offline)',
    );
  });

  it('kill surfaces per-target partial failure as an error toast and stays retryable', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(killSessionWorker).mockImplementation(async (sessionId: string) => {
      if (sessionId === 'b') throw new Error('Kill rejected');
      return { workerId: `worker-${sessionId}`, status: 'offline' };
    });
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Kill'));
    });
    const toasts = useUIStore.getState().toastQueue;
    expect(toasts.some((toast) => toast.type === 'error')).toBe(true);
    expect(toastMessages()).toContain('1 succeeded, 1 failed: B: Kill rejected; A: Kill sent');

    // Retry: the failed target is requested again through the same path.
    vi.mocked(killSessionWorker).mockClear();
    vi.mocked(killSessionWorker).mockResolvedValue({
      workerId: 'worker-b',
      status: 'offline',
    });
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Kill'));
    });
    expect(vi.mocked(killSessionWorker).mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
    expect(toastMessages()).toContain('2 succeeded, 0 failed: A: Kill sent; B: Kill sent');
  });

  it('restart runs for every target through the shared worker lifecycle action', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Restart'));
    });
    expect(vi.mocked(restartOrStartWorker).mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
    expect(toastMessages()).toContain(
      '2 succeeded, 0 failed: A: Restarted or started; B: Restarted or started',
    );
    expect(fetchSessions).toHaveBeenCalled();
  });

  it('acts on the click-time snapshot even when the selection changes mid-flight', async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(restartOrStartWorker).mockImplementation(async (sessionId: string) => {
      await gate;
      return { ok: true, workerId: `worker-${sessionId}-new`, sessionId, status: 'idle' };
    });
    render(<SelectedSessionActions />);
    openMenu();
    // Switch the selection while the menu is open; the snapshot must not move.
    act(() => {
      useSessionStore.setState({ selectedIds: new Set(['c']) });
    });
    await act(async () => {
      fireEvent.click(menuItem('Restart'));
    });
    // Duplicate submission is blocked while the action is in flight.
    expect(actionsButton().disabled).toBe(true);
    await act(async () => {
      release?.();
    });
    expect(vi.mocked(restartOrStartWorker).mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
    expect(actionsButton().disabled).toBe(false);
  });

  it('disables Delayed restart while a target intent is pending and re-enables after it settles', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    expect(menuItem('Delayed restart').disabled).toBe(false);
    expect(menuItem('Delayed restart').title).toBe(
      'Finish the current task, then restart before queued work. Starts a worker if offline.',
    );
    await act(async () => {
      fireEvent.click(menuItem('Delayed restart'));
    });
    expect(requestDelayedRestart).toHaveBeenCalledTimes(2);
    expect(toastMessages()).toContain(
      '2 succeeded, 0 failed: A: Delayed restart scheduled; B: Delayed restart scheduled',
    );
    // The applied pending intent now blocks a second request from the menu.
    expect(useWorkerStore.getState().delayedRestarts.a?.status).toBe('pending');
    openMenu();
    expect(menuItem('Delayed restart').disabled).toBe(true);
    expect(menuItem('Delayed restart').title).toBe(
      'A selected Session already has a delayed restart pending',
    );

    // A terminal intent (completed/failed/cancelled) allows a new request.
    // The menu is still open from the check above; the state update alone
    // must re-enable the item.
    act(() => {
      const current = useWorkerStore.getState().delayedRestarts;
      useWorkerStore.setState({
        delayedRestarts: {
          a: { ...current.a!, status: 'completed' },
          b: { ...current.b!, status: 'failed' },
        },
      });
    });
    expect(menuItem('Delayed restart').disabled).toBe(false);
  });

  it('reports the start action when a delayed restart targets an offline Session', async () => {
    vi.mocked(requestDelayedRestart).mockResolvedValue({
      sessionId: 'c',
      workerId: null,
      generation: null,
      revision: 2,
      action: 'start',
      status: 'restarting',
      error: null,
    });
    seedStores(['c']);
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Delayed restart'));
    });
    expect(toastMessages()).toContain('1 succeeded, 0 failed: C: Delayed start scheduled');
  });

  it('propagates busy state so the selection bar can block other actions', async () => {
    const onBusyChange = vi.fn();
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(restartOrStartWorker).mockImplementation(async (sessionId: string) => {
      await gate;
      return { ok: true, workerId: `worker-${sessionId}-new`, sessionId, status: 'idle' };
    });
    render(<SelectedSessionActions onBusyChange={onBusyChange} />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Restart'));
    });
    expect(onBusyChange).toHaveBeenCalledWith(true);
    await act(async () => {
      release?.();
    });
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
});
