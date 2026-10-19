// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { forwardRef, type ReactNode } from 'react';
import ChatView from './ChatView';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { useUIStore } from '@/stores/uiStore';
import { markDurableRow } from '@/stores/messageOrdering';
import { fetchSessionBookmarks, fetchSessionNavigation, addSessionBookmark, removeSessionBookmark } from '@/services/api';
import type { Message } from '@/types';

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionNavigation: vi.fn(),
  fetchSessionBookmarks: vi.fn(),
  addSessionBookmark: vi.fn(),
  removeSessionBookmark: vi.fn(),
}));

vi.mock('@/components/layout/ChatLayout', () => ({
  ChatLayout: ({ children, topBarRightAction }: { children: ReactNode; topBarRightAction?: ReactNode }) => (
    <div>
      <div data-testid="topbar-actions">{topBarRightAction}</div>
      {children}
    </div>
  ),
}));
vi.mock('@/components/chat/ChatMessages', () => ({
  ChatMessages: forwardRef((_props: unknown, _ref) => (
    <div data-testid="chat-messages">
      <div data-testid="chat-scroll-container" className="overflow-auto" />
    </div>
  )),
}));
vi.mock('@/components/chat/InputRow', () => ({ InputRow: () => <div data-testid="input-row" /> }));
vi.mock('@/components/chat/NativeTerminal', () => ({ NativeTerminal: () => <div data-testid="native-terminal" /> }));
vi.mock('@/components/chat/NativeQueue', () => ({ NativeQueue: () => <div data-testid="native-queue" /> }));
vi.mock('@/components/chat/ApprovalBanner', () => ({ ApprovalBanner: () => null }));
vi.mock('@/components/chat/UserInputBanner', () => ({ UserInputBanner: () => null }));
vi.mock('@/components/chat/ElicitationBanner', () => ({ ElicitationBanner: () => null }));
vi.mock('@/components/chat/TerminalInteractionBanner', () => ({ TerminalInteractionBanner: () => null }));
vi.mock('@/hooks/useMediaQuery', () => ({ useMediaQuery: () => ({ isMobile: false }) }));

const mockedNavigation = vi.mocked(fetchSessionNavigation);
const mockedBookmarks = vi.mocked(fetchSessionBookmarks);
const mockedAdd = vi.mocked(addSessionBookmark);
const mockedRemove = vi.mocked(removeSessionBookmark);

const SESSION_ID = 'rail-live';
const USER_ID = 'pan:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01';
const ASSISTANT_ID = 'pan:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa02';

function entry(messageId: string) {
  return { messageId, createdAt: '2026-10-16T00:00:00' };
}

function seedRows(projectedBookmark = false) {
  const user: Message = { role: 'user', content: 'a plain question', messageId: USER_ID };
  const assistant: Message = {
    role: 'assistant', content: 'a plain answer', messageId: ASSISTANT_ID,
    ...(projectedBookmark ? { bookmarked: true } : {}),
  };
  markDurableRow(user, 0);
  markDurableRow(assistant, 1);
  return [user, assistant];
}

function railMarkers(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>('.message-navigation-marker')];
}

beforeEach(() => {
  mockedNavigation.mockReset();
  mockedBookmarks.mockReset();
  mockedAdd.mockReset();
  mockedRemove.mockReset();
  mockedNavigation.mockResolvedValue({
    history: seedRows(), total: 2, start: 0, hasMore: false,
  } as Awaited<ReturnType<typeof fetchSessionNavigation>>);
  mockedBookmarks.mockResolvedValue({
    sessionId: SESSION_ID, revision: 0, updatedAt: null, bookmarks: {}, dangling: [],
  });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, showMessageNavigationRail: true });
  useSessionStore.setState({
    currentSessionId: SESSION_ID,
    currentMessages: seedRows(),
    historyLoadEnd: 2,
    hasMoreMessages: false,
    sessions: [{ id: SESSION_ID, historyTotal: 2 } as never],
  });
  useBookmarkStore.setState({ sessions: {} });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openRail(container: HTMLElement) {
  const dock = container.querySelector('[data-testid="message-navigation-dock"]')!;
  fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
  await waitFor(() => expect(railMarkers(container).length).toBeGreaterThan(0));
}

