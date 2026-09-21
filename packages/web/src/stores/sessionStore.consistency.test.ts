// @vitest-environment jsdom
// R1: the read-only observation probes (audit/probe-baseline.cjs) converted into
// formal red tests, plus the acceptance-matrix cases from
// audit/REPAIR_EXECUTION_PLAN.md. Every assertion checks identity/role/content/
// order/marker position, never just length or "the last message exists".
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message, Session } from '@/types';

const fetchSessionHistory = vi.hoisted(() => vi.fn());

vi.mock('@/services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/api')>()),
  fetchSessionHistory,
  fetchSessions: vi.fn(async () => []),
}));

interface Page {
  history: Message[];
  total: number;
  hasMore: boolean;
  start: number;
  historyEpoch?: string;
  historyRevision?: number;
}

let pendingPages: Array<(p: Page) => void> = [];
fetchSessionHistory.mockImplementation(
  () =>
    new Promise<Page>((resolve) => {
      pendingPages.push(resolve);
    }),
);

function msg(role: string, content: string, id?: string): Message {
  return { role, content, ...(id ? { messageId: id, nativeItemId: id } : {}) };
}

function sess(history: Message[], extra: Partial<Session> = {}): Session {
  return {
    id: 'A',
    name: 'A',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    history,
    historyStart: 0,
    historyTotal: history.length,
    historyEpoch: 'hist',
    historyRevision: 10,
    workerStatus: null,
    workerId: null,
    ...extra,
  };
}

const meta = { serverEpoch: 'server', workerId: 'worker', generation: 0, taskSeq: 1, taskId: 'task-1' };

function view(): Array<{ role: string; text: string; id: string | null }> {
  return useSessionStore.getState().currentMessages.map((m) => ({
    role: m.role,
    text: m.content,
    id: m.messageId || m.nativeItemId || null,
  }));
}

function texts(): string[] {
  return useSessionStore.getState().currentMessages.map((m) => m.content);
}

/** Index of the marker whose content is `text`; -1 when absent. */
function markerIndex(text: string): number {
  return texts().indexOf(text);
}

function resetStore(history: Message[] = [], start = 0, total = history.length, extra: Partial<Session> = {}) {
  pendingPages = [];
  fetchSessionHistory.mockClear();
  useSessionStore.setState({
    sessions: [sess(history, { historyStart: start, historyTotal: total, ...extra })],
    currentSessionId: 'A',
    currentMessages: history.slice(),
    hasMoreMessages: start > 0,
    historyLoading: false,
    initialLoading: false,
    historyLoadEnd: start,
    historyWindowStarts: { A: start },
    liveStreamBuffers: {},
    terminalWatermarks: {},
    _selectionSeq: {},
    _historyPageSeq: {},
    _historyRefreshSeq: {},
    _loadSeq: 0,
    _sessionWsTouchedSeq: {},
    _sessionLocalTouchedSeq: {},
    _sessionEventPatches: {},
    _deliveredQueueIds: {},
    _pendingQueueIds: {},
    serverEpoch: 'server',
    sessionTranscripts: {},
  });
}

async function resolvePage(promise: Promise<void>, page: Omit<Page, 'hasMore'> & { hasMore?: boolean }) {
  await act(async () => {
    const resolve = pendingPages.shift();
    expect(resolve, 'expected an in-flight history request').toBeTruthy();
    resolve!({ hasMore: page.start > 0, ...page });
    await promise;
  });
}

