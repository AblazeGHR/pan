// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

function msg(role: string, content: string, nativeItemId?: string): Message {
  return { role, content, ...(nativeItemId ? { nativeItemId } : {}) };
}

function session(id: string, history: Message[] = []): Session {
  return {
    id,
    name: id,
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: 'low',
    history,
    historyTotal: history.length,
    workerId: 'worker-a',
    workerStatus: 'running',
  };
}

let pendingHistory: Array<(value: {
  history: Message[];
  total: number;
  hasMore: boolean;
  start: number;
  historyEpoch?: string;
  historyRevision?: number;
}) => void> = [];
let pendingSessions: Array<(value: Session[]) => void> = [];

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessionHistory: vi.fn(() => new Promise((resolve) => pendingHistory.push(resolve as typeof pendingHistory[number]))),
    fetchSessions: vi.fn(() => new Promise((resolve) => pendingSessions.push(resolve as typeof pendingSessions[number]))),
  };
});

function reset(): void {
  pendingHistory = [];
  pendingSessions = [];
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    currentMessages: [],
    hasMoreMessages: false,
    historyLoading: false,
    initialLoading: false,
    historyLoadEnd: 0,
    sessionsLoading: false,
    _loadSeq: 0,
    _sessionWsTouchedSeq: {},
    _historyRefreshSeq: {},
    _sessionLocalTouchedSeq: {},
    liveStreamBuffers: {},
    terminalWatermarks: {},
    sessionTranscripts: {},
  });
}

