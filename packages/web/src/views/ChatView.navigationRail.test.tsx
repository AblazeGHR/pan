// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { forwardRef, type ReactNode } from 'react';
import ChatView from './ChatView';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchHistorySearch, fetchSessionHistory } from '@/services/api';
import type { Message } from '@/types';

const viewport = vi.hoisted(() => ({ isMobile: false }));
const mockedGlobalHistorySearch = vi.hoisted(() => vi.fn());

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(),
  fetchHistorySearch: mockedGlobalHistorySearch,
}));

// Keep the topbar action visible to the tests while leaving unrelated layout
// behavior out of scope.
vi.mock('@/components/layout/ChatLayout', () => ({
  ChatLayout: ({ children, topBarRightAction }: { children: ReactNode; topBarRightAction?: ReactNode }) => (
    <div>
      <div data-testid="topbar-actions">{topBarRightAction}</div>
      {children}
    </div>
  ),
}));
vi.mock('@/components/chat/ChatMessages', () => ({
  ChatMessages: forwardRef(({ hideScrollToBottom = false }: { hideScrollToBottom?: boolean }, _ref) => (
    <div data-testid="chat-messages" data-hide-scroll-to-bottom={hideScrollToBottom}>
      <div data-testid="chat-scroll-container" className="overflow-auto" />
    </div>
  )),
}));
vi.mock('@/components/chat/InputRow', () => ({ InputRow: () => <div data-testid="input-row" /> }));
vi.mock('@/components/chat/ApprovalBanner', () => ({ ApprovalBanner: () => null }));
vi.mock('@/components/chat/UserInputBanner', () => ({ UserInputBanner: () => null }));
vi.mock('@/components/chat/ElicitationBanner', () => ({ ElicitationBanner: () => null }));
vi.mock('@/components/chat/TerminalInteractionBanner', () => ({
  TerminalInteractionBanner: () => null,
}));
vi.mock('@/hooks/useMediaQuery', () => ({
  useMediaQuery: () => ({ isMobile: viewport.isMobile }),
}));

const mockedHistory = vi.mocked(fetchSessionHistory);
const mockedGlobalSearch = vi.mocked(fetchHistorySearch);
const USER_MESSAGE: Message = { role: 'user', content: 'hello from the user' };
const chatViewSource = readFileSync(resolve(process.cwd(), 'src/views/ChatView.tsx'), 'utf8');
const chatStylesSource = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8').replace(/\r\n/g, '\n');

