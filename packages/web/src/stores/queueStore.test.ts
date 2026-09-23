// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  acquireSessionQueueItemEdit: vi.fn(),
  fetchSessionQueue: vi.fn(),
  enqueueSessionMessage: vi.fn(),
  releaseSessionQueueItemEdit: vi.fn(),
  deleteSessionQueueItem: vi.fn(),
  updateSessionQueueItem: vi.fn(),
  reorderSessionQueue: vi.fn(),
}));

vi.mock('@/services/api', () => api);

import { useQueueStore } from './queueStore';
import { useSessionStore } from './sessionStore';
import { useWorkerStore } from './workerStore';
import type { Session } from '@/types';

type Source = 'user' | 'agent' | 'report' | 'qq';
type Kind = 'task' | 'report' | 'qq';

function item(id: string, text: string, source: Source = 'user', kind: Kind = 'task') {
  return {
    id,
    queueItemId: id,
    text,
    source,
    kind,
    createdAt: '2026-09-01T00:00:00Z',
    meta: { dispatchState: 'queued' as const, revision: 1 },
  };
}

function snapshot(items: ReturnType<typeof item>[], revision = 1) {
  Object.defineProperty(items, 'queueRevision', { value: revision, enumerable: false });
  return items;
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ currentSessionId: 's1', sessions: [], currentMessages: [] });
  useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null });
  useQueueStore.setState({
    queues: {},
    agentQueues: {},
    edits: {},
    batchSend: {},
    sendingId: null,
    panelOpen: false,
    agentQueueLoadSeq: {},
    queueRevisions: {},
    queueTombstones: {},
    queueDeliveredIds: {},
  });
  vi.clearAllMocks();
  api.acquireSessionQueueItemEdit.mockResolvedValue({ expiresAt: Date.now() + 300_000 });
  api.releaseSessionQueueItemEdit.mockResolvedValue(undefined);
});