describe('R1/P0 — ordered two-round results keep earlier analysis/tool and anchor DONE', () => {
  beforeEach(() => resetStore([msg('user', 'question', 'u')]));

  it('keeps user→analysis→tool→final order across result (probe: ordered-stream-after-result)', () => {
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [msg('assistant', 'analysis', 'a'), msg('tool', 'tool', 't'), msg('assistant', 'final', 'f')],
        meta,
      );
    });
    expect(texts()).toEqual(['question', 'analysis', 'tool', 'final']);

    act(() => {
      store.reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta);
    });
    expect(texts()).toEqual(['question', 'analysis', 'tool', 'final']);
    expect(view().map((m) => m.id)).toEqual(['u', 'a', 't', 'f']);
  });

  it('does not lose turn-1 blocks on turn 2 and keeps DONE-1 inside turn 1 (probe: second-result-loses-earlier-turn-blocks)', () => {
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [msg('assistant', 'analysis', 'a'), msg('tool', 'tool', 't'), msg('assistant', 'final', 'f')],
        meta,
      );
      store.reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta);
      store.addMessage({ role: 'system', content: 'DONE-1', nativeItemId: 'done-1' });
      store.appendDeliveredMessages('A', [{ role: 'user', content: 'question2', queueItemIds: ['q2'] }]);
      const second = { ...meta, taskSeq: 2, taskId: 'task-2' };
      store.applyLiveStream(
        'A',
        [msg('assistant', 'second analysis', 'a2'), msg('assistant', 'second final', 'f2')],
        second,
      );
      store.reconcileWorkerResult('A', { result: 'second final' }, second);
    });

    expect(texts()).toEqual([
      'question',
      'analysis',
      'tool',
      'final',
      'DONE-1',
      'question2',
      'second analysis',
      'second final',
    ]);
    // DONE-1 is anchored by task identity: after every turn-1 row, before turn 2.
    expect(markerIndex('DONE-1')).toBeGreaterThan(markerIndex('final'));
    expect(markerIndex('DONE-1')).toBeLessThan(markerIndex('question2'));
    expect(markerIndex('second final')).toBeGreaterThan(markerIndex('question2'));
  });

  it('handles a real CBC id-less shape with no fabricated item ids (probe: idless-provider-result)', () => {
    resetStore([{ role: 'user', content: 'question' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [
          { role: 'assistant', content: 'interim' },
          { role: 'tool', content: 'tool' },
          { role: 'assistant', content: 'final' },
        ],
        meta,
      );
      store.reconcileWorkerResult('A', { result: 'final' }, meta);
    });
    expect(texts()).toEqual(['question', 'interim', 'tool', 'final']);
    expect(view().every((m) => m.id === null)).toBe(true);
  });

  it('keeps exactly one canonical assistant when the result is shorter than the streamed text', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [{ role: 'assistant', content: 'streamed answer that is much longer' }],
        meta,
      );
      store.reconcileWorkerResult('A', { result: 'short', status: 'done' }, meta);
    });
    expect(texts()).toEqual(['q', 'short']);
  });

  it('appends a result that is longer than the streamed text without duplicating it', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'part' }], meta);
      store.reconcileWorkerResult('A', { result: 'part and the rest', status: 'done' }, meta);
    });
    expect(texts()).toEqual(['q', 'part and the rest']);
  });

  it('accepts a result-only turn with no streamed block', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.reconcileWorkerResult('A', { result: 'only result', status: 'done' }, meta);
    });
    expect(texts()).toEqual(['q', 'only result']);
  });

  it('is idempotent for a repeated or older result of the same task', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [{ role: 'assistant', content: 'analysis' }, { role: 'assistant', content: 'final' }],
        meta,
      );
      store.reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta);
      // Replay of the same result and a late older-task result must not append.
      store.reconcileWorkerResult('A', { result: 'final', status: 'done' }, meta);
      store.reconcileWorkerResult(
        'A',
        { result: 'stale final', status: 'done' },
        { ...meta, taskSeq: 1, taskId: 'task-1' },
      );
    });
    expect(texts()).toEqual(['q', 'analysis', 'final']);
  });

  it('appends a marker for a task whose final block is missing (cancelled/empty result)', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'interim' }], meta);
      store.addMessage({ role: 'system', content: 'DONE-1', nativeItemId: 'done-1' });
      const second = { ...meta, taskSeq: 2, taskId: 'task-2' };
      store.applyLiveStream('A', [{ role: 'assistant', content: 'second' }], second);
      store.addMessage({ role: 'system', content: 'DONE-2', nativeItemId: 'done-2' });
    });
    expect(markerIndex('DONE-1')).toBeGreaterThan(markerIndex('interim'));
    expect(markerIndex('DONE-1')).toBeLessThan(markerIndex('second'));
    expect(markerIndex('DONE-2')).toBeGreaterThan(markerIndex('second'));
  });
});

