// @vitest-environment jsdom
import { forwardRef, useImperativeHandle, type ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ChatView from './ChatView';
import { RelatedContextButton } from './RelatedContextButton';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
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
  cleanup(); vi.mocked(fetchSessionNavigation).mockReset(); vi.restoreAllMocks(); vi.unstubAllGlobals(); scroll.mockClear();
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
  useRelatedMessageStore.getState().open({ sessionId: 'owner', messageId: 'msg_original', offset: 0, total: 1 });
  render(<ChatView />);
  await waitFor(() => expect(scroll).toHaveBeenCalledWith(message, 0));
  expect(fetchSessionNavigation).not.toHaveBeenCalled();
  expect(load).toHaveBeenCalledWith(0, 1, expect.any(AbortSignal));
  expect(useRelatedMessageStore.getState().target).toBeNull();
});

it('explains stale targets and does not scroll to the replacement row', async () => {
  vi.spyOn(useBookmarkStore.getState(), 'loadBookmarks').mockResolvedValue(undefined);
  vi.spyOn(useSessionStore.getState(), 'ensureMessageLoaded').mockResolvedValue({ role: 'user', content: 'Replacement', messageId: 'replacement' });
  useSessionStore.setState({ currentSessionId: 'owner', sessions: [] });
  useNativeControlStore.setState({ sessions: {}, terminalViews: {} });
  useAppSettingsStore.setState({ showHistorySearch: false, showMessageNavigationRail: false });
  useRelatedMessageStore.getState().open({ sessionId: 'owner', messageId: 'gone', offset: 0, total: 1 });
  render(<ChatView />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('替换'));
  expect(scroll).not.toHaveBeenCalled();
});


function Location() { return <div data-testid="location">{useLocation().pathname}</div>; }
function lockerToChat() {
  return render(<MemoryRouter initialEntries={['/locker']}>
    <Location />
    <Routes>
      <Route path="/locker" element={<RelatedContextButton ownerSessionId="owner" relatedMessageId="msg_original" />} />
      <Route path="/" element={<ChatView />} />
    </Routes>
  </MemoryRouter>);
}

it('resolves one time on click and hands the exact Session/offset/id to the real shared jump', async () => {
  const message = { role: 'user', content: 'Original', messageId: 'msg_original' };
  const history = Array.from({ length: 10 }, () => ({ role: 'assistant', content: 'Other' }));
  history[3] = message;
  const load = vi.fn().mockResolvedValue(message);
  const select = vi.spyOn(useSessionStore.getState(), 'selectSession').mockImplementation(async (id) => {
    useSessionStore.setState({ currentSessionId: id });
  });
  vi.spyOn(useBookmarkStore.getState(), 'loadBookmarks').mockResolvedValue(undefined);
  vi.spyOn(useSessionStore.getState(), 'ensureMessageLoaded').mockImplementation(load);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history, start: 0, total: 10, hasMore: false });
  useSessionStore.setState({ currentSessionId: 'other', sessions: [] });
  useNativeControlStore.setState({ sessions: {}, terminalViews: {} });
  useAppSettingsStore.setState({ showHistorySearch: false, showMessageNavigationRail: false });
  lockerToChat();
  expect(fetchSessionNavigation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '跳转到相关上下文' }));
  await waitFor(() => expect(scroll).toHaveBeenCalledWith(message, 3));
  expect(fetchSessionNavigation).toHaveBeenCalledTimes(1);
  expect(fetchSessionNavigation).toHaveBeenCalledWith('owner', 0, 500, expect.any(AbortSignal));
  expect(select).toHaveBeenCalledTimes(1);
  expect(select).toHaveBeenCalledWith('owner', expect.any(AbortSignal));
  expect(load).toHaveBeenCalledWith(6, 10, expect.any(AbortSignal));
  expect(screen.getByTestId('location').textContent).toBe('/');
});

it('keeps failed resolution on Locker with the reason and one fresh resolve per retry', async () => {
  const select = vi.spyOn(useSessionStore.getState(), 'selectSession');
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [], start: 0, total: 0, hasMore: false });
  lockerToChat();
  const button = screen.getByRole('button', { name: '跳转到相关上下文' });
  for (let attempt = 1; attempt <= 2; attempt++) {
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('已被删除、替换或索引未覆盖'));
    expect((button as HTMLButtonElement).disabled).toBe(false);
    expect(fetchSessionNavigation).toHaveBeenCalledTimes(attempt);
    expect(screen.getByTestId('location').textContent).toBe('/locker');
    expect(select).not.toHaveBeenCalled();
    expect(scroll).not.toHaveBeenCalled();
  }
});

it('disables a target found hidden by settings until settings change, without a background retry', async () => {
  useAppSettingsStore.setState({ showQQ: false });
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [{ role: 'user', content: '@@@@by qq : literal', messageId: 'msg_original' }], start: 0, total: 1, hasMore: false });
  lockerToChat();
  const button = screen.getByRole('button', { name: '跳转到相关上下文' });
  fireEvent.click(button);
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('显示设置隐藏'));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(fetchSessionNavigation).toHaveBeenCalledTimes(1);
  fireEvent.click(button);
  expect(fetchSessionNavigation).toHaveBeenCalledTimes(1);
  const { act } = await import('@testing-library/react');
  act(() => useAppSettingsStore.setState({ showQQ: true }));
  expect((button as HTMLButtonElement).disabled).toBe(false);
  expect(fetchSessionNavigation).toHaveBeenCalledTimes(1);
});
