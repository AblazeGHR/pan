// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceRail } from '@/components/layout/WorkspaceRail';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { Session, Workspace } from '@/types';

const apiMocks = vi.hoisted(() => ({
  createWorkspace: vi.fn(),
  saveWorkspaceOrder: vi.fn(),
  setSessionWorkspaces: vi.fn(),
  ackSessionUnreadDone: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    createWorkspace: apiMocks.createWorkspace,
    saveWorkspaceOrder: apiMocks.saveWorkspaceOrder,
    setSessionWorkspaces: apiMocks.setSessionWorkspaces,
    ackSessionUnreadDone: apiMocks.ackSessionUnreadDone,
  };
});

vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: vi.fn() }));

const ALPHA_WS: Workspace = { id: 'ws-alpha', name: 'Alpha WS', order: null };
const BETA_WS: Workspace = { id: 'ws-beta', name: 'Beta WS', order: null };

function makeSession(
  id: string,
  name: string,
  overrides: Partial<Session> = {},
): Session {
  return {
    id,
    name,
    workspaceIds: [],
    managed: [],
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    unreadDoneCount: 0,
    unreadDoneGeneration: 0,
    unreadDoneReadGeneration: 0,
    updatedAt: new Date().toISOString(),
    ...overrides,
  } as unknown as Session;
}

/** ws-alpha: root (unread gen 2) + inherited child (unread gen 3); ws-beta: one idle. */
function baseSessions(): Session[] {
  return [
    makeSession('w-root', 'workspace-root', {
      workspaceIds: ['ws-alpha'],
      unreadDoneGeneration: 2,
      unreadDoneCount: 2,
    }),
    makeSession('w-child', 'inherited-child', {
      managedBy: 'w-root',
      unreadDoneGeneration: 3,
      unreadDoneCount: 1,
    }),
    makeSession('w-beta', 'beta-worker', { workspaceIds: ['ws-beta'] }),
    makeSession('w-loose', 'loose-session'),
  ];
}

function setupStore(sessions: Session[], overrides: { searchQuery?: string } = {}) {
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
    searchQuery: overrides.searchQuery ?? '',
    specialFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
    toastQueue: [],
  });
}

function renderRail(props: Parameters<typeof WorkspaceRail>[0] = {}) {
  return render(
    <MemoryRouter>
      <WorkspaceRail {...props} />
    </MemoryRouter>,
  );
}

/** Open the workspace row menu (the MoreHorizontal trigger). */
function openMenu(container: HTMLElement, workspaceId: string) {
  const row = container.querySelector(`[data-testid="workspace-tab-${workspaceId}"]`);
  expect(row).not.toBeNull();
  const trigger = row!.querySelector('button[title="更多"]');
  expect(trigger).not.toBeNull();
  fireEvent.click(trigger!);
}

function readAllItem(container: HTMLElement, workspaceId: string) {
  return container.querySelector(`[data-testid="workspace-read-all-${workspaceId}"]`);
}

function readAllCalls() {
  return apiMocks.ackSessionUnreadDone.mock.calls.map(([id, generation]) => ({ id, generation }));
}

