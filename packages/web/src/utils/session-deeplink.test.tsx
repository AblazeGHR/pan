// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { useSessionDeepLinkParam, openSessionFromNotification } from '@/utils/session-deeplink';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { UNGROUPED_WORKSPACES } from '@/utils/sessionFilters';
import type { Session, Workspace } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  fetchSessionHistory: vi.fn(),
  fetchWorkspaces: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchSessions: apiMock.fetchSessions,
  fetchSessionHistory: apiMock.fetchSessionHistory,
  fetchWorkspaces: apiMock.fetchWorkspaces,
}));

const routerMock = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('@/router', () => ({ router: { navigate: routerMock.navigate } }));

const MISSING = '通知对应的会话不存在或已被删除，无法跳转';
const LIST_UNAVAILABLE = '会话列表暂时无法加载，未能打开该会话';
const WORKSPACE_UNAVAILABLE = '工作区列表暂时无法加载，未能确定会话的工作区';
const ROUTE_FAILED = '无法返回聊天视图，请手动打开 Pan 聊天';

function mkSession(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    workerId: 'w1',
    ...extra,
  };
}

function mkWorkspace(id: string, name = id): Workspace {
  return { id, name, order: 0 };
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
    apiMock.fetchWorkspaces.mockReset().mockResolvedValue([]);
    routerMock.navigate.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal('focus', vi.fn());
    useSessionStore.setState({
      sessions: [mkSession('A')],
      currentSessionId: null,
      currentMessages: [],
      sessionsLoading: false,
      _loadSeq: 1,
    });
    useUIStore.setState({ toastQueue: [], activeWorkspaceId: 'all', _workspaceSeq: 0 });
    useWorkspaceStore.setState({ workspaces: [], loaded: true, loading: false, error: null });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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

  it('cold-start ?session selects the session AND its effective workspace together', async () => {
    useSessionStore.setState({ sessions: [mkSession('A', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    renderAt('/?session=A');

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('A');
    });
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws1');
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
  });

  it('cold-start for an ungrouped session selects the no-workspace scope', async () => {
    useSessionStore.setState({ sessions: [mkSession('U', { workspaceIds: [] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'ws1' });

    renderAt('/?session=U');

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('U');
    });
    expect(useUIStore.getState().activeWorkspaceId).toBe(UNGROUPED_WORKSPACES);
  });

  it('cold-start: a user workspace switch while the list loads wins over the link', async () => {
    useSessionStore.setState({ sessions: [mkSession('A', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    let resolveLoad: (value: Workspace[]) => void = () => {};
    apiMock.fetchWorkspaces.mockImplementationOnce(
      () => new Promise((resolve) => { resolveLoad = resolve as (value: Workspace[]) => void; }),
    );

    renderAt('/?session=A');
    await act(async () => { await Promise.resolve(); });

    // The user picks their own workspace while the deep link is resolving.
    act(() => { useUIStore.getState().setActiveWorkspace('ws2'); });

    await act(async () => {
      resolveLoad([mkWorkspace('ws1'), mkWorkspace('ws2')]);
      await Promise.resolve();
    });

    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
  });

  it('cold-start: an unavailable workspace list keeps the link, never claiming no-workspace', async () => {
    useSessionStore.setState({ sessions: [mkSession('A', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    apiMock.fetchWorkspaces.mockRejectedValueOnce(new Error('offline'));

    renderAt('/?session=A');

    await vi.waitFor(() => {
      expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);
    });
    // The membership has a workspace id but the list cannot be trusted: this is
    // unknown, not "no workspace". Nothing is applied and the link is kept.
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(currentSearch()).toBe('?session=A');
  });

  it('cold-start: a valid workspace with an unavailable list keeps the link, then recovers', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    apiMock.fetchWorkspaces.mockRejectedValueOnce(new Error('offline'));

    renderAt('/?session=C');
    await vi.waitFor(() => {
      expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(currentSearch()).toBe('?session=C');

    // A later successful workspace refresh resolves the preserved link.
    apiMock.fetchWorkspaces.mockResolvedValue([mkWorkspace('ws1')]);
    await act(async () => { await useWorkspaceStore.getState().loadWorkspaces(); });

    await vi.waitFor(() => {
      expect(useSessionStore.getState().currentSessionId).toBe('C');
    });
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws1');
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
  });

  it('cold-start: a target deleted while the workspace list loads reports missing and clears the link', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    let resolveLoad: (value: Workspace[]) => void = () => {};
    apiMock.fetchWorkspaces.mockImplementationOnce(
      () => new Promise((resolve) => { resolveLoad = resolve as (value: Workspace[]) => void; }),
    );
    apiMock.fetchSessions.mockResolvedValue([]); // authoritative list without C

    renderAt('/?session=C');
    await act(async () => { await Promise.resolve(); });

    // The session is deleted while the workspace list is still loading.
    useSessionStore.setState({ sessions: [] });

    await act(async () => {
      resolveLoad([mkWorkspace('ws1')]);
      await Promise.resolve();
    });

    await vi.waitFor(() => {
      expect(toastMessages()).toContain(MISSING);
    });
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    await vi.waitFor(() => {
      expect(currentSearch()).toBe('');
    });
  });
});

describe('openSessionFromNotification workspace sync', () => {
  beforeEach(() => {
    apiMock.fetchSessions.mockReset();
    apiMock.fetchSessionHistory.mockReset()
      .mockResolvedValue({ history: [], total: 0, hasMore: false, start: 0 });
    apiMock.fetchWorkspaces.mockReset().mockResolvedValue([]);
    routerMock.navigate.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal('focus', vi.fn());
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      sessionsLoading: false,
      _loadSeq: 1,
    });
    useUIStore.setState({ toastQueue: [], activeWorkspaceId: 'all', _workspaceSeq: 0 });
    useWorkspaceStore.setState({ workspaces: [], loaded: true, loading: false, error: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('switches to the target session workspaces and selects the session', async () => {
    useSessionStore.setState({
      sessions: [mkSession('A', { workspaceIds: ['ws1'] }), mkSession('C', { workspaceIds: ['ws1'] })],
    });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('C');

    expect(ok).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('C');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws1');
  });

  it('jumps across workspaces, not just to the session', async () => {
    useSessionStore.setState({
      sessions: [mkSession('A', { workspaceIds: ['ws1'] }), mkSession('C', { workspaceIds: ['ws2'] })],
    });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('ws1'), mkWorkspace('ws2')], loaded: true, loading: false, error: null,
    });
    useUIStore.setState({ activeWorkspaceId: 'ws1' });

    const ok = await openSessionFromNotification('C');

    expect(ok).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('C');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');
  });

  it('an ungrouped target session selects the no-workspace scope', async () => {
    useSessionStore.setState({ sessions: [mkSession('U', { workspaceIds: [] })] });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'ws1' });

    const ok = await openSessionFromNotification('U');

    expect(ok).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('U');
    expect(useUIStore.getState().activeWorkspaceId).toBe(UNGROUPED_WORKSPACES);
  });

  it('a managed child inherits its root workspace (own workspaceIds ignored)', async () => {
    useSessionStore.setState({
      sessions: [
        mkSession('ROOT', { workspaceIds: ['ws2'] }),
        mkSession('CHILD', { managedBy: 'ROOT', workspaceIds: ['wrong'] }),
      ],
    });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('ws2'), mkWorkspace('wrong')], loaded: true, loading: false, error: null,
    });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('CHILD');

    expect(ok).toBe(true);
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');
  });

  it('a child whose root is missing is unprovable: feedback, no workspace guessed', async () => {
    useSessionStore.setState({ sessions: [mkSession('ORPHAN', { managedBy: 'GONE', workspaceIds: ['ws1'] })] });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('ORPHAN');

    // A dangling manager is not "no workspace": it is unknown, so nothing is
    // applied and the honest feedback is surfaced.
    expect(ok).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);
  });

  it('a membership whose workspace is absent from the loaded list is unprovable (no no-workspace claim)', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['wsGone'] })] });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('C');

    expect(ok).toBe(false);
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);
  });

  it('a workspace-list load failure is unprovable, never an arbitrary or no-workspace scope', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    apiMock.fetchWorkspaces.mockRejectedValueOnce(new Error('offline'));

    const ok = await openSessionFromNotification('C');

    expect(ok).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);
    expect(apiMock.fetchWorkspaces).toHaveBeenCalled();
  });

  it('a valid workspace with an unavailable list applies nothing, then recovers on retry', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    apiMock.fetchWorkspaces.mockRejectedValueOnce(new Error('offline'));

    expect(await openSessionFromNotification('C')).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(WORKSPACE_UNAVAILABLE);

    // The list comes back: a retried click now applies BOTH workspace and session.
    apiMock.fetchWorkspaces.mockResolvedValue([mkWorkspace('ws1')]);
    await act(async () => { await useWorkspaceStore.getState().loadWorkspaces(); });

    expect(await openSessionFromNotification('C')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('C');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws1');
  });

  it('a target deleted while the workspace list loads reports missing and applies nothing', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    let resolveLoad: (value: Workspace[]) => void = () => {};
    apiMock.fetchWorkspaces.mockImplementationOnce(
      () => new Promise((resolve) => { resolveLoad = resolve as (value: Workspace[]) => void; }),
    );
    apiMock.fetchSessions.mockResolvedValue([]); // authoritative list without C

    const click = openSessionFromNotification('C');
    await act(async () => { await Promise.resolve(); });

    // The session is deleted while the workspace list is still loading.
    useSessionStore.setState({ sessions: [] });

    await act(async () => {
      resolveLoad([mkWorkspace('ws1')]);
      await click;
    });

    expect(await click).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(MISSING);
  });

  it('a user workspace switch during the lookup wins (A → B → A)', async () => {
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions.mockImplementationOnce(
      () => new Promise((resolve) => { pending.push(resolve as (value: Session[]) => void); }),
    );
    useSessionStore.setState({ sessions: [], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('wsA'), mkWorkspace('wsB')], loaded: true, loading: false, error: null,
    });
    useUIStore.setState({ activeWorkspaceId: 'wsA' });

    const click = openSessionFromNotification('C');
    await act(async () => { await Promise.resolve(); });
    // The user roams wsA → wsB → wsA while the lookup is pending: a value
    // comparison would look unchanged, so the intent must be generation-based.
    act(() => { useUIStore.getState().setActiveWorkspace('wsB'); });
    act(() => { useUIStore.getState().setActiveWorkspace('wsA'); });

    await act(async () => {
      pending[0]?.([mkSession('C', { workspaceIds: ['wsA'] })]);
      await click;
    });

    expect(await click).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useUIStore.getState().activeWorkspaceId).toBe('wsA');
  });

  it('resolves the latest ownership when it changes while the list loads', async () => {
    useSessionStore.setState({
      sessions: [
        mkSession('ROOT', { workspaceIds: ['ws1'] }),
        mkSession('CHILD', { managedBy: 'ROOT', workspaceIds: [] }),
      ],
    });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    let resolveLoad: (value: Workspace[]) => void = () => {};
    apiMock.fetchWorkspaces.mockImplementationOnce(
      () => new Promise((resolve) => { resolveLoad = resolve as (value: Workspace[]) => void; }),
    );

    const click = openSessionFromNotification('CHILD');
    await act(async () => { await Promise.resolve(); });

    // Ownership moves while the jump is waiting on the workspace list.
    useSessionStore.setState((s) => ({
      sessions: s.sessions.map((item) => (item.id === 'ROOT' ? { ...item, workspaceIds: ['ws2'] } : item)),
    }));

    await act(async () => {
      resolveLoad([mkWorkspace('ws1'), mkWorkspace('ws2')]);
      await click;
    });

    expect(await click).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('CHILD');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');
  });

  it('two notification clicks: the later one wins and its workspace is applied', async () => {
    const pending: Array<(value: Session[]) => void> = [];
    apiMock.fetchSessions.mockImplementationOnce(
      () => new Promise((resolve) => { pending.push(resolve as (value: Session[]) => void); }),
    );
    useSessionStore.setState({ sessions: [mkSession('Y', { workspaceIds: ['ws2'] })], sessionsLoading: false, _loadSeq: 1 });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('ws1'), mkWorkspace('ws2')], loaded: true, loading: false, error: null,
    });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const first = openSessionFromNotification('X');
    await act(async () => { await Promise.resolve(); });
    const second = openSessionFromNotification('Y');

    expect(await second).toBe(true);
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');

    await act(async () => {
      pending[0]?.([mkSession('X', { workspaceIds: ['ws1'] }), mkSession('Y', { workspaceIds: ['ws2'] })]);
      await first;
    });

    expect(await first).toBe(false);
    expect(useSessionStore.getState().currentSessionId).toBe('Y');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws2');
  });

  it('a router failure leaves the workspace scope untouched', async () => {
    useSessionStore.setState({ sessions: [mkSession('C', { workspaceIds: ['ws1'] })] });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });
    routerMock.navigate.mockRejectedValueOnce(new Error('route boom'));

    const ok = await openSessionFromNotification('C');

    expect(ok).toBe(false);
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(ROUTE_FAILED);
  });

  it('a deleted session reports missing and leaves the workspace scope untouched', async () => {
    useSessionStore.setState({ sessions: [], sessionsLoading: false, _loadSeq: 1 });
    apiMock.fetchSessions.mockResolvedValue([mkSession('A')]);
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('gone');

    expect(ok).toBe(false);
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
    expect(toastMessages()).toContain(MISSING);
  });

  it('callers may supply their own feedback wording without changing the jump', async () => {
    useSessionStore.setState({
      sessions: [mkSession('A', { workspaceIds: ['ws1'] }), mkSession('C', { workspaceIds: ['ws1'] })],
    });
    useWorkspaceStore.setState({ workspaces: [mkWorkspace('ws1')], loaded: true, loading: false, error: null });
    useUIStore.setState({ activeWorkspaceId: 'all' });

    const ok = await openSessionFromNotification('C', { missing: 'Custom missing copy' });

    // Overriding the wording must not change the routing result.
    expect(ok).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('C');
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws1');

    useSessionStore.setState({ sessions: [], sessionsLoading: false, _loadSeq: 1 });
    apiMock.fetchSessions.mockResolvedValue([mkSession('A')]);
    expect(await openSessionFromNotification('gone', { missing: 'Custom missing copy' })).toBe(false);
    expect(toastMessages()).toContain('Custom missing copy');
    expect(toastMessages()).not.toContain(MISSING);
  });
});
