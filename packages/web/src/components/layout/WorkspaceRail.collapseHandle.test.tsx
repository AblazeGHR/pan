// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceRail } from './WorkspaceRail';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    selectedIds: new Set(),
    multiSelectMode: false,
    sessionsLoading: false,
  });
  useWorkspaceStore.setState({
    workspaces: [],
    loaded: true,
    loading: false,
    error: null,
  });
  useUIStore.setState({
    sidebarCollapsed: false,
    railExpanded: false,
    activeWorkspaceId: 'all',
    mobileSidebarOpen: false,
    groupBy: 'none',
    dragEnabled: false,
    sortBy: 'recent',
    searchQuery: '',
    workspaceSearchQuery: '',
    specialFilters: new Set(),
    adapterFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
    toastQueue: [],
  });
});

afterEach(cleanup);

function renderRail(props: {
  mobileDrawer?: boolean;
  mobileExpanded?: boolean;
  onMobileExpandedChange?: (expanded: boolean) => void;
} = {}) {
  return render(
    <MemoryRouter>
      <WorkspaceRail {...props} />
    </MemoryRouter>,
  );
}

describe('desktop Sidebar collapse handle', () => {
  it('sits above the workspace handle, is smaller, has no visible text, and keeps an accessible name', () => {
    renderRail();

    const collapse = screen.getByTestId('sidebar-collapse-handle');
    const workspace = screen.getByTestId('workspace-rail-handle');

    // Accessibility survives the absence of a visible label.
    expect(collapse.getAttribute('aria-label')).toBe('收起侧边栏');
    expect(collapse.getAttribute('title')).toBe('收起侧边栏');
    // Icon-only: no text node inside the button.
    expect(collapse.textContent).toBe('');
    // Narrower than the 20px workspace handle.
    expect(collapse.style.width).toBe('16px');
    expect(workspace.style.width).toBe('20px');
    // Anchored above the vertical centre line by the measured workspace
    // handle half-height plus the deliberate gap.
    const offset = Number(/^calc\(50% \+ ([\d.]+)px\)$/.exec(collapse.style.bottom)?.[1]);
    expect(Number.isFinite(offset)).toBe(true);
    // jsdom has no layout (offsetHeight 0), so only the gap itself remains;
    // real geometry is measured in Chromium.
    expect(offset).toBeGreaterThanOrEqual(16);
    // Rendered before (visually above) the unchanged workspace handle.
    expect(collapse.compareDocumentPosition(workspace) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('takes keyboard focus', () => {
    renderRail();
    const collapse = screen.getByTestId('sidebar-collapse-handle');
    expect(collapse.tagName).toBe('BUTTON');
    expect(collapse.hasAttribute('disabled')).toBe(false);
    collapse.focus();
    expect(document.activeElement).toBe(collapse);
  });

  it('collapses the Sidebar without changing the workspace rail state', () => {
    renderRail();

    fireEvent.click(screen.getByTestId('sidebar-collapse-handle'));

    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    expect(localStorage.getItem('pan:sidebarCollapsed')).toBe('1');
    expect(useUIStore.getState().railExpanded).toBe(false);
    // Nothing is left to collapse, so the handle hides; the workspace handle
    // and the existing nav recovery entry remain available.
    expect(screen.queryByTestId('sidebar-collapse-handle')).toBeNull();
    expect(screen.getByTestId('workspace-rail-handle')).toBeTruthy();
  });

  it('stays above the workspace handle in both rail states and leaves its toggle intact', () => {
    renderRail();
    const workspace = screen.getByTestId('workspace-rail-handle');

    fireEvent.click(workspace);
    expect(useUIStore.getState().railExpanded).toBe(true);
    expect(screen.getByTestId('sidebar-collapse-handle')).toBeTruthy();

    fireEvent.click(screen.getByTestId('workspace-rail-handle'));
    expect(useUIStore.getState().railExpanded).toBe(false);
    expect(screen.getByTestId('sidebar-collapse-handle')).toBeTruthy();
  });
});

describe('mobile workspace drawer', () => {
  it('never mounts the Sidebar collapse handle and keeps the existing mobile toggle', () => {
    const onMobileExpandedChange = vi.fn();
    renderRail({ mobileDrawer: true, onMobileExpandedChange });

    expect(screen.queryByTestId('sidebar-collapse-handle')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '展开工作区' }));
    expect(onMobileExpandedChange).toHaveBeenCalledWith(true);
  });

  it('does not mount it while the mobile rail is expanded either', () => {
    renderRail({ mobileDrawer: true, mobileExpanded: true, onMobileExpandedChange: vi.fn() });

    expect(screen.queryByTestId('sidebar-collapse-handle')).toBeNull();
    expect(screen.getByRole('button', { name: '收起工作区面板' })).toBeTruthy();
  });
});
