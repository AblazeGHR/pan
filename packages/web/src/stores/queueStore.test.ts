// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  fetchSessionQueue: vi.fn(),
  enqueueSessionMessage: vi.fn(),
  deleteSessionQueueItem: vi.fn(),
  updateSessionQueueItem: vi.fn(),
  reorderSessionQueue: vi.fn(),
}));

vi.mock('@/services/api', () => api);

import { useQueueStore } from './queueStore';
import { useSessionStore } from './sessionStore';

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
  useQueueStore.setState({
    queues: {}, agentQueues: {}, edits: {}, batchSend: {}, sendingId: null,
    panelOpen: false, agentQueueLoadSeq: {}, queueRevisions: {},
  });
  vi.clearAllMocks();
});

describe('server-backed queue store', () => {
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

  it('edits a queued user item through the server while retaining its identity', async () => {
    const first = item('q-first', 'first');
    const edited = { ...first, text: 'first edited', meta: { ...first.meta, revision: 2 } };
    useQueueStore.setState({ queues: { s1: snapshot([first], 4) }, queueRevisions: { s1: 4 } });
    api.updateSessionQueueItem.mockResolvedValue({ item: edited, queueRevision: 5 });
    api.fetchSessionQueue.mockResolvedValue(snapshot([edited], 5));

    useQueueStore.getState().startEdit('q-first');
    useQueueStore.getState().updateEditDraft('first edited');
    useQueueStore.getState().saveEdit();
    await vi.waitFor(() => expect(api.updateSessionQueueItem).toHaveBeenCalled());
    await vi.waitFor(() => expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('first edited'));

    expect(api.updateSessionQueueItem).toHaveBeenCalledWith('s1', 'q-first', 'first edited', 1);
    expect(useQueueStore.getState().queues.s1?.[0]?.id).toBe('q-first');
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
      's1', ['q-user', 'q-report', 'q-agent', 'q-qq'], 8,
    );
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id))
      .toEqual(['q-user', 'q-report', 'q-agent', 'q-qq']);
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
});
