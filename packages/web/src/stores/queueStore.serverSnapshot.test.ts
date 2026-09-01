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

function item(id: string, text: string, revision = 1) {
  return {
    id,
    queueItemId: id,
    text,
    source: 'user' as const,
    kind: 'task' as const,
    createdAt: '2026-09-01T00:00:00Z',
    meta: { dispatchState: 'queued' as const, revision },
  };
}

function snapshot(items: ReturnType<typeof item>[], revision: number) {
  Object.defineProperty(items, 'queueRevision', { value: revision, enumerable: false });
  return items;
}

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 's1', sessions: [], currentMessages: [] });
  useQueueStore.setState({
    queues: {}, agentQueues: {}, edits: {}, batchSend: {}, sendingId: null,
    panelOpen: false, agentQueueLoadSeq: {}, queueRevisions: {},
  });
  vi.clearAllMocks();
});

describe('server-backed queue snapshot', () => {
  it('loads the server snapshot without reading a local queue', async () => {
    const localGetItem = vi.spyOn(Storage.prototype, 'getItem');
    localStorage.setItem('pan.sendQueue.s1', JSON.stringify([{ id: 'stale', text: 'stale' }]));
    api.fetchSessionQueue.mockResolvedValue(snapshot([item('q-server', 'authoritative')], 7));

    await useQueueStore.getState().loadAgentQueue('s1');

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-server']);
    expect(useQueueStore.getState().queueRevisions.s1).toBe(7);
    expect(localGetItem).not.toHaveBeenCalled();
  });

  it('does not let an older response overwrite a newer server revision', async () => {
    let resolveOld!: (value: ReturnType<typeof snapshot>) => void;
    const old = new Promise<ReturnType<typeof snapshot>>((resolve) => { resolveOld = resolve; });
    api.fetchSessionQueue.mockReturnValueOnce(old)
      .mockResolvedValueOnce(snapshot([item('q-new', 'new')], 9));

    const first = useQueueStore.getState().loadAgentQueue('s1');
    const second = useQueueStore.getState().loadAgentQueue('s1');
    await second;
    resolveOld(snapshot([item('q-old', 'old')], 8));
    await first;

    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.id)).toEqual(['q-new']);
    expect(useQueueStore.getState().queueRevisions.s1).toBe(9);
  });

  it('enqueues through the server endpoint and keeps the returned native identity', async () => {
    const queued = item('q-native', 'hello');
    api.enqueueSessionMessage.mockResolvedValue({ item: queued, queueRevision: 3 });

    await expect(useQueueStore.getState().enqueue('hello')).resolves.toBe(true);

    expect(api.enqueueSessionMessage).toHaveBeenCalledWith('s1', 'hello', expect.any(String));
    expect(useQueueStore.getState().queues.s1).toEqual([queued]);
    expect(useQueueStore.getState().queues.s1?.[0]?.id).toBe('q-native');
  });
});
