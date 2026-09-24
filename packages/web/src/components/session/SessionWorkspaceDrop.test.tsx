// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { SessionList } from './SessionList';
import { WorkspaceRail } from '@/components/layout/WorkspaceRail';
import { Layout } from '@/App';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { Session, Workspace } from '@/types';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

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

const BASE_WORKSPACE: Workspace = {
  id: 'ws-existing',
  name: 'Existing',
  order: null,
};
const SECOND_WORKSPACE: Workspace = {
  id: 'ws-second',
  name: 'Second',
  order: null,
};

function sessionWithStatus(
  id: string,
  workerStatus: string | null,
  workspaceIds: string[] = ['ws-existing'],
  managedBy: string | null = null,
): Session {
  return {
    id,
    name: id,
    workspaceIds,
    managedBy,
    workerStatus,
    managed: [],
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    updatedAt: new Date().toISOString(),
  } as unknown as Session;
}

const NULL_RECT = {
  top: 0, bottom: 0, height: 0, width: 0, left: 0, right: 0, x: 0, y: 0,
  toJSON: () => {},
};

function rect(left: number, top: number, width: number, height: number) {
  return { ...NULL_RECT, left, right: left + width, x: left, top, bottom: top + height, y: top, width, height };
}

let rectSpy: ReturnType<typeof vi.spyOn>;

function setupRects() {
  rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.dataset.sessionCardId) return rect(0, 0, 280, 64);
    if (this.dataset.workspaceTabId === 'all') return rect(300, 0, 180, 32);
    if (this.dataset.workspaceTabId === BASE_WORKSPACE.id) return rect(300, 40, 180, 40);
    if (this.dataset.workspaceTabId === SECOND_WORKSPACE.id) return rect(300, 80, 180, 40);
    if (this.dataset.workspaceTabId === '__create_workspace__') return rect(300, 130, 180, 40);
    if (this.dataset.testid === 'mobile-workspace-rail-collapsed') return rect(260, 200, 44, 600);
    if (this.dataset.testid === 'mobile-workspace-rail-expanded') return rect(253, 0, 137, 800);
    if (this.querySelector('[data-session-card-id]')) return rect(0, 0, 300, 600);
    return { ...NULL_RECT };
  });
}

function startSessionDrag(container: HTMLElement) {
  const handle = container.querySelector('[data-testid="drag-handle"]');
  if (!handle) throw new Error('Session drag handle was not rendered');
  act(() => fireEvent.pointerDown(handle, { button: 0, pointerType: 'mouse', clientX: 10, clientY: 10 }));
}

function movePointer(clientX: number, clientY: number) {
  act(() => window.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX, clientY })));
}

function releasePointer() {
  act(() => window.dispatchEvent(new Event('pointerup', { bubbles: true })));
}

