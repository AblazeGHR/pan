// @vitest-environment jsdom
/**
 * Re-audit investigation suite — history window / identity / epoch gating.
 *
 * Baseline: main 591367a65f88e9d5270e8e99920ef5448d1d68c9.
 * These tests assert the CORRECT contract. The failing ones are intentional:
 * each documents a deterministic, source-level defect that a fix must close.
 * Production code was NOT modified to make them pass.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

const api = vi.hoisted(() => ({ fetchSessionHistory: vi.fn() }));

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionHistory: api.fetchSessionHistory,
}));

function m(role: string, content: string, messageId?: string): Message {
  return { role, content, ...(messageId ? { messageId } : {}) };
}

function sess(history: Message[], extra: Partial<Session> = {}): Session {
  return {
    id: 'A',
    name: 'A',
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: 'high',
    history,
    historyTotal: history.length,
    ...extra,
  };
}

/** Shape fixture from the protected 8768 read-only evidence: canonical HTTP
 * history has stable message ids, no native live ids, and no adjacent duplicate
 * role/body rows.  The contents are synthetic; no protected service is read by
 * this test. */
function canonicalHttpHistoryFixture(sessionId: string, count: number): Message[] {
  const roles = ['user', 'assistant', 'tool'];
  return Array.from({ length: count }, (_, index) => ({
    role: roles[index % roles.length]!,
    content: `${sessionId}-canonical-${index}`,
    messageId: `${sessionId}-http-message-${index}`,
  }));
}

const ids = (msgs: Message[]) => msgs.map((x) => x.messageId ?? `<${x.role}:${x.content}>`);

describe('reaudit · history window start', () => {
  beforeEach(() => {
    api.fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      historyWindowStarts: {},
      serverEpoch: null,
      liveStreamBuffers: {},
      terminalWatermarks: {},
      _historyRefreshSeq: {},
      _historyPageSeq: {},
      _selectionSeq: {},
      _pendingQueueIds: {},
      _deliveredQueueIds: {},
      _sessionLocalTouchedSeq: {},
      _sessionWsTouchedSeq: {},
      _sessionEventPatches: {},
      sessionSettingMutations: {},
      sessionTranscripts: {},
    });
  });

  // DEFECT R1 (F-A). sessionStore.ts:1180-1182 unconditionally rewrites
  // historyWindowStarts[sid] and historyLoadEnd to the tail page's absolute
  // start. A tail refresh must NOT move the loaded window: the canonical array
  // still spans the union of both pages.
  it('R1 · tail refresh keeps the loaded window start (does not reset it to the page start)', async () => {
    const full = Array.from({ length: 150 }, (_, i) =>
      m(i % 2 === 0 ? 'user' : 'assistant', `m${i}`, `m-${i}`),
    );
    const loaded = sess(full.slice(0, 100), { historyTotal: 150, historyStart: 0 });
    useSessionStore.setState({
      sessions: [loaded],
      currentSessionId: 'A',
      currentMessages: loaded.history.slice(),
      historyWindowStarts: { A: 0 },
      historyLoadEnd: 0,
    });
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: full.slice(100), total: 150, hasMore: true, start: 100,
      historyEpoch: 'e1', historyRevision: 150,
    });

    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    const s = useSessionStore.getState();
    // 150 rows (0..149) are loaded; the window still begins at absolute 0.
    expect(s.currentMessages).toHaveLength(150);
    expect(s.historyWindowStarts.A).toBe(0); // observed: 100
    expect(s.historyLoadEnd).toBe(0); // observed: 100
  });

  // DEFECT R2 (F-A, user-visible consequence). Because R1 reset the window to
  // the tail, scrolling up re-fetches rows that are already loaded and
  // mergeHistoryPageByWindow prepends them (its "non-overlapping older page"
  // branch, sessionStore.ts:678-680), duplicating and reordering history.
  it('R2 · scrolling up after a tail refresh never duplicates or reorders loaded rows', async () => {
    const full = Array.from({ length: 150 }, (_, i) =>
      m(i % 2 === 0 ? 'user' : 'assistant', `m${i}`, `m-${i}`),
    );
    const loaded = sess(full.slice(0, 100), { historyTotal: 150, historyStart: 0 });
    useSessionStore.setState({
      sessions: [loaded],
      currentSessionId: 'A',
      currentMessages: loaded.history.slice(),
      historyWindowStarts: { A: 0 },
      historyLoadEnd: 0,
    });
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: full.slice(100), total: 150, hasMore: true, start: 100,
      historyEpoch: 'e1', historyRevision: 150,
    });
    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    api.fetchSessionHistory.mockResolvedValueOnce({
      history: full.slice(50, 100), total: 150, hasMore: true, start: 50,
      historyEpoch: 'e1', historyRevision: 150,
    });
    await act(async () => { await useSessionStore.getState().loadOlderMessages(); });

    const s = useSessionStore.getState();
    expect(ids(s.currentMessages)).toEqual(full.map((x) => x.messageId));
    // observed: 200 rows, first 50 (m-50..m-99) duplicated at the front.
    expect(new Set(ids(s.currentMessages)).size).toBe(150);
    expect(s.currentMessages).toHaveLength(150);
  });

  // DEFECT R3 (F-C). historyEpoch/historyRevision are read from the response and
  // echoed into Session (sessionStore.ts:1079-1080, 1163-1164, 1239-1240) but
  // are NEVER compared. A stale page from an older epoch monotonically regresses
  // both the epoch and the revision and replaces newer content.
  it('R3 · an older historyEpoch/historyRevision page must not be applied', async () => {
    const first = [m('user', 'u0', 'legacy:A:eNew:0'), m('assistant', 'a0', 'legacy:A:eNew:1')];
    useSessionStore.setState({
      sessions: [sess(first, {
        historyTotal: 2, historyStart: 0, historyEpoch: 'eNew', historyRevision: 5,
      })],
      currentSessionId: 'A',
      currentMessages: first.slice(),
      historyWindowStarts: { A: 0 },
    });
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [m('user', 'u0', 'legacy:A:eOld:0'), m('assistant', 'OLD-a0', 'legacy:A:eOld:1')],
      total: 2, hasMore: false, start: 0, historyEpoch: 'eOld', historyRevision: 1,
    });

    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    const s = useSessionStore.getState();
    expect(s.sessions[0]!.historyEpoch).toBe('eNew'); // observed: eOld
    expect(s.sessions[0]!.historyRevision).toBe(5); // observed: 1
    expect(s.currentMessages.map((x) => x.content)).toEqual(['u0', 'a0']); // observed: ['u0','OLD-a0']
  });
});

