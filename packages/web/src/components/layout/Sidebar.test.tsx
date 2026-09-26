// @vitest-environment jsdom
import { Profiler } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

function RouteProbe() {
  const location = useLocation();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  return <div data-testid="route-probe">{location.pathname}: {currentSessionId ?? 'none'}</div>;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Sidebar Session search controls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    useSessionStore.setState({ sessions: [], currentSessionId: null, multiSelectMode: false });
    useUIStore.setState({
      sidebarCollapsed: false,
      searchQuery: '',
      specialFilters: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
      dragEnabled: true,
    });
  });

  function renderSidebar() {
    return render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
  }

  it('shows an accessible clear button only when the query is non-empty', () => {
    renderSidebar();
    expect(screen.queryByRole('button', { name: 'Clear session search' })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('Filter...'), {
      target: { value: 'cli-session' },
    });
    expect(screen.getByRole('button', { name: 'Clear session search' })).toBeTruthy();
  });

  it('uses a right chevron when Files is folded and a down chevron when open', () => {
    useUIStore.setState({ filesCollapsed: true });
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <Sidebar />
      </MemoryRouter>,
    );

    const filesHeader = screen.getByText('Files').parentElement?.parentElement;
    expect(filesHeader?.querySelector('svg.lucide-chevron-right')).not.toBeNull();

    fireEvent.click(screen.getByText('Files'));
    expect(useUIStore.getState().filesCollapsed).toBe(false);
    const expandedHeader = screen.getByText('Files').parentElement?.parentElement;
    expect(expandedHeader?.querySelector('svg.lucide-chevron-down')).not.toBeNull();
  });

  it('opens the special filters menu to the right of its trigger', () => {
    renderSidebar();

    fireEvent.click(screen.getByTitle('Special filters'));

    const menu = screen.getByRole('menu');
    expect(menu.className).toContain('left-0');
    expect(menu.className).not.toContain('right-0');
  });

  it('clears the query and restores the unfiltered state', () => {
    useSessionStore.setState({
      sessions: [
        {
          id: 'pan-alpha',
          name: 'Alpha session',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
        {
          id: 'pan-beta',
          name: 'Beta session',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
      ],
    });
    useUIStore.setState({ searchQuery: 'alpha' });
    renderSidebar();

    expect(screen.getByText('Alpha session')).toBeTruthy();
    expect(screen.queryByText('Beta session')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Clear session search' }));

    expect(useUIStore.getState().searchQuery).toBe('');
    expect(screen.queryByRole('button', { name: 'Clear session search' })).toBeNull();
    expect(screen.getByText('Beta session')).toBeTruthy();
  });

  it('selects all filtered sessions, including sessions hidden in normal mode', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'beta', name: 'Beta', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'hidden', name: 'Hidden', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ hiddenSessionIds: new Set(['hidden']), searchQuery: 'a' });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));

    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['alpha', 'beta']));
    expect(screen.getByRole('button', { name: 'Deselect all visible sessions' })).toBeTruthy();
  });

  it('fills a partial selection and then deselects the filtered candidates', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'beta', name: 'Beta', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(['alpha']),
    });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));
    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['alpha', 'beta']));

    fireEvent.click(screen.getByRole('button', { name: 'Deselect all visible sessions' }));
    expect(useSessionStore.getState().selectedIds).toEqual(new Set());
  });

  it.each([280, 240])('keeps selection actions within a %ipx-or-narrower viewport', (viewportWidth) => {
    useSessionStore.setState({
      sessions: [{ id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] }],
      multiSelectMode: true,
      selectedIds: new Set(['alpha']),
    });
    useUIStore.getState().setSidebarWidth(200);
    expect(useUIStore.getState().sidebarWidth).toBe(280);
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: viewportWidth });
    renderSidebar();

    const sidebar = document.querySelector('aside');
    const selectionBar = document.querySelector('.sidebar-selection-bar');
    const actions = document.querySelector('.sidebar-selection-actions');
    expect(sidebar?.style.width).toBe('min(280px, 100vw)');
    expect(sidebar?.style.minWidth).toBe('min(280px, 100vw)');
    expect(selectionBar?.className).toContain('flex-wrap');
    expect(actions?.className).toContain('flex-wrap');
    expect(screen.getByRole('button', { name: 'Delete selected sessions' }).title).toBe('Delete selected sessions');
    expect(screen.getByRole('button', { name: 'Cancel selection' }).title).toBe('Cancel selection');
    expect(screen.getByRole('button', { name: 'Move selected sessions to workspace' }).textContent).toContain('Workspace');
  });

  it('keeps full action labels and explicit accessible names in the wide selection bar', () => {
    useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set(['alpha']) });
    useUIStore.setState({ sidebarWidth: 480 });
    renderSidebar();

    expect(document.querySelector('aside')?.style.width).toBe('min(480px, 100vw)');
    expect(screen.getByRole('button', { name: 'Delete selected sessions' }).textContent).toContain('Delete');
    expect(screen.getByRole('button', { name: 'Cancel selection' }).textContent).toContain('Cancel');
    expect(screen.getByRole('button', { name: 'Move selected sessions to workspace' }).textContent).toContain('Workspace');
    expect(document.querySelector('.sidebar-selection-action-icon')).toBeTruthy();
  });

  it('uses special filters for the select-all candidate range', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'meta', name: 'Meta', alwaysThinkingEnabled: false, effort: '', history: [], mcpServers: ['pan'] },
        { id: 'plain', name: 'Plain', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ specialFilters: new Set(['metaagent']) });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));

    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['meta']));
  });

  it('disables select all when the filtered candidate list is empty', () => {
    useSessionStore.setState({
      sessions: [{ id: 'plain', name: 'Plain', alwaysThinkingEnabled: false, effort: '', history: [] }],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ searchQuery: 'does-not-match' });
    renderSidebar();

    expect((screen.getByRole('button', { name: 'Select all visible sessions' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('defaults drag sorting on and hides the handle when switched off', () => {
    useSessionStore.setState({
      sessions: [{ id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] }],
    });
    renderSidebar();

    expect(screen.getByTestId('drag-handle')).toBeTruthy();
    expect(useUIStore.getState().dragEnabled).toBe(true);

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Sort sessions: recent' }), {
      pointerType: 'touch', clientX: 10, clientY: 10,
    });
    act(() => vi.advanceTimersByTime(600));
    expect(screen.getByRole('menu', { name: 'Session list options' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitemcheckbox'));

    expect(useUIStore.getState().dragEnabled).toBe(false);
    expect(screen.queryByTestId('drag-handle')).toBeNull();
    expect(localStorage.getItem('pan:dragEnabled')).toBe('false');
  });

  it('opens the drag menu on long press without cycling sort', () => {
    vi.useFakeTimers();
    renderSidebar();
    const sort = screen.getByRole('button', { name: 'Sort sessions: recent' });
    fireEvent.pointerDown(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    act(() => vi.advanceTimersByTime(600));
    fireEvent.pointerUp(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(sort);

    expect(screen.getByRole('menu', { name: 'Session list options' })).toBeTruthy();
    expect(useUIStore.getState().sortBy).toBe('recent');
    vi.useRealTimers();
  });

  it('keeps a short press cycling sort', () => {
    renderSidebar();
    const sort = screen.getByRole('button', { name: 'Sort sessions: recent' });
    fireEvent.pointerDown(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(sort);
    expect(useUIStore.getState().sortBy).toBe('name');
  });
});

describe('Sidebar session navigation from Jobs', () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionStore.setState({
      sessions: [
        { id: 'jobs-session', name: 'Jobs session', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      currentSessionId: null,
      multiSelectMode: false,
      selectedIds: new Set(),
    });
    useUIStore.setState({
      sidebarCollapsed: false,
      mobileSidebarOpen: false,
      searchQuery: '',
      specialFilters: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
      dragEnabled: false,
    });
  });

  function renderJobsSidebar() {
    return render(
      <MemoryRouter initialEntries={['/jobs']}>
        <Sidebar />
        <Routes>
          <Route path="/jobs" element={<div>Jobs view</div>} />
          <Route path="/" element={<div>Chat view</div>} />
        </Routes>
        <RouteProbe />
      </MemoryRouter>,
    );
  }

  it('opens the clicked session in Chat from Jobs, including when it is already selected', () => {
    const { container } = renderJobsSidebar();

    fireEvent.click(container.querySelector('[data-session-card-id="jobs-session"]')!);

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: jobs-session');
    expect(useSessionStore.getState().currentSessionId).toBe('jobs-session');

    // Repeating the body click while the same session is selected still
    // navigates from Jobs.
    fireEvent.click(screen.getByRole('link', { name: /Jobs/ }));
    expect(screen.getByText('Jobs view')).toBeTruthy();
    fireEvent.click(container.querySelector('[data-session-card-id="jobs-session"]')!);
    expect(screen.getByText('Chat view')).toBeTruthy();
  });

  it('does not navigate when opening the card menu', () => {
    renderJobsSidebar();

    fireEvent.click(screen.getByTitle('Session actions'));

    expect(screen.getByRole('button', { name: 'Rename' })).toBeTruthy();
    expect(screen.getByText('Jobs view')).toBeTruthy();
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('does not navigate when the multi-select checkbox is clicked', () => {
    useSessionStore.setState({ multiSelectMode: true });
    renderJobsSidebar();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Jobs session' }));

    expect(screen.getByText('Jobs view')).toBeTruthy();
    expect(useSessionStore.getState().selectedIds.has('jobs-session')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('closes an open mobile Sidebar after navigating from Jobs to Chat', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: true,
      media: '(max-width: 767px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    })));
    useUIStore.setState({ mobileSidebarOpen: true });
    const { container } = renderJobsSidebar();

    fireEvent.click(container.querySelector('[data-session-card-id="jobs-session"]')!);

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(useUIStore.getState().mobileSidebarOpen).toBe(false);
    vi.unstubAllGlobals();
  });
});

// FE-1: the sidebar chain must only re-render for state it actually reads.
// Streaming chunks, draft keystrokes, thinking/tool flags, toasts and
// interactive requests are all irrelevant to the sidebar slice.
describe('Sidebar render isolation (fine-grained selectors)', () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      multiSelectMode: false,
      selectedIds: new Set(),
      inputDrafts: {},
      currentMessages: [],
      rendering: false,
    });
    useUIStore.setState({
      sidebarCollapsed: false,
      searchQuery: '',
      specialFilters: new Set(),
      hiddenSessionIds: new Set(),
      collapsedGroups: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
      dragEnabled: true,
      toastQueue: [],
      approvalRequests: [],
      userInputRequests: [],
      elicitationRequests: [],
      terminalInteractions: [],
    });
  });

  function renderProfiledSidebar() {
    const commits: number[] = [];
    render(
      <MemoryRouter>
        <Profiler id="sidebar" onRender={() => commits.push(1)}>
          <Sidebar />
        </Profiler>
      </MemoryRouter>,
    );
    return commits;
  }

  it('ignores streaming/draft/toast updates but re-renders on a session change', () => {
    const commits = renderProfiledSidebar();
    const afterMount = commits.length;
    expect(afterMount).toBeGreaterThan(0);

    act(() => {
      // Draft keystroke.
      useSessionStore.setState((s) => ({ inputDrafts: { ...s.inputDrafts, A: 'draft' } }));
      // A stream chunk landing on the selected session.
      useSessionStore.setState({ currentMessages: [{ role: 'assistant', content: 'chunk' }] });
      // Thinking/tool rendering flag.
      useSessionStore.setState({ rendering: true });
    });
    act(() => {
      // Toast + interactive request (UI store, outside the sidebar slice).
      useUIStore.setState({ toastQueue: [{ id: 't1', message: 'hi', type: 'info' }] });
      useUIStore.setState({
        approvalRequests: [
          { sessionId: 'A', workerId: 'w1', requestId: 1, method: 'm', params: {} },
        ],
      });
    });
    expect(commits.length).toBe(afterMount);

    act(() => {
      useSessionStore.setState({
        sessions: [
          { id: 'A', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [], lastMessage: 'hi' },
        ],
      });
    });
    expect(commits.length).toBeGreaterThan(afterMount);
  });
});