describe('R1/P0 — cross-task same text and compound blocks', () => {
  it('does not swallow a same-text reply from a different task (probe: different-task-idless-same-text-swallowed)', () => {
    resetStore([{ role: 'assistant', content: 'same reply' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.appendDeliveredMessages('A', [{ role: 'user', content: 'new question', queueItemIds: ['new-q'] }]);
      store.applyLiveStream('A', [{ role: 'assistant', content: 'same reply' }], meta);
    });
    expect(texts()).toEqual(['same reply', 'new question', 'same reply']);
  });

  it('keeps two same-text blocks inside one task distinct', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [{ role: 'assistant', content: 'echo' }, { role: 'assistant', content: 'echo' }],
        meta,
      );
    });
    expect(texts()).toEqual(['q', 'echo', 'echo']);
  });

  it('keeps a compound block (text + tool + text) ordered and does not merge it into one row', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [
          { role: 'assistant', content: 'lead' },
          { role: 'tool', content: 'Bash({})' },
          { role: 'assistant', content: 'trail' },
        ],
        meta,
      );
    });
    expect(texts()).toEqual(['q', 'lead', 'Bash({})', 'trail']);
  });

  it('does not let a tool row overwrite an assistant row that shares the native id', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [{ role: 'assistant', content: 'answer', nativeItemId: 'shared' } as Message],
        meta,
      );
      store.applyLiveStream(
        'A',
        [
          { role: 'assistant', content: 'answer', nativeItemId: 'shared' } as Message,
          { role: 'tool', content: 'Bash({})', nativeItemId: 'shared' } as Message,
        ],
        meta,
      );
    });
    expect(texts()).toEqual(['q', 'answer', 'Bash({})']);
  });

  it('applies repeated and reversed deltas as a cumulative replace, not an append storm', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'ab' }], meta);
      store.applyLiveStream('A', [{ role: 'assistant', content: 'abc' }], meta);
      // A delayed shorter replay of the same task's cumulative text.
      store.applyLiveStream('A', [{ role: 'assistant', content: 'ab' }], meta);
      store.applyLiveStream('A', [{ role: 'assistant', content: 'abcd' }], meta);
    });
    expect(texts()).toEqual(['q', 'abcd']);
  });
});

describe('R1/P0 — cached projection index must be identity-validated', () => {
  it('does not overwrite a user row after an older history page is prepended (probe: cached-index-after-prepend)', () => {
    resetStore([msg('user', 'question', 'u')]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [msg('assistant', 'live', 'a')], meta);
      useSessionStore.setState((s) => ({
        currentMessages: [msg('assistant', 'old-history', 'old'), ...s.currentMessages],
      }));
      store.applyLiveStream('A', [msg('assistant', 'live updated', 'a')], meta);
    });
    expect(texts()).toEqual(['old-history', 'question', 'live updated']);
    const ids = useSessionStore.getState().currentMessages.map((m) => m.messageId ?? m.nativeItemId);
    expect(ids).toEqual(['old', 'u', 'a']);
  });
});

