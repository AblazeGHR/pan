// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, renderHook, act } from '@testing-library/react';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useQueueStore } from '@/stores/queueStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Session, Message } from '@/types';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { SessionItem } from '@/components/session/SessionItem';

// Capture WS handlers registered by useWebSocket so tests can dispatch events.
const wsMock = vi.hoisted(() => {
  const handlers: Record<string, Array<(e: unknown) => void>> = {};
  return {
    handlers,
    send: vi.fn(() => true),
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
    send: wsMock.send,
    isOpen: true,
  },
}));

// Mock the history fetch so agent-injected-message sync tests can control what
// the server "has persisted" (the injected user message lives only server-side).
const apiMock = vi.hoisted(() => ({
  fetchSessionHistory: vi.fn(),
  fetchSessionQueue: vi.fn(),
  updateUiSettings: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchSessionHistory: apiMock.fetchSessionHistory,
  fetchSessionQueue: apiMock.fetchSessionQueue,
  updateUiSettings: apiMock.updateUiSettings,
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
    wsMock.send.mockClear();
    useSessionStore.setState({
      sessions: [
        mk('B', 'B', { history: [msg('user', 'u1')], historyTotal: 1 }),
        mk('A', 'A', { history: [msg('user', 'u0')] }),
      ],
      currentSessionId: 'A',
      currentMessages: [],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      sessionsLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _touchSeq: 0,
      _sessionWsTouchedSeq: {},
    });
    useUIStore.setState({ terminalInteractions: [], toastQueue: [] });
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useQueueStore.setState({ agentQueues: {}, agentQueueLoadSeq: {} });
    apiMock.fetchSessionQueue.mockReset();
    apiMock.fetchSessionQueue.mockResolvedValue([]);
    apiMock.updateUiSettings.mockReset();
    apiMock.updateUiSettings.mockResolvedValue({});
  });

  it('requests pending native interactions when the singleton is already open', () => {
    renderHook(() => useWebSocket());

    expect(wsMock.send).toHaveBeenCalledWith({ type: 'sync_interactive' });
  });

  it('routes Claude permission requests and removes them after resolution', () => {
    useUIStore.setState({ approvalRequests: [] });
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'approval.request',
          method: 'claude/permission',
          request_id: 'claude-request-1',
          params: { tool_name: 'Bash', input: { command: 'git status' } },
        },
      });
    });

    expect(useUIStore.getState().approvalRequests).toEqual([{
      sessionId: 'A',
      workerId: 'w1',
      requestId: 'claude-request-1',
      method: 'claude/permission',
      params: { tool_name: 'Bash', input: { command: 'git status' } },
    }]);

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: { type: 'claude.permission_resolved', request_id: 'claude-request-1' },
      });
    });
    expect(useUIStore.getState().approvalRequests).toEqual([]);
  });

  it('keeps native Codex waiting status available to the active worker', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.thread_status',
          native_status: {
            type: 'active', activeFlags: ['waitingOnApproval'],
          },
        },
      });
    });

    expect(useWorkerStore.getState().workers.A?.nativeStatus).toEqual({
      type: 'active', activeFlags: ['waitingOnApproval'],
    });
  });

  it('keeps a native system error status and its summary visible to the toolbar', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.thread_status',
          native_status: { type: 'systemError', message: 'server disconnected' },
        },
      });
    });

    expect(useWorkerStore.getState().workers.A?.nativeStatus).toEqual({
      type: 'systemError', message: 'server disconnected',
    });
  });

  it('keeps the latest native Codex token usage available to the active worker', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.token_usage',
          token_usage: {
            last: { totalTokens: 150 },
            total: { totalTokens: 150 },
            modelContextWindow: 4096,
          },
        },
      });
    });

    expect(useWorkerStore.getState().workers.A?.nativeUsage).toEqual({
      last: { totalTokens: 150 },
      total: { totalTokens: 150 },
      modelContextWindow: 4096,
    });
  });

  it('keeps native Codex account rate limits available to the active worker', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.rate_limits',
          rate_limits: {
            primary: { usedPercent: 25 },
            secondary: { usedPercent: 5 },
          },
        },
      });
    });

    expect(useWorkerStore.getState().workers.A?.nativeRateLimits).toEqual({
      primary: { usedPercent: 25 },
      secondary: { usedPercent: 5 },
    });
  });

  it('renders and replaces native Codex turn plans and aggregate diffs', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.plan', item_id: 'plan:turn-1', delta: true, replace: true,
          explanation: 'Working',
          plan: [
            { step: 'Inspect', status: 'completed' },
            { step: 'Fix', status: 'inProgress' },
          ],
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.plan', item_id: 'plan:turn-1', delta: true, replace: true,
          plan: [{ step: 'Fix', status: 'completed' }],
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.diff', item_id: 'diff:turn-1', delta: true, replace: true,
          diff: '--- a/file\n+++ b/file\n+new',
        },
      });
    });

    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'thinking', content: '[x] Fix', nativeItemId: 'plan:turn-1' },
      {
        role: 'tool',
        content: 'CodexDiff({"diff":"--- a/file\\n+++ b/file\\n+new"})',
        nativeItemId: 'diff:turn-1',
      },
    ]);
  });

  it('surfaces native Codex turn errors immediately without adding a fake chat message', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.turn_error',
          error_text: 'upstream unavailable',
          error: { code: 'unavailable' },
        },
      });
    });

    expect(useUIStore.getState().toastQueue.at(-1)?.message)
      .toBe('Codex: upstream unavailable');
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('suppresses Codex warning Toasts when the notification setting is disabled', () => {
    useAppSettingsStore.getState().setCodexWarningToast(false);
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.turn_error',
          error_text: 'upstream unavailable',
        },
      });
    });

    expect(useUIStore.getState().toastQueue).toEqual([]);
  });

  it('surfaces Codex MCP startup failures without surfacing ready notifications', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.mcp_status',
          mcp_status: { name: 'pan', status: 'ready' },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.mcp_status',
          mcp_status: { name: 'pan', status: 'failed', error: 'offline' },
        },
      });
    });

    expect(useUIStore.getState().toastQueue.at(-1)?.message)
      .toBe('Codex MCP pan: offline');
  });

  it('surfaces native Codex model reroutes on the active session', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.model_rerouted',
          model_rerouted: {
            fromModel: 'gpt-a', toModel: 'gpt-b', reason: 'highRiskCyberActivity',
          },
        },
      });
    });

    expect(useUIStore.getState().toastQueue.at(-1)?.message)
      .toBe('Codex switched model: gpt-a → gpt-b (highRiskCyberActivity)');
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

  it('labels a native cancelled turn separately from an error', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.result', {
        type: 'worker.result',
        sessionId: 'A',
        workerId: 'w1',
        status: 'cancelled',
        cancelled: true,
        result: '',
      });
    });

    expect(useSessionStore.getState().currentMessages.at(-1)?.content)
      .toBe('[CANCELLED] Task completed');
    expect(useSessionStore.getState().sessions.find((x) => x.id === 'A')?.lastResult?.status)
      .toBe('cancelled');
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

  it('refreshes the durable agent queue after a worker crash', async () => {
    renderHook(() => useWebSocket());

    await act(async () => {
      wsMock.trigger('worker.crashed', {
        type: 'worker.crashed',
        sessionId: 'B',
        workerId: 'w1',
      });
      await Promise.resolve();
    });

    expect(apiMock.fetchSessionQueue).toHaveBeenCalledWith('B');
    expect(useQueueStore.getState().agentQueues.B).toEqual([]);
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

  it('renders unknown native Codex items through the generic tool fallback', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'A',
        workerId: 'w1',
        event: {
          type: 'codex.item.completed',
          item: { id: 'item-1', type: 'futureNativeItem', summary: 'kept' },
        },
      });
    });

    expect(useSessionStore.getState().currentMessages.at(-1)).toEqual({
      role: 'tool',
      content: 'futureNativeItem({"summary":"kept"})',
    });
  });

  it('replaces a running command item as native output deltas arrive', () => {
    renderHook(() => useWebSocket());
    useSessionStore.setState({ currentSessionId: 'A' });

    const toolEvent = (output?: string, replace = false) => ({
      type: 'assistant',
      delta: true,
      replace,
      message: {
        content: [{
          type: 'tool_use',
          name: 'Command',
          input: { command: 'printf hello', ...(output ? { output } : {}) },
        }],
      },
    });

    act(() => {
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', workerId: 'w1', event: toolEvent() });
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', workerId: 'w1', event: toolEvent('hel', true) });
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', workerId: 'w1', event: {
        ...toolEvent('hello', true), delta: false, final: true,
      } });
    });

    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'tool', content: 'Command({"command":"printf hello","output":"hello"})' },
    ]);
  });

  it('updates interleaved native tools by item id', () => {
    renderHook(() => useWebSocket());
    useSessionStore.setState({ currentSessionId: 'A' });

    const toolEvent = (itemId: string, output: string, replace: boolean) => ({
      type: 'assistant', delta: true, replace, item_id: itemId,
      message: { content: [{
        type: 'tool_use', name: 'Command',
        input: { command: itemId, output },
      }] },
    });
    act(() => {
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', event: toolEvent('one', 'a', false) });
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', event: toolEvent('two', 'b', false) });
      wsMock.trigger('worker.stream', { type: 'worker.stream', sessionId: 'A', event: toolEvent('one', 'aa', true) });
    });

    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'tool', content: 'Command({"command":"one","output":"aa"})', nativeItemId: 'one' },
      { role: 'tool', content: 'Command({"command":"two","output":"b"})', nativeItemId: 'two' },
    ]);
  });

  it('surfaces native terminal interaction and clears it on result', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'codex.terminal_interaction',
          item_id: 'item-1', process_id: 'process-1', stdin: 'Password: ',
          params: { threadId: 't', turnId: 'u' },
        },
      });
    });

    expect(useUIStore.getState().terminalInteractions).toEqual([{
      sessionId: 'A', workerId: 'w1', itemId: 'item-1', processId: 'process-1',
      stdin: 'Password: ', params: { threadId: 't', turnId: 'u' },
    }]);

    act(() => {
      wsMock.trigger('worker.result', {
        type: 'worker.result', sessionId: 'A', workerId: 'w1',
        status: 'done', result: 'ok',
      });
    });
    expect(useUIStore.getState().terminalInteractions).toEqual([]);
  });

  it('drops stale native interaction prompts when a worker is restarted', () => {
    renderHook(() => useWebSocket());
    useUIStore.setState({
      approvalRequests: [{
        sessionId: 'A', workerId: 'w1', requestId: 1,
        method: 'item/commandExecution/requestApproval', params: {},
      }],
      userInputRequests: [{
        sessionId: 'A', workerId: 'w1', requestId: 2,
        method: 'item/tool/requestUserInput', questions: [],
      }],
      elicitationRequests: [{
        sessionId: 'A', workerId: 'w1', requestId: 3,
        method: 'mcpServer/elicitation/request', params: {},
      }],
      terminalInteractions: [{
        sessionId: 'A', workerId: 'w1', itemId: 'item-1', processId: 'process-1',
        stdin: '', params: {},
      }],
    });

    act(() => {
      wsMock.trigger('worker.restarted', {
        type: 'worker.restarted', sessionId: 'A', workerId: 'w1',
      });
    });

    const ui = useUIStore.getState();
    expect(ui.approvalRequests).toEqual([]);
    expect(ui.userInputRequests).toEqual([]);
    expect(ui.elicitationRequests).toEqual([]);
    expect(ui.terminalInteractions).toEqual([]);
  });
});

