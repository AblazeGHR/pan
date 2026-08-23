// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session, Message } from '@/types';

// Capture WS handlers registered by useWebSocket so tests can dispatch events.
const wsMock = vi.hoisted(() => {
  const handlers: Record<string, Array<(e: unknown) => void>> = {};
  return {
    handlers,
    on: vi.fn((type: string, h: (e: unknown) => void) => {
      (handlers[type] ??= []).push(h);
      return () => {
        handlers[type] = (handlers[type] ?? []).filter((x) => x !== h);
      };
    }),
    trigger: (type: string, e: unknown) => {
      for (const h of handlers[type] ?? []) h(e);
    },
  };
});

vi.mock('@/services/ws', () => ({
  wsClient: {
    connect: vi.fn(),
    on: wsMock.on,
    send: vi.fn(() => true),
    isOpen: true,
  },
}));

function msg(role: string, content: string): Message {
  return { role, content };
}

function mk(id: string, name: string, extra?: Partial<Session>): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'running',
    workerId: 'w1',
    ...extra,
  };
}

describe('useWebSocket worker.result wiring', () => {
  beforeEach(() => {
    for (const k of Object.keys(wsMock.handlers)) delete wsMock.handlers[k];
    useSessionStore.setState({
      sessions: [
        mk('B', 'B', { history: [msg('user', 'u1')], historyTotal: 1 }),
        mk('A', 'A', { history: [msg('user', 'u0')] }),
      ],
      currentSessionId: 'A',
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _touchSeq: 0,
      _sessionWsTouchedSeq: {},
    });
  });

  it('updates a background session card in-place on worker.result', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.result', {
        type: 'worker.result',
        sessionId: 'B',
        workerId: 'w1',
        status: 'done',
        result: 'reply',
      });
    });

    const s = useSessionStore.getState().sessions.find((x) => x.id === 'B');
    // handleWorkerUpdate → card dot idle immediately
    expect(s?.workerStatus).toBe('idle');
    // applyResultToSession → card summary / historyTotal updated immediately
    expect(s?.history.map((m) => m.content)).toEqual(['u1', 'reply']);
    expect(s?.historyTotal).toBe(2);
    expect(s?.lastResult?.status).toBe('done');
    expect(s?.lastResult?.result).toBe('reply');
    // result for a non-current session must not pollute the chat pane
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('appends the [DONE] notice for the current session without duplicating history', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.result', {
        type: 'worker.result',
        sessionId: 'A',
        workerId: 'w1',
        status: 'done',
        result: 'a-reply',
      });
    });

    // Chat pane got the client-only [DONE] system notice.
    const last = useSessionStore.getState().currentMessages.at(-1);
    expect(last?.role).toBe('system');
    expect(last?.content).toContain('[DONE]');
    // Card summary shows the result text (in-place), no [DONE] in session history.
    const s = useSessionStore.getState().sessions.find((x) => x.id === 'A');
    expect(s?.history.map((m) => m.content)).toEqual(['u0', 'a-reply']);
    expect(s?.workerStatus).toBe('idle');
  });

  it('clears the card status on worker crash and keeps history intact', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.crashed', {
        type: 'worker.crashed',
        sessionId: 'B',
        workerId: 'w1',
      });
    });

    const s = useSessionStore.getState().sessions.find((x) => x.id === 'B');
    // handleWorkerUpdate(null) → dot offline (null 经 ?? 归一为 undefined，
    // WorkerDot 视同为 offline)，history 不动（崩溃安全）。
    expect(s?.workerStatus).toBeUndefined();
    expect(s?.history.map((m) => m.content)).toEqual(['u1']);
  });

  it('renders streamed tool content with backend-compatible ASCII escaping', () => {
    // 后端 cbc adapter 用 Python json.dumps(ensure_ascii=True) 落盘 tool 内容
    // （中文转小写 \uXXXX），前端 appendEvent 必须一致，否则 isServerHistoryPrefix
    // 误判 → loadSessions 全量重建把乐观用户消息抹掉。
    renderHook(() => useWebSocket());
    useSessionStore.setState({ currentSessionId: 'A' });

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'A',
        workerId: 'w1',
        event: {
          type: 'assistant',
          message: {
            content: [
              {
                type: 'tool_use',
                name: 'Bash',
                input: { command: 'ls 中文目录', path: '数据/文件.txt' },
              },
            ],
          },
        },
      });
    });

    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]?.role).toBe('tool');
    expect(msgs[0]?.content).toBe(
      'Bash({"command":"ls \\u4e2d\\u6587\\u76ee\\u5f55","path":"\\u6570\\u636e/\\u6587\\u4ef6.txt"})',
    );
  });
});
