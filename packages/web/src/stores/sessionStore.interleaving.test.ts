// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Message, Session } from '@/types';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(() => new Promise(() => {})),
}));
const row = (content: string): Message => ({ role: 'assistant', content });
const meta = { workerId: 'w', generation: 0, taskSeq: 1 };
const store = () => useSessionStore.getState();
const texts = () => store().currentMessages.map(m => m.content);
beforeEach(() => {
  const sessions = ['A', 'B'].map(id => ({ id, name: id, history: [], historyTotal: 0,
    alwaysThinkingEnabled: false, effort: '' }) as Session);
  useSessionStore.setState({ sessions, currentSessionId: 'A', currentMessages: [],
    sessionTranscripts: {}, liveStreamBuffers: {}, terminalWatermarks: {},
    unscopedReplayPending: {}, _selectionSeq: {}, _historyRefreshSeq: {}, serverEpoch: null });
  useSessionStore.setState({ _pendingQueueIds: {}, _deliveredQueueIds: {} });
});

it('history refresh between deltas never adopts the displayed clone as a second runtime row', () => {
  store().applyLiveStream('A', [row('one')], meta);
  store().applyLiveStream('A', [row('one two')], meta);
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'question' }],
    start: 0, total: 1, hasMore: false, historyRevision: 1, historyEpoch: 'h' });
  expect(texts()).toEqual(['question', 'one two']);
  store().applyLiveStream('A', [row('one two three')], meta);
  expect(texts()).toEqual(['question', 'one two three']);
  void store().selectSession('B');
  void store().selectSession('A');
  expect(texts()).toEqual(['question', 'one two three']);
});

it('converges a Steer user row inserted ahead of an unfinished tool by its message id', () => {
  const tool = (id: string, content: string): Message => ({
    role: 'tool', content, nativeItemId: id,
  });
  const first = tool('tool-1', 'first');
  const second = tool('tool-2', 'second');
  const open = tool('tool-3', 'still streaming');
  store().applyLiveStream('A', [first, second, open], meta);
  store().appendLocalMessage('A', {
    role: 'user', content: 'steer now', messageId: 'steer:one',
  });
  store().applyHistoryPage('A', {
    history: [first, second, { role: 'user', content: 'steer now', messageId: 'steer:one' }],
    start: 0, total: 3, hasMore: false, historyRevision: 3, historyEpoch: 'h',
  });
  expect(texts()).toEqual(['first', 'second', 'steer now', 'still streaming']);
  store().applyLiveStream('A', [first, second, tool('tool-3', 'still streaming more')], meta);
  expect(texts()).toEqual(['first', 'second', 'steer now', 'still streaming more']);
});

it('converges a legacy id-less Steer row only within its local history boundary', () => {
  const tool: Message = { role: 'tool', content: 'still streaming', nativeItemId: 'tool-1' };
  store().applyLiveStream('A', [tool], meta);
  store().appendLocalMessage('A', { role: 'user', content: 'steer now' });
  store().applyHistoryPage('A', {
    history: [{ role: 'user', content: 'steer now' }],
    start: 0, total: 1, hasMore: false, historyRevision: 1, historyEpoch: 'h',
  });
  expect(texts()).toEqual(['steer now', 'still streaming']);
});

it('converges an assistant item moved ahead of an interleaved tool by native id', () => {
  const first: Message = { role: 'assistant', content: 'same text', nativeItemId: 'answer-1' };
  const second: Message = { role: 'assistant', content: 'same text', nativeItemId: 'answer-2' };
  const tool: Message = { role: 'tool', content: 'tool output', nativeItemId: 'tool-1' };
  store().applyLiveStream('A', [first, tool, second], meta);
  store().applyHistoryPage('A', {
    history: [first, second, tool], start: 0, total: 3, hasMore: false,
    historyRevision: 3, historyEpoch: 'h',
  });
  expect(store().currentMessages.map((message) => message.nativeItemId))
    .toEqual(['answer-1', 'answer-2', 'tool-1']);
});

it('converges an id-less canonical assistant after a Session switch and tool reorder', () => {
  const answer: Message = { role: 'assistant', content: 'finished text', nativeItemId: 'answer-1' };
  const reasoning: Message = { role: 'thinking', content: 'reasoning', nativeItemId: 'reason-1' };
  const tool: Message = { role: 'tool', content: 'still streaming', nativeItemId: 'tool-1' };
  const nonDurable: Message = { role: 'tool', content: 'live only', nativeItemId: 'diff-1' };
  store().applyLiveStream('A', [nonDurable, reasoning, answer, tool], meta);
  void store().selectSession('B');
  void store().selectSession('A');
  store().applyHistoryPage('A', {
    history: [
      { role: 'thinking', content: 'reasoning' },
      { role: 'assistant', content: 'finished text' },
      tool,
    ],
    start: 0, total: 3, hasMore: false, historyRevision: 3, historyEpoch: 'h',
  });
  expect(texts()).toEqual(['reasoning', 'finished text', 'still streaming', 'live only']);
});

