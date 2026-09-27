// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

const fetchSessionHistory = vi.hoisted(() => vi.fn());

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionHistory,
}));

function message(role: string, content: string, messageId?: string): Message {
  return { role, content, ...(messageId ? { messageId } : {}) };
}

function session(history: Message[], total = history.length): Session {
  return {
    id: 'A',
    name: 'A',
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: 'high',
    history,
    historyTotal: total,
    historyStart: 0,
  };
}

describe('stage 3 consistency fixtures', () => {
  beforeEach(() => {
    fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      historyLoading: false,
      initialLoading: false,
      historyWindowStarts: {},
      serverEpoch: null,
      liveStreamBuffers: {},
      terminalWatermarks: {},
      sessionTranscripts: {},
      _historyRefreshSeq: {},
      _historyPageSeq: {},
      _selectionSeq: {},
    });
  });

  it('replaces only the declared tail interval and preserves 100 older rows', async () => {
    const full = Array.from({ length: 150 }, (_, index) =>
      message(index % 2 === 0 ? 'user' : 'assistant', `m${index}`, `m-${index}`),
    );
    const loaded = session(full.slice(0, 100), 150);
    useSessionStore.setState({
      sessions: [loaded],
      currentSessionId: 'A',
      currentMessages: loaded.history,
      historyWindowStarts: { A: 0 },
    });
    fetchSessionHistory.mockResolvedValueOnce({
      history: full.slice(100),
      total: 150,
      hasMore: true,
      start: 100,
      historyEpoch: 'epoch-a',
      historyRevision: 151,
    });

    await act(async () => {
      await useSessionStore.getState().refreshCurrentSessionHistory();
    });

    const state = useSessionStore.getState();
    expect(state.currentMessages.map((item) => item.messageId)).toEqual(
      full.map((item) => item.messageId),
    );
    // A tail refresh merges rows by absolute offset: it must not move the
    // oldest loaded offset. Rows 0..99 are still loaded, so the window start
    // stays 0 (the previous expectation of 100 was the defect this repairs —
    // it made loadOlderMessages request offset 100 again forever).
    expect(state.historyWindowStarts.A).toBe(0);
    expect(state.historyLoadEnd).toBe(0);
    expect(state.sessions[0]?.historyTotal).toBe(150);
  });

  it('does not use an old assistant prefix to consume a different item identity', () => {
    const old = message('assistant', 'Hello old', 'old-item');
    const current = session([old]);
    useSessionStore.setState({
      sessions: [current],
      currentSessionId: 'A',
      currentMessages: [old],
    });

    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A',
        [message('assistant', 'Hello', 'new-item')],
        { serverEpoch: 'epoch-a', workerId: 'w1', generation: 0, taskSeq: 1 },
      );
    });

    expect(useSessionStore.getState().currentMessages.map((item) => item.content)).toEqual([
      'Hello old',
      'Hello',
    ]);
  });

  it('starts a new runtime transaction after a cold server epoch without accepting old terminal state', () => {
    const current = session([]);
    useSessionStore.setState({ sessions: [current], currentSessionId: 'A' });
    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A',
        [message('assistant', 'old live', 'old-item')],
        { serverEpoch: 'epoch-old', workerId: 'w-old', generation: 3, taskSeq: 9 },
      );
      useSessionStore.getState().acceptServerEpoch('epoch-new');
      useSessionStore.getState().applyLiveStream(
        'A',
        [message('assistant', 'new live', 'new-item')],
        { serverEpoch: 'epoch-new', workerId: 'w-new', generation: 0, taskSeq: 1 },
      );
    });

    const state = useSessionStore.getState();
    expect(state.serverEpoch).toBe('epoch-new');
    expect(state.getLiveStreamMessages('A').map((item) => item.content)).toEqual(['new live']);
    expect(state.currentMessages.map((item) => item.content)).toEqual(['new live']);
  });

  it('drops an id-less stale live overlay but keeps an equal canonical row', () => {
    const canonical = message('assistant', 'same canonical row');
    const staleLive = message('assistant', 'same canonical row');
    const current = session([canonical]);
    useSessionStore.setState({
      sessions: [current],
      currentSessionId: 'A',
      currentMessages: [canonical, staleLive],
      liveStreamBuffers: {
        A: {
          messages: [staleLive],
          revision: 1,
        },
      },
    });

    act(() => useSessionStore.getState().acceptServerEpoch('epoch-after-restart'));

    expect(useSessionStore.getState().currentMessages).toEqual([canonical]);
    expect(useSessionStore.getState().liveStreamBuffers.A).toBeUndefined();
  });

  it('reuses a background projection until its durable window changes', () => {
    const selected = session([message('user', 'selected')]);
    const background = { ...session([message('user', 'background')]), id: 'B' };
    useSessionStore.setState({
      sessions: [selected, background],
      currentSessionId: 'A',
      currentMessages: selected.history,
    });
    const scope = { workerId: 'worker-b', generation: 0, taskSeq: 1 };
    const live = (content: string) => [{
      role: 'assistant', content, nativeItemId: 'item-b',
    }];

    useSessionStore.getState().applyLiveStream('B', live('first'), scope);
    const first = useSessionStore.getState();
    expect(first.liveStreamBuffers.B?.projectionDisplay?.map((row) => row.content))
      .toEqual(['background', 'first']);
    expect(first.liveStreamBuffers.B?.projectionWindow)
      .toBe(first.sessionTranscripts.B?.window);

    useSessionStore.getState().applyLiveStream('B', live('first second'), scope);
    const second = useSessionStore.getState();
    expect(second.liveStreamBuffers.B?.projectionDisplay?.map((row) => row.content))
      .toEqual(['background', 'first second']);
    expect(second.currentMessages).toBe(selected.history);

    const transcript = second.sessionTranscripts.B!;
    const window = {
      ...transcript.window,
      rows: new Map(transcript.window.rows).set(1, message('user', 'new durable row')),
      end: 2,
      total: 2,
    };
    useSessionStore.setState({
      sessions: second.sessions.map((item) => item.id === 'B'
        ? { ...item, historyTotal: 2, history: [message('user', 'background'), message('user', 'new durable row')] }
        : item),
      sessionTranscripts: {
        ...second.sessionTranscripts,
        B: { ...transcript, window },
      },
    });
    useSessionStore.getState().applyLiveStream('B', live('first second third'), scope);
    const third = useSessionStore.getState();
    expect(third.liveStreamBuffers.B?.projectionWindow).toBe(window);
    expect(third.liveStreamBuffers.B?.projectionDisplay?.map((row) => row.content))
      .toContain('new durable row');
    expect(third.currentMessages).toBe(selected.history);
  });
});
