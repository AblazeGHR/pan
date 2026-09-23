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
        { id: 'inside', name: 'Inside workspace', workspaceIds: ['ws-alpha'], alwaysThinkingEnabled: false, effort: '', history: [] },
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

  it('keeps only a handle when collapsed and opens a full-screen rail without changing the Sidebar drawer state', async () => {
    stubViewport(true);
    renderLayout();

    const handle = await screen.findByRole('button', { name: '展开工作区' });
    const collapsedRail = screen.getByTestId('mobile-workspace-rail-collapsed');
    expect(collapsedRail.className).toContain('fixed');
    expect(collapsedRail.className).toContain('w-11');
    expect(screen.queryByRole('button', { name: /Alpha/ })).toBeNull();

    useUIStore.getState().setMobileSidebarOpen(true);
    fireEvent.click(handle);

    const overlay = screen.getByTestId('mobile-workspace-rail-overlay');
    expect(overlay.className).toContain('inset-0');
    expect(overlay.className).toContain('w-screen');
    expect(overlay.className).toContain('h-[100dvh]');
    expect(screen.getByRole('button', { name: '收起工作区面板' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Alpha/ }));
    expect(useUIStore.getState().activeWorkspaceId).toBe('ws-alpha');
    expect(useUIStore.getState().railExpanded).toBe(false);
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    expect(screen.getByTestId('mobile-workspace-rail-overlay')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '收起工作区面板' }));
    await waitFor(() => expect(screen.getByTestId('mobile-workspace-rail-collapsed')).toBeTruthy());
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    expect(screen.getByText('Inside workspace')).toBeTruthy();
    expect(screen.queryByText('Outside workspace')).toBeNull();
  });

  it('keeps the desktop rail in the Sidebar layout and does not mount a mobile overlay', async () => {
    stubViewport(false);
    renderLayout();

    await waitFor(() => {
      expect(screen.queryByTestId('mobile-workspace-rail-collapsed')).toBeNull();
      expect(screen.queryByTestId('mobile-workspace-rail-overlay')).toBeNull();
    });
    const desktopHandle = screen.getByRole('button', { name: '全部' });
    const desktopRail = desktopHandle.parentElement;
    expect(desktopRail?.style.width).toBe('0px');
    fireEvent.click(desktopHandle);
    await waitFor(() => expect(desktopRail?.style.width).toBe('172px'));
  });
});
