// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { useSessionStore } from './sessionStore';
import { useWorkerStore } from './workerStore';
import type { Session } from '@/types';

const api = vi.hoisted(() => ({ fetchSessions: vi.fn(), listWorkers: vi.fn() }));
vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  ...api,
  fetchSessionHistory: vi.fn(() => new Promise(() => {})),
}));
const session = (extra: Partial<Session> = {}): Session => ({ id: 'A', name: 'A',
  history: [], alwaysThinkingEnabled: false, effort: '', ...extra });
const store = () => useSessionStore.getState();
beforeEach(() => {
  api.fetchSessions.mockReset(); api.listWorkers.mockReset();
  useSessionStore.setState({ sessions: [session()], currentSessionId: 'A',
    currentMessages: [], sessionTranscripts: {}, liveStreamBuffers: {},
    terminalWatermarks: {}, serverEpoch: null, _sessionWsTouchedSeq: {},
    _sessionEventPatches: {}, _sessionLocalTouchedSeq: {}, _sessionSettingsTouchedSeq: {},
    sessionSettingMutations: {}, _selectionSeq: {}, _historyRefreshSeq: {} });
  useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null,
    runtimeEpoch: null, workerTouchedSeq: {}, refreshSeq: 0 });
});

it('does not use the history summary revision to keep a Worker falsely running', async () => {
  useSessionStore.setState({ sessions: [session({ workerId: 'w', workerStatus: 'running',
    summaryRevision: 12, historyTotal: 12, lastMessage: 'newest history' })] });
  api.fetchSessions.mockResolvedValue([session({ workerId: 'w', workerStatus: 'idle',
    summaryRevision: 11, historyTotal: 11, lastMessage: 'older history' })]);
  await store().loadSessions();
  expect(store().sessions[0]).toMatchObject({ workerStatus: 'idle',
    summaryRevision: 12, historyTotal: 12, lastMessage: 'newest history' });
});

it('keeps the authoritative replacement Worker id after a plain list refresh', async () => {
  useSessionStore.setState({ sessions: [session({ workerId: 'old', workerStatus: 'idle' })] });
  api.fetchSessions.mockResolvedValue([session({ workerId: 'new', workerStatus: 'running', workerGeneration: 2 })]);
  await store().loadSessions();
  expect(store().sessions[0]).toMatchObject({ workerId: 'new', workerStatus: 'running' });
});

it('accepts a new generation after a previously observed destroyed Worker', async () => {
  useSessionStore.setState({ sessions: [session({ workerId: 'old', workerStatus: 'running', workerGeneration: 1 })] });
  store().applyWorkerStatus('A', null, { workerId: 'old', generation: 1 }, true);
  api.fetchSessions.mockResolvedValue([session({ workerId: 'new', workerStatus: 'running', workerGeneration: 2 })]);
  await store().loadSessions();
  expect(store().sessions[0]).toMatchObject({ workerId: 'new', workerStatus: 'running' });
});

it('lets an epoch-changing history page replace the old tail despite earlier metadata', () => {
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'old user' },
    { role: 'assistant', content: 'old answer' }], start: 0, total: 2,
    hasMore: false, historyEpoch: 'old', historyRevision: 10 });
  store().updateSession('A', { historyEpoch: 'new', historyRevision: 11 });
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'replacement' }],
    start: 0, total: 1, hasMore: false, historyEpoch: 'new', historyRevision: 11 });
  expect(store().currentMessages.map(row => row.content)).toEqual(['replacement']);
});

it('does not discard a live row when a stale epoch hint precedes an unchanged page', () => {
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'question' }],
    start: 0, total: 1, hasMore: false, historyEpoch: 'current', historyRevision: 10 });
  store().applyLiveStream('A', [{ role: 'assistant', content: 'still growing' }],
    { workerId: 'w', generation: 1, taskSeq: 1 });
  store().updateSession('A', { historyEpoch: 'stale', historyRevision: 9 });
  store().applyHistoryPage('A', { history: [{ role: 'user', content: 'question' }],
    start: 0, total: 1, hasMore: false, historyEpoch: 'current', historyRevision: 10 });
  expect(store().currentMessages.map(row => row.content)).toEqual(['question', 'still growing']);
});

it('does not carry native status or usage from a replaced Worker', () => {
  useWorkerStore.getState().updateWorker('A', 'old', 'running', 1);
  useWorkerStore.getState().updateNativeStatus('A', 'old', { type: 'busy' });
  useWorkerStore.getState().updateNativeUsage('A', 'old', { inputTokens: 100 });
  useWorkerStore.getState().updateWorker('A', 'new', 'running', 2);
  expect(useWorkerStore.getState().workers.A?.nativeStatus).toBeUndefined();
  expect(useWorkerStore.getState().workers.A?.nativeUsage).toBeUndefined();
});


