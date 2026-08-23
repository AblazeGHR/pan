// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

function mk(id: string, name: string, extra?: Partial<Session>): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

function msg(role: string, content: string): Message {
  return { role, content };
}

interface HistoryFetch {
  history: Message[];
  total: number;
  hasMore: boolean;
  start: number;
}

let pendingHistory: Array<(r: HistoryFetch) => void> = [];
let historyShouldReject = false;
let serverSessions: Session[] = [];

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: vi.fn(async () => serverSessions),
    fetchSessionHistory: vi.fn(
      (_id: string, _before: number, _limit: number) =>
        new Promise<HistoryFetch>((resolve, reject) => {
          if (historyShouldReject) {
            reject(new Error('network down'));
            return;
          }
          pendingHistory.push(resolve);
        }),
    ),
  };
});

function resolveNextHistory(r: HistoryFetch): void {
  const resolve = pendingHistory.shift();
  expect(resolve).toBeTruthy();
  resolve?.(r);
}

function resetStore(): void {
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    currentMessages: [],
    hasMoreMessages: false,
    historyLoading: false,
    historyLoadEnd: 0,
    _loadSeq: 0,
    _touchSeq: 0,
    _sessionWsTouchedSeq: {},
  });
}

describe('sessionStore selectSession fetches fresh history on entry', () => {
  beforeEach(() => {
    pendingHistory = [];
    historyShouldReject = false;
    resetStore();
  });

  it('replaces the stale snapshot with fresh server history on entry', async () => {
    // The list snapshot is stale (vanilla already showed newer messages, the
    // debounced React loadSessions never ran / was superseded).
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', {
          history: [msg('user', 'u0'), msg('assistant', 'a0')],
          historyTotal: 2,
        }),
      ],
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().selectSession('A');
    });

    // Snapshot renders immediately — no blank flash while fetching.
    expect(useSessionStore.getState().currentSessionId).toBe('A');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'u0',
      'a0',
    ]);

    await act(async () => {
      resolveNextHistory({
        history: [
          msg('user', 'u0'),
          msg('assistant', 'a0'),
          msg('user', 'u1'),
          msg('assistant', 'a1'),
        ],
        total: 4,
        hasMore: false,
        start: 0,
      });
      await promise!;
    });

    const { currentMessages, hasMoreMessages, historyLoadEnd } =
      useSessionStore.getState();
    expect(currentMessages.map((m) => m.content)).toEqual([
      'u0',
      'a0',
      'u1',
      'a1',
    ]);
    expect(hasMoreMessages).toBe(false);
    expect(historyLoadEnd).toBe(0);
    // Array entry updated too (card preview + re-entry reuse the fresh tail).
    expect(useSessionStore.getState().sessions[0]?.historyTotal).toBe(4);
  });

  it('keeps the snapshot when the fresh-history fetch fails (non-blocking)', async () => {
    historyShouldReject = true;
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [msg('user', 'u0')], historyTotal: 1 })],
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().selectSession('A');
    });
    await act(async () => {
      await promise!;
    });

    expect(useSessionStore.getState().currentSessionId).toBe('A');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'u0',
    ]);
  });

  it('does not clobber a session the user switched away from', async () => {
    useSessionStore.setState({
      sessions: [
        mk('A', 'A', { history: [msg('user', 'u0')] }),
        mk('B', 'B', { history: [] }),
      ],
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().selectSession('A');
    });
    // User switches to B before A's fetch resolves.
    act(() => {
      void useSessionStore.getState().selectSession('B');
    });

    // A's late fetch must be discarded.
    await act(async () => {
      resolveNextHistory({
        history: [msg('user', 'uX')],
        total: 1,
        hasMore: false,
        start: 0,
      });
      await promise!;
    });

    expect(useSessionStore.getState().currentSessionId).toBe('B');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([]);
  });
});

describe('sessionStore keeps the optimistic user message under a lagging snapshot', () => {
  beforeEach(() => {
    serverSessions = [];
    resetStore();
  });

  it('does not drop the just-sent message when the server history lags it', async () => {
    // 长历史会话：history 含中文 tool 消息（后端 Python json.dumps 转义）。
    // 用户刚发 u1（乐观上屏），后端 save 慢，快照还没有 u1——loadSessions
    // 的 prefix 守卫必须判定服务端是本地前缀而保留本地（用户消息不「暂时消失」）。
    const toolContent =
      'Bash({"command":"ls \\u4e2d\\u6587\\u76ee\\u5f55","path":"\\u6570\\u636e/\\u6587\\u4ef6.txt"})';
    const local = [msg('tool', toolContent), msg('user', 'u1')];
    // 服务端快照：与本地 tool 内容一致（前端已改为 Python 兼容转义），但没有 u1。
    serverSessions = [
      mk('A', 'A', { history: [msg('tool', toolContent)], historyTotal: 1 }),
    ];
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [msg('tool', toolContent)] })],
      currentSessionId: 'A',
      currentMessages: local,
    });

    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });

    const { currentMessages } = useSessionStore.getState();
    expect(currentMessages.map((m) => m.content)).toEqual([
      toolContent,
      'u1',
    ]);
  });
});

describe('sessionStore applyResultToSession in-place card update', () => {
  beforeEach(resetStore);

  it('appends the result text and bumps historyTotal immediately', () => {
    useSessionStore.setState({
      sessions: [
        mk('B', 'B', { history: [msg('user', 'u1')], historyTotal: 1 }),
      ],
    });
    act(() => {
      useSessionStore.getState().applyResultToSession('B', {
        status: 'done',
        result: 'reply',
      });
    });
    const s = useSessionStore.getState().sessions[0]!;
    expect(s.history.map((m) => m.content)).toEqual(['u1', 'reply']);
    expect(s.historyTotal).toBe(2);
    expect(s.lastResult?.status).toBe('done');
    expect(s.lastResult?.result).toBe('reply');
  });

  it('dedupes when the result equals the last assistant message', () => {
    useSessionStore.setState({
      sessions: [
        mk('B', 'B', {
          history: [msg('user', 'u1'), msg('assistant', 'reply')],
          historyTotal: 2,
        }),
      ],
    });
    act(() => {
      useSessionStore.getState().applyResultToSession('B', {
        status: 'done',
        result: 'reply',
      });
    });
    const s = useSessionStore.getState().sessions[0]!;
    expect(s.history).toHaveLength(2);
    expect(s.historyTotal).toBe(2);
  });

  it('maps an error result to error lastResult and still appends the text', () => {
    useSessionStore.setState({
      sessions: [mk('B', 'B', { history: [] })],
    });
    act(() => {
      useSessionStore.getState().applyResultToSession('B', {
        status: 'error',
        result: 'boom',
      });
    });
    const s = useSessionStore.getState().sessions[0]!;
    expect(s.lastResult?.status).toBe('error');
    expect(s.history.map((m) => m.content)).toEqual(['boom']);
  });
});
