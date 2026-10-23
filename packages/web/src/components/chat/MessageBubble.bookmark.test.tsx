// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { useUIStore } from '@/stores/uiStore';
import { MessageBubble } from './MessageBubble';
const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }));
vi.mock('@/utils/clipboard', () => clipboard);

vi.mock('./MarkdownRenderer', () => ({ MarkdownRenderer: vi.fn(({ content }: { content: string }) => <span>{content}</span>) }));

const api = vi.hoisted(() => ({
  fetchSessionBookmarks: vi.fn(),
  addSessionBookmark: vi.fn(),
  removeSessionBookmark: vi.fn(),
  fetchSessionMessageText: vi.fn(),
}));
vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  ...api,
}));

const BOOKMARK_ID = `pan:${'b'.repeat(30)}c1`;
const PLAIN_ID = `pan:${'b'.repeat(30)}c2`;

const bookmarkableRow: Message = {
  role: 'assistant',
  content: 'a converged answer',
  messageId: BOOKMARK_ID,
  ts: '2026-10-06T12:00:00',
};

const originalAdd = useBookmarkStore.getState().addBookmark;
const originalRemove = useBookmarkStore.getState().removeBookmark;

beforeEach(() => {
  clipboard.copyText.mockReset().mockResolvedValue(undefined);
  api.fetchSessionBookmarks.mockReset();
  api.addSessionBookmark.mockReset();
  api.removeSessionBookmark.mockReset();
  api.fetchSessionMessageText.mockReset().mockResolvedValue(bookmarkableRow.content);
  useSessionStore.setState({ currentSessionId: 'ses_bm' });
  useBookmarkStore.setState({ sessions: {}, addBookmark: originalAdd, removeBookmark: originalRemove });
});

it('copies only the clicked full raw Markdown body and keeps bookmark actions', async () => {
  const body = '  # 标题\n\n```ts\nconst value = "<raw>";\n```\n' + '正文'.repeat(1500) + '\n尾部  ';
  api.fetchSessionMessageText.mockResolvedValueOnce(body);
  const view = render(<MessageBubble message={{ ...bookmarkableRow, content: 'display projection' }} />);
  openMenu(view.container);
  expect(screen.getByRole('menuitem', { name: 'Add bookmark' })).toBeTruthy();
  fireEvent.click(screen.getByRole('menuitem', { name: '复制全文' }));
  await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith(body));
  expect(api.fetchSessionMessageText).toHaveBeenCalledWith('ses_bm', BOOKMARK_ID);
  expect(document.querySelector('[data-message-block-menu]')).toBeNull();
});