describe('server-backed queue store', () => {
  it('does not resurrect a delivered item when a stale ACK or prefixed id arrives', () => {
    const queued = item('q-delivery', 'queued');
    useQueueStore.setState({
      queues: { s1: [queued] },
      agentQueues: { s1: [queued] },
      queueRevisions: { s1: 1 },
    });

    useQueueStore.getState().applyQueueEvent({
      type: 'queue.item_delivered',
      sessionId: 's1',
      queueItemIds: ['queue:q-delivery'],
      queueRevision: 2,
    });
    useQueueStore.getState().applyQueueEvent({
      type: 'queue.item_added',
      sessionId: 's1',
      item: queued,
      queueRevision: 1,
    });

    expect(useQueueStore.getState().queues.s1).toEqual([]);
    expect(useQueueStore.getState().queueRevisions.s1).toBe(2);
  });

  it('keeps a queue edit transaction locked until its PATCH settles', async () => {
    const first = item('q-edit-lock', 'first');
    useQueueStore.setState({ queues: { s1: [first] }, queueRevisions: { s1: 1 } });
    let resolveEdit!: (value: unknown) => void;
    api.updateSessionQueueItem.mockReturnValueOnce(new Promise((resolve) => { resolveEdit = resolve; }));
    api.enqueueSessionMessage.mockResolvedValue({ item: item('q-new', 'new'), queueRevision: 2 });

    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));
    useQueueStore.getState().updateEditDraft('first');
    useQueueStore.getState().saveEdit();
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.saving).toBe(true));
    await expect(useQueueStore.getState().enqueue('new')).resolves.toBe(false);
    expect(api.enqueueSessionMessage).not.toHaveBeenCalled();

    resolveEdit({ item: { ...first, text: 'first' }, queueRevision: 2 });
    api.fetchSessionQueue.mockResolvedValue(snapshot([{ ...first, text: 'first' }], 2));
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1).toBeNull());
  });

  it('applies raw and prefixed queue edit identities in place', () => {
    const queued = item('q-edit-identity', 'before');
    useQueueStore.setState({
      queues: { s1: [{ ...queued, id: 'queue:q-edit-identity' }] },
      agentQueues: { s1: [{ ...queued, id: 'queue:q-edit-identity' }] },
      queueRevisions: { s1: 1 },
    });
    useSessionStore.setState({
      sessions: [{
        id: 's1', name: 's1', adapter: 'cbc', alwaysThinkingEnabled: false, effort: '',
        history: [],
      }],
      currentSessionId: 's1',
      currentMessages: [],
    });
    useSessionStore.getState().appendQueuedMessage('s1', queued);

    useQueueStore.getState().applyQueueEvent({
      type: 'queue.item_updated',
      sessionId: 's1',
      queueItemId: 'q-edit-identity',
      queueRevision: 2,
      item: { ...queued, id: 'q-edit-identity', text: 'after' },
    });

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.text)).toEqual(['after']);
    expect(useSessionStore.getState().currentMessages[0]).toMatchObject({
      content: 'after',
      queueItemIds: ['q-edit-identity'],
    });
  });

  it('loads only the server snapshot and never restores localStorage business state', async () => {
    localStorage.setItem('pan.sendQueue.s1', JSON.stringify([{ id: 'stale', text: 'stale' }]));
    api.fetchSessionQueue.mockResolvedValue(snapshot([item('q-server', 'authoritative')], 7));

    await useQueueStore.getState().loadAgentQueue('s1');

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-server']);
    expect(useQueueStore.getState().queueRevisions.s1).toBe(7);
    expect(localStorage.getItem('pan.sendQueue.s1')).toContain('stale');
  });

  it('adds a server-confirmed item and preserves its native identity', async () => {
    const queued = item('q-native', 'hello');
    api.enqueueSessionMessage.mockResolvedValue({ item: queued, queueRevision: 3 });
    useSessionStore.setState({ currentSessionId: 's1' });

    await expect(useQueueStore.getState().enqueue('hello')).resolves.toBe(true);

    expect(api.enqueueSessionMessage).toHaveBeenCalledWith('s1', 'hello', expect.any(String));
    expect(useQueueStore.getState().queues.s1).toEqual([queued]);
    expect(useQueueStore.getState().queues.s1?.[0]?.id).toBe('q-native');
  });

  it('keeps the server snapshot unchanged when enqueue fails', async () => {
    api.enqueueSessionMessage.mockRejectedValue(new Error('offline'));
    useQueueStore.setState({ queues: { s1: snapshot([item('q-old', 'old')], 2) } });

    await expect(useQueueStore.getState().enqueue('not queued')).resolves.toBe(false);

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-old']);
    expect(localStorage.getItem('pan.sendQueue.s1')).toBeNull();
  });

  it('uses the captured Session and client id when the UI transaction outlives a switch', async () => {
    const queued = item('q-captured', 'captured');
    api.enqueueSessionMessage.mockResolvedValue({ item: queued, queueRevision: 6 });
    useSessionStore.setState({ currentSessionId: 's2' });

    await expect(
      useQueueStore.getState().enqueue('captured', undefined, 's1', 'client-stable'),
    ).resolves.toBe(true);

    expect(api.enqueueSessionMessage).toHaveBeenCalledWith('s1', 'captured', 'client-stable');
    expect(useQueueStore.getState().queues.s1).toEqual([queued]);
    expect(useQueueStore.getState().queues.s2).toBeUndefined();
  });

  it('keeps the server queue update but skips optimistic history when explicitly disabled', async () => {
    const queued = item('q-live', 'live path');
    api.enqueueSessionMessage.mockResolvedValue({ item: queued, queueRevision: 6 });

    await expect(
      useQueueStore.getState().enqueue('live path', undefined, 's1', 'client-live', {
        appendOptimisticHistory: false,
      }),
    ).resolves.toBe(true);

    expect(useQueueStore.getState().queues.s1).toEqual([queued]);
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('uses the current Session runtime worker at the append boundary', async () => {
    const queued = item('q-runtime', 'runtime path');
    api.enqueueSessionMessage.mockResolvedValue({ item: queued, queueRevision: 7 });
    useWorkerStore.setState({
      workers: { s1: { id: 'w-runtime', sessionId: 's1', status: 'running' } },
    });

    await expect(useQueueStore.getState().enqueue('runtime path')).resolves.toBe(true);

    expect(useQueueStore.getState().queues.s1).toEqual([queued]);
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('edits a queued user item through the server while retaining its identity', async () => {
    const first = item('q-first', 'first');
    const edited = { ...first, text: 'first edited', meta: { ...first.meta, revision: 2 } };
    useQueueStore.setState({ queues: { s1: snapshot([first], 4) }, queueRevisions: { s1: 4 } });
    let resolveEdit!: (value: { item: typeof edited; queueRevision: number }) => void;
    api.updateSessionQueueItem.mockReturnValueOnce(new Promise((resolve) => { resolveEdit = resolve; }));
    api.fetchSessionQueue.mockResolvedValue(snapshot([edited], 5));

    useQueueStore.getState().startEdit('q-first');
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));
    useQueueStore.getState().updateEditDraft('first edited');
    useQueueStore.getState().saveEdit();
    useQueueStore.getState().saveEdit();
    expect(api.updateSessionQueueItem).toHaveBeenCalledTimes(1);
    expect(useQueueStore.getState().edits.s1?.text).toBe('first edited');
    expect(useQueueStore.getState().queues.s1).toEqual([first]);
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-first']);
    resolveEdit({ item: edited, queueRevision: 5 });
    await vi.waitFor(() => expect(api.updateSessionQueueItem).toHaveBeenCalled());
    await vi.waitFor(() =>
      expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('first edited'),
    );

    expect(api.updateSessionQueueItem).toHaveBeenCalledWith(
      's1', 'q-first', 'first edited', 1, expect.any(String),
    );
    expect(useQueueStore.getState().queues.s1).toHaveLength(1);
    expect(useQueueStore.getState().queues.s1?.[0]?.id).toBe('q-first');
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1).toBeNull());
  });

  it('updates the existing chat projection before canonical delivery, without appending a row', async () => {
    const first = item('q-edit-projection', 'before edit');
    const session = {
      id: 's1',
      name: 's1',
      alwaysThinkingEnabled: false,
      effort: '',
      history: [],
      historyTotal: 0,
    } satisfies Session;
    const edited = { ...first, text: 'after edit', meta: { ...first.meta, revision: 2 } };
    useSessionStore.setState({
      sessions: [session],
      currentSessionId: 's1',
      currentMessages: [],
    });
    // Use the production projection entry point so the durable queue identity
    // is registered as pending. Canonical history rows must never become
    // editable merely because a fixture gives them a matching-looking id.
    useSessionStore.getState().appendQueuedMessage('s1', first);
    useQueueStore.setState({
      queues: { s1: snapshot([first], 4) },
      queueRevisions: { s1: 4 },
    });
    api.updateSessionQueueItem.mockResolvedValue({ item: edited, queueRevision: 5 });
    api.fetchSessionQueue.mockResolvedValue(snapshot([edited], 5));

    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));
    useQueueStore.getState().updateEditDraft(edited.text);
    useQueueStore.getState().saveEdit();
    await vi.waitFor(() => expect(api.updateSessionQueueItem).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(useSessionStore.getState().currentMessages.map((message) => message.content))
        .toEqual(['after edit']),
    );

    // The delivery carries the same durable queue identity. It must reconcile
    // with the edited projection instead of adding a second user row.
    useSessionStore.getState().appendDeliveredMessages('s1', [{
      role: 'user', content: 'after edit', queueItemIds: [first.id],
    }]);
    expect(useSessionStore.getState().currentMessages).toHaveLength(1);
    expect(useSessionStore.getState().currentMessages[0]).toMatchObject({
      content: 'after edit',
      queueItemIds: [first.id],
    });
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1).toBeNull());
  });

  it('does not change chat history when an edit is cancelled', async () => {
    const first = item('q-cancel', 'keep this');
    const localMessage = { role: 'user', content: first.text, queueItemIds: [first.id] };
    const session = {
      id: 's1',
      name: 's1',
      alwaysThinkingEnabled: false,
      effort: '',
      history: [localMessage],
      historyTotal: 1,
    } satisfies Session;
    useSessionStore.setState({
      sessions: [session],
      currentSessionId: 's1',
      currentMessages: [localMessage],
    });
    useQueueStore.setState({ queues: { s1: snapshot([first], 1) } });

    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));
    useQueueStore.getState().updateEditDraft('discarded');
    useQueueStore.getState().cancelEdit();

    expect(api.updateSessionQueueItem).not.toHaveBeenCalled();
    expect(useSessionStore.getState().currentMessages).toEqual([localMessage]);
    expect(useSessionStore.getState().sessions[0]?.history).toEqual([localMessage]);
  });

  it('rejects an imperative enqueue while the target Session has an active edit', async () => {
    const first = item('q-imperative-guard', 'queued original');
    api.enqueueSessionMessage.mockResolvedValue({ item: item('q-new', 'must not enqueue'), queueRevision: 2 });
    useQueueStore.setState({ queues: { s1: snapshot([first], 1) } });
    useQueueStore.getState().startEdit(first.id);

    await expect(useQueueStore.getState().enqueue('imperative send', undefined, 's1', 'client-guard'))
      .resolves.toBe(false);
    expect(api.enqueueSessionMessage).not.toHaveBeenCalled();
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual([first.id]);
  });

  it('keeps one edit transaction and writes the captured Session after a switch', async () => {
    const first = item('q-switch-edit', 'A before');
    const edited = { ...first, text: 'A after', meta: { ...first.meta, revision: 2 } };
    let resolveUpdate!: (value: { item: typeof edited; queueRevision: number }) => void;
    const update = new Promise<{ item: typeof edited; queueRevision: number }>((resolve) => {
      resolveUpdate = resolve;
    });
    api.updateSessionQueueItem.mockReset().mockReturnValue(update);
    api.fetchSessionQueue.mockResolvedValue(snapshot([edited], 2));
    useSessionStore.setState({
      currentSessionId: 's1',
      sessions: [
        { id: 's1', name: 'A', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 's2', name: 'B', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      currentMessages: [],
    });
    useQueueStore.setState({ queues: { s1: snapshot([first], 1) } });

    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));
    useQueueStore.getState().updateEditDraft(edited.text);
    useQueueStore.getState().saveEdit();
    useQueueStore.getState().saveEdit();
    expect(api.updateSessionQueueItem).toHaveBeenCalledTimes(1);
    await expect(useQueueStore.getState().enqueue(
      'send while edit PATCH is pending',
      undefined,
      's1',
      'client-pending-edit',
    )).resolves.toBe(false);

    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [] });
    expect(useQueueStore.getState().edits.s1?.text).toBe('A after');
    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    resolveUpdate({ item: edited, queueRevision: 2 });
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1).toBeNull());

    expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('A after');
    expect(useQueueStore.getState().queues.s2).toBeUndefined();
    expect(useQueueStore.getState().edits.s2).toBeUndefined();
    expect(useSessionStore.getState().currentSessionId).toBe('s2');
  });

  it('releases a late lease after cancel without clearing the other Session edit', async () => {
    const first = item('q-late-acquire', 'A original');
    const second = item('q-session-b-edit', 'B original');
    let resolveAcquire!: (value: { expiresAt: number }) => void;
    api.acquireSessionQueueItemEdit.mockReturnValueOnce(
      new Promise((resolve) => { resolveAcquire = resolve; }),
    );
    useQueueStore.setState({ queues: { s1: [first], s2: [second] } });

    useQueueStore.getState().startEdit(first.id);
    const oldToken = useQueueStore.getState().edits.s1?.serverToken;
    useQueueStore.getState().cancelEdit();
    useSessionStore.setState({ currentSessionId: 's2' });
    useQueueStore.getState().startEdit(second.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s2?.acquiring).toBe(false));

    resolveAcquire({ expiresAt: Date.now() + 300_000 });
    await vi.waitFor(() => expect(api.releaseSessionQueueItemEdit).toHaveBeenCalledWith(
      's1', first.id, oldToken,
    ));
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1).toBeNull());
    expect(useQueueStore.getState().edits.s2?.id).toBe(second.id);
    expect(useQueueStore.getState().queues.s2?.map((entry) => entry.id)).toEqual([second.id]);
  });

  it('keeps an edit lock scoped to its Session when sending in another Session', async () => {
    const first = item('q-edit-session-a', 'A queued');
    const sentInB = item('q-send-session-b', 'B message');
    useQueueStore.setState({ queues: { s1: [first] } });
    api.enqueueSessionMessage.mockResolvedValue({ item: sentInB, queueRevision: 1 });
    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));

    useSessionStore.setState({ currentSessionId: 's2' });
    await expect(useQueueStore.getState().enqueue('B message', undefined, 's2', 'client-b'))
      .resolves.toBe(true);

    expect(api.enqueueSessionMessage).toHaveBeenCalledWith('s2', 'B message', 'client-b');
    expect(useQueueStore.getState().edits.s1?.id).toBe(first.id);
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual([first.id]);
    expect(useQueueStore.getState().queues.s2?.map((entry) => entry.id)).toEqual([sentInB.id]);
  });

  it('clears a same-Session edit when the queue item is removed elsewhere', async () => {
    const first = item('q-removed-while-editing', 'original');
    useQueueStore.setState({ queues: { s1: snapshot([first], 1) }, queueRevisions: { s1: 1 } });
    useQueueStore.getState().startEdit(first.id);
    await vi.waitFor(() => expect(useQueueStore.getState().edits.s1?.acquiring).toBe(false));

    useQueueStore.getState().applyQueueEvent({
      type: 'queue.item_removed',
      sessionId: 's1',
      queueItemId: first.id,
      queueRevision: 2,
    });

    expect(useQueueStore.getState().edits.s1).toBeNull();
    expect(useQueueStore.getState().queues.s1).toEqual([]);
    expect(useQueueStore.getState().queueTombstones.s1?.has(first.id)).toBe(true);
  });

  it('reorders any queued source through one server order operation', async () => {
    const user = item('q-user', 'user');
    const agent = item('q-agent', 'agent', 'agent');
    const report = item('q-report', 'report', 'report', 'report');
    const qq = item('q-qq', 'qq', 'qq', 'qq');
    const current = snapshot([user, agent, report, qq], 8);
    const reordered = snapshot([user, report, agent, qq], 9);
    useQueueStore.setState({ queues: { s1: current }, queueRevisions: { s1: 8 } });
    api.reorderSessionQueue.mockResolvedValue(reordered);

    await useQueueStore.getState().moveQueueItem('q-agent', 1);

    expect(api.reorderSessionQueue).toHaveBeenCalledWith(
      's1',
      ['q-user', 'q-report', 'q-agent', 'q-qq'],
      8,
    );
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual([
      'q-user',
      'q-report',
      'q-agent',
      'q-qq',
    ]);
  });

  it('removes only the requested queued item through the server', async () => {
    const first = item('q-first', 'first');
    const second = item('q-second', 'second');
    useQueueStore.setState({ queues: { s1: snapshot([first, second], 3) } });
    api.deleteSessionQueueItem.mockResolvedValue({ ok: true });
    api.fetchSessionQueue.mockResolvedValue(snapshot([second], 4));

    await useQueueStore.getState().removeAgentItem('q-first');

    expect(api.deleteSessionQueueItem).toHaveBeenCalledWith('s1', 'q-first');
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-second']);
  });

  it('removes delivered items immediately and records the delivery revision', () => {
    const delivered = item('q-delivered', 'already handed off');
    const stillQueued = item('q-still-queued', 'backlog');
    useQueueStore.setState({
      queues: { s1: snapshot([delivered, stillQueued], 4) },
      agentQueues: { s1: snapshot([delivered, stillQueued], 4) },
      queueRevisions: { s1: 4 },
    });

    useQueueStore.getState().applyQueueEvent({
      type: 'queue.item_delivered',
      sessionId: 's1',
      queueItemIds: ['q-delivered'],
      queueRevision: 5,
    });

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual([
      'q-still-queued',
    ]);
    expect(useQueueStore.getState().queueRevisions.s1).toBe(5);
  });
});
