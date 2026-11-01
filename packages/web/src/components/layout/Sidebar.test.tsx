// @vitest-environment jsdom
import { Profiler } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { useEditorStore } from '@/stores/editorStore';

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
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useUIStore.setState({
      sidebarCollapsed: false,
      searchQuery: '',
      workspaceSearchQuery: '',
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

  it('typing and clearing Session search leaves the workspace query untouched', () => {
    useUIStore.setState({ workspaceSearchQuery: 'Alpha WS' });
    renderSidebar();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search sessions' }), {
      target: { value: 'worker-one' },
    });
    expect(useUIStore.getState().searchQuery).toBe('worker-one');
    expect(useUIStore.getState().workspaceSearchQuery).toBe('Alpha WS');
    fireEvent.click(screen.getByRole('button', { name: 'Clear session search' }));
    expect(useUIStore.getState().searchQuery).toBe('');
    expect(useUIStore.getState().workspaceSearchQuery).toBe('Alpha WS');
  });

  it('shows an accessible clear button only when the query is non-empty', () => {
    renderSidebar();
    expect(screen.queryByRole('button', { name: 'Clear session search' })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('Filter...'), {
      target: { value: 'cli-session' },
    });
    expect(screen.getByRole('button', { name: 'Clear session search' })).toBeTruthy();
  });

  it('hides Group by by default while keeping Manager collapse and the toggle cycle usable', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'manager', name: 'Manager', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'child', name: 'Child', managedBy: 'manager', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
    });
    useUIStore.setState({ groupBy: 'workdir', collapsedGroups: new Set() });
    renderSidebar();

    expect(screen.queryByTitle(/Group by/)).toBeNull();
    const collapseButton = screen.getByTitle('Collapse all groups');
    fireEvent.click(collapseButton);
    expect(useUIStore.getState().collapsedGroups.size).toBeGreaterThan(0);

    act(() => useAppSettingsStore.getState().setShowGroupBy(true));
    const groupButton = screen.getByTitle('Group by none (click to cycle)');
    fireEvent.click(groupButton);
    expect(useUIStore.getState().groupBy).toBe('none');

    act(() => useAppSettingsStore.getState().setShowGroupBy(false));
    expect(screen.queryByTitle(/Group by/)).toBeNull();
    expect(useUIStore.getState().groupBy).toBe('manager');
    expect(localStorage.getItem('pan:groupBy')).toBe('manager');
  });

  it('renders the CWD root as a collapsible section labelled CWD', async () => {
    vi.useRealTimers();
    useEditorStore.setState({
      sessionId: 's1',
      workdir: 'D:\\project',
      roots: [{ id: 'cwd:D:/project', kind: 'cwd', path: 'D:/project', label: 'CWD' }],
      rootTrees: { 'cwd:D:/project': { nodes: [], loading: false } },
      rootTreeGenerations: {},
      expanded: new Set(),
      tempDirs: [],
      workspaceId: null,
      workspaceDirs: [],
    });
    render(
      <MemoryRouter initialEntries={['/editor']}>
        <Sidebar />
      </MemoryRouter>,
    );

    // Finish React.lazy's actual module work before querying its rendered root.
    await act(async () => { await vi.dynamicImportSettled(); });
    expect(await screen.findByText('CWD')).toBeTruthy();
    expect(screen.getByText('D:/project')).toBeTruthy();
    const cwdHeader = screen.getByTestId('editor-root-header');
    expect(cwdHeader.querySelector('svg.lucide-chevron-down')).not.toBeNull();

    fireEvent.click(cwdHeader);
    expect(cwdHeader.querySelector('svg.lucide-chevron-right')).not.toBeNull();
  });

  it('opens the special filters menu constrained to the viewport', () => {
    renderSidebar();

    fireEvent.click(screen.getByTitle('Special filters'));

    // Viewport-constrained placement (fixed + measured trigger): the panel
    // stays inside the window instead of relying on the old absolute left-0
    // anchoring, which could overflow narrow screens.
    const menu = screen.getByRole('menu');
    expect(menu.className).toContain('fixed');
    const style = menu.getAttribute('style') ?? '';
    const left = Number(/left:\s*([\d.]+)px/.exec(style)?.[1]);
    const top = Number(/top:\s*([\d.]+)px/.exec(style)?.[1]);
    const maxHeight = Number(/max-height:\s*([\d.]+)px/.exec(style)?.[1]);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + 256).toBeLessThanOrEqual(window.innerWidth);
    expect(top).toBeGreaterThan(0);
    expect(maxHeight).toBeGreaterThan(0);
    expect(maxHeight).toBeLessThanOrEqual(window.innerHeight);
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
    fireEvent.click(screen.getByRole('button', { name: /Actions/ }));
    expect(screen.getByRole('menuitem', { name: 'Workspace' }).textContent).toContain('Workspace');
  });

  it('keeps full action labels and explicit accessible names in the wide selection bar', () => {
    useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set(['alpha']) });
    useUIStore.setState({ sidebarWidth: 480 });
    renderSidebar();

    expect(document.querySelector('aside')?.style.width).toBe('min(480px, 100vw)');
    expect(screen.getByRole('button', { name: 'Delete selected sessions' }).textContent).toContain('Delete');
    expect(screen.getByRole('button', { name: 'Cancel selection' }).textContent).toContain('Cancel');
    fireEvent.click(screen.getByRole('button', { name: /Actions/ }));
    expect(screen.getByRole('menuitem', { name: 'Workspace' }).textContent).toContain('Workspace');
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