it('switching away and back invalidates cached displayed clones without duplicating the next delta', () => {
  store().applyLiveStream('A', [row('one')], meta);
  store().applyLiveStream('A', [row('one two')], meta);
  void store().selectSession('B');
  void store().selectSession('A');
  store().applyLiveStream('A', [row('one two three')], meta);
  expect(texts()).toEqual(['one two three']);
});

it('completed runtime rows remain anchored when history is refreshed repeatedly then another task starts', () => {
  for (let taskSeq = 1; taskSeq <= 5; taskSeq++) {
    store().applyLiveStream('A', [row(`answer-${taskSeq}`)], { ...meta, taskSeq });
    store().reconcileWorkerResult('A', { result: `answer-${taskSeq}`, status: 'done' }, { ...meta, taskSeq });
    const history = Array.from({ length: taskSeq }, (_, i) => row(`answer-${i + 1}`));
    store().applyHistoryPage('A', { history, start: 0, total: taskSeq, hasMore: false,
      historyRevision: taskSeq, historyEpoch: 'h' });
    void store().selectSession('B');
    void store().selectSession('A');
    expect(texts()).toEqual(history.map(m => m.content));
  }
});

it('moves a delivered queue user from after DONE onto its canonical task boundary across A/B/A', () => {
  const task = { ...meta, taskSeq: 7 };
  store().applyLiveStream('A', [row('answer')], task);
  store().reconcileWorkerResult('A', { result: 'answer', status: 'done' }, task);
  store().addMessage({ role: 'system', content: '[DONE] Task completed',
    nativeItemId: 'worker.result:A:7' });
  // The hand-off WS event can trail worker.result even though the backend
  // writes the user row before the answer in durable history.
  store().appendQueuedMessage('A', { id: 'q-done-boundary', text: 'queued prompt' });
  store().appendDeliveredMessages('A', [{ role: 'user', content: 'queued prompt',
    queueItemIds: ['q-done-boundary'] }]);
  expect(texts()).toEqual(['answer', '[DONE] Task completed', 'queued prompt']);

  store().applyHistoryPage('A', { history: [
    { role: 'user', content: 'queued prompt' }, row('answer'),
  ], start: 0, total: 2, hasMore: false, historyEpoch: 'h', historyRevision: 2 });
  expect(texts()).toEqual(['queued prompt', 'answer', '[DONE] Task completed']);
  void store().selectSession('B');
  void store().selectSession('A');
  expect(texts()).toEqual(['queued prompt', 'answer', '[DONE] Task completed']);
  expect(texts().filter((text) => text === 'queued prompt')).toHaveLength(1);
});

it('keeps agent injected canonical user rows before DONE without creating a live duplicate', () => {
  const task = { ...meta, taskSeq: 8 };
  store().applyLiveStream('A', [row('agent answer')], task);
  store().reconcileWorkerResult('A', { result: 'agent answer', status: 'done' }, task);
  store().addMessage({ role: 'system', content: '[DONE] Task completed',
    nativeItemId: 'worker.result:A:8' });
  store().applyHistoryPage('A', { history: [
    { role: 'user', content: 'injected instruction', messageId: 'agent-msg-8' },
    row('agent answer'),
  ], start: 0, total: 2, hasMore: false, historyEpoch: 'h', historyRevision: 2 });
  expect(texts()).toEqual(['injected instruction', 'agent answer', '[DONE] Task completed']);
  void store().selectSession('B');
  void store().selectSession('A');
  expect(texts()).toEqual(['injected instruction', 'agent answer', '[DONE] Task completed']);
});

