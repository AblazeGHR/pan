// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useQueueStore } from './queueStore';
import { useSessionStore } from './sessionStore';
import { useUIStore } from './uiStore';
import { wsClient } from '@/services/ws';

vi.mock('@/services/ws', () => ({
  wsClient: {
    send: vi.fn(() => true),
    isOpen: true,
  },
}));

function setSession(workerStatus = 'running', workerId: string | null = 'w1') {
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [
      {
        id: 's1',
        name: 'Test',
        adapter: 'cbc',
        model: null,
        permissionMode: null,
        alwaysThinkingEnabled: false,
        effort: '',
        workerStatus,
        workerId,
        history: [],
      },
    ],
  });
}

/** 仅更新 worker 状态，保留 currentMessages（真实 app 中状态事件不会清空聊天区）。 */
function setWorkerStatus(workerStatus: string, workerId: string | null = 'w1') {
  useSessionStore.setState((s) => ({
    sessions: s.sessions.map((x) =>
      x.id === 's1' ? { ...x, workerStatus, workerId } : x,
    ),
  }));
}

function reset() {
  localStorage.clear();
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
  });
  useQueueStore.setState({ queues: {}, edits: {}, batchSend: {}, sendingId: null, panelOpen: false });
  useUIStore.setState({ toastQueue: [] });
  vi.mocked(wsClient.send).mockClear();
  vi.mocked(wsClient.send).mockReturnValue(true);
}

beforeEach(reset);

describe('queueStore enqueue', () => {
  it('adds an item when worker busy, persists to localStorage, AND optimistically shows it in the chat', () => {
    setSession('running');
    const ok = useQueueStore.getState().enqueue('hello queue');
    expect(ok).toBe(true);
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('hello queue');
    expect(q[0]?.status).toBe('pending');
    // 与 InputRow 直接发送一致：入队即上屏（乐观），不等 worker idle 后 flush 才显示
    const msgs = useSessionStore.getState().currentMessages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatchObject({ role: 'user', content: 'hello queue' });
    const raw = localStorage.getItem('pan.sendQueue.s1');
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string)[0].text).toBe('hello queue');
  });

  it('rejects beyond the 50-item cap', () => {
    setSession('running');
    const many: unknown[] = [];
    for (let i = 0; i < 50; i++) {
      many.push({ id: 'id' + i, text: 'm' + i, createdAt: Date.now(), status: 'pending' });
    }
    localStorage.setItem('pan.sendQueue.s1', JSON.stringify(many));
    useQueueStore.getState().loadForSession('s1');
    const ok = useQueueStore.getState().enqueue('overflow');
    expect(ok).toBe(false);
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(50);
  });
});

describe('queueStore flush', () => {
  it('auto-sends the head when worker idle, keeps the optimistic chat message, removes the item (no duplicate)', () => {
    setSession('running');
    useQueueStore.getState().enqueue('one');
    useQueueStore.getState().enqueue('two');
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(2);
    // 入队即上屏：两条乐观消息都在聊天里
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['one', 'two']);

    setWorkerStatus('idle');
    useQueueStore.getState().flush();
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('two');
    // 'one' 已发送 → 不重复追加（入队时那条乐观消息就是它）
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['one', 'two']);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledWith({
      type: 'user_inject',
      sessionId: 's1',
      text: 'one',
    });
  });

  it('keeps the head on send failure (WS closed); optimistic chat message stays', () => {
    setSession('running');
    useQueueStore.getState().enqueue('keep me');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['keep me']);
    vi.mocked(wsClient.send).mockReturnValue(false);
    setWorkerStatus('idle');
    useQueueStore.getState().flush();
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('keep me');
    // 发送失败不撤乐观消息：聊天里保持入队时的样子（与直接发送失败一致）
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['keep me']);
  });

  it('does not flush while a single-flight send is in progress', () => {
    setSession('running');
    useQueueStore.getState().enqueue('a');
    useQueueStore.getState().enqueue('b');
    // 模拟已有 1 条在发送中
    useQueueStore.setState({ sendingId: 'some-id' });
    setSession('idle');
    useQueueStore.getState().flush();
    expect(vi.mocked(wsClient.send)).not.toHaveBeenCalled();
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(2);
  });
});

describe('queueStore batch send', () => {
  it('concatenates all messages into one when enabled (N optimistic rows collapse to one combined)', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    useQueueStore.getState().toggleBatchSend(); // 开启（worker running，暂不发送）
    expect(useQueueStore.getState().batchSend['s1']).toBe(true);
    expect(localStorage.getItem('pan.sendQueue.batch.s1')).toBe('1');
    // 入队即上屏：两条乐观消息
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['first', 'second']);

    setWorkerStatus('idle');
    useQueueStore.getState().flush();
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(0);
    // 批量发送后：N 条乐观消息替换为 1 条合并消息（与服务端一致，避免聊天里两条、
    // 服务端一条的分叉）
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'first\n\nsecond',
    ]);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledWith({
      type: 'user_inject',
      sessionId: 's1',
      text: 'first\n\nsecond',
    });
  });
});