describe('ChatView: live bookmark overlay on the navigation rail', () => {
  it('a bookmark added in the current Session appears on the rail without any Session switch', async () => {
    const view = render(<ChatView />);
    await openRail(view.container);
    // Only the user row is navigable before the bookmark.
    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);

    mockedAdd.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: true, changed: true,
      revision: 1, updatedAt: '2026-10-16T00:00:00', bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) },
    });
    await act(async () => { await useBookmarkStore.getState().addBookmark(SESSION_ID, ASSISTANT_ID); });

    // No Session switch, no history re-read: the rail itself must re-derive.
    expect(mockedNavigation.mock.calls.length).toBe(1);
    const kinds = railMarkers(view.container).map((marker) => marker.dataset.kind);
    expect(kinds).toContain('bookmark');
    const bookmarkMarker = railMarkers(view.container).find((marker) => marker.dataset.kind === 'bookmark')!;
    expect(bookmarkMarker.getAttribute('data-message-id')).toBe(ASSISTANT_ID);
  });

  it('a bookmark removed in the current Session disappears from the rail immediately', async () => {
    mockedBookmarks.mockResolvedValue({
      sessionId: SESSION_ID, revision: 1, updatedAt: null,
      bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) }, dangling: [],
    });
    const view = render(<ChatView />);
    await openRail(view.container);
    await waitFor(() => expect(
      railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true));

    mockedRemove.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    await act(async () => { await useBookmarkStore.getState().removeBookmark(SESSION_ID, ASSISTANT_ID); });

    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);
    expect(mockedNavigation.mock.calls.length).toBe(1);
  });

  it('a removal overrides a stale projected flag on a loaded row', async () => {
    // Production regression: the row was loaded with the backend's
    // `bookmarked: true` projection.  After the delete commits, the store's
    // authoritative absence must win over that projection in the same mount.
    mockedBookmarks.mockResolvedValue({
      sessionId: SESSION_ID, revision: 1, updatedAt: null,
      bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) }, dangling: [],
    });
    useSessionStore.setState({
      currentSessionId: SESSION_ID,
      currentMessages: seedRows(true),
      historyLoadEnd: 2,
      hasMoreMessages: false,
      sessions: [{ id: SESSION_ID, historyTotal: 2 } as never],
    });
    const view = render(<ChatView />);
    await openRail(view.container);
    await waitFor(() => expect(
      railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true));

    mockedRemove.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    await act(async () => { await useBookmarkStore.getState().removeBookmark(SESSION_ID, ASSISTANT_ID); });

    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);
    expect(mockedNavigation.mock.calls.length).toBe(1);
  });

  it('a removal outside the loaded window updates the indexed rail rows', async () => {
    // The assistant row exists only in the navigation index, not in the
    // current message window.  A bookmark overlay change must still re-derive
    // every indexed target — not just the rows currently loaded in the chat.
    mockedBookmarks.mockResolvedValue({
      sessionId: SESSION_ID, revision: 1, updatedAt: null,
      bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) }, dangling: [],
    });
    const userOnly = seedRows(false).slice(0, 1);
    useSessionStore.setState({
      currentSessionId: SESSION_ID,
      currentMessages: userOnly,
      historyLoadEnd: 1,
      hasMoreMessages: false,
      sessions: [{ id: SESSION_ID, historyTotal: 2 } as never],
    });
    const view = render(<ChatView />);
    await openRail(view.container);
    await waitFor(() => expect(
      railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true));

    mockedRemove.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    await act(async () => { await useBookmarkStore.getState().removeBookmark(SESSION_ID, ASSISTANT_ID); });

    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);
  });

  it('a stale navigation page that lands after the removal does not resurrect the tag', async () => {
    // A page fetch issued before the delete still carries the old projection.
    // The overlay must classify it from the current marks at ingestion.
    mockedBookmarks.mockResolvedValue({
      sessionId: SESSION_ID, revision: 1, updatedAt: null,
      bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) }, dangling: [],
    });
    useBookmarkStore.setState({ sessions: {} });
    let release!: (value: Awaited<ReturnType<typeof fetchSessionNavigation>>) => void;
    mockedNavigation.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const view = render(<ChatView />);
    const dock = view.container.querySelector('[data-testid="message-navigation-dock"]')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(mockedNavigation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(useBookmarkStore.getState().sessions[SESSION_ID]?.loaded).toBe(true));

    mockedRemove.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    await act(async () => { await useBookmarkStore.getState().removeBookmark(SESSION_ID, ASSISTANT_ID); });

    await act(async () => {
      // Old list/navigation payload: still projects the deleted bookmark.
      release({ history: seedRows(true), total: 2, start: 0, hasMore: false });
      await Promise.resolve(); await Promise.resolve();
    });
    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);
  });

  it('a failed remove restores the rail marker and reports the error', async () => {
    const showToast = vi.fn();
    const originalToast = useUIStore.getState().showToast;
    useUIStore.setState({ showToast });
    try {
      mockedBookmarks.mockResolvedValue({
        sessionId: SESSION_ID, revision: 1, updatedAt: null,
        bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) }, dangling: [],
      });
      useSessionStore.setState({
        currentSessionId: SESSION_ID,
        currentMessages: seedRows(true),
        historyLoadEnd: 2,
        hasMoreMessages: false,
        sessions: [{ id: SESSION_ID, historyTotal: 2 } as never],
      });
      const view = render(<ChatView />);
      await openRail(view.container);
      await waitFor(() => expect(
        railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true));

      mockedRemove.mockRejectedValue(new Error('书签删除失败'));
      await act(async () => { await useBookmarkStore.getState().removeBookmark(SESSION_ID, ASSISTANT_ID); });

      await waitFor(() => expect(showToast).toHaveBeenCalledWith('书签删除失败', 'error'));
      expect(railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true);
    } finally {
      useUIStore.setState({ showToast: originalToast });
    }
  });

  it('a failed save converges the rail back without a Session switch', async () => {
    mockedAdd.mockRejectedValue(new Error('书签存储不可读，请检查数据目录'));
    const view = render(<ChatView />);
    await openRail(view.container);
    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);

    await act(async () => { await useBookmarkStore.getState().addBookmark(SESSION_ID, ASSISTANT_ID); });

    // Optimistic marker may appear at click time; the failure must converge it
    // away immediately — never leave a phantom tag until a Session switch.
    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toEqual(['user']);
    expect(mockedNavigation.mock.calls.length).toBe(1);
  });

  it('a navigation page that lands after the add keeps the overlay tag', async () => {
    // Production race: the page fetch was issued before the bookmark existed,
    // so its rows carry no bookmark projection; it must not delete the entry
    // the overlay already published when it finally lands.
    let release!: (value: Awaited<ReturnType<typeof fetchSessionNavigation>>) => void;
    mockedNavigation.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const view = render(<ChatView />);
    const dock = view.container.querySelector('[data-testid="message-navigation-dock"]')!;
    fireEvent.pointerEnter(dock, { pointerType: 'mouse' });
    expect(mockedNavigation).toHaveBeenCalledTimes(1);

    mockedAdd.mockResolvedValue({
      sessionId: SESSION_ID, messageId: ASSISTANT_ID, bookmarked: true, changed: true,
      revision: 1, updatedAt: '2026-10-16T00:00:00', bookmarks: { [ASSISTANT_ID]: entry(ASSISTANT_ID) },
    });
    await act(async () => { await useBookmarkStore.getState().addBookmark(SESSION_ID, ASSISTANT_ID); });
    expect(railMarkers(view.container).some((marker) => marker.dataset.kind === 'bookmark')).toBe(true);

    await act(async () => {
      release({ history: seedRows(), total: 2, start: 0, hasMore: false });
      await Promise.resolve(); await Promise.resolve();
    });
    expect(railMarkers(view.container).map((marker) => marker.dataset.kind)).toContain('bookmark');
  });
});
