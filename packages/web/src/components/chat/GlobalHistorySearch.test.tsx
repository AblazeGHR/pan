// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import type { ComponentProps, RefObject } from 'react';
import { ApiRequestError } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { ApiHistorySearchHit, ApiHistorySearchResponse, Message } from '@/types';
import type { ChatMessagesHandle } from './ChatMessages';
import { GlobalHistorySearch } from './GlobalHistorySearch';

const { fetchSearch, fetchHistory, prepareSearch } = vi.hoisted(() => ({
  fetchSearch: vi.fn(),
  fetchHistory: vi.fn(),
  prepareSearch: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchHistorySearch: fetchSearch,
  fetchSessionHistory: fetchHistory,
  prepareHistorySearch: prepareSearch,
}));

const hit = (overrides: Partial<ApiHistorySearchHit> = {}): ApiHistorySearchHit => ({
  sessionId: 's2',
  messageId: 'target',
  messageIndex: 2,
  role: 'assistant',
  snippet: 'needle in this Session',
  historyEpoch: 'epoch-2',
  historyRevision: 3,
  historyTotal: 5,
  ...overrides,
});

function searchPage(
  hits: ApiHistorySearchHit[] = [],
  hasMore = false,
  nextCursor: string | null = null,
): ApiHistorySearchResponse {
  return { hits, versions: [], limit: 50, hasMore, nextCursor };
}

function historyPage(messages: Message[], start: number, total = 5) {
  return {
    history: messages,
    start,
    total,
    hasMore: start > 0,
    historyEpoch: 'epoch-2',
    historyRevision: 3,
  };
}

function makeChatRef(scrollToMessage = vi.fn(() => true)) {
  return {
    ref: { current: { scrollToMessage } } as RefObject<ChatMessagesHandle | null>,
    scrollToMessage,
  };
}

function renderSearch(props: Partial<ComponentProps<typeof GlobalHistorySearch>> = {}) {
  const chat = makeChatRef();
  const onHighlightMessage = vi.fn();
  const view = render(
    <GlobalHistorySearch
      chatRef={chat.ref}
      isMobile={false}
      open
      onOpenChange={vi.fn()}
      onHighlightMessage={onHighlightMessage}
      {...props}
    />,
  );
  return { ...view, chat, onHighlightMessage };
}

async function enterQuery(input: HTMLElement, query: string) {
  fireEvent.change(input, { target: { value: query } });
  await act(async () => new Promise((resolve) => window.setTimeout(resolve, 275)));
}

beforeEach(() => {
  fetchSearch.mockReset();
  fetchHistory.mockReset();
  prepareSearch.mockReset();
  prepareSearch.mockImplementation(async (_query, _roles, _limit, _signal, onProgress) => {
    const result = await fetchSearch.mock.results.at(-1)?.value;
    if (result) onProgress({ result, completed: 1, total: 1, done: true, failedSessions: [] });
  });
  fetchSearch.mockResolvedValue(searchPage([hit()]));
  fetchHistory.mockResolvedValue(historyPage([], 0));
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [
      { id: 's1', name: 'Source Session' },
      { id: 's2', name: 'Target Session', historyTotal: 5 },
    ] as never,
    selectSession: vi.fn(async (sessionId: string, signal?: AbortSignal) => {
      if (!signal?.aborted) useSessionStore.setState({ currentSessionId: sessionId, currentMessages: [] });
    }),
    ensureMessageLoaded: vi.fn(async () => null),
  });
  globalThis.requestAnimationFrame = ((callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(Date.now()), 0)) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) => window.clearTimeout(id)) as typeof cancelAnimationFrame;
});

