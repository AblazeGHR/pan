// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import type { RefObject } from 'react';
import { SessionHistorySearch } from './SessionHistorySearch';
import type { ChatMessagesHandle } from './ChatMessages';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';

const { fetchHistory } = vi.hoisted(() => ({ fetchHistory: vi.fn() }));
vi.mock('@/services/api', async (original) => ({ ...await original<typeof import('@/services/api')>(), fetchHistorySearch: fetchHistory }));

const MESSAGES: Message[] = [
  { role: 'user', content: 'Needle alpha', messageId: 'user-a' },
  { role: 'assistant', content: 'middle answer', messageId: 'assistant-mid' },
  { role: 'assistant', content: 'needle beta', messageId: 'assistant-b' },
];

function historyPage(messages: Message[], total = messages.length, start = 0, revision = 1, query = 'needle') {
  let count = 0;
  const hits = messages.flatMap((message, index) => {
    const matchCount = message.content.toLowerCase().split(query.toLowerCase()).length-1;
    if (!matchCount) return [];
    const matchStart = count;
    count += matchCount;
    return [{ sessionId: 's1', messageId: message.messageId!, messageIndex: start+index, role: message.role,
      snippet: message.content, historyTotal: total, historyEpoch: 'epoch-1', historyRevision: revision,
      matchCount, matchStart }];
  });
  return {
    hits, totalMatches: count, totalMessages: hits.length, hasMore: false, nextCursor: null, limit: 100,
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
  fetchHistory.mockImplementation((query: string) => Promise.resolve(historyPage(MESSAGES, 3, 0, 1, query)));
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
  it('counts repeated occurrences, navigates within one block, and filters before searching', async () => {
    const body: Message = { role: 'tool', content: 'needle needle needle', messageId: 'tool-target' };
    fetchHistory.mockImplementation((_query: string, _limit: number, _cursor: string, _signal: AbortSignal, options: { roles: string[] }) =>
      Promise.resolve(historyPage(options.roles.includes('tool') ? [body] : [])));
    useSessionStore.setState({ currentMessages: [body] });
    const chat = makeChatRef();
    const highlight = vi.fn();
    const view = render(<SessionHistorySearch chatRef={chat.ref} isMobile={false} onHighlightMessage={highlight} />);
    fireEvent.click(view.getByRole('button', { name: 'Search Session history' }));
    fireEvent.change(view.getByTestId('session-history-search-input'), { target: { value: 'needle' } });
    await waitFor(() => expect(view.getByTestId('session-history-search-count').textContent).toBe('1 / 3'));
    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenCalledWith(body, 0));
    fireEvent.keyDown(view.getByTestId('session-history-search-input'), { key: 'Enter' });
    await waitFor(() => expect(view.getByTestId('session-history-search-count').textContent).toBe('2 / 3'));
    fireEvent.click(view.getByRole('checkbox', { name: 'Search tool messages' }));
    await waitFor(() => expect(view.getByRole('status').textContent).toBe('No results in searchable history'));
    expect(fetchHistory).toHaveBeenLastCalledWith('needle', 100, undefined, expect.any(AbortSignal),
      { sessionId: 's1', roles: ['user', 'assistant', 'thinking'], countMode: 'content' });
    expect(highlight).toHaveBeenLastCalledWith(null);
  });

  it('does not issue requests when all content types are unmounted from search', async () => {
    const view = render(<SessionHistorySearch chatRef={makeChatRef().ref} isMobile={false} onHighlightMessage={vi.fn()} />);
    fireEvent.click(view.getByRole('button', { name: 'Search Session history' }));
    for (const role of ['user', 'assistant', 'tool', 'thinking']) {
      fireEvent.click(view.getByRole('checkbox', { name: `Search ${role} messages` }));
    }
    fireEvent.change(view.getByTestId('session-history-search-input'), { target: { value: 'needle' } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 150)); });
    expect(fetchHistory).not.toHaveBeenCalled();
    expect(view.getByRole('status').textContent).toBe('Select content types to search.');
  });
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

    await waitFor(() => expect(getByRole('button', { name: 'Previous result' }).hasAttribute('disabled')).toBe(false));
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
    await waitFor(() => expect(getByRole('status').textContent).toBe('No results in searchable history'));
  });

  it('cancels a scan on Session switch and ignores the old response', async () => {
    const requests: Array<{
      sessionId: string;
      signal?: AbortSignal;
      resolve: (value: ReturnType<typeof historyPage>) => void;
    }> = [];
    fetchHistory.mockImplementation((_query: string, _limit: number, _cursor: string, signal: AbortSignal, options: {sessionId: string}) =>
      new Promise((resolve) => requests.push({ sessionId: options.sessionId, signal, resolve })),
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
    fetchHistory.mockImplementation((_query: string, _limit: number, _cursor: string, requestSignal?: AbortSignal) => {
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
      ], 3, 0, 1, 'needle target'))
      .mockResolvedValueOnce(historyPage([
        { role: 'user', content: 'new first', messageId: 'first' },
        { role: 'assistant', content: 'middle', messageId: 'middle' },
        { role: 'assistant', content: 'needle target', messageId: 'target' },
        { role: 'assistant', content: 'last', messageId: 'last' },
      ], 4, 0, 1, 'needle target'));
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
    expect(useSessionStore.getState().ensureMessageLoaded).toHaveBeenNthCalledWith(1, 1, 3, expect.any(AbortSignal));
    expect(useSessionStore.getState().ensureMessageLoaded).toHaveBeenNthCalledWith(2, 1, 4, expect.any(AbortSignal));
  });

  it('replaces the hit order and N/M position after relocating the selected message ID', async () => {
    const lead: Message = { role: 'user', content: 'needle lead', messageId: 'lead' };
    const target: Message = { role: 'assistant', content: 'needle target', messageId: 'target' };
    const oldMatch: Message = { role: 'assistant', content: 'needle old match', messageId: 'old-match' };
    const newMatch: Message = { role: 'assistant', content: 'needle new match', messageId: 'new-match' };
    fetchHistory
      .mockResolvedValueOnce(historyPage([lead, target, oldMatch], 3))
      .mockResolvedValueOnce({ ...historyPage([target, lead, newMatch], 3), hits: historyPage([target, lead, newMatch], 3).hits.slice(0, 1) })
      .mockResolvedValueOnce({ ...historyPage([target, lead, newMatch], 3), hits: historyPage([target, lead, newMatch], 3).hits.slice(1) });
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

    await waitFor(() => expect(getByRole('button', { name: 'Next result' }).hasAttribute('disabled')).toBe(false));
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

    await waitFor(() => expect(getByRole('status').textContent).toContain('Results expired. Search again.'));
    expect(chat.scrollToMessage).not.toHaveBeenCalled();
  });
});
