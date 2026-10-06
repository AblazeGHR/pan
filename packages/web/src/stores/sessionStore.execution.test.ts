import { beforeEach, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Session } from '@/types';

vi.mock('@/services/api', async original => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(() => new Promise(() => {})),
}));
const scope = { workerId: 'w', generation: 1 };
const store = () => useSessionStore.getState();
beforeEach(() => {
  const session: Session = { id: 'A', name: 'A', history: [],
    alwaysThinkingEnabled: false, effort: '', workerId: 'w', workerStatus: 'idle' };
  useSessionStore.setState({ sessions: [session, { ...session, id: 'B' }],
    currentSessionId: 'A', currentMessages: [], sessionTranscripts: {},
    liveStreamBuffers: {}, terminalWatermarks: {}, unscopedReplayPending: {},
    _historyRefreshSeq: {}, serverEpoch: null });
});

it('accepts first report execution after a persisted legacy task terminal', () => {
  store().reconcileWorkerResult('A', { status: 'done', result: 'old' },
    { ...scope, taskSeq: 100, terminalKey: 'old' });
  expect(store().applyWorkerStatus('A', 'running', { ...scope, executionSeq: 1 })).toBe(true);
  expect(store().applyLiveStream('A', [{ role: 'assistant', content: 'new report' }],
    { ...scope, executionSeq: 1 })).toBe(true);
  expect(store().currentMessages.at(-1)?.content).toBe('new report');
});

it('separates consecutive reports and rejects completed and unscoped late frames', () => {
  for (let executionSeq = 1; executionSeq <= 3; executionSeq++) {
    const meta = { ...scope, executionSeq };
    expect(store().applyWorkerStatus('A', 'running', meta)).toBe(true);
    expect(store().applyLiveStream('A', [{ role: 'assistant', content: `answer ${executionSeq}` }], meta)).toBe(true);
    expect(store().reconcileWorkerResult('A', { result: `answer ${executionSeq}`, status: 'done' },
      { ...meta, resultCursor: executionSeq, terminalKey: `report-${executionSeq}` })).toBe(true);
    expect(store().canApplyLiveStream('A', meta)).toBe(false);
  }
  expect(store().canApplyLiveStream('A', scope)).toBe(false);
  expect(store().canApplyLiveStream('A', { ...scope, executionSeq: 1 })).toBe(false);
  expect(store().currentMessages.filter(m => m.role === 'assistant').map(m => m.content))
    .toEqual(['answer 1', 'answer 2', 'answer 3']);
});

it('keeps a background report out of selected chat and retains its own buffer', () => {
  useSessionStore.setState({ currentSessionId: 'B' });
  store().applyWorkerStatus('A', 'running', { ...scope, executionSeq: 2 });
  store().applyLiveStream('A', [{ role: 'assistant', content: 'background report' }],
    { ...scope, executionSeq: 2 });
  expect(store().currentMessages).toEqual([]);
  expect(store().liveStreamBuffers.A?.messages[0]?.content).toBe('background report');
});

it('allows a reset execution counter only on a newer worker generation', () => {
  store().reconcileWorkerResult('A', { result: 'old generation', status: 'done' },
    { ...scope, executionSeq: 50 });
  expect(store().canApplyLiveStream('A', { ...scope, executionSeq: 1 })).toBe(false);
  expect(store().canApplyLiveStream('A', { ...scope, generation: 2, executionSeq: 1 })).toBe(true);
  store().applyWorkerStatus('A', 'running', { ...scope, executionSeq: 51 });
  store().applyLiveStream('A', [{ role: 'assistant', content: 'old partial' }],
    { ...scope, executionSeq: 51 });
  store().applyWorkerStatus('A', 'running', { ...scope, generation: 2, executionSeq: 1 });
  expect(store().getLiveStreamMessages('A')).toEqual([]);
});

it('uses execution ordering for delayed running summary snapshots', () => {
  store().reconcileWorkerResult('A', { result: 'report complete', status: 'done' },
    { ...scope, executionSeq: 2 });
  store().updateSession('A', { workerId: 'w', workerGeneration: 1,
    workerExecutionSeq: 2, workerStatus: 'running' }, true);
  expect(store().sessions[0]?.workerStatus).toBe('idle');
  store().updateSession('A', { workerId: 'w', workerGeneration: 1,
    workerExecutionSeq: 3, workerStatus: 'running' }, true);
  expect(store().sessions[0]?.workerStatus).toBe('running');
});
