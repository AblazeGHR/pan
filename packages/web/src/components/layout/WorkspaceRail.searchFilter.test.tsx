// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SessionList } from '@/components/session/SessionList';
import { WorkspaceRail } from '@/components/layout/WorkspaceRail';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { Session, Workspace } from '@/types';

const apiMocks = vi.hoisted(() => ({
  createWorkspace: vi.fn(),
  saveWorkspaceOrder: vi.fn(),
  setSessionWorkspaces: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    createWorkspace: apiMocks.createWorkspace,
    saveWorkspaceOrder: apiMocks.saveWorkspaceOrder,
    setSessionWorkspaces: apiMocks.setSessionWorkspaces,
  };
});

vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: vi.fn() }));

const ALPHA_WS: Workspace = { id: 'ws-alpha', name: 'Alpha WS', order: null };
const BETA_WS: Workspace = { id: 'ws-beta', name: 'Beta WS', order: null };

function makeSession(id: string, name: string, overrides: Partial<Session> = {}): Session {
  return {
    id,
    name,
    workspaceIds: [],
    managed: [],
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    updatedAt: new Date().toISOString(),
    ...overrides,
  } as unknown as Session;
}

/** worker-one (ws-alpha) + worker-two (ws-beta) + loose (ungrouped). */
function baseSessions(): Session[] {
  return [
    makeSession('w-one', 'worker-one', { workspaceIds: ['ws-alpha'] }),
    makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'] }),
    makeSession('w-loose', 'loose-three'),
  ];
}

function setupStore(sessions: Session[], overrides: { railExpanded?: boolean; workspaceSearchQuery?: string } = {}) {
  useSessionStore.setState({
    sessions,
    currentSessionId: null,
    selectedIds: new Set(),
    multiSelectMode: false,
    sessionsLoading: false,
  });
  useWorkspaceStore.setState({
    workspaces: [ALPHA_WS, BETA_WS],
    loaded: true,
    loading: false,
    error: null,
  });
  useUIStore.setState({
    railExpanded: overrides.railExpanded ?? true,
    mobileSidebarOpen: false,
    activeWorkspaceId: 'all',
    groupBy: 'none',
    dragEnabled: false,
    sortBy: 'recent',
    customOrder: [],
    searchQuery: '',
    workspaceSearchQuery: overrides.workspaceSearchQuery ?? '',
    specialFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
    toastQueue: [],
  });
}

function renderRail() {
  return render(
    <MemoryRouter>
      <WorkspaceRail />
    </MemoryRouter>,
  );
}

/** Render the session list next to the rail to verify query isolation. */
function renderListAndRail() {
  return render(
    <MemoryRouter>
      <SessionList />
      <WorkspaceRail />
    </MemoryRouter>,
  );
}

function row(container: HTMLElement, id: string): Element | null {
  return container.querySelector(`[data-testid="workspace-tab-${id}"]`);
}

function allRow(container: HTMLElement): Element | null {
  return container.querySelector('[data-workspace-tab-id="all"]');
}

function ungroupedRow(container: HTMLElement): Element | null {
  return container.querySelector('[data-testid="workspace-tab-ungrouped"]');
}

