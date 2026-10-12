import { beforeEach, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Message, Session } from '@/types';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(() => new Promise(() => {})),
}));
const meta = { workerId: 'worker-a', generation: 1, taskSeq: 1 };
const row = (role: string, content: string, nativeItemId?: string): Message => ({
  role,
  content,
  ...(nativeItemId ? { nativeItemId } : {}),
});
const initial = [row('user', 'question')];
const final = [
  initial[0]!,
  row('tool', 'persisted tool', 'tool-persisted'),
  row('assistant', 'final answer', 'body'),
];
function page(history: Message[], revision = 3, epoch = 'epoch') {
  return {
    history,
    total: history.length,
    start: 0,
    hasMore: false,
    historyEpoch: epoch,
    historyRevision: revision,
  };
}
function finish(revision = 3, scope: typeof meta & { executionSeq?: number } = meta) {
  useSessionStore.getState().reconcileWorkerResult(
    'A',
    {
      result: 'final answer',
      status: 'done',
      terminalCoverage: { historyEpoch: 'epoch', historyRevision: revision },
    },
    scope,
  );
  useSessionStore
    .getState()
    .appendWorkerResultMarker('A', row('system', 'DONE', 'worker.result:one'), scope, {
      historyEpoch: 'epoch',
      historyRevision: revision,
    });
}
function stream() {
  useSessionStore
    .getState()
    .applyLiveStream(
      'A',
      [
        row('tool', 'persisted tool', 'tool-persisted'),
        row('assistant', 'final answer', 'body'),
        row('thinking', 'transient reasoning', 'think-transient'),
        row('tool', 'transient tool progress', 'tool-transient'),
      ],
      meta,
    );
}

it('removes interrupted live fragments on restart while preserving canonical rows and local input', () => {
  const store = useSessionStore.getState;
  store().applyLiveStream('A', [row('assistant', 'old prefix', 'interrupted-body')], meta);
  store().appendLocalMessage('A', row('user', 'queued while running'));
  store().clearLiveStream('A');
  expect(store().currentMessages.map(message => message.content)).toEqual(['question', 'queued while running']);
  expect(store().sessionTranscripts.A!.runtime.some(message => message.nativeItemId === 'interrupted-body')).toBe(false);
});

it.each([false, true])('preserves persisted partial output at lifecycle replacement (terminal=%s)', terminal => {
  const store = useSessionStore.getState;
  const partial = row('assistant', 'persisted partial', 'partial-body');
  store().applyLiveStream('A', [partial, row('assistant', 'unpersisted prefix', 'orphan-body')], meta);
  store().applyHistoryPage('A', page([...initial, { ...partial, messageId: 'pan:persisted' }], 2));
  store().appendLocalMessage('A', row('user', 'keep input'));
  if (terminal) store().applyWorkerStatus('A', null, meta, true);
  else store().clearLiveStream('A');
  expect(store().currentMessages.map(message => message.content)).toEqual(['question', 'persisted partial', 'keep input']);
  store().applyWorkerStatus('A', 'running', { ...meta, generation: 2, taskSeq: 2 });
  store().applyLiveStream('A', [row('assistant', 'new body', 'new-body')], { ...meta, generation: 2, taskSeq: 2 });
  expect(store().currentMessages.some(message => message.nativeItemId === 'orphan-body')).toBe(false);
  expect(store().currentMessages.some(message => message.nativeItemId === 'new-body')).toBe(true);
});
beforeEach(() => {
  const session: Session = {
    id: 'A',
    name: 'A',
    adapter: 'codex',
    history: initial,
    historyTotal: 1,
    historyEpoch: 'epoch',
    historyRevision: 1,
    alwaysThinkingEnabled: false,
    effort: '',
    workerId: 'worker-a',
    workerStatus: 'running',
  };
  useSessionStore.setState({
    sessions: [session],
    currentSessionId: 'A',
    currentMessages: [],
    sessionTranscripts: {},
    liveStreamBuffers: {},
    terminalWatermarks: {},
    _historyRefreshSeq: {},
    serverEpoch: 'server',
  });
  useSessionStore.getState().applyHistoryPage('A', page(initial, 1));
});