it('disables copy for an empty body and reports actual clipboard failure', async () => {
  const view = render(<MessageBubble message={{ ...bookmarkableRow, content: '  \n' }} />);
  openMenu(view.container);
  expect(screen.getByRole('menuitem', { name: '复制全文' }).hasAttribute('disabled')).toBe(true);
  cleanup();
  clipboard.copyText.mockRejectedValueOnce(new Error('denied'));
  const toast = vi.spyOn(useUIStore.getState(), 'showToast');
  const next = render(<MessageBubble message={bookmarkableRow} />);
  openMenu(next.container);
  fireEvent.click(screen.getByRole('menuitem', { name: '复制全文' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('复制失败，请重试', 'error'));
  toast.mockRestore();
});

it('keeps the clicked identity when switching Session during the canonical read, and never copies on read failure', async () => {
  let resolve!: (body: string) => void;
  api.fetchSessionMessageText.mockImplementationOnce(() => new Promise<string>(done => { resolve = done; }));
  const view = render(<MessageBubble message={bookmarkableRow} />);
  openMenu(view.container);
  fireEvent.click(screen.getByRole('menuitem', { name: '复制全文' }));
  act(() => useSessionStore.setState({ currentSessionId: 'other-session' }));
  resolve('clicked original');
  await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith('clicked original'));
  expect(api.fetchSessionMessageText).toHaveBeenCalledWith('ses_bm', BOOKMARK_ID);
  clipboard.copyText.mockClear();
  api.fetchSessionMessageText.mockRejectedValueOnce(new Error('message missing'));
  openMenu(view.container);
  fireEvent.click(screen.getByRole('menuitem', { name: '复制全文' }));
  await waitFor(() => expect(document.querySelector('[data-message-block-menu]')).toBeNull());
  await act(async () => { await Promise.resolve(); });
  expect(clipboard.copyText).not.toHaveBeenCalled();
});

afterEach(() => {
  cleanup();
  useBookmarkStore.setState({ sessions: {}, addBookmark: originalAdd, removeBookmark: originalRemove });
});

function openMenu(container: HTMLElement): void {
  fireEvent.click(container.querySelector<HTMLButtonElement>('[data-message-bookmark-gutter]')!);
}

function markBookmarked(messageId: string, value = true) {
  act(() => {
    useBookmarkStore.setState({
      sessions: { ses_bm: {
        appliedThrough: 1, snapshotRevision: 1,
        authoritativeMarks: { [messageId]: value },
        authoritativeTouched: { [messageId]: 1 }, pendingEvents: [],
        marks: { [messageId]: value }, loaded: true, transactions: {}, error: null,
      } },
    });
  });
}

describe('message block bookmark entry', () => {
  it('offers the row gutter only for converged bookmarkable content', () => {
    // Missing id, streaming, tool/thinking/system roles, legacy ids: no entry.
    for (const message of [
      { role: 'assistant', content: 'runtime row' },
      { ...bookmarkableRow, streaming: true },
      { ...bookmarkableRow, role: 'tool' },
      { ...bookmarkableRow, role: 'thinking' },
      { ...bookmarkableRow, role: 'system' },
      { ...bookmarkableRow, messageId: 'legacy:ses_x:epoch:3' },
    ] as Message[]) {
      const view = render(<MessageBubble message={message} />);
      expect(view.container.querySelector('[data-message-bookmark-gutter]')).toBeNull();
      cleanup();
    }
    for (const role of ['user', 'assistant']) {
      const view = render(<MessageBubble message={{ ...bookmarkableRow, role }} />);
      expect(view.container.querySelector('[data-message-bookmark-gutter]')).not.toBeNull();
      cleanup();
    }
  });

  it('adds the bookmark through the store with this row identity', () => {
    const add = vi.fn(async () => true);
    useBookmarkStore.setState({ addBookmark: add });
    const view = render(<MessageBubble message={bookmarkableRow} />);
    openMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add bookmark' }));
    expect(add).toHaveBeenCalledWith('ses_bm', BOOKMARK_ID);
    // The menu closes after acting and no second tag is added to the row.
    expect(document.querySelector('[data-message-block-menu]')).toBeNull();
  });

  it('removes the bookmark when the row is already marked', () => {
    const remove = vi.fn(async () => true);
    useBookmarkStore.setState({ removeBookmark: remove });
    markBookmarked(BOOKMARK_ID);
    const view = render(<MessageBubble message={bookmarkableRow} />);
    openMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove bookmark' }));
    expect(remove).toHaveBeenCalledWith('ses_bm', BOOKMARK_ID);
  });

  it('shows the body tag for a projected bookmark and for the store overlay', () => {
    const projected = render(<MessageBubble message={{ ...bookmarkableRow, bookmarked: true }} />);
    expect(projected.container.querySelector('[data-message-bookmark-tag]')).not.toBeNull();
    expect(projected.container.querySelector('.message-row')?.getAttribute('data-bookmarked')).toBe('true');
    cleanup();

    // The overlay (optimistic mutation / dual-client broadcast) wins the same way.
    const lifted = render(<MessageBubble message={bookmarkableRow} />);
    expect(lifted.container.querySelector('[data-message-bookmark-tag]')).toBeNull();
    markBookmarked(PLAIN_ID === BOOKMARK_ID ? PLAIN_ID : BOOKMARK_ID);
    expect(lifted.container.querySelector('[data-message-bookmark-tag]')).not.toBeNull();
  });

  it('does not offer a bookmark for a binding-free runtime row but keeps the gutter for a report binding', () => {
    const view = render(<MessageBubble message={{ role: 'assistant', content: 'runtime', messageId: undefined }} />);
    expect(view.container.querySelector('[data-message-bookmark-gutter]')).toBeNull();
  });

  it('drops the tag after a confirmed remove even when the list projected the bookmark', async () => {
    // Production regression: the history/list GET projected `bookmarked: true`
    // onto the row.  A confirmed delete must override that stale projection
    // in the SAME mount — no Session switch, no remount, no refetch.
    markBookmarked(BOOKMARK_ID);
    api.removeSessionBookmark.mockResolvedValue({
      sessionId: 'ses_bm', messageId: BOOKMARK_ID, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    const view = render(<MessageBubble message={{ ...bookmarkableRow, bookmarked: true }} />);
    expect(view.container.querySelector('[data-message-bookmark-tag]')).not.toBeNull();

    openMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove bookmark' }));

    await waitFor(() => expect(view.container.querySelector('[data-message-bookmark-tag]')).toBeNull());
    expect(view.container.querySelector('.message-row')?.getAttribute('data-bookmarked')).toBe('false');
    expect(api.removeSessionBookmark).toHaveBeenCalledWith('ses_bm', BOOKMARK_ID);
  });

  it('restores the projected tag and surfaces the error when a remove fails', async () => {
    const showToast = vi.fn();
    const originalToast = useUIStore.getState().showToast;
    useUIStore.setState({ showToast });
    try {
      markBookmarked(BOOKMARK_ID);
      api.removeSessionBookmark.mockRejectedValue(new Error('书签删除失败'));
      const view = render(<MessageBubble message={{ ...bookmarkableRow, bookmarked: true }} />);
      openMenu(view.container);
      fireEvent.click(screen.getByRole('menuitem', { name: 'Remove bookmark' }));

      await waitFor(() => expect(showToast).toHaveBeenCalledWith('书签删除失败', 'error'));
      expect(view.container.querySelector('[data-message-bookmark-tag]')).not.toBeNull();
      expect(view.container.querySelector('.message-row')?.getAttribute('data-bookmarked')).toBe('true');
    } finally {
      useUIStore.setState({ showToast: originalToast });
    }
  });
});