describe('Workspace menu Read all', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.ackSessionUnreadDone.mockImplementation(
      async (_id: string, generation: number) => ({
        ok: true,
        unreadDoneGeneration: generation,
        unreadDoneReadGeneration: generation,
        unreadDoneCount: 0,
      }),
    );
    setupStore(baseSessions());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('adds the English Read all entry while keeping the other menu actions', () => {
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    const item = readAllItem(container, 'ws-alpha');
    expect(item).not.toBeNull();
    expect(item!.textContent).toContain('Read all');
    expect(container.textContent).toContain('重命名');
    expect(container.textContent).toContain('删除');
  });

  it('read-alls every effective member with its observed generation, including inherited children', async () => {
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    await act(async () => {
      fireEvent.click(readAllItem(container, 'ws-alpha')!);
    });

    // Only the workspace members: the inherited child counts, ws-beta and the
    // ungrouped session do not.
    expect(readAllCalls()).toEqual(
      expect.arrayContaining([
        { id: 'w-root', generation: 2 },
        { id: 'w-child', generation: 3 },
      ]),
    );
    expect(apiMocks.ackSessionUnreadDone).toHaveBeenCalledTimes(2);
    const toast = useUIStore.getState().toastQueue.at(-1);
    expect(toast?.message).toBe('2 succeeded, 0 failed');
    expect(toast?.type).toBe('info');
  });

  it('targets the real membership, not the search/filter-visible subset', async () => {
    // The keyword matches only the workspace name, so no Session card would be
    // visible; Read all must still reach both members.
    setupStore(baseSessions(), { searchQuery: 'alpha ws' });
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    await act(async () => {
      fireEvent.click(readAllItem(container, 'ws-alpha')!);
    });
    expect(apiMocks.ackSessionUnreadDone).toHaveBeenCalledTimes(2);
    expect(readAllCalls().map((call) => call.id).sort()).toEqual(['w-child', 'w-root']);
  });

  it('keeps the click-time observed generation so later unread is preserved', async () => {
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    await act(async () => {
      fireEvent.click(readAllItem(container, 'ws-alpha')!);
    });
    // The ack carries the generation observed at click time.
    expect(readAllCalls()).toContainEqual({ id: 'w-root', generation: 2 });
    expect(readAllCalls()).toContainEqual({ id: 'w-child', generation: 3 });
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'w-root')?.unreadDoneCount).toBe(0);

    // A completion that lands AFTER the click is not covered by that Read all.
    apiMocks.ackSessionUnreadDone.mockClear();
    act(() => {
      useSessionStore.getState().updateSession('w-root', {
        unreadDoneGeneration: 5,
        unreadDoneCount: 5,
      });
    });
    expect(apiMocks.ackSessionUnreadDone).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'w-root')?.unreadDoneCount).toBe(5);
  });

  it('guards against duplicate clicks while the read-all is in flight', async () => {
    let release: (() => void) | undefined;
    apiMocks.ackSessionUnreadDone.mockImplementation(
      () => new Promise((resolve) => {
        release = () => resolve({
          ok: true, unreadDoneGeneration: 0, unreadDoneReadGeneration: 0, unreadDoneCount: 0,
        });
      }),
    );
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    await act(async () => {
      fireEvent.click(readAllItem(container, 'ws-alpha')!);
    });
    expect(apiMocks.ackSessionUnreadDone).toHaveBeenCalledTimes(2);

    // Reopen the menu mid-flight: the item is disabled and cannot re-issue.
    openMenu(container, 'ws-alpha');
    expect(readAllItem(container, 'ws-alpha')!.hasAttribute('disabled')).toBe(true);
    fireEvent.click(readAllItem(container, 'ws-alpha')!);
    expect(apiMocks.ackSessionUnreadDone).toHaveBeenCalledTimes(2);

    await act(async () => {
      release?.();
    });
  });

  it('reports partial success honestly when some members fail', async () => {
    apiMocks.ackSessionUnreadDone.mockImplementation(async (id: string, generation: number) => {
      if (id === 'w-child') throw new Error('offline');
      return { ok: true, unreadDoneGeneration: generation, unreadDoneReadGeneration: generation, unreadDoneCount: 0 };
    });
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    await act(async () => {
      fireEvent.click(readAllItem(container, 'ws-alpha')!);
    });
    const toast = useUIStore.getState().toastQueue.at(-1);
    expect(toast?.type).toBe('error');
    expect(toast?.message).toBe('1 succeeded, 1 failed: inherited-child: offline');
    // The failing member keeps its unread (no local clearing on error).
    expect(useSessionStore.getState().sessions.find((s) => s.id === 'w-child')?.unreadDoneCount).toBe(1);
  });

  it('disables Read all for an empty workspace instead of issuing a no-op', () => {
    setupStore([
      makeSession('w-beta', 'beta-worker', { workspaceIds: ['ws-beta'] }),
    ]);
    const { container } = renderRail();
    openMenu(container, 'ws-alpha');
    expect(readAllItem(container, 'ws-alpha')!.hasAttribute('disabled')).toBe(true);
    fireEvent.click(readAllItem(container, 'ws-alpha')!);
    expect(apiMocks.ackSessionUnreadDone).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue).toHaveLength(0);
  });

  it('keeps the menu reachable in the mobile drawer', () => {
    const { container } = renderRail({
      mobileDrawer: true,
      mobileExpanded: true,
      onMobileExpandedChange: vi.fn(),
    });
    const row = container.querySelector('[data-testid="workspace-tab-ws-alpha"]');
    const trigger = row?.querySelector('button[title="更多"]');
    expect(trigger).not.toBeNull();
    // Touch has no hover: the trigger must not be hover-gated on mobile.
    expect(trigger!.className).not.toContain('hidden');
    fireEvent.click(trigger!);
    expect(readAllItem(container, 'ws-alpha')!.textContent).toContain('Read all');
  });
});
