// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SessionList } from '@/components/session/SessionList';
import { WorkspaceRail } from '@/components/layout/WorkspaceRail';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useHistorySearchViewStore } from '@/stores/historySearchViewStore';
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

function setupStore(sessions: Session[]) {
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
    railExpanded: true,
    mobileSidebarOpen: false,
    activeWorkspaceId: 'all',
    groupBy: 'none',
    dragEnabled: false,
    sortBy: 'recent',
    customOrder: [],
    searchQuery: '',
    workspaceSearchQuery: '',
    specialFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
    toastQueue: [],
  });
}

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

function sessionCard(container: HTMLElement, id: string): Element | null {
  return container.querySelector(`[data-session-card-id="${id}"]`);
}

/** Drive the global-search hit scope exactly like GlobalHistorySearch does. */
function setHits(ids: string[] | null, preparing = false) {
  act(() => useHistorySearchViewStore.getState().update(ids, preparing));
}

describe('Workspace rail filters real Workspaces by global-search hits', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.setSessionWorkspaces.mockResolvedValue(undefined);
    apiMocks.saveWorkspaceOrder.mockResolvedValue(undefined);
    useHistorySearchViewStore.getState().update(null);
    setupStore(baseSessions());
  });

  afterEach(() => {
    cleanup();
    useHistorySearchViewStore.getState().update(null);
  });

  it('keeps only the Workspace that contains a hit Session', () => {
    const { container } = renderListAndRail();
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    setHits(['w-one']);

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // 'all' still lists every hit; 'ungrouped' has no hit.
    expect(allRow(container)).not.toBeNull();
    expect(ungroupedRow(container)).toBeNull();
    // The Session list shows exactly the hit.
    expect(sessionCard(container, 'w-one')).not.toBeNull();
    expect(sessionCard(container, 'w-two')).toBeNull();
    expect(sessionCard(container, 'w-loose')).toBeNull();
  });

  it('shows every hit Workspace and every hit Session when hits span workspaces', () => {
    const { container } = renderListAndRail();
    setHits(['w-one', 'w-two']);

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(sessionCard(container, 'w-one')).not.toBeNull();
    expect(sessionCard(container, 'w-two')).not.toBeNull();
    expect(sessionCard(container, 'w-loose')).toBeNull();
  });

  it('keeps the ungrouped scope for ungrouped hits and drops every real Workspace', () => {
    const { container } = renderListAndRail();
    setHits(['w-loose']);

    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
    expect(allRow(container)).not.toBeNull();
    expect(sessionCard(container, 'w-loose')).not.toBeNull();
  });

  it('hides every real Workspace for an empty hit set and shows the no-match empty state', () => {
    const { container, queryByText } = renderListAndRail();
    setHits([], false);

    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    expect(ungroupedRow(container)).toBeNull();
    expect(allRow(container)).not.toBeNull();
    expect(queryByText('No matching sessions')).not.toBeNull();
  });

  it('keeps the list quiet while preparation is still running', () => {
    const { container, queryByText } = renderListAndRail();
    setHits([], true);

    expect(row(container, 'ws-alpha')).toBeNull();
    // The sidebar banner owns the "still searching…" copy; the list stays blank.
    expect(queryByText('No matching sessions')).toBeNull();
  });

  it('restores every Workspace and Session when the global search closes', () => {
    const { container } = renderListAndRail();
    setHits(['w-one']);
    expect(row(container, 'ws-beta')).toBeNull();

    setHits(null);

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
    expect(sessionCard(container, 'w-one')).not.toBeNull();
    expect(sessionCard(container, 'w-two')).not.toBeNull();
    expect(sessionCard(container, 'w-loose')).not.toBeNull();
  });

  it('lists only the selected Workspace’s hit Sessions on click, all hits under 全部', () => {
    const { container } = renderListAndRail();
    setHits(['w-one', 'w-two']);

    fireEvent.click(row(container, 'ws-beta')!);
    expect(sessionCard(container, 'w-two')).not.toBeNull();
    expect(sessionCard(container, 'w-one')).toBeNull();

    fireEvent.click(allRow(container)!);
    expect(sessionCard(container, 'w-one')).not.toBeNull();
    expect(sessionCard(container, 'w-two')).not.toBeNull();
    // Workspace filtering itself is unaffected by the selection.
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
  });

  it('composes the hit scope with the rail’s own name query (AND)', () => {
    const { container } = renderListAndRail();
    setHits(['w-one', 'w-two']);
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    act(() => useUIStore.getState().setWorkspaceSearchQuery('alpha'));
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    // The hit scope itself is untouched by the rail query.
    expect(useHistorySearchViewStore.getState().matchingSessionIds?.has('w-one')).toBe(true);
    expect(useHistorySearchViewStore.getState().matchingSessionIds?.has('w-two')).toBe(true);

    act(() => useUIStore.getState().setWorkspaceSearchQuery('beta'));
    expect(row(container, 'ws-alpha')).toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();

    act(() => useUIStore.getState().setWorkspaceSearchQuery(''));
    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
  });

  it('counts a hit on an inherited child as its root Workspace', () => {
    setupStore([
      makeSession('mgr', 'manager-root', { workspaceIds: ['ws-alpha'] }),
      makeSession('kid', 'kid-leaf', { managedBy: 'mgr' }),
      makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'] }),
    ]);
    const { container } = renderListAndRail();
    setHits(['kid']);

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).toBeNull();
    expect(sessionCard(container, 'kid')).not.toBeNull();
  });

  it('keeps Workspace membership counts and identity untouched by the hit scope', () => {
    setupStore([
      makeSession('mgr', 'manager-root', { workspaceIds: ['ws-alpha'] }),
      makeSession('kid', 'kid-leaf', { managedBy: 'mgr' }),
      makeSession('w-two', 'worker-two', { workspaceIds: ['ws-beta'] }),
    ]);
    const { container } = renderListAndRail();
    expect(container.querySelector('[data-testid="workspace-count-ws-alpha"]')?.textContent).toBe('2');

    setHits(['kid']);

    // Still the full membership count — never a hit count.
    expect(container.querySelector('[data-testid="workspace-count-ws-alpha"]')?.textContent).toBe('2');
    expect(row(container, 'ws-alpha')?.getAttribute('title')).toBe('Alpha WS');
  });

  it('leaves the rail unfiltered while collapsed even during a global search', () => {
    setupStore(baseSessions());
    act(() => useUIStore.setState({ railExpanded: false }));
    const { container } = renderListAndRail();
    setHits(['w-one']);

    expect(row(container, 'ws-alpha')).not.toBeNull();
    expect(row(container, 'ws-beta')).not.toBeNull();
    expect(ungroupedRow(container)).not.toBeNull();
  });
});
