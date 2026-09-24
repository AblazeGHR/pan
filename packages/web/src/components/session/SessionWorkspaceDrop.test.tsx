// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { SessionList } from './SessionList';
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
    if (this.dataset.testid === 'mobile-workspace-rail-collapsed') return rect(760, 450, 44, 44);
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

  afterEach(() => rectSpy?.mockRestore());

  it('moves a dragged Session to the Workspace tab using the final pointer target', async () => {
    const { container } = render(<><SessionList /><WorkspaceRail /></>);
    startSessionDrag(container);
    movePointer(350, 55);
    releasePointer();

    await waitFor(() => expect(apiMocks.setSessionWorkspaces).toHaveBeenCalledWith('session-alpha', ['ws-existing']));
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-existing']);
    expect(apiMocks.saveWorkspaceOrder).not.toHaveBeenCalled();
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

  it('opens the collapsed mobile rail during a drag and accepts a drop there', async () => {
    const { container } = render(<><SessionList /><WorkspaceRail mobileOverlay /></>);
    startSessionDrag(container);
    movePointer(780, 470);
    expect(container.querySelector('[data-testid="mobile-workspace-rail-overlay"]')).not.toBeNull();
    movePointer(350, 55);
    releasePointer();

    await waitFor(() => expect(apiMocks.setSessionWorkspaces).toHaveBeenCalledWith('session-alpha', ['ws-existing']));
    await waitFor(() => expect(container.querySelector('[data-testid="mobile-workspace-rail-collapsed"]')).not.toBeNull());
  });
});