describe('R1/P1 — history pagination window, revision and epoch', () => {
  it('keeps the oldest loaded offset when a tail refresh overlaps a later older page (probe: tail-refresh-then-load-overlapping-old-page)', async () => {
    const full = Array.from({ length: 6 }, (_, i) => msg(i % 2 ? 'assistant' : 'user', `m${i}`, `m${i}`));
    resetStore(full, 0, 6);

    const refresh = useSessionStore.getState().refreshCurrentSessionHistory();
    await resolvePage(refresh, { history: full.slice(4), total: 6, start: 4, historyRevision: 11, historyEpoch: 'hist' });
    expect(useSessionStore.getState().historyWindowStarts.A).toBe(0);

    useSessionStore.setState({ historyLoading: false, historyLoadEnd: 4 });
    const older = useSessionStore.getState().loadOlderMessages();
    await resolvePage(older, { history: full.slice(2, 4), total: 6, start: 2, historyRevision: 11, historyEpoch: 'hist' });

    expect(texts()).toEqual(full.map((m) => m.content));
    expect(useSessionStore.getState().historyWindowStarts.A).toBe(0);
  });

  it('rejects a same-epoch response carrying an older revision (probe: history-revision-regression)', async () => {
    resetStore([msg('assistant', 'revision10', 'same')]);
    const refresh = useSessionStore.getState().refreshCurrentSessionHistory();
    await resolvePage(refresh, {
      history: [msg('assistant', 'revision9', 'same')],
      total: 1,
      start: 0,
      historyRevision: 9,
      historyEpoch: 'hist',
    });
    expect(texts()).toEqual(['revision10']);
    expect(useSessionStore.getState().sessions[0]?.historyRevision).toBe(10);
  });

  it('drops the old canonical tail when the history epoch changes (probe: history-epoch-replacement-removes-old-tail)', async () => {
    resetStore([msg('user', 'old user', 'old-u'), msg('assistant', 'old answer', 'old-a')]);
    const refresh = useSessionStore.getState().refreshCurrentSessionHistory();
    await resolvePage(refresh, {
      history: [msg('user', 'replacement', 'new-u')],
      total: 1,
      start: 0,
      historyRevision: 20,
      historyEpoch: 'replacement-epoch',
    });
    expect(texts()).toEqual(['replacement']);
  });

  it('still accepts an older disjoint page inside a stable append-only epoch (gap filling)', async () => {
    const full = Array.from({ length: 8 }, (_, i) => msg(i % 2 ? 'assistant' : 'user', `m${i}`, `m${i}`));
    resetStore(full.slice(4), 4, 8);
    const older = useSessionStore.getState().loadOlderMessages();
    await resolvePage(older, { history: full.slice(0, 4), total: 8, start: 0, historyRevision: 8, historyEpoch: 'hist' });
    expect(texts()).toEqual(full.map((m) => m.content));
    expect(useSessionStore.getState().historyWindowStarts.A).toBe(0);
  });

  it('loads 0..149 and keeps oldest at 0 after a tail refresh (stage3 expectation corrected)', async () => {
    const full = Array.from({ length: 150 }, (_, i) => msg(i % 2 ? 'assistant' : 'user', `m${i}`, `m-${i}`));
    resetStore(full.slice(0, 100), 0, 150);
    const refresh = useSessionStore.getState().refreshCurrentSessionHistory();
    await resolvePage(refresh, { history: full.slice(100), total: 150, start: 100, historyRevision: 151, historyEpoch: 'hist' });
    expect(texts()).toEqual(full.map((m) => m.content));
    expect(useSessionStore.getState().historyWindowStarts.A).toBe(0);
    expect(useSessionStore.getState().sessions[0]?.historyTotal).toBe(150);
  });
});

describe('R1/P1 — terminal coverage and late events', () => {
  beforeEach(() => resetStore([msg('user', 'question', 'u')]));

  it('keeps the seen body when the terminal result is covered but a late delta arrives', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'answer' }], meta);
      store.reconcileWorkerResult(
        'A',
        { result: 'answer', status: 'done' } as never,
        meta,
      );
      // A late delta from the finished task must not resurrect a live row.
      store.applyLiveStream('A', [{ role: 'assistant', content: 'ans' }], meta);
      store.applyWorkerStatus('A', 'idle', meta, true);
    });
    expect(texts()).toEqual(['q', 'answer']);
  });

  it('does not let an old idle status reorder or clear the finished transcript', () => {
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'one' }], meta);
      store.reconcileWorkerResult('A', { result: 'one', status: 'done' }, meta);
      const second = { ...meta, taskSeq: 2, taskId: 'task-2' };
      store.applyLiveStream('A', [{ role: 'assistant', content: 'two' }], second);
      store.reconcileWorkerResult('A', { result: 'two', status: 'done' }, second);
      // Late status for task 1.
      store.applyWorkerStatus('A', 'idle', meta, true);
    });
    expect(texts()).toEqual(['question', 'one', 'two']);
  });

  it('resolves result-before-idle into the same final transcript (no double assistant)', () => {
    resetStore([{ role: 'user', content: 'q' }]);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream('A', [{ role: 'assistant', content: 'par' }], meta);
      store.reconcileWorkerResult('A', { result: 'partial complete', status: 'done' }, meta);
      store.applyWorkerStatus('A', 'idle', meta, true);
    });
    expect(texts()).toEqual(['q', 'partial complete']);
    expect(view().filter((m) => m.role === 'assistant')).toHaveLength(1);
  });
});

describe('R1/P2 — canonical projection is not replaced by summary-only snapshots', () => {
  it('keeps the full turn when a summary refresh reports the same total', async () => {
    const full = [msg('user', 'u0', 'u0'), msg('assistant', 'a0', 'a0')];
    resetStore(full, 0, 2);
    const store = useSessionStore.getState();
    act(() => {
      store.applyLiveStream(
        'A',
        [msg('assistant', 'streaming', 's'), msg('tool', 'tool', 't'), msg('assistant', 'final', 'f')],
        { ...meta, taskSeq: 2, taskId: 'task-2' },
      );
    });
    expect(texts()).toEqual(['u0', 'a0', 'streaming', 'tool', 'final']);
  });
});
