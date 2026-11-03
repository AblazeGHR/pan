// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { markSessionRemovedDuringCreation } from './sessionCreation';
import { useSessionStore } from './sessionStore';
import { loadDraft, subscribeDraftPersistence } from './composerDraftStore';
import { useBookmarkStore } from './bookmarkStore';
import { useQueueStore } from './queueStore';
import { useWorkerStore } from './workerStore';
import { useNativeControlStore } from './nativeControlStore';
import { useAppSettingsStore } from './appSettingsStore';

beforeEach(() => {
  useAppSettingsStore.setState({ loaded: true });
  useSessionStore.setState({ sessions: [], currentSessionId: null, currentMessages: [] });
});
afterEach(() => vi.unstubAllGlobals());

it('keeps creation visible without requesting nonexistent sidecars; real missing identities still fail', async () => {
  let finish!: (value: Response) => void;
  const requests: string[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    requests.push(url);
    if (url === '/api/sessions') return new Promise<Response>(resolve => { finish = resolve; });
    return Promise.resolve(Response.json({ error: { message: 'Session does not exist' } }));
  }));
  useAppSettingsStore.setState({ loaded: true });
  const errors: string[] = [];
  const unsubscribe = subscribeDraftPersistence((_id, error) => { if (error) errors.push(error); });
  const creation = useSessionStore.getState().createNewSession('pending-boundary');
  const id = useSessionStore.getState().currentSessionId!;
  expect(id).toContain('__pending_');
  await Promise.all([
    loadDraft(id), useBookmarkStore.getState().loadBookmarks(id),
    useQueueStore.getState().loadAgentQueue(id),
    useWorkerStore.getState().refreshDelayedRestart(id),
    useNativeControlStore.getState().refresh(id),
    useSessionStore.getState().refreshCurrentSessionHistory(),
  ]);
  expect(errors).toEqual([]);
  expect(requests.filter(url => url.includes(encodeURIComponent(id)))).toEqual([]);
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
  finish(Response.json({ id: 'real-boundary', name: 'created', history: [] }));
  await creation;
  expect(useSessionStore.getState().currentSessionId).toBe('real-boundary');
  await loadDraft('real-boundary');
  expect(errors).toContain('Session does not exist');
  unsubscribe();
  vi.unstubAllGlobals();
});

for (const selection of ['other', null]) {
  it(`does not steal selection ${selection} after a delayed success or failure`, async () => {
    const replies: ((value: Response) => void)[] = [];
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => replies.push(resolve))));
    const first = useSessionStore.getState().createNewSession('same-name');
    const firstId = useSessionStore.getState().currentSessionId;
    const second = useSessionStore.getState().createNewSession('same-name');
    const secondId = useSessionStore.getState().currentSessionId;
    expect(firstId).not.toBe(secondId);
    useSessionStore.setState({ currentSessionId: selection, currentMessages: [{ role: 'user', content: 'keep' }], initialLoading: true });
    await vi.waitFor(() => expect(replies).toHaveLength(2));
    const rejected = expect(first).rejects.toThrow('creation failed');
    replies[0]!(Response.json({ error: 'creation failed' }));
    await rejected;
    expect(useSessionStore.getState().currentSessionId).toBe(selection);
    expect(useSessionStore.getState().initialLoading).toBe(true);
    replies[1]!(Response.json({ id: 'second-real', name: 'same-name', history: [] }));
    await second;
    expect(useSessionStore.getState().currentSessionId).toBe(selection);
    expect(useSessionStore.getState().currentMessages[0]?.content).toBe('keep');
    expect(useSessionStore.getState().initialLoading).toBe(true);
  });
}

it('an older success cannot replace the second selected creation; late retired draft loads never request', async () => {
  const replies: ((value: Response) => void)[] = [];
  const transport = vi.fn(() => new Promise<Response>(resolve => replies.push(resolve)));
  vi.stubGlobal('fetch', transport);
  const a = useSessionStore.getState().createNewSession('parallel');
  const aId = useSessionStore.getState().currentSessionId!;
  const b = useSessionStore.getState().createNewSession('parallel');
  const bId = useSessionStore.getState().currentSessionId;
  await vi.waitFor(() => expect(replies).toHaveLength(2));
  replies[0]!(Response.json({ id: 'parallel-a', history: [] }));
  await a;
  expect(useSessionStore.getState().currentSessionId).toBe(bId);
  await loadDraft(aId);
  expect(transport).toHaveBeenCalledTimes(2);
  replies[1]!(Response.json({ id: 'parallel-b', history: [] }));
  await b;
  expect(useSessionStore.getState().currentSessionId).toBe('parallel-b');
});

it('a real Session deleted after WS publication cannot be resurrected by the held create response', async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  const creation = useSessionStore.getState().createNewSession('deleted-before-response');
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
  markSessionRemovedDuringCreation('real-deleted');
  finish(Response.json({ id: 'real-deleted', history: [] }));
  await creation;
  expect(useSessionStore.getState().sessions).toEqual([]);
  expect(useSessionStore.getState().currentSessionId).toBeNull();
});

it('unknown identities, including unregistered pending-looking strings, retain real HTTP 404 errors', async () => {
  const transport = vi.fn(async () => Response.json({ detail: 'real missing' }, { status: 404 }));
  vi.stubGlobal('fetch', transport);
  await useBookmarkStore.getState().loadBookmarks('__pending_unregistered');
  expect(transport).toHaveBeenCalledTimes(1);
  expect(useBookmarkStore.getState().sessions.__pending_unregistered?.error).toContain('HTTP 404');
});
