// @vitest-environment jsdom
import { forwardRef, useImperativeHandle, type ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import ChatView from './ChatView';
import { useSessionStore } from '@/stores/sessionStore';
import { useRelatedMessageStore } from '@/stores/relatedMessageStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import { fetchSessionNavigation } from '@/services/api';

const scroll = vi.hoisted(() => vi.fn(() => true));
vi.mock('@/services/api', () => ({ fetchSessionNavigation: vi.fn() }));
vi.mock('@/components/layout/ChatLayout', () => ({ ChatLayout: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock('@/components/chat/ChatMessages', () => ({ ChatMessages: forwardRef((_, ref) => {
  useImperativeHandle(ref, () => ({ scrollToMessage: scroll }));
  return <div>History</div>;
}) }));
vi.mock('@/components/chat/MessageNavigationDock', () => ({ MessageNavigationDock: () => null }));
vi.mock('@/components/chat/InputRow', () => ({ InputRow: () => null }));
vi.mock('@/components/chat/ApprovalBanner', () => ({ ApprovalBanner: () => null }));
vi.mock('@/components/chat/UserInputBanner', () => ({ UserInputBanner: () => null }));
vi.mock('@/components/chat/ElicitationBanner', () => ({ ElicitationBanner: () => null }));
vi.mock('@/components/chat/TerminalInteractionBanner', () => ({ TerminalInteractionBanner: () => null }));
vi.mock('@/hooks/useMediaQuery', () => ({ useMediaQuery: () => ({ isMobile: false }) }));

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); scroll.mockClear();
  useRelatedMessageStore.setState({ target: null });
});

it('consumes the owner/id handoff with both tools off and calls the real shared jump', async () => {
  const message = { role: 'user', content: 'Original', messageId: 'msg_original' };
  const load = vi.fn().mockResolvedValue(message);
  vi.spyOn(useBookmarkStore.getState(), 'loadBookmarks').mockResolvedValue(undefined);
  vi.spyOn(useSessionStore.getState(), 'ensureMessageLoaded').mockImplementation(load);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [message], start: 0, total: 1, hasMore: false, historyEpoch: 'a' });
  useSessionStore.setState({ currentSessionId: 'owner', currentMessages: [], sessions: [] });
  useNativeControlStore.setState({ sessions: {}, terminalViews: {} });
  useAppSettingsStore.setState({ showHistorySearch: false, showMessageNavigationRail: false });
  useRelatedMessageStore.getState().open('owner', 'msg_original');
  render(<ChatView />);
  await waitFor(() => expect(scroll).toHaveBeenCalledWith(message, 0));
  expect(fetchSessionNavigation).toHaveBeenCalledWith('owner', 0, 500, expect.any(AbortSignal));
  expect(load).toHaveBeenCalledWith(0, 1, expect.any(AbortSignal));
  expect(useRelatedMessageStore.getState().target).toBeNull();
});

it('explains stale targets and does not scroll to the replacement row', async () => {
  vi.spyOn(useBookmarkStore.getState(), 'loadBookmarks').mockResolvedValue(undefined);
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [], start: 0, total: 0, hasMore: false });
  useSessionStore.setState({ currentSessionId: 'owner', sessions: [] });
  useNativeControlStore.setState({ sessions: {}, terminalViews: {} });
  useAppSettingsStore.setState({ showHistorySearch: false, showMessageNavigationRail: false });
  useRelatedMessageStore.getState().open('owner', 'gone');
  render(<ChatView />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('删除'));
  expect(scroll).not.toHaveBeenCalled();
});
