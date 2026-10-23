// @vitest-environment jsdom
/** Real hook/store/component regression: repair HTTP reads are unavailable;
 * background summaries and Job indicators must react to WS ingestion alone. */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, render, cleanup, act } from '@testing-library/react';

const wsMock = vi.hoisted(() => {
  const handlers: Record<string, Array<(e: unknown) => void>> = {};
  return {
    handlers,
    connect: vi.fn(),
    send: vi.fn(() => true),
    sendInteractiveSync: vi.fn(() => true),
    sendAuthoritativeResync: vi.fn(
      (_payload: Record<string, unknown>, _mode?: 'initial' | 'recovery') => true,
    ),
    getConnectionGeneration: vi.fn(() => 1),
    reconnect: vi.fn(),
    isConnectionFresh: vi.fn(() => true),
    on: vi.fn((type: string, h: (e: unknown) => void) => {
      (handlers[type] ??= []).push(h);
      return () => { handlers[type] = (handlers[type] ?? []).filter((x) => x !== h); };
    }),
    trigger: (type: string, e: unknown) => { for (const h of handlers[type] ?? []) h(e); },
  };
});

vi.mock('@/services/ws', () => ({
  wsClient: {
    connect: wsMock.connect, reconnect: wsMock.reconnect, on: wsMock.on,
    send: wsMock.send, sendInteractiveSync: wsMock.sendInteractiveSync,
    sendAuthoritativeResync: wsMock.sendAuthoritativeResync,
    getConnectionGeneration: wsMock.getConnectionGeneration,
    isOpen: true, isConnectionFresh: wsMock.isConnectionFresh,
  },
}));

const apiMock = vi.hoisted(() => ({
  fetchSessionHistory: vi.fn(),
  fetchSessions: vi.fn(),
  listWorkers: vi.fn(),
  fetchSessionQueue: vi.fn(),
  updateUiSettings: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchSessionHistory: apiMock.fetchSessionHistory,
  fetchSessions: apiMock.fetchSessions,
  listWorkers: apiMock.listWorkers,
  fetchSessionQueue: apiMock.fetchSessionQueue,
  updateUiSettings: apiMock.updateUiSettings,
}));

import { useWebSocket } from '@/hooks/useWebSocket';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useQueueStore } from '@/stores/queueStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { SessionItem } from '@/components/session/SessionItem';
import { ViewEntryBadge } from '@/components/layout/ViewEntryBadge';
import { useJobStore } from '@/stores/jobStore';
import type { Job } from '@/types/jobs';
import type { Session } from '@/types';

function mk(id: string): Session {
  return { id, name: id, alwaysThinkingEnabled: false, effort: '', history: [], workerStatus: 'idle', workerId: 'w1' };
}

function resetStore() {
  useSessionStore.setState({
    sessions: [mk('A')], currentSessionId: 'A', currentMessages: [],
    hasMoreMessages: false, historyLoading: false, initialLoading: false, sessionsLoading: false,
    historyLoadEnd: 0, historyWindowStarts: { A: 0 }, _loadSeq: 0, _sessionWsTouchedSeq: {},
    _sessionLocalTouchedSeq: {}, _historyRefreshSeq: {}, _historyPageSeq: {}, _selectionSeq: {},
    liveStreamBuffers: {}, terminalWatermarks: {}, unscopedReplayPending: {},
    _deliveredQueueIds: {}, _sessionEventPatches: {}, sessionSettingMutations: {},
    sessionTranscripts: {},
  });
}


const job = (patch: Partial<Job> = {}): Job => ({
  jobId: 'job-live', kind: 'session-message', status: 'scheduled', name: 'live',
  description: '', source: { type: 'agent' }, target: { sessionId: 'B' },
  paused: false, runCount: 0, createdAt: 1, updatedAt: 1, ...patch,
});
function Cards() {
  const sessions = useSessionStore(s => s.sessions);
  return <><ViewEntryBadge />{sessions.map(session =>
    <div key={session.id} data-card={session.id}><SessionItem session={session} isActive={session.id === 'A'} /></div>)}</>;
}
function summary(id: string, revision: number, text: string) {
  return { id, summaryRevision: revision, historyRevision: revision, historyEpoch: `history-${id}`,
    historyTotal: revision, lastMessage: text, lastDisplayPreview: text,
    lastAssistantPreview: text, updatedAt: `${revision}` };
}
function emit(type: string, event: Record<string, unknown>) {
  act(() => wsMock.trigger(type, { type, ...event }));
}