describe('reaudit · live → result → history identity rebuild', () => {
  beforeEach(() => {
    api.fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      historyWindowStarts: {},
      serverEpoch: null,
      liveStreamBuffers: {},
      terminalWatermarks: {},
      _historyRefreshSeq: {},
      _historyPageSeq: {},
      _selectionSeq: {},
      _pendingQueueIds: {},
      _deliveredQueueIds: {},
      _sessionLocalTouchedSeq: {},
      _sessionWsTouchedSeq: {},
      _sessionEventPatches: {},
      sessionSettingMutations: {},
      sessionTranscripts: {},
    });
  });

  // DEFECT R4 (F-B). reconcileWorkerResult rebuilds the projection as
  // mergeServerHistoryWithLive(session.history, finalLiveMessages). Any live row
  // absent from session.history (a tool/thinking block that streamed before the
  // final assistant text) is appended AFTER the assistant, so the arrival order
  // [tool, assistant] becomes [assistant, tool].
  it('R4 · a tool block that streamed before the assistant keeps its position through result', () => {
    useSessionStore.setState({
      sessions: [sess([])],
      currentSessionId: 'A',
      currentMessages: [],
    });
    const meta = { serverEpoch: 'e1', workerId: 'w1', generation: 0, taskSeq: 1 };
    act(() => {
      useSessionStore.getState().applyWorkerStatus('A', 'running', meta);
      useSessionStore.getState().applyLiveStream('A', [
        { role: 'tool', content: 'Run(x)', nativeItemId: 'tool-1' },
        { role: 'assistant', content: 'Hello', nativeItemId: 'item-2' },
      ], { ...meta, itemId: 'item-2' });
    });
    expect(useSessionStore.getState().currentMessages.map((x) => x.role)).toEqual(['tool', 'assistant']);

    act(() => {
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'Hello world' }, meta);
    });

    // Arrival order must survive: tool first, then the completed assistant text.
    expect(useSessionStore.getState().currentMessages.map((x) => x.role)).toEqual(['tool', 'assistant']);
    // observed: ['assistant', 'tool']
  });

  // DEFECT R5 (F-B, second half). The canonical history page carries messageId
  // while the leftover live row carries nativeItemId; mergeHistoryPageByWindow
  // (sessionStore.ts:688-696) only matches on explicit identity, so the live
  // tool/thinking row is never consumed and survives as a duplicate.
  it('R5 · a history refresh after result must not leave a duplicate tool row', async () => {
    useSessionStore.setState({
      sessions: [sess([])],
      currentSessionId: 'A',
      currentMessages: [],
      historyWindowStarts: { A: 0 },
    });
    const meta = { serverEpoch: 'e1', workerId: 'w1', generation: 0, taskSeq: 1 };
    act(() => {
      useSessionStore.getState().applyWorkerStatus('A', 'running', meta);
      useSessionStore.getState().applyLiveStream('A', [
        { role: 'tool', content: 'Run(x)', nativeItemId: 'tool-1' },
        { role: 'assistant', content: 'Hello', nativeItemId: 'item-2' },
      ], { ...meta, itemId: 'item-2' });
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'Hello world' }, meta);
    });
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        m('user', 'u0', 'm-0'),
        m('tool', 'Run(x)', 'm-1'),
        m('assistant', 'Hello world', 'm-2'),
      ],
      total: 3, hasMore: false, start: 0, historyEpoch: 'e1', historyRevision: 3,
    });

    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    const contents = useSessionStore.getState().currentMessages.map((x) => `${x.role}:${x.content}`);
    expect(contents).toEqual(['user:u0', 'tool:Run(x)', 'assistant:Hello world']);
    // observed: ['user:u0','tool:Run(x)','assistant:Hello world','tool:Run(x)']
  });
});

