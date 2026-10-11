// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { useSessionDeepLinkParam } from '@/utils/session-deeplink';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  fetchSessionHistory: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchSessions: apiMock.fetchSessions,
  fetchSessionHistory: apiMock.fetchSessionHistory,
}));

const MISSING = '通知对应的会话不存在或已被删除，无法跳转';
const LIST_UNAVAILABLE = '会话列表暂时无法加载，未能打开该会话';

function mkSession(id: string): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    workerId: 'w1',
  };
}

function Harness() {
  useSessionDeepLinkParam();
  return null;
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function RouteControl({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to, { replace: true })}>
      route-control
    </button>
  );
}

function renderAt(entry: string, options?: { controlTo?: string }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationProbe />
      <Harness />
      {options?.controlTo ? <RouteControl to={options.controlTo} /> : null}
    </MemoryRouter>,
  );
}

function clickRouteControl(): void {
  screen.getByText('route-control').click();
}

function currentSearch(): string {
  return screen.getByTestId('location-search').textContent ?? '';
}

function toastMessages(): string[] {
  return useUIStore.getState().toastQueue.map((toast) => toast.message);
}

describe('useSessionDeepLinkParam', () => {
  beforeEach(() => {
    apiMock.fetchSessions.mockReset();
    apiMock.fetchSessionHistory.mockReset()
      .mockResolvedValue({ history: [], total: 0, hasMore: false, start: 0 });
    useSessionStore.setState({
      sessions: [mkSession('A')],
      currentSessionId: null,
      currentMessages: [],
      sessionsLoading: false,
      _loadSeq: 1,
    });
    useUIStore.setState({ toastQueue: [] });
  });

  afterEach(() => {
    cleanup();
  });

  it('selects a cached session and clears only the session param', async () => {
    renderAt('/?session=A&panE2E=1&keep=1');

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('A');
    });
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('?panE2E=1&keep=1');
    });
    expect(toastMessages()).toEqual([]);
  });

  it('reports deletion only after an authoritative list without the session', async () => {
    apiMock.fetchSessions.mockResolvedValue([mkSession('A')]);
    renderAt('/?session=gone');

    await vi.waitFor(() => {
      expect(toastMessages()).toEqual([MISSING]);
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('');
  });

  it('keeps the link on a failed list load, never claims deletion, and recovers later', async () => {
    apiMock.fetchSessions.mockRejectedValueOnce(new Error('offline'));
    renderAt('/?session=C');

    await vi.waitFor(() => {
      expect(toastMessages()).toEqual([LIST_UNAVAILABLE]);
    });
    // No false deletion: the parameter survives for a later recovery.
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('?session=C');

    // A later successful refresh brings the session in; the hook resolves.
    apiMock.fetchSessions.mockResolvedValue([mkSession('A'), mkSession('C')]);
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('C');
    });
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
    expect(toastMessages()).not.toContain(MISSING);
    expect(toastMessages()).toEqual([LIST_UNAVAILABLE]);
  });

  it('does not conclude while a list load is still in flight', async () => {
    useSessionStore.setState({ sessions: [], sessionsLoading: true, _loadSeq: 1 });
    renderAt('/?session=C');

    await act(async () => { await Promise.resolve(); });
    expect(toastMessages()).toEqual([]);
    expect(currentSearch()).toBe('?session=C');
    expect(apiMock.fetchSessions).not.toHaveBeenCalled();

    // The in-flight load settles without the session: NOW the hook may run
    // its own authoritative lookup, which confirms deletion.
    apiMock.fetchSessions.mockResolvedValue([mkSession('A')]);
    act(() => { useSessionStore.setState({ sessionsLoading: false }); });

    await vi.waitFor(() => {
      expect(toastMessages()).toEqual([MISSING]);
    });
    expect(currentSearch()).toBe('');
  });

  it('recovers when a concurrent refresh supersedes its own load', async () => {
    // Cold start races the app's initial load: the lookup's fetch gets
    // superseded (its response is dropped), which must not end in 'unknown'
    // with a permanent warning — it retries once with a clean sequence.
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions
      .mockImplementationOnce(() => new Promise((resolve) => { pending.push(resolve); }))
      .mockImplementationOnce(() => new Promise((resolve) => { pending.push(resolve); }))
      .mockResolvedValue([mkSession('A')]);
    renderAt('/?session=C');
    await act(async () => { await Promise.resolve(); }); // lookup load in flight

    // The app starts its own refresh, superseding the lookup's load.
    void useSessionStore.getState().loadSessions();
    await act(async () => {
      pending[0]?.([mkSession('A')]); // lookup's superseded response
      pending[1]?.([mkSession('A')]); // app refresh settles without the session
      await Promise.resolve();
    });

    await vi.waitFor(() => {
      expect(toastMessages()).toEqual([MISSING]);
    });
    expect(currentSearch()).toBe('');
    expect(apiMock.fetchSessions).toHaveBeenCalledTimes(3);
  });

  it('a removed ?session is not acted on by its own pending lookup', async () => {
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions.mockImplementationOnce(
      () => new Promise((resolve) => { pending.push(resolve as (value: Session[]) => void); }),
    );
    renderAt('/?session=C&keep=1', { controlTo: '/?keep=1' });
    await act(async () => { await Promise.resolve(); });
    expect(apiMock.fetchSessions).toHaveBeenCalledTimes(1);

    // The user navigates; the parameter is gone while the lookup is pending.
    await act(async () => { clickRouteControl(); });
    expect(currentSearch()).toBe('?keep=1');

    await act(async () => {
      pending[0]?.([mkSession('A'), mkSession('C')]);
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); });

    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('?keep=1');
  });

  it('a user selection during the lookup wins and the link is consumed without overriding', async () => {
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions.mockImplementationOnce(
      () => new Promise((resolve) => { pending.push(resolve as (value: Session[]) => void); }),
    );
    renderAt('/?session=C');
    await act(async () => { await Promise.resolve(); });
    expect(apiMock.fetchSessions).toHaveBeenCalledTimes(1);

    // The user picks their own session while the deep link is resolving.
    await act(async () => { await useSessionStore.getState().selectSession('A'); });

    await act(async () => {
      pending[0]?.([mkSession('A'), mkSession('C')]);
      await Promise.resolve();
    });

    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
    expect(useSessionStore.getState().currentSessionId).toBe('A');
    expect(toastMessages()).toEqual([]);
  });

  it('unmounting while a lookup is pending applies nothing late', async () => {
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions.mockImplementationOnce(
      () => new Promise((resolve) => { pending.push(resolve as (value: Session[]) => void); }),
    );
    const rendered = renderAt('/?session=C');
    await act(async () => { await Promise.resolve(); });
    expect(apiMock.fetchSessions).toHaveBeenCalledTimes(1);

    rendered.unmount();
    await act(async () => {
      pending[0]?.([mkSession('A'), mkSession('C')]);
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); });

    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(toastMessages()).toEqual([]);
  });

  it('rejects a malformed session reference without touching the store', async () => {
    renderAt(`/?session=${encodeURIComponent('bad ref!')}`);

    await vi.waitFor(() => {
      expect(toastMessages()).toEqual(['会话链接无效']);
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('');
    expect(apiMock.fetchSessions).not.toHaveBeenCalled();
  });
});
