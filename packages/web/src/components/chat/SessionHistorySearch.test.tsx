// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import type { RefObject } from 'react';
import { SessionHistorySearch } from './SessionHistorySearch';
import type { ChatMessagesHandle } from './ChatMessages';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';

const { fetchHistory } = vi.hoisted(() => ({ fetchHistory: vi.fn() }));
vi.mock('@/services/api', () => ({ fetchSessionHistory: fetchHistory }));

const MESSAGES: Message[] = [
  { role: 'user', content: 'Needle alpha', messageId: 'user-a' },
  { role: 'assistant', content: 'middle answer', messageId: 'assistant-mid' },
  { role: 'assistant', content: 'needle beta', messageId: 'assistant-b' },
];

function historyPage(messages: Message[], total = messages.length, start = 0, revision = 1) {
  return {
    history: messages,
    total,
    start,
    hasMore: start > 0,
    historyEpoch: 'epoch-1',
    historyRevision: revision,
  };
}

function makeChatRef(scrollToMessage = vi.fn(() => true)) {
  return {
    ref: { current: { scrollToMessage } } as RefObject<ChatMessagesHandle | null>,
    scrollToMessage,
  };
}

beforeEach(() => {
  fetchHistory.mockReset();
  fetchHistory.mockResolvedValue(historyPage(MESSAGES));
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: MESSAGES,
    sessions: [{ id: 's1', historyTotal: 3, historyEpoch: 'epoch-1', historyRevision: 1 } as never],
    ensureMessageLoaded: vi.fn(async (fromEnd: number, total: number) =>
      useSessionStore.getState().currentMessages[total - 1 - fromEnd] ?? null),
  });
  globalThis.requestAnimationFrame = ((callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(Date.now()), 0)) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) => window.clearTimeout(id)) as typeof cancelAnimationFrame;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SessionHistorySearch', () => {
  it('shows per-message N/M and cycles with Enter, Shift+Enter, and the buttons', async () => {
    const chat = makeChatRef();
    const onHighlightMessage = vi.fn();
    const { getByRole, getByTestId, queryByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={onHighlightMessage} />,
    );

    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    const input = getByTestId('session-history-search-input');
    fireEvent.change(input, { target: { value: 'needle' } });

    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 2'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenCalledWith(MESSAGES[0], 0));
    expect(getByTestId('session-history-search-snippet').textContent).toContain('Needle alpha');

    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('2 / 2'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(MESSAGES[2], 2));

    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 2'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(MESSAGES[0], 0));

    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('2 / 2'));
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 2'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(MESSAGES[0], 0));

    fireEvent.click(getByRole('button', { name: 'Previous result' }));
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('2 / 2'));
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(queryByTestId('session-history-search-input')).toBeNull();
    expect(onHighlightMessage).toHaveBeenLastCalledWith(null);
  });

  it('resets to the first result when the query changes and reports an empty search', async () => {
    const chat = makeChatRef();
    const { getByRole, getByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    const input = getByTestId('session-history-search-input');
    fireEvent.change(input, { target: { value: 'needle' } });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 2'));
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('2 / 2'));

    fireEvent.change(input, { target: { value: 'beta' } });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 1'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(MESSAGES[2], 2));

    fireEvent.change(input, { target: { value: 'absent' } });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('0 / 0'));
    expect(getByRole('status').textContent).toBe('No results in searchable history');
  });

  it('cancels a scan on Session switch and ignores the old response', async () => {
    const requests: Array<{
      sessionId: string;
      signal?: AbortSignal;
      resolve: (value: ReturnType<typeof historyPage>) => void;
    }> = [];
    fetchHistory.mockImplementation((sessionId: string, _before: number, _limit: number, signal?: AbortSignal) =>
      new Promise((resolve) => requests.push({ sessionId, signal, resolve })),
    );
    const chat = makeChatRef();
    const { getByRole, getByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    fireEvent.change(getByTestId('session-history-search-input'), { target: { value: 'needle' } });
    await waitFor(() => expect(requests).toHaveLength(1));

    act(() => useSessionStore.setState({
      currentSessionId: 's2',
      sessions: [
        { id: 's1', historyTotal: 3, historyEpoch: 'epoch-1', historyRevision: 1 },
        { id: 's2', historyTotal: 1, historyEpoch: 'epoch-2', historyRevision: 8 },
      ] as never,
      currentMessages: [{ role: 'user', content: 'needle new', messageId: 'new-id' }],
    }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[0]!.sessionId).toBe('s1');
    expect(requests[0]!.signal?.aborted).toBe(true);
    expect(requests[1]!.sessionId).toBe('s2');

    await act(async () => {
      requests[1]!.resolve({
        ...historyPage([{ role: 'user', content: 'needle new', messageId: 'new-id' }], 1, 0, 8),
        historyEpoch: 'epoch-2',
      });
    });
    await waitFor(() => expect(getByTestId('session-history-search-snippet').textContent).toContain('needle new'));
    await act(async () => {
      requests[0]!.resolve(historyPage([{ role: 'user', content: 'needle old', messageId: 'old-id' }], 1, 0));
    });
    expect(getByTestId('session-history-search-snippet').textContent).toContain('needle new');
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenCalledWith(
      expect.objectContaining({ messageId: 'new-id' }),
      0,
    ));
  });

  it('aborts an active scan when the search popup closes', async () => {
    let signal: AbortSignal | undefined;
    let resolvePage!: (value: ReturnType<typeof historyPage>) => void;
    fetchHistory.mockImplementation((_sessionId: string, _before: number, _limit: number, requestSignal?: AbortSignal) => {
      signal = requestSignal;
      return new Promise((resolve) => { resolvePage = resolve; });
    });
    const chat = makeChatRef();
    const { getByRole, getByTestId, queryByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    const input = getByTestId('session-history-search-input');
    fireEvent.change(input, { target: { value: 'needle' } });
    await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(1));
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(signal?.aborted).toBe(true);
    expect(queryByTestId('session-history-search-input')).toBeNull();
    await act(async () => { resolvePage(historyPage(MESSAGES)); });
    expect(queryByTestId('session-history-search-input')).toBeNull();
    expect(chat.scrollToMessage).not.toHaveBeenCalled();
  });

  it('re-finds by messageId when the indexed row changed, then loads and scrolls the current ID', async () => {
    fetchHistory
      .mockResolvedValueOnce(historyPage([
        { role: 'user', content: 'needle first', messageId: 'first' },
        { role: 'assistant', content: 'needle target', messageId: 'target' },
        { role: 'assistant', content: 'last', messageId: 'last' },
      ]))
      .mockResolvedValueOnce(historyPage([
        { role: 'user', content: 'new first', messageId: 'first' },
        { role: 'assistant', content: 'middle', messageId: 'middle' },
        { role: 'assistant', content: 'needle target', messageId: 'target' },
        { role: 'assistant', content: 'last', messageId: 'last' },
      ], 4));
    useSessionStore.setState({
      sessions: [{ id: 's1', historyTotal: 3, historyEpoch: 'epoch-1', historyRevision: 1 } as never],
      ensureMessageLoaded: vi.fn()
        .mockResolvedValueOnce({ role: 'assistant', content: 'stale at index', messageId: 'wrong' })
        .mockImplementationOnce(async () => {
          const target = { role: 'assistant', content: 'needle target', messageId: 'target' };
          useSessionStore.setState((state) => ({ currentMessages: [...state.currentMessages, target] }));
          return target;
        }),
    });
    const chat = makeChatRef();
    const { getByRole, getByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    fireEvent.change(getByTestId('session-history-search-input'), { target: { value: 'needle target' } });

    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenCalledWith(
      expect.objectContaining({ messageId: 'target' }),
      2,
    ));
    expect(useSessionStore.getState().ensureMessageLoaded).toHaveBeenNthCalledWith(1, 1, 3);
    expect(useSessionStore.getState().ensureMessageLoaded).toHaveBeenNthCalledWith(2, 1, 4);
  });

  it('replaces the hit order and N/M position after relocating the selected message ID', async () => {
    const lead: Message = { role: 'user', content: 'needle lead', messageId: 'lead' };
    const target: Message = { role: 'assistant', content: 'needle target', messageId: 'target' };
    const oldMatch: Message = { role: 'assistant', content: 'needle old match', messageId: 'old-match' };
    const newMatch: Message = { role: 'assistant', content: 'needle new match', messageId: 'new-match' };
    fetchHistory
      .mockResolvedValueOnce(historyPage([lead, target, oldMatch], 3))
      .mockResolvedValueOnce(historyPage([target, lead, newMatch], 3));
    const ensureMessageLoaded = vi.fn()
      .mockImplementationOnce(async () => {
        useSessionStore.setState({ currentMessages: [lead] });
        return lead;
      })
      .mockResolvedValueOnce({ role: 'assistant', content: 'wrong row', messageId: 'wrong' })
      .mockImplementationOnce(async () => {
        useSessionStore.setState((state) => ({ currentMessages: [...state.currentMessages, target] }));
        return target;
      });
    useSessionStore.setState({
      currentMessages: [],
      sessions: [{ id: 's1', historyTotal: 3, historyEpoch: 'epoch-1', historyRevision: 1 } as never],
      ensureMessageLoaded,
    });

    const chat = makeChatRef();
    const { getByRole, getByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    const input = getByTestId('session-history-search-input');
    fireEvent.change(input, { target: { value: 'needle' } });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 3'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(lead, 0));

    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('1 / 3'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(target, 0));

    fireEvent.click(getByRole('button', { name: 'Next result' }));
    await waitFor(() => expect(getByTestId('session-history-search-count').textContent).toBe('2 / 3'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenLastCalledWith(lead, 1));
    expect(ensureMessageLoaded).toHaveBeenCalledTimes(3);
  });

  it('shows an expired result when re-location cannot find the persistent message ID', async () => {
    fetchHistory
      .mockResolvedValueOnce(historyPage([{ role: 'user', content: 'needle target', messageId: 'target' }], 1))
      .mockResolvedValueOnce(historyPage([{ role: 'user', content: 'different body', messageId: 'other' }], 1, 0, 2));
    useSessionStore.setState({
      sessions: [{ id: 's1', historyTotal: 1, historyEpoch: 'epoch-1', historyRevision: 1 } as never],
      ensureMessageLoaded: vi.fn().mockResolvedValue({ role: 'user', content: 'stale target', messageId: 'other' }),
    });
    const chat = makeChatRef();
    const { getByRole, getByTestId } = render(
      <SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={vi.fn()} />,
    );
    fireEvent.click(getByRole('button', { name: 'Search Session history' }));
    fireEvent.change(getByTestId('session-history-search-input'), { target: { value: 'needle' } });

    await waitFor(() => expect(getByRole('status').textContent).toBe('Results expired. Search again.'));
    expect(chat.scrollToMessage).not.toHaveBeenCalled();
  });
});