it('shows provisional matches during preparation and enables paging only at completion', async () => {
  let report!: Parameters<typeof import('@/services/api').prepareHistorySearch>[4];
  let finish!: () => void;
  prepareSearch.mockImplementation((_q, _roles, _limit, _signal, progress) => {
    report = progress;
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  const view = renderSearch();
  await enterQuery(view.getByTestId('global-history-search-input'), 'needle');
  expect(view.getByText(/occurrences found so far/)).toBeTruthy();
  expect(view.queryByTestId('global-history-search-load-more')).toBeNull();
  act(() => report({ result: { ...searchPage([hit()], true, 'final-page'), totalMatches: 9 },
    completed: 1, total: 2, done: false, failedSessions: [] }));
  expect(view.getByText(/9 occurrences found so far/)).toBeTruthy();
  expect(view.queryByTestId('global-history-search-load-more')).toBeNull();
  await act(async () => {
    report({ result: { ...searchPage([hit()], true, 'final-page'), totalMatches: 12 },
      completed: 2, total: 2, done: true, failedSessions: [] });
    finish();
  });
  expect(view.getByText(/12 occurrences/)).toBeTruthy();
  expect(view.getByTestId('global-history-search-load-more')).toBeTruthy();
});

it('keeps results explicitly incomplete on preparation failure', async () => {
  prepareSearch.mockImplementation(async (_q, _roles, _limit, _signal, report) => {
    report({ result: searchPage([hit()], true, 'must-not-use'), completed: 2, total: 2,
      done: true, failedSessions: ['old'] });
  });
  const view = renderSearch();
  await enterQuery(view.getByTestId('global-history-search-input'), 'needle');
  expect(view.getByText(/Results incomplete/)).toBeTruthy();
  expect(view.queryByTestId('global-history-search-load-more')).toBeNull();
  expect(view.getByRole('button', { name: 'Retry search' })).toBeTruthy();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('GlobalHistorySearch', () => {
  it('does not search an empty query, pages on demand, and never claims a total hit count', async () => {
    fetchSearch
      .mockResolvedValueOnce(searchPage([hit()], true, 'cursor-next'))
      .mockResolvedValueOnce(searchPage([hit({ messageId: 'second', snippet: 'needle second' })]));
    const { getByTestId, getByRole, queryByText } = renderSearch();
    expect(fetchSearch).not.toHaveBeenCalled();

    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledTimes(1));
    expect(fetchSearch).toHaveBeenCalledWith('needle', 50, undefined, expect.any(AbortSignal),
      { roles: ['user', 'assistant', 'tool', 'thinking'], countMode: 'content' });
    expect(getByRole('button', { name: /Target Session, assistant, needle in this Session/ })).not.toBeNull();
    expect(getByTestId('global-history-search').textContent).toContain('1 messages shown');
    expect(queryByText(/total matches/i)).toBeNull();

    fireEvent.click(getByTestId('global-history-search-load-more'));
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledTimes(2));
    expect(fetchSearch).toHaveBeenLastCalledWith('needle', 50, 'cursor-next', expect.any(AbortSignal),
      { roles: ['user', 'assistant', 'tool', 'thinking'], countMode: 'content' });
    expect(getByRole('button', { name: /Target Session, assistant, needle second/ })).not.toBeNull();
    expect(getByTestId('global-history-search').textContent).toContain('2 messages shown');
  });

  it('aborts old pages when the query changes and ignores their late response', async () => {
    const pending: Array<{
      query: string;
      signal?: AbortSignal;
      resolve: (response: ApiHistorySearchResponse) => void;
    }> = [];
    fetchSearch.mockImplementation((query: string, _limit: number, _cursor: string | undefined, signal?: AbortSignal) =>
      new Promise((resolve) => pending.push({ query, signal, resolve })),
    );
    const { getByTestId, queryByText } = renderSearch();
    const input = getByTestId('global-history-search-input');

    await enterQuery(input, 'old');
    await waitFor(() => expect(pending).toHaveLength(1));
    fireEvent.change(input, { target: { value: 'new' } });
    expect(pending[0]!.signal?.aborted).toBe(true);
    await act(async () => new Promise((resolve) => window.setTimeout(resolve, 275)));
    await waitFor(() => expect(pending).toHaveLength(2));

    await act(async () => {
      pending[0]!.resolve(searchPage([hit({ messageId: 'old-hit', snippet: 'old result' })]));
      await Promise.resolve();
    });
    expect(queryByText('old result')).toBeNull();
    await act(async () => {
      pending[1]!.resolve(searchPage([hit({ messageId: 'new-hit', snippet: 'new result' })]));
      await Promise.resolve();
    });
    await waitFor(() => expect(getByTestId('global-history-search').textContent).toContain('new result'));
    expect(getByTestId('global-history-search').textContent).not.toContain('old result');
  });

  it.each([
    [409, 'Search results expired. Search again.'],
    [503, 'History search is temporarily unavailable. Try again.'],
  ])('shows a recoverable HTTP %i state', async (status, message) => {
    fetchSearch
      .mockRejectedValueOnce(new ApiRequestError(status, `HTTP ${status}`))
      .mockResolvedValueOnce(searchPage([hit()]));
    const { getByTestId, getByRole } = renderSearch();
    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(getByRole('status').textContent).toContain(message));
    fireEvent.click(getByRole('button', { name: status === 409 ? 'Search again' : 'Retry search' }));
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getByRole('button', { name: /Target Session, assistant/ })).not.toBeNull());
  });

  it('cancels active search requests when closed or unmounted', async () => {
    const signals: AbortSignal[] = [];
    fetchSearch.mockImplementation((_query: string, _limit: number, _cursor: string | undefined, signal?: AbortSignal) => {
      if (signal) signals.push(signal);
      return new Promise(() => {});
    });
    const view = renderSearch();
    await enterQuery(view.getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(signals).toHaveLength(1));
    view.rerender(
      <GlobalHistorySearch
        chatRef={view.chat.ref}
        isMobile={false}
        open={false}
        onOpenChange={vi.fn()}
        onHighlightMessage={view.onHighlightMessage}
      />,
    );
    expect(signals[0]!.aborted).toBe(true);

    view.rerender(
      <GlobalHistorySearch
        chatRef={view.chat.ref}
        isMobile={false}
        open
        onOpenChange={vi.fn()}
        onHighlightMessage={view.onHighlightMessage}
      />,
    );
    await enterQuery(view.getByTestId('global-history-search-input'), 'again');
    await waitFor(() => expect(signals).toHaveLength(2));
    view.unmount();
    expect(signals[1]!.aborted).toBe(true);
  });

  it('switches Session, loads an old page, relocates by ID, then scrolls and highlights the mounted target', async () => {
    const target: Message = { role: 'assistant', content: 'needle target', messageId: 'target' };
    fetchSearch.mockResolvedValueOnce(searchPage([hit()])).mockResolvedValueOnce(searchPage([hit({ messageIndex: 3 })]));
    fetchHistory
      .mockResolvedValueOnce(historyPage([
        { role: 'user', content: 'before', messageId: 'before' },
        target,
        { role: 'assistant', content: 'after', messageId: 'after' },
      ], 2))
      .mockResolvedValueOnce(historyPage([
        { role: 'user', content: 'start', messageId: 'start' },
        { role: 'assistant', content: 'middle', messageId: 'middle' },
      ], 0));
    const ensureMessageLoaded = vi.fn()
      .mockResolvedValueOnce({ role: 'assistant', content: 'offset changed', messageId: 'wrong' })
      .mockImplementationOnce(async () => {
        useSessionStore.setState({ currentMessages: [target] });
        return target;
      });
    useSessionStore.setState({
      ensureMessageLoaded,
      selectSession: vi.fn(async (sessionId: string, signal?: AbortSignal) => {
        if (!signal?.aborted) useSessionStore.setState({ currentSessionId: sessionId, currentMessages: [] });
      }),
    });
    const chat = makeChatRef();
    const onHighlightMessage = vi.fn();
    const { getByTestId, getByRole } = renderSearch({
      chatRef: chat.ref,
      onHighlightMessage,
    });
    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(getByRole('button', { name: /Target Session, assistant/ })).not.toBeNull());

    fireEvent.click(getByRole('button', { name: /Target Session, assistant/ }));

    await waitFor(() => expect(chat.scrollToMessage).toHaveBeenCalledWith(target, 3));
    expect(useSessionStore.getState().currentSessionId).toBe('s2');
    expect(useSessionStore.getState().selectSession).toHaveBeenCalledWith('s2', expect.any(AbortSignal));
    expect(ensureMessageLoaded).toHaveBeenNthCalledWith(1, 2, 5, expect.any(AbortSignal));
    expect(ensureMessageLoaded).toHaveBeenNthCalledWith(2, 1, 5, expect.any(AbortSignal));
    expect(fetchSearch).toHaveBeenLastCalledWith('needle', 1, undefined, expect.any(AbortSignal),
      { sessionId: 's2', roles: ['user', 'assistant', 'tool', 'thinking'], countMode: 'content', messageId: 'target' });
    expect(fetchHistory).not.toHaveBeenCalled();
    expect(onHighlightMessage).toHaveBeenLastCalledWith('s2', 'target', 'needle');
  });

  it('reports an expired target when the ID is missing from the fresh Session history', async () => {
    fetchSearch.mockResolvedValue(searchPage([hit()]));
    fetchHistory.mockResolvedValue(historyPage([
      { role: 'assistant', content: 'different content', messageId: 'different' },
    ], 0));
    useSessionStore.setState({
      currentSessionId: 's1',
      ensureMessageLoaded: vi.fn().mockResolvedValue({ role: 'user', content: 'stale', messageId: 'wrong' }),
    });
    const { getByTestId, getByRole, getByText } = renderSearch();
    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(getByRole('button', { name: /Target Session, assistant/ })).not.toBeNull());
    fireEvent.click(getByRole('button', { name: /Target Session, assistant/ }));
    await waitFor(() => expect(getByText('This result expired. Search again.')).not.toBeNull());
  });

  it('keeps the expired state when a result Session has been removed', async () => {
    const { getByTestId, getByText } = renderSearch();
    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(getByText('needle in this Session')).not.toBeNull());
    act(() => useSessionStore.setState({ sessions: [{ id: 's1', name: 'Source Session' }] as never }));
    fireEvent.click(getByText('needle in this Session').closest('button')!);
    await waitFor(() => expect(getByText('This result expired. Search again.')).not.toBeNull());
  });

  it('aborts navigation if the user switches Sessions while a target page is loading', async () => {
    let pageSignal: AbortSignal | undefined;
    let resolveTarget!: (message: Message | null) => void;
    fetchSearch.mockResolvedValue(searchPage([hit()]));
    const ensureMessageLoaded = vi.fn((_fromEnd: number, _total: number, signal?: AbortSignal) => {
      pageSignal = signal;
      return new Promise<Message | null>((resolve) => { resolveTarget = resolve; });
    });
    useSessionStore.setState({
      ensureMessageLoaded,
      selectSession: vi.fn(async (sessionId: string, signal?: AbortSignal) => {
        if (!signal?.aborted) useSessionStore.setState({ currentSessionId: sessionId, currentMessages: [] });
      }),
    });
    const chat = makeChatRef();
    const { getByTestId, getByRole } = renderSearch({ chatRef: chat.ref });
    await enterQuery(getByTestId('global-history-search-input'), 'needle');
    await waitFor(() => expect(getByRole('button', { name: /Target Session, assistant/ })).not.toBeNull());
    fireEvent.click(getByRole('button', { name: /Target Session, assistant/ }));
    await waitFor(() => expect(ensureMessageLoaded).toHaveBeenCalledTimes(1));

    act(() => useSessionStore.setState({ currentSessionId: 's1' }));
    expect(pageSignal?.aborted).toBe(true);
    await act(async () => { resolveTarget({ role: 'assistant', content: 'needle', messageId: 'target' }); });
    expect(chat.scrollToMessage).not.toHaveBeenCalled();
  });
});
