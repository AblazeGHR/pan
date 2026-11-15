import { beforeEach, expect, it, vi } from 'vitest';
import { anchorHistoryTerminals, useSessionStore } from './sessionStore';
import type { HistoryTerminal, Message, Session } from '@/types';

vi.mock('@/services/api', async original => ({
  ...(await original<typeof import('@/services/api')>()),
  fetchSessionHistory: vi.fn(() => new Promise(() => {})),
}));
const body = (id: string): Message => ({ role: 'assistant', content: id, messageId: id });
const terminal = (order: number, status: 'done' | 'error', anchor: string | null): Message => ({
  role: 'terminal', content: '', kind: 'terminal', messageId: `terminal:${order}`,
  terminal: { eventId: `terminal:${order}`, terminalKey: String(order), status,
    // Deliberately reversed time proves ordering uses the persisted cursor.
    occurredAt: `2026-10-27T00:00:0${9 - order}Z`, order, anchorMessageId: anchor,
    taskSeq: order, executionSeq: order, taskId: null, workerId: 'w', generation: 1,
    ...(status === 'error' ? { error: { source: 'cli.error', code: 429, message: 'capacity' } } : {}),
  } satisfies HistoryTerminal,
});
const old = body('old-body');
const next = body('new-body');
const error = terminal(1, 'error', old.messageId!);
const done = terminal(2, 'done', next.messageId!);
const page = (history: Message[], start = 0, total = history.length, revision = 4) => ({
  history, start, total, historyEpoch: 'epoch', historyRevision: revision, hasMore: start > 0,
});
const store = useSessionStore.getState;
beforeEach(() => {
  const session = { id: 'A', name: 'A', adapter: 'codex', history: [], historyTotal: 0,
    alwaysThinkingEnabled: false, effort: '', historyEpoch: 'epoch', historyRevision: 0 } as Session;
  useSessionStore.setState({ sessions: [session, { ...session, id: 'B' }], currentSessionId: 'A',
    currentMessages: [], sessionTranscripts: {}, liveStreamBuffers: {}, terminalWatermarks: {},
    _historyRefreshSeq: {}, serverEpoch: 'server' });
});

it('anchors delayed error before the newer turn, deduplicates events, orders by cursor', () => {
  expect(anchorHistoryTerminals([old, next, done, error, error])).toEqual([old, error, next, done]);
  const later = terminal(3, 'done', old.messageId!);
  expect(anchorHistoryTerminals([old, later, error])).toEqual([old, error, later]);
});

it('keeps no-body and paged-out anchors independent without inventing an attachment', () => {
  const independent = terminal(3, 'error', null);
  expect(anchorHistoryTerminals([next, error, independent])).toEqual([next, error, independent]);
});

it('converges delayed canonical pages, refresh duplicates and pagination to the same display', () => {
  store().applyHistoryPage('A', page([next, done, error], 1, 4));
  expect(store().currentMessages.map(row => row.messageId)).toEqual(['new-body', 'terminal:2', 'terminal:1']);
  store().applyHistoryPage('A', page([old], 0, 4));
  expect(store().currentMessages).toEqual([old, error, next, done]);
  store().applyHistoryPage('A', page([old, next, done, error]));
  expect(store().currentMessages).toEqual([old, error, next, done]);
});

it('late older error cannot overwrite a new turn or another selected Session', () => {
  store().applyHistoryPage('A', page([old], 0, 1, 1));
  const meta = { workerId: 'w', generation: 1, taskSeq: 2, executionSeq: 2 };
  store().applyLiveStream('A', [next], meta);
  expect(store().reconcileWorkerResult('A', { status: 'error', result: 'error diagnosis',
    historyTerminalId: error.messageId }, { ...meta, taskSeq: 1, executionSeq: 1 })).toBe(false);
  store().applyHistoryPage('A', page([old, next, done, error]));
  expect(store().currentMessages.some(row => row.content === 'error diagnosis')).toBe(false);
  const visible = [body('B-visible')];
  useSessionStore.setState({ currentSessionId: 'B', currentMessages: visible });
  store().applyHistoryPage('A', page([old, next, done, error]));
  expect(store().currentMessages).toBe(visible);
});

it('error terminal never replaces its live assistant body with diagnosis text', () => {
  store().applyHistoryPage('A', page([], 0, 0, 0));
  const meta = { workerId: 'w', generation: 1, taskSeq: 1, executionSeq: 1 };
  store().applyLiveStream('A', [old], meta);
  store().reconcileWorkerResult('A', { status: 'error', result: 'actual error',
    historyTerminalId: error.messageId, historyEpoch: 'epoch', historyRevision: 2 }, meta);
  store().applyHistoryPage('A', page([old, error], 0, 2, 2));
  expect(store().currentMessages).toEqual([old, error]);
});

const nativeErrorCard = (order: number, anchor: string | null, message: string,
  source = 'cli.error', status: 'done' | 'error' = 'error'): Message => ({
  role: 'terminal', content: '', kind: 'terminal', messageId: `terminal:${order}`,
  terminal: { eventId: `terminal:${order}`, terminalKey: String(order), status,
    occurredAt: `2026-10-27T00:00:0${9 - order}Z`, order, anchorMessageId: anchor,
    taskSeq: order, executionSeq: order, taskId: null, workerId: 'w', generation: 1,
    ...(status === 'error' ? { error: { source, code: 6004, message } } : {}) },
});

it('folds only the same failed run’s exact native-error echo into its red card', () => {
  const text = '429 您的使用量已超出频率限制，将在 2026-11-13 10:43:39 UTC+8 重置。';
  const echo: Message = { role: 'assistant', content: text, messageId: 'echo' };
  // The durable body stays in the rows; only its normal bubble is folded.
  expect(anchorHistoryTerminals([echo, nativeErrorCard(1, 'echo', text)])).toEqual([
    nativeErrorCard(1, 'echo', text)]);
  // Same prefix but a different full text is an ordinary answer.
  const prefix: Message = { role: 'assistant', content: `${text}（普通说明）`, messageId: 'prefix' };
  expect(anchorHistoryTerminals([prefix, nativeErrorCard(2, 'prefix', text)])).toEqual([
    prefix, nativeErrorCard(2, 'prefix', text)]);
  // A previous turn with the identical text keeps its own message.
  const earlier: Message = { role: 'assistant', content: text, messageId: 'earlier' };
  expect(anchorHistoryTerminals([earlier, echo, nativeErrorCard(3, 'echo', text)])).toEqual([
    earlier, nativeErrorCard(3, 'echo', text)]);
  // No fold without an identical native detail on the same anchor.
  expect(anchorHistoryTerminals([echo, nativeErrorCard(4, 'echo', text, 'cli.error', 'done')]))
    .toEqual([echo, nativeErrorCard(4, 'echo', text, 'cli.error', 'done')]);
  expect(anchorHistoryTerminals([echo, nativeErrorCard(5, 'echo', '')]))
    .toEqual([echo, nativeErrorCard(5, 'echo', '')]);
  for (const source of ['pan.terminal_result', 'pan.runtime', 'worker.stream.result', '']) {
    const card = nativeErrorCard(6, 'echo', text, source);
    expect(anchorHistoryTerminals([echo, card]), source).toEqual([echo, card]);
  }
  // A paged-out anchor never folds an unrelated row.
  expect(anchorHistoryTerminals([echo])).toEqual([echo]);
  const other: Message = { role: 'assistant', content: text, messageId: 'other' };
  expect(anchorHistoryTerminals([other, nativeErrorCard(7, 'echo', text)])).toEqual([
    other, nativeErrorCard(7, 'echo', text)]);
});