it.each([false, true])(
  'removes non-durable blocks only after final task history is loaded (page before done=%s)',
  (beforeDone) => {
    stream();
    if (beforeDone) useSessionStore.getState().applyHistoryPage('A', page(final));
    finish();
    if (!beforeDone) useSessionStore.getState().applyHistoryPage('A', page(final));
    expect(useSessionStore.getState().currentMessages.map((message) => message.content)).toEqual([
      'question',
      'persisted tool',
      'final answer',
      'DONE',
    ]);
    expect(
      useSessionStore
        .getState()
        .sessionTranscripts.A!.runtime.some((message) => message.content.startsWith('transient')),
    ).toBe(false);
  },
);

it('retains transient rows while terminal history coverage is stale or incomplete', () => {
  stream();
  finish();
  useSessionStore.getState().applyHistoryPage('A', page(initial, 2));
  expect(
    useSessionStore
      .getState()
      .currentMessages.some((message) => message.content === 'transient reasoning'),
  ).toBe(true);
  useSessionStore
    .getState()
    .applyHistoryPage('A', { ...page([final[2]!]), total: 3, start: 2, hasMore: true });
  expect(
    useSessionStore
      .getState()
      .currentMessages.some((message) => message.content === 'transient reasoning'),
  ).toBe(true);
});

it('retains a completed scope when its loaded tail has no identifiable task boundary', () => {
  const tools = Array.from({ length: 60 }, (_, index) =>
    row('tool', `persisted tool ${index}`, `tool-${index}`));
  const history = [...initial, ...tools, row('assistant', 'final answer', 'body')];
  useSessionStore.getState().applyLiveStream('A', [
    ...tools, row('assistant', 'final answer', 'body'),
    row('thinking', 'transient old reasoning', 'old-reasoning'),
  ], meta);
  finish(history.length);
  useSessionStore.getState().applyHistoryPage('A', {
    ...page(history.slice(-50), history.length),
    total: history.length, start: history.length - 50, hasMore: true,
  });
  expect(useSessionStore.getState().currentMessages.map(message => message.content))
    .toContain('transient old reasoning');
});

it.each([false, true, 'later-completed'])('converges a task whose running status arrived before its history boundary was loaded (new task=%s)', (newTask) => {
  const scope = { ...meta, executionSeq: 2 };
  useSessionStore.setState({
    sessions: useSessionStore.getState().sessions.map(session => ({
      ...session, history: [], historyTotal: 0,
    })),
    sessionTranscripts: {}, currentMessages: [],
  });
  useSessionStore.getState().applyWorkerStatus('A', 'running', scope);
  useSessionStore.getState().applyHistoryPage('A', {
    ...page(initial, 5001), total: 5001, start: 5000, hasMore: true,
  });
  useSessionStore.getState().applyLiveStream('A', [
    row('tool', 'persisted tool', 'tool-persisted'),
    row('assistant', 'final answer', 'body'),
    row('thinking', '', 'old-reasoning'),
  ], scope);
  expect(useSessionStore.getState().liveStreamBuffers.A!.historyStartOffset).toBe(0);
  finish(5003, scope);
  if (newTask) {
    const next = { ...scope, taskSeq: 2, executionSeq: 3 };
    useSessionStore.getState().applyWorkerStatus('A', 'running', next);
    useSessionStore.getState().applyLiveStream('A', [
      row('thinking', 'new task reasoning', 'new-reasoning'),
    ], next);
  }
  const laterRows = newTask === 'later-completed' ? [
    row('user', 'later completed question'),
    row('assistant', 'later completed answer', 'later-body'),
  ] : [];
  useSessionStore.getState().applyHistoryPage('A', {
    ...page([...final, ...laterRows], 5003 + laterRows.length),
    total: 5003 + laterRows.length, start: 5000, hasMore: true,
  });
  expect(useSessionStore.getState().currentMessages.some(message =>
    message.nativeItemId === 'old-reasoning')).toBe(false);
  expect(useSessionStore.getState().currentMessages.map(message => message.content))
    .toEqual(['question', 'persisted tool', 'final answer', 'DONE',
      ...laterRows.map(message => message.content),
      ...(newTask ? ['new task reasoning'] : [])]);
});