describe('useWebSocket agent-injected message sync', () => {
  beforeEach(() => {
    for (const k of Object.keys(wsMock.handlers)) delete wsMock.handlers[k];
    apiMock.fetchSessionHistory.mockReset();
    apiMock.fetchSessionHistory.mockResolvedValue({
      history: [],
      total: 0,
      hasMore: false,
      start: 0,
    });
    useSessionStore.setState({
      sessions: [mk('A', 'A', { history: [msg('user', 'u0')] })],
      currentSessionId: 'A',
      currentMessages: [msg('user', 'u0')],
      hasMoreMessages: false,
      historyLoading: false,
      initialLoading: false,
      sessionsLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _touchSeq: 0,
      _sessionWsTouchedSeq: {},
    });
  });

  /** 触发事件并 flush 微任务（syncAgentInjectedMessage 的 fetch promise 链）。 */
  async function flushTrigger(type: string, e: unknown): Promise<void> {
    await act(async () => {
      wsMock.trigger(type, e);
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it('merges the agent-injected user message into currentMessages on running + source=agent', async () => {
    renderHook(() => useWebSocket());
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('user', '////by agent : S | title\ninstruct'),
      ],
      total: 2,
      hasMore: false,
      start: 0,
    });

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });

    expect(apiMock.fetchSessionHistory).toHaveBeenCalledWith('A', 0, 50);
    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs.map((m) => m.content)).toEqual([
      'u0',
      '////by agent : S | title\ninstruct',
    ]);
  });

  it('retries when the first history snapshot races the injected message persistence', async () => {
    renderHook(() => useWebSocket());
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [msg('user', 'u0')],
      total: 1,
      hasMore: false,
      start: 0,
    });
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('user', '@@@@by qq : group:42 | Chat | bot 100\nnew message'),
      ],
      total: 2,
      hasMore: false,
      start: 0,
    });

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'report',
    });
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['u0']);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 70));
    });

    expect(apiMock.fetchSessionHistory).toHaveBeenCalledTimes(2);
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'u0',
      '@@@@by qq : group:42 | Chat | bot 100\nnew message',
    ]);
  });

  it('does not sync for user-originated tasks', async () => {
    renderHook(() => useWebSocket());

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'user',
    });

    expect(apiMock.fetchSessionHistory).not.toHaveBeenCalled();
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['u0']);
  });

  it('refreshes the durable queue when receipt transitions to running', async () => {
    renderHook(() => useWebSocket());
    apiMock.fetchSessionQueue.mockResolvedValueOnce([]);

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'user',
    });

    expect(apiMock.fetchSessionQueue).toHaveBeenCalledWith('A');
  });

  it('does not sync when the event targets a non-current session', async () => {
    renderHook(() => useWebSocket());

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'B',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });

    expect(apiMock.fetchSessionHistory).not.toHaveBeenCalled();
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['u0']);
  });

  it('keeps in-flight streamed blocks when the server snapshot lags (diverged tail)', async () => {
    // 本地已流式出 a1，但服务端尚未落盘 → 服务端历史 = [u0, agentMsg]。
    renderHook(() => useWebSocket());
    useSessionStore.setState({
      currentMessages: [msg('user', 'u0'), msg('assistant', 'a1')],
    });
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [msg('user', 'u0'), msg('user', '////by agent : S | title\ninstruct')],
      total: 2,
      hasMore: false,
      start: 0,
    });

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });

    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs.map((m) => m.content)).toEqual([
      'u0',
      '////by agent : S | title\ninstruct',
      'a1',
    ]);
  });

  it('does not duplicate streamed blocks already persisted on the server', async () => {
    // 本地 a1 已同时被服务端落盘 → 服务端历史 = [u0, agentMsg, a1]，合并不应双份 a1。
    renderHook(() => useWebSocket());
    useSessionStore.setState({
      currentMessages: [msg('user', 'u0'), msg('assistant', 'a1')],
    });
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('user', '////by agent : S | title\ninstruct'),
        msg('assistant', 'a1'),
      ],
      total: 3,
      hasMore: false,
      start: 0,
    });

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });

    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs.map((m) => m.content)).toEqual([
      'u0',
      '////by agent : S | title\ninstruct',
      'a1',
    ]);
  });

  it('is idempotent across repeated running events for the same task', async () => {
    renderHook(() => useWebSocket());
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('user', '////by agent : S | title\ninstruct'),
      ],
      total: 2,
      hasMore: false,
      start: 0,
    });
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('user', '////by agent : S | title\ninstruct'),
      ],
      total: 2,
      hasMore: false,
      start: 0,
    });

    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });
    await flushTrigger('worker.status', {
      type: 'worker.status',
      sessionId: 'A',
      workerId: 'w1',
      status: 'running',
      source: 'agent',
    });

    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs).toHaveLength(2);
    expect(msgs.map((m) => m.content)).toEqual([
      'u0',
      '////by agent : S | title\ninstruct',
    ]);
  });
});