describe('T-055 per-session live stream reconciliation', () => {
  beforeEach(reset);

  it('keeps A deltas through A → B → A and appends later deltas to the same item', async () => {
    const a = session('A', [msg('user', 'question A')]);
    const b = session('B', [msg('user', 'question B')]);
    useSessionStore.setState({
      sessions: [a, b],
      currentSessionId: 'A',
      currentMessages: a.history,
    });

    act(() => {
      useSessionStore.getState().applyLiveStream('A', [msg('assistant', 'Hel', 'item-a')], {
        workerId: 'worker-a', generation: 1, taskSeq: 1,
      });
      useSessionStore.setState({ currentSessionId: 'B', currentMessages: b.history });
      useSessionStore.getState().applyLiveStream('A', [msg('assistant', 'Hello', 'item-a')], {
        workerId: 'worker-a', generation: 1, taskSeq: 1,
      });
    });

    const switchBack = useSessionStore.getState().selectSession('A');
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question A')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await switchBack;
    });

    expect(useSessionStore.getState().currentMessages).toEqual([
      msg('user', 'question A'),
      msg('assistant', 'Hello', 'item-a'),
    ]);
  });

  it('preserves a midstream Steer row between earlier and later blocks when the Session streams in the background', async () => {
    const a = session('A', [msg('user', 'question A')]);
    const b = session('B', [msg('user', 'question B')]);
    useSessionStore.setState({
      sessions: [a, b],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    const meta = { workerId: 'worker-a', generation: 1, taskSeq: 1 };

    act(() => {
      useSessionStore.getState().applyWorkerStatus('A', 'running', meta);
      useSessionStore.getState().applyLiveStream('A', [msg('tool', 'Command(one)', 'tool-1')], meta);
      useSessionStore.getState().appendLocalMessage('A', msg('user', 'Steer A'));
      useSessionStore.setState({ currentSessionId: 'B', currentMessages: b.history });
      useSessionStore.getState().applyLiveStream('A', [
        msg('tool', 'Command(one completed)', 'tool-1'),
        msg('assistant', 'answer A', 'answer-1'),
      ], meta);
    });

    const switchBack = useSessionStore.getState().selectSession('A');
    expect(useSessionStore.getState().currentMessages.map(({ role, content }) => [role, content]))
      .toEqual([
        ['user', 'question A'],
        ['tool', 'Command(one completed)'],
        ['user', 'Steer A'],
        ['assistant', 'answer A'],
      ]);
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question A')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await switchBack;
    });
    expect(useSessionStore.getState().currentMessages.map(({ role, content }) => [role, content]))
      .toEqual([
        ['user', 'question A'],
        ['tool', 'Command(one completed)'],
        ['user', 'Steer A'],
        ['assistant', 'answer A'],
      ]);
  });

  it('rebinds a late Codex delta to its canonical row after A → B → A history refresh', async () => {
    // Summary metadata can already include the canonical rows while this
    // browser still holds an older/shorter history window.
    const a = session('A', [msg('user', 'question A')]);
    const b = session('B', [msg('user', 'question B')]);
    const full = 'answer that was persisted while A was in the background';
    useSessionStore.setState({
      sessions: [a, b],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    useSessionStore.getState().applyWorkerStatus('A', 'running', {
      workerId: 'worker-a', generation: 1, taskSeq: 1,
    });
    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A', [msg('assistant', full, 'completed-item')],
        { workerId: 'worker-a', generation: 1, taskSeq: 1 },
      );
    });
    useSessionStore.setState((state) => ({
      sessions: state.sessions.map((candidate) => candidate.id === 'A'
        ? { ...candidate, historyTotal: 2 }
        : candidate),
      sessionTranscripts: {
        ...state.sessionTranscripts,
        A: { ...state.sessionTranscripts.A!, anchorOffset: 2 },
      },
    }));

    const switchToB = useSessionStore.getState().selectSession('B');
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question B')], total: 1, hasMore: false, start: 0,
      });
      await switchToB;
    });

    const switchBackToA = useSessionStore.getState().selectSession('A');
    expect(useSessionStore.getState().currentMessages.map((row) => row.content)).toEqual([
      'question A', full,
    ]);
    await act(async () => {
      pendingHistory.shift()?.({
        history: [
          msg('user', 'question A'),
          { ...msg('assistant', full), messageId: 'canonical-assistant' },
        ],
        total: 2,
        hasMore: false,
        start: 0,
        historyRevision: 2,
      });
      await switchBackToA;
    });
    expect(useSessionStore.getState().currentMessages.filter((row) => row.role === 'assistant'))
      .toEqual([{ role: 'assistant', content: full, messageId: 'canonical-assistant' }]);

    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A', [msg('assistant', full.slice(0, 24), 'completed-item')],
        { workerId: 'worker-a', generation: 1, taskSeq: 1 },
      );
    });
    expect(useSessionStore.getState().currentMessages.filter((row) => row.role === 'assistant'))
      .toEqual([{
        role: 'assistant', content: full, messageId: 'canonical-assistant',
        nativeItemId: 'completed-item',
      }]);
  });

  it('keeps live suffixes over focus/snapshot/history reloads that are still server prefixes', async () => {
    const a = session('A', [msg('user', 'question')]);
    useSessionStore.setState({
      sessions: [a],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A',
        [msg('assistant', 'streaming answer', 'item-a')],
        { workerId: 'worker-a', generation: 2, taskSeq: 8 },
      );
    });

    const loading = useSessionStore.getState().loadSessions();
    await act(async () => {
      pendingSessions.shift()?.([session('A', [msg('user', 'question')])]);
      await loading;
    });
    expect(useSessionStore.getState().currentMessages.at(-1)?.content).toBe('streaming answer');

    const refreshing = useSessionStore.getState().refreshCurrentSessionHistory();
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await refreshing;
    });
    expect(useSessionStore.getState().currentMessages.at(-1)?.content).toBe('streaming answer');
  });

  it('keeps a local Steer through an old history snapshot and A → B → A', async () => {
    const a = session('A', [msg('user', 'question A')]);
    const b = session('B', [msg('user', 'question B')]);
    useSessionStore.setState({
      sessions: [a, b],
      currentSessionId: 'A',
      currentMessages: a.history,
    });

    act(() => {
      useSessionStore.getState().appendLocalMessage('A', {
        role: 'user', content: 'steer instruction',
      });
    });

    const refreshing = useSessionStore.getState().refreshCurrentSessionHistory();
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question A')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await refreshing;
    });
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'question A', 'steer instruction',
    ]);

    useSessionStore.setState({ currentSessionId: 'B', currentMessages: b.history });
    const switchBack = useSessionStore.getState().selectSession('A');
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'question A')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await switchBack;
    });
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'question A', 'steer instruction',
    ]);
    expect(useSessionStore.getState().currentMessages.some((m) => m.content === 'question B'))
      .toBe(false);
  });

  it('does not let a summary=1 refresh erase a local user projection', async () => {
    const a = session('A', [msg('user', 'question A')]);
    useSessionStore.setState({
      sessions: [a],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    act(() => {
      useSessionStore.getState().appendLocalMessage('A', {
        role: 'user', content: 'steer instruction',
      });
    });

    const loading = useSessionStore.getState().loadSessions();
    await act(async () => {
      pendingSessions.shift()?.([{ ...session('A'), historyTotal: 2 }]);
      await loading;
    });

    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'question A', 'steer instruction',
    ]);
    expect(useSessionStore.getState().sessions[0]?.history.map((m) => m.content)).toEqual([
      'question A', 'steer instruction',
    ]);
  });

  it('converges an edited queue projection to canonical history without a duplicate', async () => {
    const a = session('A', [msg('user', 'before')]);
    useSessionStore.setState({
      sessions: [a],
      currentSessionId: 'A',
      currentMessages: a.history,
    });

    useSessionStore.getState().appendQueuedMessage('A', {
      id: 'q-edit-canonical',
      text: 'before edit',
    });
    useSessionStore.getState().updateQueuedMessage('A', {
      id: 'q-edit-canonical',
      text: 'after edit',
    });
    expect(useSessionStore.getState().currentMessages.map((message) => message.content)).toEqual([
      'before', 'after edit',
    ]);

    const refreshing = useSessionStore.getState().refreshCurrentSessionHistory();
    await act(async () => {
      pendingHistory.shift()?.({
        // Legacy canonical history can lack the queue IDs persisted by newer Workers.
        history: [msg('user', 'before'), msg('user', 'after edit')],
        total: 2,
        hasMore: false,
        start: 0,
      });
      await refreshing;
    });

    expect(useSessionStore.getState().currentMessages.map((message) => message.content)).toEqual([
      'before', 'after edit',
    ]);
    expect(useSessionStore.getState().currentMessages.filter(
      (message) => message.content === 'after edit',
    )).toHaveLength(1);
  });

  it('does not let an old loadSessions response write history into the new Session', async () => {
    const a = session('A', [msg('user', 'A history')]);
    const b = session('B', [msg('user', 'B history')]);
    useSessionStore.setState({
      sessions: [a, b],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    const loading = useSessionStore.getState().loadSessions();
    useSessionStore.setState({ currentSessionId: 'B', currentMessages: b.history });
    await act(async () => {
      pendingSessions.shift()?.([a, b]);
      await loading;
    });
    expect(useSessionStore.getState().currentSessionId).toBe('B');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'B history',
    ]);
  });

  it('keeps ordinary user and Steer rows while stream/result snapshots interleave', () => {
    const a = session('A', [msg('user', 'question')]);
    useSessionStore.setState({
      sessions: [a],
      currentSessionId: 'A',
      currentMessages: a.history,
    });

    act(() => {
      useSessionStore.getState().appendDeliveredMessages('A', [{
        role: 'user', content: 'ordinary user', queueItemIds: ['q-ordinary'],
      }]);
      useSessionStore.getState().applyLiveStream(
        'A', [msg('assistant', 'partial', 'stream-item')],
        { workerId: 'worker-a', generation: 1, taskSeq: 1, itemId: 'stream-item' },
      );
      useSessionStore.getState().appendLocalMessage('A', {
        role: 'user', content: 'steer instruction',
      });
      useSessionStore.getState().applyLiveStream(
        'A', [msg('assistant', 'partial more', 'stream-item')],
        { workerId: 'worker-a', generation: 1, taskSeq: 1, itemId: 'stream-item' },
      );
      useSessionStore.getState().reconcileWorkerResult(
        'A', { status: 'done', result: 'final answer' },
        { workerId: 'worker-a', generation: 1, taskSeq: 1, itemId: 'stream-item' },
      );
      useSessionStore.getState().applyWorkerStatus(
        'A', 'idle',
        { workerId: 'worker-a', generation: 1, taskSeq: 1, itemId: 'stream-item' },
      );
    });

    const contents = useSessionStore.getState().currentMessages.map((m) => m.content);
    expect(contents).toContain('ordinary user');
    expect(contents).toContain('steer instruction');
    expect(contents).toContain('final answer');
    expect(useSessionStore.getState().sessions[0]?.history.map((m) => m.content)).toEqual([
      'question', 'ordinary user', 'steer instruction', 'final answer',
    ]);
  });

  it('keeps same-text queue messages distinct and makes canonical replay idempotent', async () => {
    const a = session('A', [msg('user', 'before')]);
    useSessionStore.setState({
      sessions: [a],
      currentSessionId: 'A',
      currentMessages: a.history,
    });
    act(() => {
      useSessionStore.getState().appendQueuedMessage('A', { id: 'q1', text: 'repeat' });
      useSessionStore.getState().appendQueuedMessage('A', { id: 'q2', text: 'repeat' });
      useSessionStore.getState().appendDeliveredMessages('A', [{
        role: 'user', content: 'repeat', queueItemIds: ['q1'],
      }]);
    });
    expect(useSessionStore.getState().currentMessages.filter((m) => m.content === 'repeat'))
      .toHaveLength(2);

    const refreshing = useSessionStore.getState().refreshCurrentSessionHistory();
    await act(async () => {
      pendingHistory.shift()?.({
        history: [msg('user', 'before'), msg('user', 'repeat'), msg('user', 'repeat')],
        total: 3,
        hasMore: false,
        start: 0,
      });
      await refreshing;
    });
    expect(useSessionStore.getState().currentMessages.filter((m) => m.content === 'repeat'))
      .toHaveLength(2);
    useSessionStore.getState().appendDeliveredMessages('A', [{
      role: 'user', content: 'repeat', queueItemIds: ['q1'],
    }]);
    expect(useSessionStore.getState().currentMessages.filter((m) => m.content === 'repeat'))
      .toHaveLength(2);
  });

  it('final reconciliation leaves one canonical assistant and no DONE row in history', () => {
    const a = session('A', [msg('user', 'question')]);
    useSessionStore.setState({ sessions: [a], currentSessionId: 'A', currentMessages: a.history });
    act(() => {
      useSessionStore.getState().applyLiveStream(
        'A',
        [msg('assistant', 'partial', 'item-a')],
        { workerId: 'worker-a', generation: 3, taskSeq: 9, turnId: 'turn-a' },
      );
      useSessionStore.getState().reconcileWorkerResult(
        'A',
        { status: 'done', result: 'final answer' },
        { workerId: 'worker-a', generation: 3, taskSeq: 9, turnId: 'turn-a' },
      );
      useSessionStore.getState().applyWorkerStatus(
        'A', 'idle', { workerId: 'worker-a', generation: 3, taskSeq: 9 },
      );
    });

    const current = useSessionStore.getState();
    expect(current.sessions[0]?.history.filter((m) => m.role === 'assistant')).toEqual([
      msg('assistant', 'final answer', 'item-a'),
    ]);
    expect(current.sessions[0]?.history.some((m) => m.role === 'system')).toBe(false);
    expect(current.currentMessages.filter((m) => m.role === 'assistant')).toEqual([
      msg('assistant', 'final answer', 'item-a'),
    ]);
  });

  it('reconciles Claude tool rows across JSON formatting during terminal history recovery', async () => {
    const a = { ...session('A', [msg('user', 'previous turn')]), adapter: 'claude' };
    useSessionStore.setState({ sessions: [a], currentSessionId: 'A', currentMessages: a.history });
    const meta = { workerId: 'worker-a', generation: 1, taskSeq: 1 };
    const answer = 'answer:background-live-claude';

    act(() => {
      useSessionStore.getState().applyWorkerStatus('A', 'running', meta);
      useSessionStore.getState().applyLiveStream('A', [
        msg('thinking', 'think:background-live-claude'),
        msg('tool', 'Bash({"command":"background-live-claude"})'),
        msg('assistant', answer),
      ], meta);
      useSessionStore.getState().reconcileWorkerResult('A', {
        status: 'done',
        result: answer,
        terminalCoverage: { historyEpoch: 'epoch-a', historyRevision: 4 },
      }, meta);
    });

    await act(async () => {
      // Terminal coverage starts an asynchronous authoritative history read.
      await Promise.resolve();
      pendingHistory.shift()?.({
        history: [
          msg('user', 'previous turn'),
          msg('user', 'background-live-claude'),
          msg('thinking', 'think:background-live-claude'),
          // Claude persistence uses Python's default JSON separators, while
          // the browser's live tool row uses compact JSON.
          msg('tool', 'Bash({"command": "background-live-claude"})'),
          msg('assistant', answer),
        ],
        total: 5,
        hasMore: false,
        start: 0,
        historyEpoch: 'epoch-a',
        historyRevision: 4,
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(useSessionStore.getState().currentMessages.map(({ role, content }) => [role, content]))
      .toEqual([
        ['user', 'previous turn'],
        ['user', 'background-live-claude'],
        ['thinking', 'think:background-live-claude'],
        ['tool', 'Bash({"command": "background-live-claude"})'],
        ['assistant', answer],
      ]);
  });

  it('terminal watermark rejects a delayed old running event while accepting idle idempotently', () => {
    const a = session('A');
    useSessionStore.setState({ sessions: [a] });
    act(() => {
      useSessionStore.getState().applyWorkerStatus(
        'A', 'running', { workerId: 'worker-a', generation: 4, taskSeq: 10 },
      );
      useSessionStore.getState().reconcileWorkerResult(
        'A', { status: 'done', result: 'done' },
        { workerId: 'worker-a', generation: 4, taskSeq: 10 },
      );
      useSessionStore.getState().applyWorkerStatus(
        'A', 'idle', { workerId: 'worker-a', generation: 4, taskSeq: 10 },
      );
      expect(useSessionStore.getState().applyWorkerStatus(
        'A', 'running', { workerId: 'worker-a', generation: 4, taskSeq: 10 },
      )).toBe(false);
    });
    expect(useSessionStore.getState().sessions[0]?.workerStatus).toBe('idle');
  });
});