it('converges a real Codex terminal reorder before the next same-text turn', () => {
  const first = { workerId: 'w', generation: 0, taskSeq: 1 };
  const second = { ...first, taskSeq: 2 };
  store().appendQueuedMessage('A', { id: 'q-first', text: 'first question' });
  store().appendDeliveredMessages('A', [{
    role: 'user', content: 'first question', queueItemIds: ['q-first'],
  }]);
  store().applyWorkerStatus('A', 'running', first);
  // The browser observed the completed answer before a reasoning item. The
  // persisted history puts reasoning before that answer and also includes an
  // earlier assistant item that this browser never streamed.
  store().applyLiveStream('A', [
    { role: 'assistant', content: 'first final', nativeItemId: 'answer-1' },
    { role: 'thinking', content: 'reasoning', nativeItemId: 'reason-1' },
  ], first);
  store().reconcileWorkerResult('A', { result: 'first final', status: 'done' }, first);
  store().addMessage({
    role: 'system', content: '[DONE] Task completed', nativeItemId: 'worker.result:A:1',
  });
  const firstHistory: Message[] = [
    { role: 'user', content: 'first question' },
    { role: 'assistant', content: 'interim answer' },
    { role: 'thinking', content: 'reasoning' },
    { role: 'assistant', content: 'first final' },
  ];
  store().applyHistoryPage('A', {
    history: firstHistory, start: 0, total: 4, hasMore: false,
    historyEpoch: 'h', historyRevision: 4,
  });
  expect(texts()).toEqual([
    'first question', 'interim answer', 'reasoning', 'first final', '[DONE] Task completed',
  ]);

  store().appendQueuedMessage('A', { id: 'q-second', text: 'same question' });
  store().appendDeliveredMessages('A', [{
    role: 'user', content: 'same question', queueItemIds: ['q-second'],
  }]);
  store().applyWorkerStatus('A', 'running', second);
  store().applyLiveStream('A', [
    { role: 'assistant', content: 'same reply', nativeItemId: 'answer-2' },
  ], second);
  store().reconcileWorkerResult('A', { result: 'same reply', status: 'done' }, second);
  store().addMessage({
    role: 'system', content: '[DONE] Task completed', nativeItemId: 'worker.result:A:2',
  });
  store().applyHistoryPage('A', {
    history: [
      ...firstHistory,
      { role: 'user', content: 'same question' },
      { role: 'assistant', content: 'same reply' },
    ],
    start: 0, total: 6, hasMore: false, historyEpoch: 'h', historyRevision: 6,
  });
  expect(texts()).toEqual([
    'first question', 'interim answer', 'reasoning', 'first final', '[DONE] Task completed',
    'same question', 'same reply', '[DONE] Task completed',
  ]);
});

it('a queued second user row keeps its position after the first completed turn', () => {
  store().appendQueuedMessage('A', { id: 'q1', text: 'question-1' });
  store().appendDeliveredMessages('A', [{ role: 'user', content: 'question-1', queueItemIds: ['q1'] }]);
  store().applyLiveStream('A', [row('answer-1')], meta);
  store().reconcileWorkerResult('A', { result: 'answer-1', status: 'done' }, meta);
  store().addMessage({ role: 'system', content: '[DONE] Task completed' });
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'question-1' }, row('answer-1')],
    start: 0, total: 2, hasMore: false, historyRevision: 2, historyEpoch: 'h' });
  store().appendQueuedMessage('A', { id: 'q2', text: 'question-2' });
  store().appendDeliveredMessages('A', [{ role: 'user', content: 'question-2', queueItemIds: ['q2'] }]);
  store().applyLiveStream('A', [row('answer-2')], { ...meta, taskSeq: 2 });
  store().reconcileWorkerResult('A', { result: 'answer-2', status: 'done' }, { ...meta, taskSeq: 2 });
  expect(texts()).toEqual(['question-1', 'answer-1', '[DONE] Task completed', 'question-2', 'answer-2']);
});

it('editing and removing pending messages also updates the background transcript', () => {
  store().appendQueuedMessage('A', { id: 'q1', text: 'original' });
  void store().selectSession('B');
  store().updateQueuedMessage('A', { id: 'q1', text: 'edited' });
  void store().selectSession('A');
  expect(texts()).toEqual(['edited']);
  void store().selectSession('B');
  store().removeQueuedMessage('A', 'q1');
  void store().selectSession('A');
  expect(texts()).toEqual([]);
});

it('background delivery replaces the pending row and survives repeated delivery', () => {
  store().appendQueuedMessage('A', { id: 'q1', text: 'question' });
  void store().selectSession('B');
  const message = { role: 'user', content: 'delivered question', queueItemIds: ['q1'] };
  store().appendDeliveredMessages('A', [message]);
  store().appendDeliveredMessages('A', [message]);
  void store().selectSession('A');
  expect(texts()).toEqual(['delivered question']);
});

it('unchanged live blocks keep their rendered object references', () => {
  const first = row('first item');
  const second = row('second item');
  store().applyLiveStream('A', [first, second], meta);
  const rendered = store().currentMessages[0];
  store().applyLiveStream('A', [first, row('second item grows')], meta);
  expect(store().currentMessages[0]).toBe(rendered);
});