describe('useWebSocket worker.stream lastMessage preview', () => {
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
      initialLoading: false,
      sessionsLoading: false,
      historyLoadEnd: 0,
      _loadSeq: 0,
      _touchSeq: 0,
      _sessionWsTouchedSeq: {},
    });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function streamText(sessionId: string, text: string): void {
    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId,
        workerId: 'w1',
        event: {
          type: 'assistant',
          message: { content: [{ type: 'text', text }] },
        },
      });
    });
  }

  function lastMessageOf(id: string): string | undefined {
    return useSessionStore
      .getState()
      .sessions.find((x) => x.id === id)?.lastMessage;
  }

  it('updates a background session card lastMessage on stream text', () => {
    renderHook(() => useWebSocket());
    streamText('B', 'Hello world');

    expect(lastMessageOf('B')).toBe('Hello world');
    // 非当前 session 的流式事件不污染消息区
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('truncates the card preview to 200 chars', () => {
    renderHook(() => useWebSocket());
    const long = 'x'.repeat(500);
    streamText('B', long);
    expect(lastMessageOf('B')).toBe(long.slice(0, 200));
  });

  it('throttles lastMessage updates within 500ms, flushing the latest text', () => {
    renderHook(() => useWebSocket());

    streamText('B', 'a');
    expect(lastMessageOf('B')).toBe('a'); // 首个事件立即 flush

    // 500ms 窗口内的事件合并，未到点不更新卡片
    vi.advanceTimersByTime(100);
    streamText('B', 'ab');
    vi.advanceTimersByTime(100);
    streamText('B', 'abc');
    expect(lastMessageOf('B')).toBe('a');

    // 到点 → 尾随 timer flush 最新文本
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(lastMessageOf('B')).toBe('abc');
  });

  it('skips stream events without text content', () => {
    renderHook(() => useWebSocket());
    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'B',
        workerId: 'w1',
        event: {
          type: 'assistant',
          message: { content: [{ type: 'thinking', thinking: 'hmm' }] },
        },
      });
    });
    expect(lastMessageOf('B')).toBeUndefined();
  });

  it('result wins over a pending throttled stream preview', () => {
    renderHook(() => useWebSocket());

    streamText('B', 'first');
    expect(lastMessageOf('B')).toBe('first');

    vi.advanceTimersByTime(100);
    streamText('B', 'pending-stream'); // 排了尾随 timer

    act(() => {
      wsMock.trigger('worker.result', {
        type: 'worker.result',
        sessionId: 'B',
        workerId: 'w1',
        status: 'done',
        result: 'final-result',
      });
    });
    expect(lastMessageOf('B')).toBe('final-result');

    // 迟到的节流 timer 不得覆盖 result
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(lastMessageOf('B')).toBe('final-result');
  });

  it('merges native app-server deltas and replaces them with the final item', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'A',
        workerId: 'w1',
        event: {
          type: 'content.part',
          role: 'assistant',
          delta: true,
          stream_text: 'Hel',
          part: { type: 'text', text: 'Hel' },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'A',
        workerId: 'w1',
        event: {
          type: 'content.part',
          role: 'assistant',
          delta: true,
          stream_text: 'Hello',
          part: { type: 'text', text: 'lo' },
        },
      });
    });
    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'assistant', content: 'Hello' },
    ]);

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream',
        sessionId: 'A',
        workerId: 'w1',
        event: {
          type: 'assistant',
          final: true,
          message: { content: [{ type: 'text', text: 'Hello!' }] },
        },
      });
    });
    expect(useSessionStore.getState().currentMessages).toEqual([
      { role: 'assistant', content: 'Hello!' },
    ]);
  });

  it('keeps one selected-session assistant message across an interleaved turn, result, and history refresh', async () => {
    renderHook(() => useWebSocket());

    act(() => {
      // The native stream identifies the in-flight assistant item with one
      // id, while the completed item may be observed with another id. The
      // turn id is the stable identity for the one assistant reply.
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'content.part', role: 'assistant', delta: true,
          turn_id: 'turn-1', item_id: 'delta-item',
          part: { type: 'text', text: '## Answer\n\n' },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'assistant', item_id: 'tool-item',
          message: {
            content: [{
              type: 'tool_use', name: 'Command', input: { command: 'true' },
            }],
          },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'assistant', final: true, turn_id: 'turn-1',
          item_id: 'completed-item',
          message: { content: [{ type: 'text', text: '## Answer\n\nbody' }] },
        },
      });
      wsMock.trigger('worker.result', {
        type: 'worker.result', sessionId: 'A', workerId: 'w1',
        status: 'done', result: '## Answer\n\nbody',
      });
    });

    const messages = useSessionStore.getState().currentMessages;
    expect(messages.filter((message) => message.role === 'assistant')).toEqual([
      { role: 'assistant', content: '## Answer\n\nbody', nativeItemId: 'turn:turn-1' },
    ]);
    expect(messages.filter((message) => message.role === 'tool')).toHaveLength(1);
    expect(messages.at(-1)?.role).toBe('system');

    // A browser refresh/re-entry rebuilds currentMessages from the persisted
    // history. That history is the canonical comparison: it contains one
    // assistant reply, not the transient stream item and the result separately.
    apiMock.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        msg('user', 'u0'),
        msg('tool', 'Command({"command":"true"})'),
        msg('assistant', '## Answer\n\nbody'),
      ],
      total: 3,
      hasMore: false,
      start: 0,
    });
    await act(async () => {
      await useSessionStore.getState().selectSession('A');
    });
    expect(useSessionStore.getState().currentMessages.filter((message) => message.role === 'assistant'))
      .toEqual([msg('assistant', '## Answer\n\nbody')]);
  });

  it('renders each selected-session delta through Markdown once without the sidebar raw duplicate', () => {
    renderHook(() => useWebSocket());
    const chat = render(
      <MessageBubble message={{ role: 'assistant', content: '' }} />,
    );
    const sidebar = render(
      <SessionItem
        session={useSessionStore.getState().sessions.find((s) => s.id === 'A')!}
        isActive
      />,
    );

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'content.part', role: 'assistant', delta: true,
          turn_id: 'turn-render', stream_text: '## Answer\n\n',
          part: { type: 'text', text: '## Answer\n\n' },
        },
      });
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'content.part', role: 'assistant', delta: true,
          turn_id: 'turn-render', stream_text: '## Answer\n\n**body**',
          part: { type: 'text', text: '**body**' },
        },
      });
    });

    expect(useSessionStore.getState().currentMessages.filter((m) => m.role === 'assistant'))
      .toHaveLength(1);
    expect(useSessionStore.getState().currentMessages.at(-1)?.content)
      .toBe('## Answer\n\n**body**');
    chat.rerender(
      <MessageBubble message={useSessionStore.getState().currentMessages.at(-1)!} />,
    );
    expect(chat.container.querySelectorAll('.msg.assistant')).toHaveLength(1);
    expect(sidebar.container.querySelector('.text-xs.text-text-tertiary')).toBeNull();

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'A', workerId: 'w1',
        event: {
          type: 'assistant', final: true, turn_id: 'turn-render',
          item_id: 'completed-item',
          message: { content: [{ type: 'text', text: '## Answer\n\n**body**' }] },
        },
      });
      wsMock.trigger('worker.result', {
        type: 'worker.result', sessionId: 'A', workerId: 'w1',
        status: 'done', result: '## Answer\n\n**body**',
      });
    });

    expect(useSessionStore.getState().currentMessages.filter((m) => m.role === 'assistant'))
      .toHaveLength(1);
    chat.rerender(
      <MessageBubble message={useSessionStore.getState().currentMessages.find((m) => m.role === 'assistant')!} />,
    );
    expect(chat.container.querySelectorAll('.msg.assistant')).toHaveLength(1);
  });

  it('does not append background-session stream or result messages to the selected chat', () => {
    renderHook(() => useWebSocket());

    act(() => {
      wsMock.trigger('worker.stream', {
        type: 'worker.stream', sessionId: 'B', workerId: 'w1',
        event: {
          type: 'content.part', role: 'assistant', delta: true,
          turn_id: 'turn-background',
          part: { type: 'text', text: 'background' },
        },
      });
      wsMock.trigger('worker.result', {
        type: 'worker.result', sessionId: 'B', workerId: 'w1',
        status: 'done', result: 'background',
      });
    });

    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });
});
