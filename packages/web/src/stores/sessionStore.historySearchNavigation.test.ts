import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Session } from '@/types';

const { fetchHistory } = vi.hoisted(() => ({ fetchHistory: vi.fn() }));

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionHistory: fetchHistory,
}));

function session(id: string, history: Session['history'] = []): Session {
  return {
    id,
    name: id,
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: '',
    history,
    historyTotal: 20,
    historyStart: 10,
    historyTruncated: true,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SessionStore search navigation cancellation', () => {
  it('forwards selection cancellation and ignores a page response after cancellation', async () => {
    let requestSignal: AbortSignal | undefined;
    let resolvePage!: (page: {
      history: [];
      total: number;
      start: number;
      hasMore: boolean;
      historyEpoch: string;
      historyRevision: number;
    }) => void;
    fetchHistory.mockImplementation((_sessionId: string, _before: number, _limit: number, signal?: AbortSignal) => {
      requestSignal = signal;
      return new Promise((resolve) => { resolvePage = resolve; });
    });
    const target = session('target');
    useSessionStore.setState({
      sessions: [session('other'), target],
      currentSessionId: 'other',
      currentMessages: [],
      initialLoading: false,
      historyLoading: false,
      _selectionSeq: {},
      _historyPageSeq: {},
      sessionTranscripts: {},
      liveStreamBuffers: {},
    });
    const controller = new AbortController();

    const selection = useSessionStore.getState().selectSession('target', controller.signal);
    expect(requestSignal).toBe(controller.signal);
    controller.abort();
    resolvePage({
      history: [], total: 20, start: 10, hasMore: true,
      historyEpoch: 'epoch-1', historyRevision: 2,
    });
    await selection;

    expect(useSessionStore.getState().currentSessionId).toBe('target');
    expect(useSessionStore.getState().currentMessages).toEqual([]);
    expect(useSessionStore.getState().initialLoading).toBe(false);
  });

  it('stops ensureMessageLoaded when selection changes during an older-page load', async () => {
    const loadOlderMessages = vi.fn(async () => {
      useSessionStore.setState({ currentSessionId: 'other', _selectionSeq: { target: 4, other: 1 } });
    });
    useSessionStore.setState({
      sessions: [session('target'), session('other')],
      currentSessionId: 'target',
      currentMessages: [],
      historyLoadEnd: 10,
      hasMoreMessages: true,
      historyLoading: false,
      _selectionSeq: { target: 4, other: 1 },
      loadOlderMessages,
    });

    const message = await useSessionStore.getState().ensureMessageLoaded(16, 20);

    expect(message).toBeNull();
    expect(loadOlderMessages).toHaveBeenCalledTimes(1);
    expect(loadOlderMessages).toHaveBeenCalledWith(50, undefined, true);
    expect(useSessionStore.getState().currentSessionId).toBe('other');
  });
});