describe('reaudit · legitimate same-text history rows', () => {
  beforeEach(() => {
    api.fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      historyWindowStarts: {},
      _pendingQueueIds: {},
      _deliveredQueueIds: {},
      _sessionLocalTouchedSeq: {},
      liveStreamBuffers: {},
      terminalWatermarks: {},
    });
  });

  // POSITIVE CONTROL (implementation holds): two legitimately identical user
  // prompts and a queued optimistic row of the same text must all coexist.
  it('R6 · identical text with distinct identities is never text-deduped', () => {
    const page: Message[] = [
      m('user', 'same', 'm-0'),
      m('assistant', 'a1', 'm-1'),
      m('user', 'same', 'm-2'),
    ];
    useSessionStore.setState({
      sessions: [sess(page, { historyTotal: 3, historyStart: 0 })],
      currentSessionId: 'A',
      currentMessages: page.slice(),
      historyWindowStarts: { A: 0 },
    });

    act(() => {
      useSessionStore.getState().appendQueuedMessage('A', { id: 'q1', text: 'same' });
    });

    const history = useSessionStore.getState().sessions[0]!.history;
    expect(history.map((x) => [x.content, x.messageId ?? null])).toEqual([
      ['same', 'm-0'],
      ['a1', 'm-1'],
      ['same', 'm-2'],
      ['same', null],
    ]);
    expect(history.filter((x) => x.content === 'same')).toHaveLength(3);
  });
});

