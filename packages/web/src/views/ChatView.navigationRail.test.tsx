// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { forwardRef, type ReactNode } from 'react';
import ChatView from './ChatView';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchSessionHistory } from '@/services/api';
import type { Message } from '@/types';

const viewport = vi.hoisted(() => ({ isMobile: false }));

vi.mock('@/services/api', () => ({
  fetchSessionHistory: vi.fn(),
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
  ChatMessages: forwardRef(() => (
    <div data-testid="chat-messages">
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
const USER_MESSAGE: Message = { role: 'user', content: 'hello from the user' };

beforeEach(() => {
  viewport.isMobile = false;
  mockedHistory.mockReset();
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
  it('does not mount the dock by default or request history', async () => {
    const { container } = render(<ChatView />);
    await act(async () => { await Promise.resolve(); });

    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(container.querySelector('[data-testid="message-navigation-dock"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-message-navigation-toggle"]')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="chat-messages"]')).not.toBeNull();
  });

  it('shows a folded desktop handle when enabled and indexes only after hover expansion', async () => {
    useAppSettingsStore.setState({ showMessageNavigationRail: true });
    const { container } = render(<ChatView />);
    await act(async () => { await Promise.resolve(); });

    const dock = container.querySelector<HTMLElement>('[data-testid="message-navigation-dock"]')!;
    const stage = container.querySelector('.chat-view-stage');
    const scrollContainer = container.querySelector('[data-testid="chat-scroll-container"]');
    expect(dock.parentElement).toBe(stage);
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
});
