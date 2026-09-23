// @vitest-environment jsdom
/**
 * Re-audit investigation suite — queue ACK / tombstone / edit / failure
 * recovery. These are POSITIVE CONTROLS: the current implementation passes
 * them, so the next TA does not need to re-derive this behaviour from scratch.
 *
 * Baseline: main 591367a65f88e9d5270e8e99920ef5448d1d68c9.
 *
 * Q7/Q8 were RED on that baseline (two real gaps found by the follow-up
 * audit): an equal-revision authoritative full GET could not repair a stale
 * projection after a queue.snapshot hint or a failed reorder.  Fixed by the
 * fresh-observation repair path in loadAgentQueue; Q9/Q10 pin the
 * same-clientMessageId business retry and the outcome-unknown timeout path.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';

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

import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import type { AgentQueueItem, Session } from '@/types';

function qi(id: string, text: string, revision = 1): AgentQueueItem {
  return {
    id, queueItemId: id, kind: 'task', text, createdAt: 1, source: 'user',
    meta: { dispatchState: 'queued', revision },
  };
}

function sess(): Session {
  return { id: 'A', name: 'A', adapter: 'cbc', alwaysThinkingEnabled: false, effort: '', history: [], historyTotal: 0 };
}

describe('reaudit · queue ACK / tombstone / edit / recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({
      sessions: [sess()], currentSessionId: 'A', currentMessages: [],
      _pendingQueueIds: {}, _deliveredQueueIds: {}, _sessionLocalTouchedSeq: {},
    });
    useQueueStore.setState({
      queues: {}, agentQueues: {}, queueRevisions: {}, queueTombstones: {}, queueDeliveredIds: {},
      edits: {}, batchSend: {}, sendingId: null, agentQueueLoadSeq: {}, panelOpen: false,
    } as never);
    useUIStore.setState({ toastQueue: [] });
    useWorkerStore.setState({ workers: {} });
    api.fetchSessionQueue.mockResolvedValue([]);
    api.acquireSessionQueueItemEdit.mockResolvedValue({ expiresAt: Date.now() + 300_000 });
    api.releaseSessionQueueItemEdit.mockResolvedValue(undefined);
  });

  it('Q1 · a late enqueue ACK (older revision) never revives a delivered item', async () => {
    let resolveEnqueue: (v: unknown) => void = () => {};
    api.enqueueSessionMessage.mockReturnValue(new Promise((res) => { resolveEnqueue = res; }));
    const pending = useQueueStore.getState().enqueue('hi', undefined, 'A', 'c1');

    await act(async () => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.item_delivered', sessionId: 'A', queueItemIds: ['q1'], queueRevision: 2,
        messages: [{ role: 'user', content: 'hi', queueItemIds: ['q1'] }],
      });
    });
    await act(async () => {
      resolveEnqueue({ item: qi('q1', 'hi', 1), queueRevision: 1 });
      await pending;
    });

    const q = useQueueStore.getState();
    expect(q.queues.A ?? []).toEqual([]);
    expect(q.queueRevisions.A).toBe(2);
    // The delivered user row is appended exactly once to canonical history.
    expect(useSessionStore.getState().sessions[0]!.history).toHaveLength(1);
  });

  it('Q2 · a late PATCH ACK cannot rewrite an already delivered row', async () => {
    let resolvePatch: (v: unknown) => void = () => {};
    api.updateSessionQueueItem.mockReturnValue(new Promise((res) => { resolvePatch = res; }));
    useQueueStore.setState({ queues: { A: [qi('q1', 'original', 1)] }, queueRevisions: { A: 1 } });
    useSessionStore.setState({
      sessions: [{ ...sess(), history: [{ role: 'user', content: 'original', queueItemIds: ['q1'] }] }],
      currentSessionId: 'A',
      currentMessages: [{ role: 'user', content: 'original', queueItemIds: ['q1'] }],
      _pendingQueueIds: { A: new Set(['q1']) },
    });
    act(() => useQueueStore.getState().startEdit('q1'));
    await vi.waitFor(() => expect(useQueueStore.getState().edits.A?.acquiring).toBe(false));
    act(() => useQueueStore.getState().updateEditDraft('edited'));
    act(() => useQueueStore.getState().saveEdit());

    await act(async () => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.item_delivered', sessionId: 'A', queueItemIds: ['q1'], queueRevision: 2,
        messages: [{ role: 'user', content: 'original', queueItemIds: ['q1'] }],
      });
    });
    await act(async () => { resolvePatch({ item: qi('q1', 'edited', 2), queueRevision: 2 }); await Promise.resolve(); });

    expect(useSessionStore.getState().currentMessages.map((x) => x.content)).toEqual(['original']);
  });

  it('Q3 · a failed delete keeps the item, records no tombstone, and toasts', async () => {
    api.deleteSessionQueueItem.mockRejectedValue(new Error('boom'));
    useQueueStore.setState({ queues: { A: [qi('q1', 'x')] }, queueRevisions: { A: 1 } });
    api.fetchSessionQueue.mockResolvedValue(Object.assign([qi('q1', 'x')], { queueRevision: 1 }));

    await act(async () => { await useQueueStore.getState().removeAgentItem('q1', 'A'); });

    expect((useQueueStore.getState().queues.A ?? []).map((x) => x.id)).toEqual(['q1']);
    expect([...(useQueueStore.getState().queueTombstones.A ?? [])]).toEqual([]);
    expect(useUIStore.getState().toastQueue.some((t) => t.type === 'error')).toBe(true);
  });

  it('Q4 · a tombstoned id cannot be re-added by a newer queue event', () => {
    useQueueStore.setState({ queues: { A: [] }, queueRevisions: { A: 5 }, queueTombstones: { A: new Set(['q1']) } });
    act(() => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.item_added', sessionId: 'A', queueItemId: 'q1', queueRevision: 6,
        item: qi('q1', 'resurrected') as unknown as Record<string, unknown>,
      });
    });
    expect(useQueueStore.getState().queues.A ?? []).toEqual([]);
  });

  it('Q5 · reorder applies optimistically then converges to the server snapshot', async () => {
    useQueueStore.setState({ queues: { A: [qi('q1', 'a'), qi('q2', 'b')] }, queueRevisions: { A: 3 } });
    api.reorderSessionQueue.mockResolvedValue(
      Object.assign([qi('q2', 'b'), qi('q1', 'a')], { queueRevision: 4 }),
    );
    await act(async () => { await useQueueStore.getState().moveQueueItem('q2', -1); });
    expect((useQueueStore.getState().queues.A ?? []).map((x) => x.id)).toEqual(['q2', 'q1']);
    expect(useQueueStore.getState().queueRevisions.A).toBe(4);
  });

  it('Q6 · an equal-revision snapshot with a different shape is idempotent (ignored)', () => {
    useQueueStore.setState({ queues: { A: [qi('q1', 'a')] }, queueRevisions: { A: 4 } });
    act(() => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.item_updated', sessionId: 'A', queueItemId: 'q1', queueRevision: 4,
        item: qi('q1', 'different-shape') as unknown as Record<string, unknown>,
      });
    });
    expect((useQueueStore.getState().queues.A ?? []).map((x) => x.text)).toEqual(['a']);
  });

  it('Q7 · after a queue.snapshot hint, the equal-revision full GET repairs a stale projection', async () => {
    // The delivery event was lost (WS gap); only the item-less snapshot hint
    // arrived, so the local revision advanced while the item list stayed
    // stale.  The authoritative full GET at the same revision must repair the
    // projection — the server state at that revision IS the business truth.
    api.fetchSessionQueue
      .mockResolvedValueOnce(Object.assign([qi('q1', 'a'), qi('q2', 'b')], { queueRevision: 5 }))
      .mockResolvedValueOnce(Object.assign([qi('q2', 'b')], { queueRevision: 6 }));

    await act(async () => { await useQueueStore.getState().loadAgentQueue('A'); });
    expect(useQueueStore.getState().queues.A?.map((x) => x.id)).toEqual(['q1', 'q2']);

    act(() => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.snapshot', sessionId: 'A', queueRevision: 6,
      });
    });
    expect(useQueueStore.getState().queueRevisions.A).toBe(6);
    // Still stale — the hint carried no items.
    expect(useQueueStore.getState().queues.A?.map((x) => x.id)).toEqual(['q1', 'q2']);

    await act(async () => { await useQueueStore.getState().loadAgentQueue('A'); });
    expect(useQueueStore.getState().queues.A?.map((x) => x.id)).toEqual(['q2']);
    expect(useQueueStore.getState().queueRevisions.A).toBe(6);
  });

  it('Q8 · a failed reorder converges back to the server order via the repair GET', async () => {
    api.fetchSessionQueue
      .mockResolvedValue(Object.assign([qi('q1', 'a'), qi('q2', 'b')], { queueRevision: 3 }));
    api.reorderSessionQueue.mockRejectedValue(new Error('queue revision conflict'));

    await act(async () => { await useQueueStore.getState().loadAgentQueue('A'); });
    await act(async () => {
      await useQueueStore.getState().moveQueueItem('q2', -1);
    });

    // The optimistic reorder was applied locally, then the mutation failed;
    // the failure path's authoritative GET (same revision, server state) must
    // restore the canonical order instead of leaving the divergence in place.
    expect(useQueueStore.getState().queues.A?.map((x) => x.id)).toEqual(['q1', 'q2']);
    expect(useQueueStore.getState().queueRevisions.A).toBe(3);
  });

  it('Q9 · a same-clientMessageId business retry resolves to the original receipt exactly once', async () => {
    // First send: accepted, queued.
    api.enqueueSessionMessage
      .mockResolvedValueOnce({ item: qi('q1', 'hi', 1), queueRevision: 1 });
    await act(async () => {
      await expect(useQueueStore.getState().enqueue('hi', undefined, 'A', 'c1'))
        .resolves.toBe(true);
    });
    expect(useQueueStore.getState().queues.A?.map((x) => x.id)).toEqual(['q1']);

    // Delivered before the outcome reached the user (the outcome-unknown gap).
    await act(async () => {
      useQueueStore.getState().applyQueueEvent({
        type: 'queue.item_delivered', sessionId: 'A', queueItemIds: ['q1'], queueRevision: 2,
        messages: [{ role: 'user', content: 'hi', queueItemIds: ['q1'] }],
      });
    });
    expect(useQueueStore.getState().queues.A ?? []).toEqual([]);

    // Business retry with the SAME client id: the server resolves the durable
    // ledger receipt (duplicate), so no second queue row and no second
    // delivered/history row may appear — and the id must not be shadowed
    // into an error path.
    api.enqueueSessionMessage
      .mockResolvedValueOnce({ item: qi('q1', 'hi', 1), queueRevision: 2, duplicate: true });
    await act(async () => {
      await expect(useQueueStore.getState().enqueue('hi', undefined, 'A', 'c1'))
        .resolves.toBe(true);
    });
    expect(api.enqueueSessionMessage).toHaveBeenCalledTimes(2);
    expect(useQueueStore.getState().queues.A ?? []).toEqual([]);
    expect(useSessionStore.getState().sessions[0]!.history).toHaveLength(1);
  });

  it('Q10 · a network timeout keeps the outcome unknown (no auto-resubmit, no phantom row)', async () => {
    api.enqueueSessionMessage.mockRejectedValue(new TypeError('network timeout'));
    await act(async () => {
      await expect(useQueueStore.getState().enqueue('hi', undefined, 'A', 'c1'))
        .resolves.toBe(false);
    });
    // The send transaction failed conservatively: no optimistic row, no
    // revision bump, exactly one attempt (retry is the user's decision).
    expect(useQueueStore.getState().queues.A ?? []).toEqual([]);
    expect(useQueueStore.getState().queueRevisions.A).toBeUndefined();
    expect(api.enqueueSessionMessage).toHaveBeenCalledTimes(1);
    expect(useUIStore.getState().toastQueue.some((t) => t.type === 'error')).toBe(true);
  });
});
