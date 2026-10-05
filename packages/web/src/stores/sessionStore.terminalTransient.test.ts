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
function finish(revision = 3) {
  useSessionStore.getState().reconcileWorkerResult(
    'A',
    {
      result: 'final answer',
      status: 'done',
      terminalCoverage: { historyEpoch: 'epoch', historyRevision: revision },
    },
    meta,
  );
  useSessionStore
    .getState()
    .appendWorkerResultMarker('A', row('system', 'DONE', 'worker.result:one'), meta, {
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
