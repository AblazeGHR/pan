// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { MessageBubble } from './MessageBubble';

vi.mock('./MarkdownRenderer', () => ({ MarkdownRenderer: vi.fn(({ content }: { content: string }) => <span>{content}</span>) }));

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
  useSessionStore.setState({ currentSessionId: 'ses_bm' });
  useBookmarkStore.setState({ sessions: {} });
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
        authoritativeRevision: 1, authoritativeMarks: { [messageId]: value },
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
});
