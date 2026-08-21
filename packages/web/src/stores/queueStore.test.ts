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

function lastMsg(): string | undefined {
  const msgs = useSessionStore.getState().currentMessages;
  return msgs.length ? msgs[msgs.length - 1]?.content : undefined;
}

beforeEach(reset);

describe('queueStore enqueue', () => {
  it('adds an item when worker busy, persists to localStorage, does NOT touch messages', () => {
    setSession('running');
    const ok = useQueueStore.getState().enqueue('hello queue');
    expect(ok).toBe(true);
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('hello queue');
    expect(q[0]?.status).toBe('pending');
    expect(useSessionStore.getState().currentMessages.length).toBe(0);
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
  it('auto-sends the head when worker idle, adds user message, removes the item', () => {
    setSession('running');
    useQueueStore.getState().enqueue('one');
    useQueueStore.getState().enqueue('two');
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(2);

    setSession('idle');
    useQueueStore.getState().flush();
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('two');
    expect(lastMsg()).toBe('one');
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledWith({
      type: 'user_inject',
      sessionId: 's1',
      text: 'one',
    });
  });

  it('keeps the head on send failure (WS closed)', () => {
    setSession('running');
    useQueueStore.getState().enqueue('keep me');
    vi.mocked(wsClient.send).mockReturnValue(false);
    setSession('idle');
    useQueueStore.getState().flush();
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('keep me');
    expect(useSessionStore.getState().currentMessages.length).toBe(0);
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
  it('concatenates all messages into one when enabled', () => {
    setSession('running');
    useQueueStore.getState().enqueue('first');
    useQueueStore.getState().enqueue('second');
    useQueueStore.getState().toggleBatchSend(); // 开启（worker running，暂不发送）
    expect(useQueueStore.getState().batchSend['s1']).toBe(true);
    expect(localStorage.getItem('pan.sendQueue.batch.s1')).toBe('1');

    setSession('idle');
    useQueueStore.getState().flush();
    expect((useQueueStore.getState().queues['s1'] ?? []).length).toBe(0);
    expect(lastMsg()).toBe('first\n\nsecond');
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(wsClient.send)).toHaveBeenCalledWith({
      type: 'user_inject',
      sessionId: 's1',
      text: 'first\n\nsecond',
    });
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