describe('live background Session and Job ingestion', () => {
  it.each(['thinking', 'codex.diff'])('does not promote a %s cumulative delta into the card body', type => {
    renderHook(() => useWebSocket());
    emit('session.updated', { sessionId: 'B', session: summary('B', 1, 'user fallback') });
    emit('worker.stream', { sessionId: 'B', workerId: 'w1', generation: 0, taskSeq: 1,
      event: { type, delta: true, stream_text: 'internal detail', content: 'internal detail', diff: 'internal detail' } });
    expect(useSessionStore.getState().sessions.find(s => s.id === 'B')?.lastMessage).toBe('user fallback');
  });
  beforeEach(() => {
    for (const key of Object.keys(wsMock.handlers)) delete wsMock.handlers[key];
    apiMock.fetchSessions.mockRejectedValue(new Error('HTTP is unavailable after hydrate'));
    apiMock.listWorkers.mockRejectedValue(new Error('no workers'));
    apiMock.fetchSessionQueue.mockResolvedValue([]);
    resetStore();
    useSessionStore.setState({ sessions: [mk('A'), mk('B'), mk('C')] });
    useJobStore.setState({ jobs: [], eventSeqByJob: {}, mutationSeq: 0, loadSeq: 0 });
    useWorkerStore.setState({ workers: {} });
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useQueueStore.setState({ agentQueues: {}, agentQueueLoadSeq: {} });
  });
  afterEach(cleanup);

  it.each(['agent', 'report', 'automation', 'system_prompt'])('updates an unselected %s receipt and assistant summary synchronously without a history GET', source => {
    renderHook(() => useWebSocket());
    const { container } = render(<Cards />);
    const receipt = { role: 'user', content: source === 'agent' ? '////by agent : sender' : '@@@@by agent : sender',
      source, queueItemIds: ['q-B'], messageId: 'receipt-B' };
    emit('queue.item_delivered', { sessionId: 'B', queueRevision: 1,
      queueItemIds: ['q-B'], messages: [receipt], session: summary('B', 1, receipt.content) });
    emit('worker.stream', { sessionId: 'B', workerId: 'w1', generation: 0, taskSeq: 1,
      event: { type: 'assistant', item_id: 'reply-B', content: 'B answer' }, session: summary('B', 2, 'B answer') });
    // A delayed receipt already carries the current server summary. It must
    // not replace the assistant preview with the injected input again.
    emit('queue.item_delivered', { sessionId: 'B', queueRevision: 1,
      queueItemIds: ['q-B'], messages: [receipt], session: summary('B', 2, 'B answer') });
    emit('queue.item_delivered', { sessionId: 'B', queueRevision: 1,
      queueItemIds: ['q-B'], messages: [receipt], session: summary('B', 1, receipt.content) });
    expect(container.querySelector('[data-card="B"]')!.textContent).toContain('B answer');
    const state = useSessionStore.getState();
    expect(state.sessions.find(s => s.id === 'B')).toMatchObject(summary('B', 2, 'B answer'));
    expect(state.currentSessionId).toBe('A');
    expect(state.currentMessages).toEqual([]);
    expect(apiMock.fetchSessionHistory).not.toHaveBeenCalled();
  });

  it('keeps concurrent Session previews isolated and rejects stale/repeated summary revisions', () => {
    renderHook(() => useWebSocket());
    const { container } = render(<Cards />);
    for (const [id, revision, text] of [['B', 4, 'B current'], ['C', 7, 'C current'], ['B', 2, 'B old'], ['C', 3, 'C old'], ['B', 4, 'B current']] as const) {
      emit('session.updated', { sessionId: id, session: summary(id, revision, text) });
    }
    expect(container.querySelector('[data-card="B"]')!.textContent).toContain('B current');
    expect(container.querySelector('[data-card="C"]')!.textContent).toContain('C current');
    expect(useSessionStore.getState().sessions.find(s => s.id === 'B')?.historyRevision).toBe(4);
    expect(useSessionStore.getState().sessions.find(s => s.id === 'C')?.historyRevision).toBe(7);
  });

  it('ingests a delivery summary even when the receipt has no message rows', () => {
    renderHook(() => useWebSocket());
    const { container } = render(<Cards />);
    emit('queue.item_delivered', { sessionId: 'B', queueRevision: 1,
      queueItemIds: [], messages: [], session: summary('B', 2, 'B authoritative') });
    expect(container.querySelector('[data-card="B"]')!.textContent).toContain('B authoritative');
    expect(useSessionStore.getState().sessions.find(s => s.id === 'B')?.historyRevision).toBe(2);
  });

  it.each(['session-message', 'session-broadcast', 'scheduled-task'] as const)('updates %s J and nav badge through schedule/running/target/terminal and replay while Jobs is unmounted', kind => {
    renderHook(() => useWebSocket());
    const { container } = render(<Cards />);
    const indicator = (id: string) => container.querySelector(`[data-card="${id}"] [data-testid="session-job-badge"]`)?.getAttribute('data-job-state') ?? null;
    const badge = () => container.querySelector('[data-testid="jobs-running-badge"]')?.textContent ?? null;
    const send = (seq: number, patch: Partial<Job> = {}) => emit(seq === 1 ? 'job.created' : 'job.updated',
      { serverEpoch: 'server-1', eventSeq: seq, job: job({ kind, updatedAt: seq, ...patch }) });
    send(1); expect(indicator('B')).toBe('scheduled'); expect(badge()).toBeNull();
    send(2, { status: 'running' }); expect(indicator('B')).toBe('running'); expect(badge()).toBe('1');
    send(3, { status: 'running', target: { sessionId: 'C' } });
    expect(indicator('B')).toBeNull(); expect(indicator('C')).toBe('running');
    send(4, { status: 'completed' }); expect(indicator('C')).toBeNull(); expect(badge()).toBeNull();
    send(2, { status: 'running' }); send(4, { status: 'running' });
    // Retry publishes old durable state under a newer transport cursor.
    send(5, { status: 'running', updatedAt: 2 });
    expect(badge()).toBeNull(); expect(indicator('B')).toBeNull(); expect(indicator('C')).toBeNull();
    // A new server epoch starts from cursor 1, but Job time still advances.
    emit('job.updated', { serverEpoch: 'server-2', eventSeq: 1, job: job({ updatedAt: 6, status: 'running' }) });
    expect(badge()).toBe('1');
    emit('job.deleted', { serverEpoch: 'server-2', eventSeq: 2, jobId: 'job-live' });
    emit('job.created', { serverEpoch: 'server-2', eventSeq: 1, job: job({ updatedAt: 6, status: 'running' }) });
    expect(badge()).toBeNull(); expect(indicator('B')).toBeNull();
  });
});