it('ignores older Worker generation and task metadata without losing newer history', async () => {
  useSessionStore.setState({ sessions: [session({ workerId: 'new', workerStatus: 'running',
    workerGeneration: 2, workerTaskSeq: 3, lastLegalWorkerState: 'running' })] });
  api.fetchSessions.mockResolvedValue([session({ workerId: 'old', workerStatus: 'idle',
    workerGeneration: 1, workerTaskSeq: 10, lastLegalWorkerState: 'idle', summaryRevision: 20 })]);
  await store().loadSessions();
  expect(store().sessions[0]).toMatchObject({ workerId: 'new', workerStatus: 'running',
    workerGeneration: 2, workerTaskSeq: 3, summaryRevision: 20 });
});

it('ignores native envelopes belonging to a replaced Worker', () => {
  useWorkerStore.getState().updateWorker('A', 'new', 'running', 2);
  useWorkerStore.getState().updateNativeStatus('A', 'old', { type: 'busy' });
  useWorkerStore.getState().updateNativeUsage('A', 'old', { inputTokens: 100 });
  useWorkerStore.getState().updateNativeRateLimits('A', 'old', { stale: true });
  expect(useWorkerStore.getState().workers.A).toEqual({ id: 'new', sessionId: 'A',
    status: 'running', generation: 2 });
});


it('does not resurrect a completed task through a late running summary', async () => {
  store().applyWorkerStatus('A', 'running', { workerId: 'w', generation: 1, taskSeq: 3 });
  store().reconcileWorkerResult('A', { status: 'done', result: 'complete' },
    { workerId: 'w', generation: 1, taskSeq: 3 });
  store().applyWorkerStatus('A', 'idle', { workerId: 'w', generation: 1, taskSeq: 3,
    lastLegalWorkerState: 'idle' });
  api.fetchSessions.mockResolvedValue([session({ workerId: 'w', workerStatus: 'running',
    workerGeneration: 1, workerTaskSeq: 3, lastLegalWorkerState: 'running' })]);
  await store().loadSessions();
  expect(store().sessions[0]).toMatchObject({ workerStatus: 'idle', lastLegalWorkerState: 'idle' });
  api.fetchSessions.mockResolvedValue([session({ workerId: 'w', workerStatus: 'running',
    workerGeneration: 1, workerTaskSeq: 4, lastLegalWorkerState: 'running' })]);
  await store().loadSessions();
  expect(store().sessions[0]?.workerStatus).toBe('running');
});


it('clears native turn metadata when the same Worker id restarts in a new generation', () => {
  useWorkerStore.getState().updateWorker('A', 'w', 'running', 1);
  useWorkerStore.getState().updateNativeStatus('A', 'w', { type: 'busy' });
  useWorkerStore.getState().updateNativeUsage('A', 'w', { inputTokens: 100 });
  useWorkerStore.getState().updateWorker('A', 'w', 'running', 2);
  expect(useWorkerStore.getState().workers.A).toEqual({ id: 'w', sessionId: 'A',
    status: 'running', generation: 2 });
});


it('does not replay a pre-fetch running event patch over the completed HTTP truth', async () => {
  store().updateSession('A', { workerStatus: 'running', workerId: 'w', workerGeneration: 1, workerTaskSeq: 1 }, true);
  api.fetchSessions.mockResolvedValue([session({ workerStatus: 'idle', workerId: 'w', workerGeneration: 1, workerTaskSeq: 1 })]);
  await store().loadSessions();
  expect(store().sessions[0]?.workerStatus).toBe('idle');
});

it('does not let stream preview writes shield stale running from a completed snapshot', async () => {
  useSessionStore.setState({ sessions: [session({ workerStatus: 'running', workerId: 'w' })] });
  let resolve!: (sessions: Session[]) => void;
  api.fetchSessions.mockImplementation(() => new Promise<Session[]>(done => { resolve = done; }));
  const pending = store().loadSessions();
  store().updateSession('A', { lastMessage: 'delayed preview' });
  resolve([session({ workerStatus: 'idle', workerId: 'w' })]);
  await pending;
  expect(store().sessions[0]?.workerStatus).toBe('idle');
});


it('applies a summary snapshot batch in one notification without replacing loaded history', () => {
  const history = [{ role: 'user', content: 'loaded' }];
  useSessionStore.setState({ sessions: Array.from({ length: 1000 }, (_, index) => session({ id: `batch-${index}`, history, historyEpoch: 'loaded', historyRevision: 2, historyStart: 10, workerStatus: 'running' })) });
  const listener = vi.fn();
  const unsubscribe = useSessionStore.subscribe(listener);
  store().applySessionSnapshots(store().sessions.map(row => ({ ...row, workerStatus: 'idle', history: [], historyEpoch: 'summary', historyRevision: 3, historyStart: 0 })));
  unsubscribe();
  expect(listener).toHaveBeenCalledTimes(1);
  expect(store().sessions.every(row => row.workerStatus === 'idle' && row.history === history && row.historyEpoch === 'loaded' && row.historyStart === 10)).toBe(true);
});