describe('queueStore optimistic chat rows', () => {
  it('remove() takes the optimistic chat message back out (no ghost)', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'first',
      'second',
    ]);

    const firstId = (useQueueStore.getState().queues['s1'] ?? [])[0]?.id as string;
    useQueueStore.getState().remove(firstId);
    expect((useQueueStore.getState().queues['s1'] ?? []).map((x) => x.text)).toEqual(['second']);
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['second']);
  });

  it('startEdit() removes the optimistic row; saveEdit() re-adds it with the edited text', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().startEdit(secondId);
    // 编辑中的条目不再「待发送」→ 乐观消息撤掉
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual(['first']);

    useQueueStore.getState().updateEditDraft('second v2');
    useQueueStore.getState().saveEdit();
    // 保存重新入队 → 新的乐观消息（编辑后文本）
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'first',
      'second v2',
    ]);
  });

  it('cancelEdit() restores the optimistic row with the original text', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().startEdit(secondId);
    useQueueStore.getState().updateEditDraft('typo');
    useQueueStore.getState().cancelEdit();
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'first',
      'second',
    ]);
  });

  it('clear() removes all optimistic rows', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'first',
      'second',
    ]);

    useQueueStore.getState().clear();
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(0);
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });
});

describe('queueStore edit (dequeue first)', () => {
  it('startEdit removes the item from queue; saveEdit reinserts at original index with new text', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().startEdit(secondId);
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('first');
    // 编辑态已持久化（刷新可恢复）
    expect(localStorage.getItem('pan.sendQueue.editing.s1')).toBeTruthy();

    useQueueStore.getState().updateEditDraft('second v2');
    useQueueStore.getState().saveEdit();
    const q2 = useQueueStore.getState().queues['s1'] ?? [];
    expect(q2.length).toBe(2);
    expect(q2[1]?.text).toBe('second v2');
    expect(q2[1]?.id).toBe(secondId);
    expect(localStorage.getItem('pan.sendQueue.editing.s1')).toBeNull();
  });

  it('cancelEdit restores the original value at original index', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().startEdit(secondId);
    useQueueStore.getState().updateEditDraft('typo draft');
    useQueueStore.getState().cancelEdit();

    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(2);
    expect(q[1]?.text).toBe('second'); // 原值恢复
    expect(q[1]?.id).toBe(secondId);
  });

  it('starting a new edit restores the previous one first (no data loss)', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const firstId = (useQueueStore.getState().queues['s1'] ?? [])[0]?.id as string;
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().startEdit(secondId);
    useQueueStore.getState().startEdit(firstId);

    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.id).toBe(secondId); // 旧的编辑已恢复
    expect(useQueueStore.getState().edits['s1']?.id).toBe(firstId); // 新的编辑进行中
  });

  it('restores editing state from localStorage after reload', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;
    useQueueStore.getState().startEdit(secondId);
    useQueueStore.getState().updateEditDraft('in-progress draft');

    // 模拟刷新：清空内存镜像后重新 loadForSession
    useQueueStore.setState({ queues: {}, edits: {}, batchSend: {} });
    useQueueStore.getState().loadForSession('s1');

    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('first');
    const edit = useQueueStore.getState().edits['s1'];
    expect(edit?.id).toBe(secondId);
    expect(edit?.text).toBe('in-progress draft');
    expect(edit?.index).toBe(1);
  });
});

describe('queueStore move / clear / removeSession', () => {
  it('move swaps adjacent items and persists', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    const secondId = (useQueueStore.getState().queues['s1'] ?? [])[1]?.id as string;

    useQueueStore.getState().move(secondId, -1);
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q[0]?.text).toBe('second');
    expect(q[1]?.text).toBe('first');
    const persisted = JSON.parse(localStorage.getItem('pan.sendQueue.s1') as string);
    expect(persisted[0].text).toBe('second');
  });

  it('clear empties the queue', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().clear();
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(0);
    expect(JSON.parse(localStorage.getItem('pan.sendQueue.s1') as string).length).toBe(0);
  });

  it('removeSession cleans up orphan localStorage keys and store state', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().toggleBatchSend();
    useQueueStore.getState().removeSession('s1');
    expect(localStorage.getItem('pan.sendQueue.s1')).toBeNull();
    expect(localStorage.getItem('pan.sendQueue.batch.s1')).toBeNull();
    expect(useQueueStore.getState().queues['s1']).toBeUndefined();
  });
});