describe('Workspace rail drag interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.setSessionWorkspaces.mockResolvedValue(undefined);
    apiMocks.createWorkspace.mockImplementation(async (name: string) => ({
      id: 'ws-created', name, order: null,
    }));
    apiMocks.saveWorkspaceOrder.mockResolvedValue(undefined);
    useSessionStore.setState({
      sessions: [{
        id: 'session-alpha', name: 'Alpha', workspaceIds: [], managed: [],
        alwaysThinkingEnabled: false, effort: '', history: [], updatedAt: new Date().toISOString(),
      } as unknown as Session],
      currentSessionId: null,
      selectedIds: new Set(),
      multiSelectMode: false,
      sessionsLoading: false,
    });
    useWorkspaceStore.setState({ workspaces: [BASE_WORKSPACE], loaded: true, loading: false, error: null });
    useUIStore.setState({
      railExpanded: true,
      groupBy: 'none',
      dragEnabled: true,
      sortBy: 'recent',
      customOrder: [],
      searchQuery: '',
      specialFilters: new Set(),
      hiddenSessionIds: new Set(),
      collapsedGroups: new Set(),
      toastQueue: [],
    });
    setupRects();
  });

  afterEach(() => {
    rectSpy?.mockRestore();
    vi.unstubAllGlobals();
  });

  it('moves a dragged Session to the Workspace tab using the final pointer target', async () => {
    const { container } = render(<><SessionList /><WorkspaceRail /></>);
    startSessionDrag(container);
    movePointer(350, 55);
    releasePointer();

    await waitFor(() => expect(apiMocks.setSessionWorkspaces).toHaveBeenCalledWith('session-alpha', ['ws-existing']));
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-existing']);
    expect(apiMocks.saveWorkspaceOrder).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="workspace-count-ws-existing"]')?.textContent).toBe('1');
  });

  it('reorders Workspace tabs and persists order without changing Session membership', async () => {
    useWorkspaceStore.setState({ workspaces: [BASE_WORKSPACE, SECOND_WORKSPACE] });
    const { container } = render(<><SessionList /><WorkspaceRail /></>);
    const source = container.querySelector('[data-workspace-tab-id="ws-existing"]');
    expect(source).not.toBeNull();

    act(() => fireEvent.pointerDown(source!, { button: 0, pointerType: 'mouse', clientY: 55 }));
    movePointer(350, 112);
    expect(document.body.classList.contains('select-none')).toBe(true);
    releasePointer();

    await waitFor(() => expect(apiMocks.saveWorkspaceOrder).toHaveBeenCalledWith(['ws-second', 'ws-existing']));
    expect(useWorkspaceStore.getState().workspaces.map((workspace) => workspace.id)).toEqual(['ws-second', 'ws-existing']);
    expect(apiMocks.setSessionWorkspaces).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual([]);
    expect(container.querySelector('[data-testid="workspace-count-ws-existing"]')?.textContent).toBe('0');
  });

  it('supports touch sorting from the mobile WorkspaceRail drag handle', async () => {
    useWorkspaceStore.setState({ workspaces: [BASE_WORKSPACE, SECOND_WORKSPACE] });
    const onMobileExpandedChange = vi.fn();
    const { container } = render(
      <WorkspaceRail mobileDrawer mobileExpanded onMobileExpandedChange={onMobileExpandedChange} />,
    );
    const handle = container.querySelector('[aria-label="拖动工作区「Existing」排序"]');
    expect(handle).not.toBeNull();
    expect(handle?.className).toContain('touch-none');

    act(() => fireEvent.pointerDown(handle!, { button: 0, pointerType: 'touch', clientY: 55 }));
    movePointer(350, 112);
    releasePointer();

    await waitFor(() => expect(apiMocks.saveWorkspaceOrder).toHaveBeenCalledWith(['ws-second', 'ws-existing']));
    expect(useWorkspaceStore.getState().workspaces.map((workspace) => workspace.id)).toEqual(['ws-second', 'ws-existing']);
    expect(apiMocks.setSessionWorkspaces).not.toHaveBeenCalled();
  });

  it.each([
    ['running', 'bg-accent/10', 'text-accent'],
    ['idle', 'bg-success/10', 'text-success'],
    ['held', 'bg-warning/10', 'text-warning'],
  ])('colours the count badge for %s workers', (status, backgroundClass, textClass) => {
    useSessionStore.setState({ sessions: [sessionWithStatus('status-session', status)] });
    const { container } = render(<WorkspaceRail />);
    const badge = container.querySelector('[data-testid="workspace-count-ws-existing"]');
    expect(badge?.className).toContain(backgroundClass);
    expect(badge?.className).toContain(textClass);
    expect(badge?.textContent).toBe('1');
  });

  it.each([
    [['idle', 'held'], 'held', 'bg-warning/10'],
    [['held', 'running'], 'running', 'bg-accent/10'],
    [['idle', 'held', 'running'], 'running', 'bg-accent/10'],
  ])('uses the highest-priority worker state in a Workspace', (statuses, expectedStatus, expectedClass) => {
    useSessionStore.setState({
      sessions: (statuses as string[]).map((status, index) => sessionWithStatus(`member-${index}`, status)),
    });
    const { container } = render(<WorkspaceRail />);
    const badge = container.querySelector('[data-testid="workspace-count-ws-existing"]');
    expect(badge?.getAttribute('data-worker-status')).toBe(expectedStatus);
    expect(badge?.className).toContain(expectedClass);
    expect(badge?.textContent).toBe(String((statuses as string[]).length));
  });

  it('keeps the current count style for offline, null, and unknown worker states', () => {
    useSessionStore.setState({
      sessions: [
        sessionWithStatus('offline-member', 'offline'),
        sessionWithStatus('null-member', null),
        sessionWithStatus('unknown-member', 'future-state'),
      ],
    });
    const { container } = render(<WorkspaceRail />);
    const badge = container.querySelector('[data-testid="workspace-count-ws-existing"]');
    expect(badge?.getAttribute('data-worker-status')).toBe('offline');
    expect(badge?.className).toContain('border-border-muted');
    expect(badge?.className).toContain('bg-bg-tertiary');
    expect(badge?.className).toContain('text-text-tertiary');
    expect(badge?.textContent).toBe('3');
  });

  it('counts managed descendants in their root Workspace and ignores stale child membership', () => {
    useWorkspaceStore.setState({ workspaces: [BASE_WORKSPACE, SECOND_WORKSPACE] });
    useSessionStore.setState({
      sessions: [
        sessionWithStatus('manager', 'idle', ['ws-existing']),
        sessionWithStatus('worker', 'held', ['ws-second'], 'manager'),
        sessionWithStatus('nested-worker', 'running', ['ws-second'], 'worker'),
      ],
    });
    const { container } = render(<WorkspaceRail />);
    const inheritedCount = container.querySelector('[data-testid="workspace-count-ws-existing"]');
    const staleChildWorkspaceCount = container.querySelector('[data-testid="workspace-count-ws-second"]');
    expect(inheritedCount?.textContent).toBe('3');
    expect(inheritedCount?.getAttribute('data-worker-status')).toBe('running');
    expect(inheritedCount?.className).toContain('bg-accent/10');
    expect(staleChildWorkspaceCount?.textContent).toBe('0');
    expect(staleChildWorkspaceCount?.getAttribute('data-worker-status')).toBe('offline');
  });

  it('creates a uniquely named Workspace when the Session is dropped on New Workspace', async () => {
    const duplicate: Workspace = { id: 'ws-alpha', name: 'Alpha', order: null };
    useWorkspaceStore.setState({ workspaces: [BASE_WORKSPACE, duplicate] });
    const { container } = render(<><SessionList /><WorkspaceRail /></>);
    expect(container.querySelector('button[data-workspace-tab-id="__create_workspace__"]')).not.toBeNull();

    startSessionDrag(container);
    movePointer(350, 145);
    releasePointer();

    await waitFor(() => expect(apiMocks.createWorkspace).toHaveBeenCalledWith('Alpha-1'));
    await waitFor(() => expect(apiMocks.setSessionWorkspaces).toHaveBeenCalledWith('session-alpha', ['ws-created']));
    expect(useWorkspaceStore.getState().workspaces.some((workspace) => workspace.name === 'Alpha-1')).toBe(true);
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-created']);
  });

  it('opens the Sidebar and attached WorkspaceRail during a touch drop from the mobile chat list', async () => {
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    })));
    const { container } = render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<SessionList />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(container.querySelector('[aria-label="打开侧边栏"]')).not.toBeNull());
    const chatDragHandle = container.querySelector('main [data-testid="drag-handle"]');
    expect(chatDragHandle).not.toBeNull();
    act(() => fireEvent.pointerDown(chatDragHandle!, {
      button: 0,
      pointerType: 'touch',
      clientX: 10,
      clientY: 10,
    }));
    movePointer(10, 500); // the left edge opens the attached Sidebar + WorkspaceRail for the drop
    await waitFor(() => expect(container.querySelector('[data-testid="mobile-workspace-rail-expanded"]')).not.toBeNull());
    movePointer(350, 55);
    releasePointer();

    await waitFor(() => expect(apiMocks.setSessionWorkspaces).toHaveBeenCalledWith('session-alpha', ['ws-existing']));
    await waitFor(() => {
      expect(useUIStore.getState().mobileSidebarOpen).toBe(false);
      expect(container.querySelector('[data-testid="mobile-workspace-rail-collapsed"]')).toBeNull();
    });
  });
});
