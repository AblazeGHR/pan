// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Session } from '@/types';

const api = vi.hoisted(() => ({ fetchSessions: vi.fn() }));

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessions: api.fetchSessions,
}));

function mk(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 0,
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

describe('session summary reconciliation', () => {
  beforeEach(() => {
    api.fetchSessions.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      _loadSeq: 0,
      _sessionWsTouchedSeq: {},
      _sessionLocalTouchedSeq: {},
      _deliveredQueueIds: {},
      _sessionEventPatches: {},
      sessionTranscripts: {},
      liveStreamBuffers: {},
      terminalWatermarks: {},
    });
  });

  it('does not replay an old WS summary over a newer HTTP projection', async () => {
    useSessionStore.setState({ sessions: [mk('s1')] });
    useSessionStore.getState().updateSession('s1', {
      summaryRevision: 10, lastMessage: 'MA send', lastDisplayPreview: 'MA send',
      legalOffline: false,
    }, true);
    api.fetchSessions.mockResolvedValueOnce([mk('s1', {
      summaryRevision: 11, lastMessage: 'answer', lastDisplayPreview: 'answer',
      legalOffline: true,
    })]);
    await useSessionStore.getState().loadSessions();
    expect(useSessionStore.getState().sessions[0]).toMatchObject({
      summaryRevision: 11, lastMessage: 'answer', lastDisplayPreview: 'answer',
      legalOffline: true,
    });
  });

  it('does not derive an authoritative card preview from a partial history window or runtime MA suffix', () => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 12, historyTotal: 3, historyEpoch: 'h', historyRevision: 3,
      lastMessage: 'answer', lastDisplayPreview: 'answer', lastAssistantPreview: 'answer',
    })] });
    useSessionStore.getState().appendDeliveredMessages('s1', [{
      role: 'user', source: 'agent', content: 'MA send', queueItemIds: ['q1'],
    }], undefined, { summaryRevision: 12, lastMessage: 'answer', lastDisplayPreview: 'answer' });
    useSessionStore.getState().applyHistoryPage('s1', {
      history: [{ role: 'user', source: 'agent', content: 'MA send', messageId: 'm1' }],
      start: 0, total: 3, hasMore: false, historyEpoch: 'h', historyRevision: 3,
    });
    expect(useSessionStore.getState().sessions[0]?.lastMessage).toBe('answer');
  });

  it('keeps a live preview across equal-revision summary reads, then accepts a newer durable summary', () => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 10, lastMessage: 'MA send', lastDisplayPreview: 'MA send',
    })] });
    useSessionStore.getState().updateSession('s1', { lastMessage: 'partial', lastDisplayPreview: 'partial' });
    useSessionStore.getState().updateSession('s1', {
      summaryRevision: 10, lastMessage: 'MA send', lastDisplayPreview: 'MA send',
    });
    expect(useSessionStore.getState().sessions[0]?.lastMessage).toBe('partial');
    useSessionStore.getState().updateSession('s1', {
      summaryRevision: 11, lastMessage: 'answer', lastDisplayPreview: 'answer',
    });
    expect(useSessionStore.getState().sessions[0]?.lastMessage).toBe('answer');
  });

  it('distinguishes metadata summary changes from history changes and loaded window revisions', () => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 10, historyRevision: 2, historyEpoch: 'h',
      lastMessage: 'MA send', lastDisplayPreview: 'MA send',
    })] });
    const state = () => useSessionStore.getState();
    state().updateSession('s1', { lastMessage: 'partial', lastDisplayPreview: 'partial' });
    state().updateSession('s1', {
      summaryRevision: 11, historyRevision: 2, lastMessage: 'MA send', lastDisplayPreview: 'MA send',
    });
    expect(state().sessions[0]?.lastMessage).toBe('partial');
    state().applyHistoryPage('s1', {
      history: [{ role: 'assistant', content: 'answer' }], start: 2, total: 3,
      hasMore: true, historyEpoch: 'h', historyRevision: 3,
    });
    expect(state().sessions[0]?.summaryHistoryRevision).toBe(2);
    state().updateSession('s1', {
      summaryRevision: 12, historyRevision: 3, lastMessage: 'answer', lastDisplayPreview: 'answer',
    });
    expect(state().sessions[0]?.lastMessage).toBe('answer');
    state().applySessionSnapshots([mk('s1', {
      summaryRevision: 11, historyRevision: 2, lastMessage: 'MA send', lastDisplayPreview: 'MA send',
    })]);
    expect(state().sessions[0]?.lastMessage).toBe('answer');
  });

  it.each(['agent', 'report', 'automation', 'system_prompt', 'user'])('keeps the response when its proven %s execution handoff arrives after the delta', source => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 10, historyRevision: 3, lastMessage: 'partial', lastDisplayPreview: 'partial',
    })], liveStreamBuffers: { s1: {
      workerId: 'w', generation: 0, executionSeq: 2, revision: 1, historyStartOffset: 3,
      messages: [{ role: 'assistant', content: 'partial' }],
    } } });
    useSessionStore.getState().appendDeliveredMessages('s1', [{
      role: 'user', source, content: 'MA input', queueItemIds: ['late-q'],
    }], { workerId: 'w', generation: 0, executionSeq: 2 }, {
      summaryRevision: 11, historyRevision: 4, historyTotal: 4,
      lastMessage: 'MA input', lastDisplayPreview: 'MA input', lastUserPreview: 'MA input',
    });
    expect(useSessionStore.getState().sessions[0]).toMatchObject({
      summaryRevision: 11, summaryHistoryRevision: 4, lastUserPreview: 'MA input',
      lastMessage: 'partial', lastDisplayPreview: 'partial',
    });
  });

  it.each(['other-execution', 'later-history'])('accepts a delivered preview for %s instead of protecting unrelated live text', scenario => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 10, historyRevision: 3, lastMessage: 'partial', lastDisplayPreview: 'partial',
    })], liveStreamBuffers: { s1: {
      workerId: 'w', generation: 0, executionSeq: 2, revision: 1, historyStartOffset: 3,
      messages: [{ role: 'assistant', content: 'partial' }],
    } } });
    useSessionStore.getState().appendDeliveredMessages('s1', [{
      role: 'user', source: 'report', content: 'new report', queueItemIds: ['new-q'],
    }], { workerId: 'w', generation: 0, executionSeq: scenario === 'other-execution' ? 3 : 2 }, {
      summaryRevision: 12, historyRevision: 5, historyTotal: scenario === 'later-history' ? 5 : 4,
      lastMessage: 'new report', lastDisplayPreview: 'new report',
    });
    expect(useSessionStore.getState().sessions[0]?.lastMessage).toBe('new report');
  });

  it('keeps queued and edited optimistic text out of the authoritative card preview and count', () => {
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 10, historyRevision: 1, historyTotal: 1,
      lastMessage: 'answer', lastDisplayPreview: 'answer',
    })] });
    const state = () => useSessionStore.getState();
    state().appendQueuedMessage('s1', { id: 'pending', text: 'not handed off' });
    state().updateQueuedMessage('s1', { id: 'pending', text: 'edited pending' });
    expect(state().sessions[0]).toMatchObject({ lastMessage: 'answer', historyTotal: 1 });
    expect(state().sessions[0]?.history.at(-1)?.content).toBe('edited pending');
  });

  it('reuses unchanged Session objects across summary refreshes', async () => {
    const first = mk('s1', { name: 'stable', lastMessage: 'same', historyTotal: 3 });
    useSessionStore.setState({ sessions: [first] });
    api.fetchSessions.mockResolvedValueOnce([mk('s1', {
      name: 'stable', lastMessage: 'same', historyTotal: 3,
    })]);

    await useSessionStore.getState().loadSessions();

    expect(useSessionStore.getState().sessions[0]).toBe(first);
  });

  it('keeps authoritative legality when an older HTTP summary arrives', async () => {
    const diagnostics = { coverage: 'pan-managed', revision: 9, outstanding: [] };
    useSessionStore.setState({ sessions: [mk('s1', {
      summaryRevision: 20, legalOffline: false, illegalOffline: true,
      legalityDiagnostics: diagnostics,
    })] });
    api.fetchSessions.mockResolvedValueOnce([mk('s1', {
      summaryRevision: 19, legalOffline: true, illegalOffline: false,
      legalityDiagnostics: { ...diagnostics, revision: 8 },
    })]);
    await useSessionStore.getState().loadSessions();
    expect(useSessionStore.getState().sessions[0]).toMatchObject({
      summaryRevision: 20, legalOffline: false, illegalOffline: true,
      legalityDiagnostics: diagnostics,
    });
  });

  it('does not let an older summary erase a local completion during the fetch', async () => {
    const first = mk('s1', { historyTotal: 1, lastMessage: 'old' });
    let resolveSnapshot!: (sessions: Session[]) => void;
    api.fetchSessions.mockReturnValueOnce(new Promise<Session[]>((resolve) => {
      resolveSnapshot = resolve;
    }));
    useSessionStore.setState({ sessions: [first] });

    const refresh = useSessionStore.getState().loadSessions();
    useSessionStore.getState().applyResultToSession('s1', {
      status: 'done', result: 'new result',
    });
    resolveSnapshot([mk('s1', { historyTotal: 1, lastMessage: 'old' })]);
    await refresh;

    const updated = useSessionStore.getState().sessions[0];
    expect(updated?.historyTotal).toBe(2);
    expect(updated?.lastMessage).toBe('new result');
  });
});