// A real card activation must always land on the Chat view — from Jobs,
// Terminal, plugin panels or Manage — including a re-click of the already
// selected Session. Only the card body navigates: quick actions, the
// multi-select checkbox and background store selections must not.
// (The /editor sidebar intentionally shows the directory browser instead of
// the Session list, so no card surface exists there to click.)
describe('Sidebar session card activation returns to Chat from any non-chat route', () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionStore.setState({
      sessions: [
        { id: 'nav-a', name: 'Nav A', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'nav-b', name: 'Nav B', alwaysThinkingEnabled: false, effort: '', history: [] },
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

  function renderAt(initial: string) {
    return render(
      <MemoryRouter initialEntries={[initial]}>
        <Sidebar />
        <Routes>
          <Route path="/" element={<div>Chat view</div>} />
          <Route path="/editor" element={<div>Editor view</div>} />
          <Route path="/jobs" element={<div>Jobs view</div>} />
          <Route path="/terminals" element={<div>Terminal view</div>} />
          <Route path="/plugins/:pluginId/:panelId" element={<div>Plugin panel view</div>} />
          <Route path="/manage/:sessionId" element={<div>Manage view</div>} />
        </Routes>
        <RouteProbe />
      </MemoryRouter>,
    );
  }

  function clickCard(container: HTMLElement, id: string) {
    fireEvent.click(container.querySelector(`[data-session-card-id="${id}"]`)!);
  }

  it('opens the clicked session in Chat from the Terminal route', () => {
    const { container } = renderAt('/terminals');
    expect(screen.getByText('Terminal view')).toBeTruthy();

    clickCard(container, 'nav-a');

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: nav-a');
  });

  it('opens the clicked session in Chat from a plugin panel route', () => {
    const { container } = renderAt('/plugins/demo/account-switcher');
    expect(screen.getByText('Plugin panel view')).toBeTruthy();

    clickCard(container, 'nav-b');

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: nav-b');
  });

  it('opens the clicked session in Chat from the Manage route', () => {
    const { container } = renderAt('/manage/nav-a');
    expect(screen.getByText('Manage view')).toBeTruthy();

    clickCard(container, 'nav-b');

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: nav-b');
  });

  it('returns to Chat when re-clicking the already selected session from the Terminal route', () => {
    useSessionStore.setState({ currentSessionId: 'nav-a' });
    const { container } = renderAt('/terminals');
    expect(screen.getByText('Terminal view')).toBeTruthy();

    clickCard(container, 'nav-a');

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: nav-a');
  });


  it('stays on the chat route without stacking navigation when a card is clicked there', () => {
    const { container } = renderAt('/');

    clickCard(container, 'nav-a');

    expect(screen.getByText('Chat view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/: nav-a');
  });

  it('does not navigate when a card quick action is used from the Terminal route', () => {
    const { container } = renderAt('/terminals');

    fireEvent.click(container.querySelector('[data-session-card-id="nav-a"] [data-quick-action="pin"]')!);

    expect(screen.getByText('Terminal view')).toBeTruthy();
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('does not navigate when the multi-select checkbox is clicked from the Terminal route', () => {
    useSessionStore.setState({ multiSelectMode: true });
    const { container } = renderAt('/terminals');

    fireEvent.click(container.querySelector('[data-session-card-id="nav-a"] [data-testid="select-checkbox-zone"]')!);

    expect(screen.getByText('Terminal view')).toBeTruthy();
    expect(useSessionStore.getState().selectedIds.has('nav-a')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
  });

  it('does not navigate when the store selects a session in the background', () => {
    renderAt('/terminals');

    act(() => {
      void useSessionStore.getState().selectSession('nav-a');
    });

    expect(screen.getByText('Terminal view')).toBeTruthy();
    expect(screen.getByTestId('route-probe').textContent).toContain('/terminals: nav-a');
    expect(useSessionStore.getState().currentSessionId).toBe('nav-a');
  });

  it('closes an open mobile Sidebar after navigating from the Terminal route to Chat', () => {
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
    const { container } = renderAt('/terminals');

    clickCard(container, 'nav-b');

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
