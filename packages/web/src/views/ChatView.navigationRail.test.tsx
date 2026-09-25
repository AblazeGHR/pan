// @vitest-environment jsdom
//
// `Show message navigation rail` (Appearance) must *unmount* the rail, not hide
// it: mounting the rail is what triggers its full-history index pass, so a CSS
// hide would keep paying that cost (one history request per 200 messages).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { forwardRef, type ReactNode } from 'react';
import ChatView from './ChatView';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { fetchSessionHistory } from '@/services/api';
import type { Message } from '@/types';

vi.mock('@/services/api', () => ({
  fetchSessionHistory: vi.fn(),
}));

// Every child except the rail is stubbed: this file is about whether the rail is
// mounted at all.
vi.mock('@/components/layout/ChatLayout', () => ({
  ChatLayout: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/chat/ChatMessages', () => ({
  ChatMessages: forwardRef(() => <div data-testid="chat-messages" />),
}));
vi.mock('@/components/chat/InputRow', () => ({ InputRow: () => <div data-testid="input-row" /> }));
vi.mock('@/components/chat/ApprovalBanner', () => ({ ApprovalBanner: () => null }));
vi.mock('@/components/chat/UserInputBanner', () => ({ UserInputBanner: () => null }));
vi.mock('@/components/chat/ElicitationBanner', () => ({ ElicitationBanner: () => null }));
vi.mock('@/components/chat/TerminalInteractionBanner', () => ({
  TerminalInteractionBanner: () => null,
}));

const mockedHistory = vi.mocked(fetchSessionHistory);
const USER_MESSAGE: Message = { role: 'user', content: 'hello from the user' };

beforeEach(() => {
  mockedHistory.mockReset();
  mockedHistory.mockResolvedValue({
    // Three pages worth of one message: the rail walks `before = start` until it
    // reaches the head of the history.
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
  it('does not mount the rail by default — and skips the index request entirely', async () => {
    const { container } = render(<ChatView />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();
    // The chat itself is untouched.
    expect(container.querySelector('[data-testid="chat-messages"]')).not.toBeNull();
  });

  it('mounts the rail (and its history index) once the switch is on', async () => {
    useAppSettingsStore.setState({ showMessageNavigationRail: true });

    const { container } = render(<ChatView />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);
    expect(mockedHistory.mock.calls[0]![0]).toBe('rail-view');
  });

  it('takes effect immediately when the switch is toggled while the view is open', async () => {
    const { container } = render(<ChatView />);
    await act(async () => {
      await Promise.resolve();
    });
    // Default (off): no rail and no index traffic.
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    expect(mockedHistory).not.toHaveBeenCalled();

    await act(async () => {
      useAppSettingsStore.setState({ showMessageNavigationRail: true });
      await Promise.resolve();
    });
    expect(container.querySelector('.message-navigation-rail')).not.toBeNull();
    expect(mockedHistory).toHaveBeenCalledTimes(1);

    await act(async () => {
      useAppSettingsStore.setState({ showMessageNavigationRail: false });
      await Promise.resolve();
    });
    expect(container.querySelector('.message-navigation-rail')).toBeNull();
    // No further index traffic after the unmount.
    expect(mockedHistory).toHaveBeenCalledTimes(1);
  });
});
