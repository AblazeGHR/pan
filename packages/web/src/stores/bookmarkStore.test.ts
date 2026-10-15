// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  fetchSessionBookmarks: vi.fn(),
  addSessionBookmark: vi.fn(),
  removeSessionBookmark: vi.fn(),
}));

vi.mock('@/services/api', () => api);

import { useBookmarkStore } from './bookmarkStore';
import { useUIStore } from './uiStore';

const ID_A = 'pan:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01';
const ID_A2 = 'pan:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa02';
const ID_B = 'pan:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb01';

function entry(messageId: string) {
  return { messageId, createdAt: '2026-10-14T00:00:00' };
}

function sessionState(id: string) {
  const state = useBookmarkStore.getState().sessions[id];
  if (!state) throw new Error(`no bookmark state for ${id}`);
  return state;
}

beforeEach(() => {
  api.fetchSessionBookmarks.mockReset();
  api.addSessionBookmark.mockReset();
  api.removeSessionBookmark.mockReset();
  api.addSessionBookmark.mockResolvedValue({ ok: true, changed: true });
  api.removeSessionBookmark.mockResolvedValue({ ok: true, changed: true });
  useBookmarkStore.setState({ sessions: {} });
});

describe('bookmark store', () => {
  it('cold-loads one envelope into per-session marks', async () => {
    api.fetchSessionBookmarks.mockResolvedValue({
      sessionId: 'ses_a', revision: 3, updatedAt: '2026-10-14T00:00:00',
      bookmarks: { [ID_A]: entry(ID_A) }, dangling: [],
    });
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A2)).toBe(false);
    // Per-session isolation: another session has no marks at all.
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_A)).toBe(false);

    api.fetchSessionBookmarks.mockClear();
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    expect(api.fetchSessionBookmarks).not.toHaveBeenCalled();
  });

  it('applies an add optimistically and confirms on success', async () => {
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(api.addSessionBookmark).toHaveBeenCalledWith('ses_a', ID_A);
    expect(sessionState('ses_a').transactions).toEqual({});
    expect(sessionState('ses_a').error).toBeNull();
  });

  it('rolls the optimistic mark back when the request fails', async () => {
    api.addSessionBookmark.mockRejectedValue(new Error('boom'));
    const ok = await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(ok).toBe(false);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').error).toBe('boom');
  });

  it('treats repeating the current state as a no-op without a request', async () => {
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    api.addSessionBookmark.mockClear();
    expect(await useBookmarkStore.getState().addBookmark('ses_a', ID_A)).toBe(true);
    expect(api.addSessionBookmark).not.toHaveBeenCalled();

    api.removeSessionBookmark.mockClear();
    expect(await useBookmarkStore.getState().removeBookmark('ses_a', ID_A2)).toBe(true);
    expect(api.removeSessionBookmark).not.toHaveBeenCalled();
  });

  it('keeps one in-flight mutation per target', async () => {
    let release!: (value: unknown) => void;
    api.addSessionBookmark.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const first = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    const second = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(await second).toBe(false);      // single-flight refusal
    release({ ok: true, changed: true });
    expect(await first).toBe(true);
    expect(api.addSessionBookmark).toHaveBeenCalledTimes(1);
  });

  it('applies a bookmarks.changed broadcast from any client', async () => {
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 9,
      action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').authoritativeRevision).toBe(9);

    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 10,
      action: 'added', messageIds: [ID_A, ID_B],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    // Another session's state is untouched by this session's broadcast.
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_B)).toBe(false);
  });

  it('a broadcast does not settle an in-flight transaction; its own response does', async () => {
    let release!: (value: unknown) => void;
    api.addSessionBookmark.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(sessionState('ses_a').transactions[ID_A]?.desired).toBe(true);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 2,
      action: 'added', messageIds: [ID_A],
    });
    // The broadcast proves server truth but cannot prove it is this request's
    // ack: the transaction stays in flight until its own response lands.
    expect(sessionState('ses_a').transactions[ID_A]).toBeDefined();
    release({ ok: true, changed: true });
    await pending;
    expect(sessionState('ses_a').transactions[ID_A]).toBeUndefined();
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
  });

  it('reports a failed cold load instead of pretending the set is empty', async () => {
    api.fetchSessionBookmarks.mockRejectedValue(new Error('unreadable'));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    const state = sessionState('ses_a');
    expect(state.loaded).toBe(false);
    expect(state.error).toBe('unreadable');
    expect(state.marks).toEqual({});
    // A forced retry re-reads after the failure.
    api.fetchSessionBookmarks.mockResolvedValue({
      sessionId: 'ses_a', revision: 1, updatedAt: null, bookmarks: {}, dangling: [],
    });
    await useBookmarkStore.getState().loadBookmarks('ses_a', true);
    expect(sessionState('ses_a').loaded).toBe(true);
  });

  it('ignores malformed broadcasts', () => {
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: '', revision: 1,
      action: 'added', messageIds: [],
    });
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 1,
      action: 'nonsense' as 'added', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().sessions).toEqual({});
  });
});