// ── Migrated from frontend-reaudit-astra-20260921/audit/probe-current-main.cjs
//    (evidence/current-main-probes.json, baseline 591367a). Same HEAD, so the
//    snapshots are directly comparable; these assert the CORRECT contract.
describe('reaudit · multi-block turn order & consecutive DONE rounds', () => {
  const meta1 = {
    serverEpoch: 'server', workerId: 'worker', generation: 0, taskSeq: 1, taskId: 'task-1',
  } as const;
  const meta2 = {
    serverEpoch: 'server', workerId: 'worker', generation: 0, taskSeq: 2, taskId: 'task-2',
  } as const;
  // Live rows carry a provider item id; canonical rows carry a messageId.
  const live = (role: string, content: string, nativeItemId: string): Message => ({ role, content, nativeItemId });
  const idless = (role: string, content: string): Message => ({ role, content });
  const row = (x: Message) => `${x.role}:${x.content}`;

  beforeEach(() => {
    api.fetchSessionHistory.mockReset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
      historyLoading: false,
      initialLoading: false,
      historyLoadEnd: 0,
      historyWindowStarts: {},
      serverEpoch: null,
      liveStreamBuffers: {},
      terminalWatermarks: {},
      _historyRefreshSeq: {},
      _historyPageSeq: {},
      _selectionSeq: {},
      _pendingQueueIds: {},
      _deliveredQueueIds: {},
      _sessionLocalTouchedSeq: {},
      _sessionWsTouchedSeq: {},
      _sessionEventPatches: {},
      sessionSettingMutations: {},
      sessionTranscripts: {},
    });
  });

  // DEFECT R7 (astra: ordered-stream-after-result). One turn streams
  // assistant-analysis, then a tool, then the final assistant text. worker.result
  // rebuilds the projection with mergeServerHistoryWithLive, which appends every
  // live row that is absent from session.history — so the final assistant (the
  // only row that reached history) is emitted first and the earlier blocks follow.
  it('R7 · [user, analysis, tool, final] keeps its order through result', () => {
    const start = [m('user', 'question', 'u')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    act(() => {
      useSessionStore.getState().applyLiveStream('A', [
        live('assistant', 'analysis', 'a'),
        live('tool', 'tool', 't'),
        live('assistant', 'final', 'f'),
      ], meta1);
    });
    expect(useSessionStore.getState().currentMessages.map(row)).toEqual([
      'user:question', 'assistant:analysis', 'tool:tool', 'assistant:final',
    ]);

    act(() => {
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta1);
    });
    expect(useSessionStore.getState().currentMessages.map(row)).toEqual([
      'user:question', 'assistant:analysis', 'tool:tool', 'assistant:final',
    ]);
    // observed: ['user:question','assistant:final','assistant:analysis','tool:tool']
  });

  // DEFECT R8 (astra: second-result-loses-earlier-turn-blocks) — the priority
  // case. Two consecutive turns each ending DONE, with the turn-1 blocks, a
  // system DONE marker and a delivered user row between them. The second
  // reconcile must not lose or move any earlier row.
  it('R8 · consecutive DONE rounds preserve every earlier block and its position', () => {
    const start = [m('user', 'question', 'u')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    const expected = [
      'user:question',
      'assistant:analysis',
      'tool:tool',
      'assistant:final',
      'system:DONE-1',
      'user:question2',
      'assistant:second analysis',
      'assistant:second final',
    ];

    act(() => {
      useSessionStore.getState().applyLiveStream('A', [
        live('assistant', 'analysis', 'a'),
        live('tool', 'tool', 't'),
        live('assistant', 'final', 'f'),
      ], meta1);
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta1);
      useSessionStore.getState().addMessage({ role: 'system', content: 'DONE-1', nativeItemId: 'done-1' });
      useSessionStore.getState().appendDeliveredMessages('A', [
        { role: 'user', content: 'question2', queueItemIds: ['q2'] },
      ]);
      useSessionStore.getState().applyLiveStream('A', [
        live('assistant', 'second analysis', 'a2'),
        live('assistant', 'second final', 'f2'),
      ], meta2);
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'second final' }, meta2);
    });

    const rows = useSessionStore.getState().currentMessages.map(row);
    expect(rows).toEqual(expected);
    // observed: ['user:question','assistant:final','user:question2','assistant:second final',
    //            'system:DONE-1','assistant:second analysis']  (8 -> 6 rows: 'analysis','tool' lost)
    expect(rows).toHaveLength(expected.length);
    expect(rows).toContain('tool:tool');
  });

  // DEFECT R9 (astra: cached-index-after-prepend) — the projection cache is not
  // revalidated after an external prepend. applyLiveStream (sessionStore.ts:1645-1651)
  // resolves the second fallback from `previous.projectionIndexes` with a BOUNDS
  // check only: neither role nor identity is verified, so the stale index lands on
  // a different row and overwrites it.
  it('R9 · a stale cached projection index must not overwrite a different-role row', () => {
    const start = [m('user', 'question', 'u')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    act(() => {
      useSessionStore.getState().applyLiveStream('A', [live('assistant', 'live', 'a')], meta1);
    });
    // An older-history page is prepended by another path, shifting every index.
    act(() => {
      useSessionStore.setState((s) => ({
        currentMessages: [{ role: 'assistant', content: 'old-history', messageId: 'old' }, ...s.currentMessages],
      }));
    });
    act(() => {
      useSessionStore.getState().applyLiveStream('A', [live('assistant', 'live updated', 'a')], meta1);
    });

    expect(useSessionStore.getState().currentMessages.map(row)).toEqual([
      'assistant:old-history', 'user:question', 'assistant:live updated',
    ]);
    // observed: ['assistant:old-history','assistant:live updated','assistant:live']
    //           (the user row was overwritten and the live row duplicated)
    expect(useSessionStore.getState().currentMessages.some((x) => x.role === 'user')).toBe(true);
  });

  // DEFECT R10 (astra: different-task-idless-same-text-swallowed). projectionKeys
  // falls back to `${role}:legacy:${content}` for id-less rows, so a NEW turn's
  // assistant reply with identical text is merged into the OLD assistant row.
  it('R10 · an id-less new reply with identical text must not merge into the old row', () => {
    const start = [idless('assistant', 'same reply')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    act(() => {
      useSessionStore.getState().appendDeliveredMessages('A', [
        { role: 'user', content: 'new question', queueItemIds: ['new-q'] },
      ]);
      useSessionStore.getState().applyLiveStream('A', [idless('assistant', 'same reply')], meta1);
    });

    expect(useSessionStore.getState().currentMessages.map(row)).toEqual([
      'assistant:same reply', 'user:new question', 'assistant:same reply',
    ]);
    // observed: 2 rows — the second reply was swallowed
  });

  // DEFECT R11 (astra: idless-provider-result). Without provider ids the result
  // rebuild duplicates the final assistant text and moves it before the interim
  // blocks.
  it('R11 · an id-less provider result keeps [interim, tool, final] without a duplicate', () => {
    const start = [m('user', 'question', 'u')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    act(() => {
      useSessionStore.getState().applyLiveStream('A', [
        idless('assistant', 'interim'),
        idless('tool', 'tool'),
        idless('assistant', 'final'),
      ], meta1);
      useSessionStore.getState().reconcileWorkerResult('A', { result: 'final' }, meta1);
    });

    expect(useSessionStore.getState().currentMessages.map(row)).toEqual([
      'user:question', 'assistant:interim', 'tool:tool', 'assistant:final',
    ]);
    // observed: ['user:question','assistant:final','assistant:interim','tool:tool','assistant:final']
  });

  it('R11a · a canonical tail arriving after result stays after the completed turn', async () => {
    const start = [m('user', 'question', 'u')];
    useSessionStore.setState({
      sessions: [sess(start)], currentSessionId: 'A',
      currentMessages: start.slice(), historyWindowStarts: { A: 0 },
    });
    const meta = { serverEpoch: 'e1', workerId: 'w1', generation: 0, taskSeq: 1, taskId: 'task-1' };
    act(() => {
      useSessionStore.getState().applyLiveStream('A', [
        { role: 'thinking', content: 'plan', nativeItemId: 'plan-1' },
        { role: 'tool', content: 'Run(x)', nativeItemId: 'tool-1' },
        { role: 'assistant', content: 'answer', nativeItemId: 'answer-1' },
      ], meta);
      useSessionStore.getState().reconcileWorkerResult(
        'A', { result: 'answer', status: 'done' }, meta,
      );
    });

    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [
        m('user', 'question', 'u'),
        m('thinking', 'plan', 'h-plan'),
        m('tool', 'Run(x)', 'h-tool'),
        m('assistant', 'answer', 'h-answer'),
        m('user', 'tail user', 'tail-user'),
      ],
      total: 5, hasMore: false, start: 0, historyEpoch: 'e1', historyRevision: 5,
    });
    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    expect(useSessionStore.getState().currentMessages.map((row) => `${row.role}:${row.content}`))
      .toEqual([
        'user:question', 'thinking:plan', 'tool:Run(x)', 'assistant:answer', 'user:tail user',
      ]);
  });

  // DEFECT R12 (astra: history-epoch-replacement-removes-old-tail). A historyEpoch
  // change means the server replaced its whole history (session.replace_history).
  // history_revision is a persisted Session-level cursor, not an epoch-local
  // counter: replace_history mints the new epoch and increments revision 10→11.
  // The old local tail must not survive the replacement.
  it('R12 · a historyEpoch change must drop the stale old-epoch tail', async () => {
    const start = [m('user', 'old user', 'old-u'), m('assistant', 'old answer', 'old-a')];
    useSessionStore.setState({
      sessions: [sess(start, { historyTotal: 2, historyStart: 0, historyEpoch: 'hist', historyRevision: 10 })],
      currentSessionId: 'A',
      currentMessages: start.slice(),
      historyWindowStarts: { A: 0 },
    });
    api.fetchSessionHistory.mockResolvedValueOnce({
      history: [m('user', 'replacement', 'new-u')],
      total: 1, hasMore: false, start: 0,
      historyEpoch: 'replacement-epoch', historyRevision: 11,
    });

    await act(async () => { await useSessionStore.getState().refreshCurrentSessionHistory(); });

    expect(useSessionStore.getState().currentMessages.map(row)).toEqual(['user:replacement']);
    // observed: ['user:replacement','assistant:old answer'] — the stale tail survived
  });
});

