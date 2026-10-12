// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
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

function setupStore(sessions: Session[], overrides: { railExpanded?: boolean; searchQuery?: string } = {}) {
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
    searchQuery: overrides.searchQuery ?? '',
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

/** Render the session list next to the rail to prove both share one search box. */
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

describe('Workspace rail shares the Session search box and status filters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.setSessionWorkspaces.mockResolvedValue(undefined);
    apiMocks.saveWorkspaceOrder.mockResolvedValue(undefined);
    setupStore(baseSessions());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('matches Workspace names in the existing search box while expanded', () => {
    setupStore(baseSessions());
    const { container } = renderRail();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    // Type into the shared search box (bound to uiStore.searchQuery).
    act(() => useUIStore.setState({ searchQuery: 'alpha' }));

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // No session name matches 'alpha', so the virtual scopes have no content.
    expect(allRow(container)).toBeNull();
    expect(ungroupedRow(container)).toBeNull();
  });

  it('keeps every row unfiltered while collapsed (original Session-only search)', () => {
    setupStore(baseSessions(), { railExpanded: false, searchQuery: 'alpha' });
    const { container } = renderRail();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
  });

  it('restores both lists synchronously when the search is cleared', () => {
    setupStore(baseSessions(), { searchQuery: 'alpha' });
    const { container } = renderRail();
    expect(row(container, 'ws-beta')).toBeNull();

    act(() => useUIStore.getState().setSearchQuery(''));

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

  it('applies the illegal filter (legal running, actual not running)', () => {
    const sessions = baseSessions();
    sessions[1]!.lastLegalWorkerState = 'running';
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
      useUIStore.setState({ searchQuery: 'alpha' });
      useUIStore.getState().toggleSpecialFilter('running');
    });
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();

    // Name matches but no member runs → hidden.
    act(() => useUIStore.setState({ searchQuery: 'beta' }));
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
    // ws-alpha's only member is named 'zzz-runner' (keyword 'alpha' hides it
    // from the Session list) but it IS running; the workspace name matches.
    const sessions = [
      makeSession('w-one', 'zzz-runner', { workspaceIds: ['ws-alpha'], workerStatus: 'running' }),
      makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'], workerStatus: 'idle' }),
    ];
    setupStore(sessions);
    const { container } = renderRail();

    act(() => {
      useUIStore.setState({ searchQuery: 'alpha' });
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

  it('does not break the Session search while the rail also filters', () => {
    setupStore(baseSessions());
    const { container } = renderListAndRail();

    // Both lists start complete.
    expect(container.querySelector('[data-session-card-id="w-one"]')).not.toBeNull();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    act(() => useUIStore.setState({ searchQuery: 'worker-one' }));

    // The Session list narrows by session fields; the rail narrows by name.
    expect(container.querySelector('[data-session-card-id="w-one"]')).not.toBeNull();
    expect(container.querySelector('[data-session-card-id="w-two"]')).toBeNull();
    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();

    // Clearing restores both lists.
    act(() => useUIStore.getState().setSearchQuery(''));
    expect(container.querySelector('[data-session-card-id="w-one"]')).not.toBeNull();
    expect(container.querySelector('[data-session-card-id="w-two"]')).not.toBeNull();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
  });
});