it('keeps canonical thinking and tools even when their final text differs from live progress', () => {
  stream();
  finish(4);
  useSessionStore
    .getState()
    .applyHistoryPage(
      'A',
      page(
        [...final.slice(0, 2), row('thinking', 'durable reasoning', 'think-transient'), final[2]!],
        4,
      ),
    );
  const contents = useSessionStore.getState().currentMessages.map((message) => message.content);
  expect(contents).toContain('durable reasoning');
  expect(contents).toContain('persisted tool');
  expect(contents).not.toContain('transient reasoning');
  expect(contents).not.toContain('transient tool progress');
});

it('clears the empty orphan without discarding nonempty runtime outside the loaded task region', () => {
  useSessionStore.setState({
    sessions: useSessionStore.getState().sessions.map(session => ({
      ...session, history: [], historyTotal: 0,
    })),
    sessionTranscripts: {}, currentMessages: [],
  });
  useSessionStore.getState().applyWorkerStatus('A', 'running', meta);
  useSessionStore.getState().applyHistoryPage('A', {
    ...page(initial, 5001), total: 5001, start: 5000, hasMore: true,
  });
  useSessionStore.getState().applyLiveStream('A', [
    row('tool', 'older unloaded tool', 'unloaded-tool'),
    row('assistant', 'final answer', 'body'),
    row('thinking', '', 'empty-orphan'),
  ], meta);
  finish(5003);
  useSessionStore.getState().applyHistoryPage('A', {
    ...page(final, 5003), total: 5003, start: 5000, hasMore: true,
  });
  const contents = useSessionStore.getState().currentMessages.map(message => message.content);
  expect(contents).toContain('older unloaded tool');
  expect(useSessionStore.getState().currentMessages.some(message =>
    message.nativeItemId === 'empty-orphan')).toBe(false);
  expect(useSessionStore.getState().sessionTranscripts.A!.completedRuns).toHaveLength(1);
});

it('does not discard live progress for a legacy result without authoritative coverage', () => {
  stream();
  useSessionStore.getState().reconcileWorkerResult('A', { result: 'final answer' }, meta);
  useSessionStore.getState().applyHistoryPage('A', page(final));
  expect(
    useSessionStore
      .getState()
      .currentMessages.some((message) => message.content === 'transient reasoning'),
  ).toBe(true);
});

it('preserves a newer running task while converging the completed task', () => {
  stream();
  finish();
  const next = { ...meta, taskSeq: 2 };
  useSessionStore.getState().applyWorkerStatus('A', 'running', next);
  useSessionStore
    .getState()
    .applyLiveStream('A', [row('thinking', 'new task reasoning', 'new-think')], next);
  useSessionStore.getState().applyHistoryPage('A', page(final));
  const contents = useSessionStore.getState().currentMessages.map((message) => message.content);
  expect(contents).not.toContain('transient reasoning');
  expect(contents).toContain('new task reasoning');
  expect(contents).toContain('DONE');
});

it('converges consecutive completed tasks even when the first history response was delayed', () => {
  stream();
  finish();
  const next = { ...meta, taskSeq: 2 };
  useSessionStore.getState().applyWorkerStatus('A', 'running', next);
  useSessionStore
    .getState()
    .applyLiveStream(
      'A',
      [
        row('assistant', 'second answer', 'second-body'),
        row('tool', 'second transient', 'second-tool'),
      ],
      next,
    );
  useSessionStore
    .getState()
    .reconcileWorkerResult(
      'A',
      { result: 'second answer', terminalCoverage: { historyEpoch: 'epoch', historyRevision: 5 } },
      next,
    );
  useSessionStore
    .getState()
    .applyHistoryPage(
      'A',
      page(
        [
          ...final,
          row('user', 'second question'),
          row('assistant', 'second answer', 'second-body'),
        ],
        5,
      ),
    );
  const contents = useSessionStore.getState().currentMessages.map((message) => message.content);
  expect(contents).not.toContain('transient reasoning');
  expect(contents).not.toContain('second transient');
  expect(contents.filter((content) => content === 'second answer')).toHaveLength(1);
});
