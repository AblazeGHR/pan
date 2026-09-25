// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './App';
import { useSessionStore } from './stores/sessionStore';
import { useUIStore } from './stores/uiStore';
import { useWorkspaceStore } from './stores/workspaceStore';

vi.mock('./hooks/useWebSocket', () => ({ useWebSocket: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('responsive WorkspaceRail placement', () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionStore.setState({
      sessions: [
        { id: 'inside', name: 'Inside workspace', adapter: 'codex', workspaceIds: ['ws-alpha'], alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'outside', name: 'Outside workspace', workspaceIds: [], alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      currentSessionId: null,
      multiSelectMode: false,
      selectedIds: new Set(),
    });
    useWorkspaceStore.setState({
      workspaces: [{ id: 'ws-alpha', name: 'Alpha', order: null }],
      loaded: true,
      loading: false,
      error: null,
    });
    useUIStore.setState({
      sidebarCollapsed: false,
      mobileSidebarOpen: false,
      activeWorkspaceId: 'all',
      railExpanded: false,
      searchQuery: '',
      specialFilters: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
      dragEnabled: true,
    });
  });

  function stubViewport(isMobile: boolean) {
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
      matches: isMobile && query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    })));
  }

  function renderLayout() {
    return render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<div>Chat body</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  it('attaches a touch-sized handle to the mobile Sidebar and expands beside it to fill the viewport', async () => {
    stubViewport(true);
    const { container } = renderLayout();

    expect(await screen.findByRole('button', { name: '打开侧边栏' })).toBeTruthy();
    expect(screen.queryByTestId('mobile-workspace-rail-collapsed')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '打开侧边栏' }));

    const drawer = await screen.findByTestId('mobile-sidebar-workspace-drawer');
    const handle = await screen.findByRole('button', { name: '展开工作区' });
    const collapsedRail = screen.getByTestId('mobile-workspace-rail-collapsed');
    expect(collapsedRail.className).toContain('w-11');
    expect(collapsedRail.className).toContain('flex-none');
    expect(handle.className).toContain('rounded-r-lg');
    expect(handle.className).toContain('border-l-0');
    expect(handle.className).not.toContain('rounded-l-lg');
    expect(drawer.querySelector('aside')?.style.width).toBe('min(280px, 100vw)');
    expect(container.querySelector('[data-workspace-tab-id="ws-alpha"]')).toBeNull();

    fireEvent.click(handle);

    const expandedRail = screen.getByTestId('mobile-workspace-rail-expanded');
    expect(drawer.style.width).toBe('100vw');
    expect(drawer.querySelector('aside')?.style.width).toBe('50vw');
    expect(expandedRail.className).toContain('flex-1');
    expect(expandedRail.className).not.toContain('fixed');
    expect(screen.getByRole('button', { name: '收起工作区面板' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '展开工作区' })).toBeNull();

    const alphaTab = container.querySelector('[data-workspace-tab-id="ws-alpha"]');
    expect(alphaTab).not.toBeNull();
    fireEvent.click(alphaTab!);
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws-alpha');
    expect(useUIStore.getState().railExpanded).toBe(false);
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    expect(screen.getByTestId('mobile-workspace-rail-expanded')).toBeTruthy();
    expect(screen.getByText('Inside workspace')).toBeTruthy();
    expect(screen.queryByText('Outside workspace')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '收起工作区面板' }));
    await waitFor(() => expect(screen.getByTestId('mobile-workspace-rail-collapsed')).toBeTruthy());
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);

    fireEvent.click(screen.getByTestId('mobile-sidebar-close'));
    await waitFor(() => {
      expect(useUIStore.getState().mobileSidebarOpen).toBe(false);
      expect(screen.queryByTestId('mobile-workspace-rail-collapsed')).toBeNull();
    });
  });

  it('keeps the desktop rail in the Sidebar layout and does not mount a mobile overlay', async () => {
    stubViewport(false);
    renderLayout();

    await waitFor(() => {
      expect(screen.queryByTestId('mobile-workspace-rail-collapsed')).toBeNull();
      expect(screen.queryByTestId('mobile-workspace-rail-expanded')).toBeNull();
    });
    const desktopHandle = screen.getByRole('button', { name: '全部' });
    const desktopRail = desktopHandle.parentElement;
    expect(desktopRail?.style.width).toBe('0px');
    fireEvent.click(desktopHandle);
    await waitFor(() => expect(desktopRail?.style.width).toBe('172px'));
  });
});