describe('bookmark failure feedback', () => {
  it('rolls back and surfaces the error through the shared toast', async () => {
    const showToast = vi.fn();
    const original = useUIStore.getState().showToast;
    useUIStore.setState({ showToast });
    try {
      api.addSessionBookmark.mockRejectedValue(new Error('书签存储不可读，请检查数据目录'));
      const ok = await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
      expect(ok).toBe(false);
      expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
      expect(showToast).toHaveBeenCalledWith('书签存储不可读，请检查数据目录', 'error');
    } finally {
      useUIStore.setState({ showToast: original });
    }
  });
});

describe('concurrency gates (review-driven)', () => {
  const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
  const envelope = (revision: number, ids: string[]) => ({
    sessionId: 'ses_a', revision, updatedAt: '2026-10-16T00:00:00',
    bookmarks: Object.fromEntries(ids.map((id) => [id, entry(id)])), dangling: [],
  });
  const mutation = (revision: number, ids: string[], changed = true) => ({
    ok: true, sessionId: 'ses_a', messageId: ids[ids.length - 1] ?? '', bookmarked: true,
    changed, revision, updatedAt: '2026-10-16T00:00:00',
    bookmarks: Object.fromEntries(ids.map((id) => [id, entry(id)])),
  });

  it('a late GET must not clobber a newer broadcast', async () => {
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 5, action: 'added', messageIds: [ID_A],
    });
    gate.resolve(envelope(2, []));
    await load;
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').authoritativeRevision).toBe(5);
    expect(sessionState('ses_a').loaded).toBe(true);
  });

  it('a late GET must not clobber a mutation that committed while it was in flight', async () => {
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a');
    api.addSessionBookmark.mockResolvedValue(mutation(4, [ID_A]));
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    gate.resolve(envelope(1, []));
    await load;
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').authoritativeRevision).toBe(4);
  });

  it('an out-of-order broadcast must not roll back newer state', () => {
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 5, action: 'added', messageIds: [ID_A],
    });
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 4, action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').authoritativeRevision).toBe(5);
  });

  it('a stale request failure must not roll back a newer broadcast', async () => {
    const gate = deferred<ReturnType<typeof mutation>>();
    api.addSessionBookmark.mockReturnValue(gate.promise);
    const add = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 6, action: 'added', messageIds: [ID_A],
    });
    gate.reject(new Error('stale failure'));
    expect(await add).toBe(false);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').error).toBe('stale failure');
  });

  it('a broadcast never settles an in-flight transaction; the response does', async () => {
    const gate = deferred<ReturnType<typeof mutation>>();
    api.addSessionBookmark.mockReturnValue(gate.promise);
    const add = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 3, action: 'added', messageIds: [ID_A],
    });
    // Still in flight: a second click stays single-flighted.
    expect(await useBookmarkStore.getState().addBookmark('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').transactions[ID_A]).toBeDefined();
    gate.resolve(mutation(3, [ID_A]));
    expect(await add).toBe(true);
    expect(sessionState('ses_a').transactions[ID_A]).toBeUndefined();
  });

  it('a stale success envelope is content-skipped but still settles its own transaction', async () => {
    const gate = deferred<ReturnType<typeof mutation>>();
    api.addSessionBookmark.mockReturnValue(gate.promise);
    const add = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 9, action: 'removed', messageIds: [ID_A],
    });
    gate.resolve(mutation(4, [ID_A]));   // older than the broadcast
    expect(await add).toBe(true);
    expect(sessionState('ses_a').transactions[ID_A]).toBeUndefined();
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').authoritativeRevision).toBe(9);
  });
});