beforeEach(() => {
  viewport.isMobile = false;
  mockedHistory.mockReset();
  mockedGlobalSearch.mockReset();
  mockedGlobalSearch.mockResolvedValue({
    hits: [], versions: [], limit: 50, hasMore: false, nextCursor: null,
  });
  mockedHistory.mockResolvedValue({
    history: [USER_MESSAGE],
    total: 1,
    start: 0,
    hasMore: false,
  } as Awaited<ReturnType<typeof fetchSessionHistory>>);
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  useSessionStore.setState({
    currentSessionId: 'rail-view',
    currentMessages: [USER_MESSAGE],
    historyLoadEnd: 0,
    hasMoreMessages: false,
    sessions: [],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ChatView: message navigation rail switch', () => {
  it('mounts the shared sidebar for either feature and stacks search above navigation', async () => {
    const { container, findByTestId } = render(<ChatView />);
    expect(container.querySelector('[data-testid="chat-tools-sidebar"]')).toBeNull();
    act(() => useAppSettingsStore.setState({ showHistorySearch: true }));
    const search = await findByTestId('session-history-search');
    const global = await findByTestId('global-history-search');
    const sidebar = container.querySelector('[data-testid="chat-tools-sidebar"]')!;
    expect(sidebar.contains(search)).toBe(true);
    expect(sidebar.contains(global)).toBe(true);
    expect(sidebar.querySelector('.chat-tools-sidebar__navigation')).toBeNull();
    act(() => useAppSettingsStore.setState({ showMessageNavigationRail: true }));
    const dock = sidebar.querySelector('[data-testid="message-navigation-dock"]')!;
    const panel = sidebar.querySelector('#message-navigation-panel')!;
    expect(panel.contains(search)).toBe(true);
    expect(panel.contains(global)).toBe(true);
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(panel.hasAttribute('inert')).toBe(true);
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(panel.children[0]?.className).toBe('chat-tools-sidebar__search');
    expect(panel.children[1]?.className).toBe('chat-tools-sidebar__navigation');
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    expect(chatStylesSource).toMatch(/\.chat-tools-sidebar__search\s*\{[^}]*flex-direction: column;/);
    fireEvent.click(search.querySelector('button')!);
    expect(container.querySelector('.session-history-search__popup')?.parentElement?.classList.contains('chat-view-stage')).toBe(true);
    expect(sidebar.querySelector('.session-history-search__popup')).toBeNull();
    act(() => useAppSettingsStore.setState({ showHistorySearch: false }));
    expect(container.querySelector('[data-testid="chat-tools-sidebar"]')).not.toBeNull();
    expect(container.querySelector('.chat-tools-sidebar__search')).toBeNull();
    act(() => useAppSettingsStore.setState({ showMessageNavigationRail: false }));
    expect(container.querySelector('[data-testid="chat-tools-sidebar"]')).toBeNull();
  });
  it('folds both search buttons inside the only desktop panel, including search-only mode', async () => {
    useAppSettingsStore.setState({ showHistorySearch: true });
    const { container, findByTestId, getByRole } = render(<ChatView />);
    await findByTestId('global-history-search');
    const panel = container.querySelector('#message-navigation-panel')!;
    const dock = container.querySelector('[data-testid="message-navigation-dock"]')!;
    expect(container.querySelectorAll('.message-navigation-dock__handle')).toHaveLength(1);
    expect(panel.querySelectorAll('.chat-tools-sidebar__search button')).toHaveLength(2);
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(mockedHistory).not.toHaveBeenCalled();
    fireEvent.click(getByRole('button', { name: 'Open chat tools sidebar' }));
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    fireEvent.keyDown(dock, { key: 'Escape' });
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(panel.hasAttribute('inert')).toBe(true);
    expect(container.querySelector('.chat-tools-sidebar__navigation')).toBeNull();
    // Ctrl+F remains usable while the buttons are folded: the popup is portaled.
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true });
    expect(await findByTestId('session-history-search-input')).not.toBeNull();
  });

  it('uses one mobile toggle to fold search-only and combined content without resetting on a feature change', async () => {
    viewport.isMobile = true;
    useAppSettingsStore.setState({ showHistorySearch: true });
    const { container, findByTestId, getByRole } = render(<ChatView />);
    await findByTestId('global-history-search');
    const panel = container.querySelector('#message-navigation-panel')!;
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    const toggle = getByRole('button', { name: 'Open chat tools sidebar' });
    fireEvent.click(toggle);
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    act(() => useAppSettingsStore.setState({ showMessageNavigationRail: true }));
    expect(panel.children[0]?.className).toBe('chat-tools-sidebar__search');
    expect(panel.children[1]?.className).toBe('chat-tools-sidebar__navigation');
    expect(panel.getAttribute('aria-hidden')).toBe('false');
    fireEvent.click(toggle);
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    act(() => useAppSettingsStore.setState({ showHistorySearch: false }));
    expect(getByRole('button', { name: 'Open message navigation rail' })).not.toBeNull();
    expect(panel.querySelector('.chat-tools-sidebar__search')).toBeNull();
    act(() => useAppSettingsStore.setState({ showMessageNavigationRail: false }));
    expect(container.querySelector('[data-testid="chat-tools-sidebar"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-message-navigation-toggle"]')).toBeNull();
  });
  it('does not mount the dock by default or request history', async () => {
    const { container } = render(<ChatView />);
    await act(async () => { await Promise.resolve(); });

    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(container.querySelector('[data-testid="message-navigation-dock"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-message-navigation-toggle"]')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="chat-messages"]')).not.toBeNull();
  });

  it('leaves Session search UI and its keyboard listener unmounted while disabled', () => {
    const addListener = vi.spyOn(window, 'addEventListener');
    const { container } = render(<ChatView />);
    const keydownListeners = addListener.mock.calls.filter(([type]) => type === 'keydown');
    const shortcut = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, cancelable: true });
    fireEvent(window, shortcut);

    expect(container.querySelector('[data-testid="session-history-search"]')).toBeNull();
    expect(container.querySelector('[data-testid="global-history-search"]')).toBeNull();
    expect(keydownListeners).toHaveLength(0);
    expect(shortcut.defaultPrevented).toBe(false);
    expect(mockedHistory).not.toHaveBeenCalled();
    expect(mockedGlobalSearch).not.toHaveBeenCalled();
    addListener.mockRestore();
  });

  it('loads search only through the enabled Suspense branch and constrains its popup to the chat stage', () => {
    expect(chatViewSource).toMatch(
      /lazy\(\s*\(\s*\)\s*=>\s*import\(\s*['"]@\/components\/chat\/SessionHistorySearch['"]\s*\)\s*\.then\(\s*\(\s*module\s*\)\s*=>\s*\(\s*\{\s*default\s*:\s*module\.SessionHistorySearch\s*,?\s*\}\s*\)\s*\)\s*,?\s*\)/s,
    );
    expect(chatViewSource).toMatch(/<Suspense\s+fallback=\{null\}\s*>/);
    expect(chatViewSource).not.toMatch(
      /^\s*import\s+(?!\s*\()[\s\S]*?\s+from\s*['"]@\/components\/chat\/SessionHistorySearch['"]/m,
    );
    expect(chatViewSource).toMatch(
      /lazy\(\s*\(\s*\)\s*=>\s*import\(\s*['"]@\/components\/chat\/GlobalHistorySearch['"]\s*\)/s,
    );
    expect(chatViewSource).not.toMatch(
      /^\s*import\s+(?!\s*\()[\s\S]*?\s+from\s*['"]@\/components\/chat\/GlobalHistorySearch['"]/m,
    );

    const searchStyles = chatStylesSource.slice(
      chatStylesSource.indexOf('.session-history-search {'),
      chatStylesSource.indexOf('.message-navigation-dock {'),
    );
    expect(chatStylesSource).toContain('.chat-view-stage {\n  position: relative;');
    expect(searchStyles).toContain('right: 92px;');
    expect(searchStyles).toContain('width: min(430px, calc(100% - 104px));');
    expect(searchStyles).toContain('justify-content: flex-end;');
    const globalSearchWrapperStyles = searchStyles.slice(
      searchStyles.indexOf('.global-history-search {'),
      searchStyles.indexOf('.global-history-search__toggle {'),
    );
    expect(globalSearchWrapperStyles).toContain('top: 8px;');
    expect(globalSearchWrapperStyles).toContain('bottom: 8px;');
    expect(globalSearchWrapperStyles).toContain('align-items: flex-start;');
    expect(globalSearchWrapperStyles).toContain('pointer-events: none;');
    expect(searchStyles).toContain('.global-history-search__toggle {');
    expect(searchStyles).toMatch(/\.global-history-search__toggle \{[\s\S]*?pointer-events: auto;/);
    expect(searchStyles).toContain('.session-history-search__popup {');
    expect(searchStyles).toContain('.global-history-search__popup {');
    expect(searchStyles).toContain('max-height: max(0px, calc(100% - 52px));');
    expect(searchStyles).toMatch(/\.global-history-search__popup \{[\s\S]*?top: 36px;/);
    expect(searchStyles).toMatch(/\.session-history-search__popup \{[\s\S]*?top: 36px;/);
    expect(searchStyles).toMatch(/\.global-history-search__popup \{[\s\S]*?pointer-events: auto;/);
    expect(searchStyles).toContain('width: 100%;');
    expect(searchStyles).toMatch(/\.global-history-search__results \{[\s\S]*?min-height: 0;[\s\S]*?overflow-y: auto;/);
    expect(searchStyles).not.toContain('100vw');
    expect(searchStyles).not.toContain('100vh');
    expect(chatViewSource).toMatch(/className="chat-view-stage flex flex-1 min-h-0 min-w-0"[\s\S]*?<\/div>\s*<InputRow \/>/);
    expect(chatStylesSource).toContain('right: 58px;\n    width: min(430px, calc(100% - 72px));');
    expect(chatStylesSource).toContain('right: 96px;\n    width: min(560px, calc(100% - 112px));');
  });

  it('mounts a mobile search control below the Session title and opens it with Ctrl+F', async () => {
    viewport.isMobile = true;
    useAppSettingsStore.setState({ showHistorySearch: true });
    const { container, findByTestId } = render(<ChatView />);

    const search = await findByTestId('session-history-search');
    expect(search.getAttribute('data-layout'))
      .toBe('mobile-below-session-title');
    expect(mockedHistory).not.toHaveBeenCalled();
    const shortcut = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, cancelable: true });
    fireEvent(window, shortcut);
    expect(shortcut.defaultPrevented).toBe(true);
    expect(await findByTestId('session-history-search-input')).not.toBeNull();

    act(() => useAppSettingsStore.setState({ showHistorySearch: false }));
    expect(container.querySelector('[data-testid="session-history-search"]')).toBeNull();
    const afterDisable = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, cancelable: true });
    fireEvent(window, afterDisable);
    expect(afterDisable.defaultPrevented).toBe(false);
  });

  it('mounts the enabled desktop control in the chat top-right contract', async () => {
    useAppSettingsStore.setState({ showHistorySearch: true });
    const { findByTestId } = render(<ChatView />);
    const search = await findByTestId('session-history-search');
    expect(search.getAttribute('data-layout'))
      .toBe('desktop-chat-top-right');
  });

  it('keeps global search distinct, mutually exclusive, and idle until a non-empty query', async () => {
    useAppSettingsStore.setState({ showHistorySearch: true });
    const { findByTestId, getByRole, queryByTestId } = render(<ChatView />);
    const global = await findByTestId('global-history-search');
    expect(global.getAttribute('data-layout')).toBe('desktop-chat-top-right');
    expect(mockedGlobalSearch).not.toHaveBeenCalled();

    fireEvent.click(getByRole('button', { name: 'Open chat tools sidebar' }));
    fireEvent.click(getByRole('button', { name: 'Search all Session history' }));
    const input = await findByTestId('global-history-search-input');
    expect(queryByTestId('session-history-search-input')).toBeNull();
    expect(mockedGlobalSearch).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: 'across sessions' } });
    await waitFor(() => expect(mockedGlobalSearch).toHaveBeenCalledWith('across sessions', 50, undefined, expect.any(AbortSignal),
      { roles: ['user', 'assistant', 'tool', 'thinking'], countMode: 'content' }));

    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    expect(queryByTestId('global-history-search-input')).toBeNull();
    expect(await findByTestId('session-history-search-input')).not.toBeNull();
  });

  it('shows a folded desktop handle when enabled and indexes only after hover expansion', async () => {
    useAppSettingsStore.setState({ showMessageNavigationRail: true });
    const { container } = render(<ChatView />);
    await act(async () => { await Promise.resolve(); });

    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const stage = container.querySelector('.chat-view-stage');
    const scrollContainer = container.querySelector('[data-testid="chat-scroll-container"]');
    expect(dock.closest('[data-testid="chat-tools-sidebar"]')?.parentElement).toBe(stage);
    expect(scrollContainer?.closest('.chat-view-stage')).toBe(stage);
    expect(scrollContainer?.classList.contains('overflow-auto')).toBe(true);
    expect(dock.getAttribute('data-placement')).toBe('viewport-end-before-scrollbar');
    expect(dock.getAttribute('data-expanded')).toBe('false');
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);
    expect(mockedHistory.mock.calls[0]![0]).toBe('rail-view');
  });

  it('unmounts the dock and releases its indexed content when the master switch turns off', async () => {
    const { container } = render(<ChatView />);
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-dock')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    await act(async () => {
      useAppSettingsStore.setState({ showMessageNavigationRail: true });
      await Promise.resolve();
    });
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppSettingsStore.setState({ showMessageNavigationRail: false });
      await Promise.resolve();
    });
    expect(container.querySelector('.message-navigation-dock')).toBeNull();
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });

  it('uses the topbar button on mobile and keeps one index across repeated toggles', async () => {
    viewport.isMobile = true;
    useAppSettingsStore.setState({ showMessageNavigationRail: true });
    const { container, getByRole } = render(<ChatView />);

    const toggle = getByRole('button', { name: 'Open message navigation rail' });
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    expect(dock.getAttribute('data-placement')).toBe('viewport-end');
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    await act(async () => { await Promise.resolve(); });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('false');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();

    fireEvent.click(toggle);
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('false');
    expect(mockedHistory).toHaveBeenCalledTimes(1);

    const marker = container.querySelector<HTMLButtonElement>('.message-navigation-marker')!;
    act(() => marker.focus());
    fireEvent.keyDown(marker, { key: 'Escape' });
    expect(container.querySelector('#message-navigation-panel')?.getAttribute('aria-hidden')).toBe('true');
    expect(document.activeElement).toBe(toggle);
  });

  it('hides the bottom button only while the enabled mobile navigation rail is expanded', async () => {
    viewport.isMobile = true;
    useAppSettingsStore.setState({ showMessageNavigationRail: true });
    const { container, getByRole, rerender } = render(<ChatView />);
    const chatMessages = container.querySelector('[data-testid="chat-messages"]')!;
    const toggle = getByRole('button', { name: 'Open message navigation rail' });

    expect(chatMessages.getAttribute('data-hide-scroll-to-bottom')).toBe('false');
    fireEvent.click(toggle);
    expect(chatMessages.getAttribute('data-hide-scroll-to-bottom')).toBe('true');
    fireEvent.click(getByRole('button', { name: 'Close message navigation rail' }));
    expect(chatMessages.getAttribute('data-hide-scroll-to-bottom')).toBe('false');

    await act(async () => {
      useAppSettingsStore.setState({ showMessageNavigationRail: false });
      await Promise.resolve();
    });
    expect(chatMessages.getAttribute('data-hide-scroll-to-bottom')).toBe('false');

    viewport.isMobile = false;
    useAppSettingsStore.setState({ showMessageNavigationRail: true });
    rerender(<ChatView />);
    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(chatMessages.getAttribute('data-hide-scroll-to-bottom')).toBe('false');
  });
});