describe('reaudit · background history recovery never adopts selected rows', () => {
  it('R13 · applying Session B history while Session A is selected cannot copy A runtime rows', async () => {
    const aHistory = canonicalHttpHistoryFixture('A', 733);
    const bHistory = canonicalHttpHistoryFixture('B', 471);
    const a = sess(aHistory, { historyTotal: aHistory.length });
    const b = { ...sess(bHistory, { historyTotal: bHistory.length }), id: 'B', name: 'B' };
    const canonicalIds = (rows: Message[]) => rows.map((row) => row.messageId);
    expect(new Set(canonicalIds(aHistory)).size).toBe(733);
    expect(new Set(canonicalIds(bHistory)).size).toBe(471);
    expect(aHistory.some((row, index) => index > 0
      && row.role === aHistory[index - 1]!.role
      && row.content === aHistory[index - 1]!.content)).toBe(false);
    const userAssistant = aHistory
      .filter((row) => row.role === 'user' || row.role === 'assistant')
      .map((row) => `${row.role}:${row.content}`);
    expect(new Set(userAssistant).size).toBe(userAssistant.length);
    expect(aHistory.every((row) => !row.nativeItemId)).toBe(true);
    useSessionStore.setState({
      sessions: [a, b], currentSessionId: 'A',
      currentMessages: [...aHistory, { role: 'assistant', content: 'A live tail' }],
      historyWindowStarts: { A: 0, B: 0 }, sessionTranscripts: {},
    });

    act(() => {
      useSessionStore.getState().applyHistoryPage('B', {
        history: bHistory.map((row) => ({ ...row })),
        start: 0, total: bHistory.length, hasMore: false,
        historyEpoch: 'b-epoch', historyRevision: bHistory.length,
      });
    });

    expect(useSessionStore.getState().sessionTranscripts.B?.runtime).toEqual([]);
    expect(useSessionStore.getState().sessions.find((session) => session.id === 'B')?.history)
      .toEqual(bHistory);

    api.fetchSessionHistory.mockResolvedValueOnce({
      history: bHistory,
      start: 0, total: bHistory.length, hasMore: false,
      historyEpoch: 'b-epoch', historyRevision: bHistory.length,
    });
    await act(async () => { await useSessionStore.getState().selectSession('B'); });
    expect(useSessionStore.getState().currentMessages.map((row) => row.content))
      .toEqual(bHistory.map((row) => row.content));
  });
});