describe('Workspace rail owns its query and shares status filters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.setSessionWorkspaces.mockResolvedValue(undefined);
    apiMocks.saveWorkspaceOrder.mockResolvedValue(undefined);
    setupStore(baseSessions());
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('matches Workspace names in the workspace search box while expanded', () => {
    setupStore(baseSessions());
    const { container } = renderRail();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    // Type into the workspace search box.
    act(() => useUIStore.setState({ workspaceSearchQuery: 'alpha' }));

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // Workspace keywords do not filter the virtual scopes.
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
  });

  it('keeps every row unfiltered while collapsed (original Session-only search)', () => {
    setupStore(baseSessions(), { railExpanded: false, workspaceSearchQuery: 'alpha' });
    const { container } = renderRail();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
  });

  it('restores workspace rows when only the workspace query is cleared', () => {
    setupStore(baseSessions(), { workspaceSearchQuery: 'alpha' });
    const { container } = renderRail();
    expect(row(container, 'ws-beta')).toBeNull();

    act(() => useUIStore.getState().setWorkspaceSearchQuery(''));

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
  });

  it('applies the running filter to Workspaces via member aggregation', () => {
    const sessions = baseSessions();
    sessions[0]!.workerStatus = 'running';
    setupStore(sessions);
    const { container } = renderRail();

    act(() => useUIStore.getState().toggleSpecialFilter('running'));

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // 'all' has a running session; 'ungrouped' does not.
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).toBeNull();
  });

  it('applies the unread filter with the authoritative unreadDoneCount', () => {
    const sessions = baseSessions();
    sessions[1]!.unreadDoneCount = 2;
    setupStore(sessions);
    const { container } = renderRail();

    act(() => useUIStore.getState().toggleSpecialFilter('unread'));

    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(row(container, 'ws-alpha')).toBeNull();
    expect(ungroupedRow(container)).toBeNull();
  });

  it('applies the queued-messages filter via queuePendingCount', () => {
    const sessions = baseSessions();
    sessions[1]!.queuePendingCount = 1;
    setupStore(sessions);
    const { container } = renderRail();

    act(() => useUIStore.getState().toggleSpecialFilter('queue'));

    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(row(container, 'ws-alpha')).toBeNull();
  });

  it('applies the authoritative illegal filter', () => {
    const sessions = baseSessions();
    sessions[1]!.lastLegalWorkerState = 'running';
    sessions[1]!.illegalOffline = true;
    sessions[1]!.workerStatus = 'idle';
    setupStore(sessions);
    const { container } = renderRail();

    act(() => useUIStore.getState().toggleSpecialFilter('illegal'));

    expect(row(container, 'ws-beta')).not.toBeNull();
    // worker-one is actually running → legal, never illegal.
    expect(row(container, 'ws-alpha')).toBeNull();
  });

  it('keeps the original keyword AND status combination semantics', () => {
    const sessions = baseSessions();
    sessions[0]!.workerStatus = 'running';
    setupStore(sessions);
    const { container } = renderRail();

    // Name matches + a member actually runs → visible.
    act(() => {
      useUIStore.setState({ workspaceSearchQuery: 'alpha', searchQuery: 'no-session-matches' });
      useUIStore.getState().toggleSpecialFilter('running');
    });
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();

    // Name matches but no member runs → hidden.
    act(() => useUIStore.setState({ workspaceSearchQuery: 'beta' }));
    expect(row(container, 'ws-beta')).toBeNull();
    expect(row(container, 'ws-alpha')).toBeNull();
  });

  it('lets different members satisfy different status filters (workspace-level AND)', () => {
    const sessions = [
      // ws-alpha: the root runs, the inherited child carries the unread.
      makeSession('w-root', 'workspace-root', { workspaceIds: ['ws-alpha'], workerStatus: 'running' }),
      makeSession('w-child', 'inherited-unread', { managedBy: 'w-root', unreadDoneCount: 2 }),
      // ws-beta: running only — no unread member.
      makeSession('w-beta', 'beta-worker', { workspaceIds: ['ws-beta'], workerStatus: 'running' }),
    ];
    setupStore(sessions);
    const { container } = renderRail();

    act(() => {
      useUIStore.getState().toggleSpecialFilter('running');
      useUIStore.getState().toggleSpecialFilter('unread');
    });

    // Running by one member and unread by another still satisfies both.
    expect(row(container, 'ws-alpha')).not.toBeNull();
    // The AND still requires every selected filter to be met somewhere.
    expect(row(container, 'ws-beta')).toBeNull();
  });

  it('aggregates actual member states — a keyword-hidden member still counts', () => {
    // Workspace name and member names differ; status still uses all members.
    const sessions = [
      makeSession('w-one', 'zzz-runner', { workspaceIds: ['ws-alpha'], workerStatus: 'running' }),
      makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'], workerStatus: 'idle' }),
    ];
    setupStore(sessions);
    const { container } = renderRail();

    act(() => {
      useUIStore.setState({ workspaceSearchQuery: 'alpha', searchQuery: 'no-session-matches' });
      useUIStore.getState().toggleSpecialFilter('running');
    });

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
  });

  it('aggregates status through management-tree inheritance', () => {
    const sessions = [
      makeSession('mgr', 'manager-root', { workspaceIds: ['ws-alpha'], workerStatus: 'idle' }),
      makeSession('kid', 'kid-leaf', { managedBy: 'mgr', workerStatus: 'running' }),
      makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'], workerStatus: 'idle' }),
    ];
    setupStore(sessions);
    const { container } = renderRail();

    // The inherited child makes ws-alpha match, and it is counted as a member.
    expect(container.querySelector('[data-testid="workspace-count-ws-alpha"]')?.textContent).toBe('2');

    act(() => useUIStore.getState().toggleSpecialFilter('running'));

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // The child inherits the root's workspace — it is never ungrouped.
    expect(ungroupedRow(container)).toBeNull();
  });

  it('reflects Session state changes immediately without remounting', () => {
    setupStore(baseSessions());
    const { container } = renderRail();
    act(() => useUIStore.getState().toggleSpecialFilter('running'));
    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();

    act(() => {
      useSessionStore.setState({
        sessions: baseSessions().map((session) =>
          session.id === 'w-two' ? { ...session, workerStatus: 'running' } : session,
        ),
      });
    });

    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(row(container, 'ws-alpha')).toBeNull();
  });

  it('keeps both queries and clear actions independent with a real SessionList', () => {
    const { container, getByRole } = renderListAndRail();
    act(() => useUIStore.getState().setSearchQuery('worker-one'));
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    fireEvent.change(getByRole('textbox', { name: 'Search workspaces' }), { target: { value: 'beta' } });
    expect(useUIStore.getState().searchQuery).toBe('worker-one');
    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(container.querySelector('[data-session-card-id="w-one"]')).not.toBeNull();
    expect(container.querySelector('[data-session-card-id="w-two"]')).toBeNull();
    act(() => useUIStore.getState().setSearchQuery(''));
    expect(useUIStore.getState().workspaceSearchQuery).toBe('beta');
    expect(container.querySelector('[data-session-card-id="w-two"]')).not.toBeNull();
    act(() => useUIStore.getState().setSearchQuery('worker-two'));
    fireEvent.click(getByRole('button', { name: 'Clear workspace search' }));
    expect(useUIStore.getState().searchQuery).toBe('worker-two');
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(container.querySelector('[data-session-card-id="w-one"]')).toBeNull();
  });

  it.each([false, true])('retains the query through collapse and remount, mobile=%s', (mobile) => {
    const view = render(<WorkspaceRail mobileDrawer={mobile} mobileExpanded />);
    fireEvent.change(view.getByRole('textbox'), { target: { value: 'beta' } });
    act(() => useUIStore.getState().setRailExpanded(false));
    view.rerender(<WorkspaceRail mobileDrawer={mobile} mobileExpanded={false} />);
    expect(view.queryByRole('textbox')).toBeNull();
    expect(useUIStore.getState().workspaceSearchQuery).toBe('beta');
    view.unmount();
    act(() => useUIStore.getState().setRailExpanded(true));
    const reopened = render(<WorkspaceRail mobileDrawer={mobile} mobileExpanded />);
    expect((reopened.getByRole('textbox') as HTMLInputElement).value).toBe('beta');
    expect(row(reopened.container, 'ws-alpha')).toBeNull();
    expect(row(reopened.container, 'ws-beta')).not.toBeNull();
  });

  it('keeps input keys and focus from navigating or switching workspace', () => {
    const onKeyDown = vi.fn();
    const view = render(<div onKeyDown={onKeyDown}><WorkspaceRail /></div>);
    const input = view.getByRole('textbox');
    input.focus();
    for (const key of ['Enter', 'ArrowDown', 'ArrowUp', 'Escape', 'k']) fireEvent.keyDown(input, { key, ctrlKey: true });
    expect(onKeyDown).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(input);
    expect(useUIStore.getState().activeWorkspaceId).toBe('all');
  });
});
