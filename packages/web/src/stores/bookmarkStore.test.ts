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
    api.addSessionBookmark.mockResolvedValue(mutation(1, [ID_A]));
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
    api.addSessionBookmark.mockResolvedValue(mutation(1, [ID_A]));
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    api.addSessionBookmark.mockClear();
    expect(await useBookmarkStore.getState().addBookmark('ses_a', ID_A)).toBe(true);
    expect(api.addSessionBookmark).not.toHaveBeenCalled();

    api.removeSessionBookmark.mockClear();
    expect(await useBookmarkStore.getState().removeBookmark('ses_a', ID_A2)).toBe(true);
    expect(api.removeSessionBookmark).not.toHaveBeenCalled();
  });

  it('a remove before the cold load still reaches the server', async () => {
    // The row's projected `bookmarked` flag can offer Remove before the
    // Session envelope has loaded.  Without store evidence the request must
    // still be sent (the delete is idempotent), never silently skipped.
    api.removeSessionBookmark.mockResolvedValue({
      ok: true, sessionId: 'ses_a', messageId: ID_A, bookmarked: false, changed: true,
      revision: 1, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    expect(await useBookmarkStore.getState().removeBookmark('ses_a', ID_A)).toBe(true);
    expect(api.removeSessionBookmark).toHaveBeenCalledWith('ses_a', ID_A);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').loaded).toBe(true);
  });

  it('a late cold GET issued before a remove cannot resurrect the bookmark', async () => {
    // Production ordering: the list GET is in flight, the user removes the
    // projected bookmark, and the old snapshot lands afterwards.  The
    // deletion's complete envelope (rev 2) outranks the rev-1 snapshot.
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a');
    api.removeSessionBookmark.mockResolvedValue({
      ok: true, sessionId: 'ses_a', messageId: ID_A, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    expect(await useBookmarkStore.getState().removeBookmark('ses_a', ID_A)).toBe(true);
    gate.resolve(envelope(1, [ID_A]));
    await load;

    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').appliedThrough).toBe(2);
    expect(sessionState('ses_a').snapshotRevision).toBe(2);
  });

  it('the same message id is isolated per Session', async () => {
    api.fetchSessionBookmarks.mockResolvedValue(envelope(1, [ID_A]));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_b', revision: 1,
      action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_A)).toBe(false);
  });

  it('a late remove response stays in its own Session after a switch', async () => {
    const gate = deferred<ReturnType<typeof mutation>>();
    api.removeSessionBookmark.mockReturnValue(gate.promise);
    const removing = useBookmarkStore.getState().removeBookmark('ses_a', ID_A);
    api.fetchSessionBookmarks.mockResolvedValue({
      sessionId: 'ses_b', revision: 3, updatedAt: null, bookmarks: {}, dangling: [],
    });
    await useBookmarkStore.getState().loadBookmarks('ses_b');
    gate.resolve({
      ok: true, sessionId: 'ses_a', messageId: ID_A, bookmarked: false, changed: true,
      revision: 2, updatedAt: '2026-10-16T00:00:00', bookmarks: {},
    });
    expect(await removing).toBe(true);

    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_A)).toBe(false);
    expect(sessionState('ses_a').appliedThrough).toBe(2);
    expect(sessionState('ses_b').appliedThrough).toBe(3);
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
    api.fetchSessionBookmarks.mockResolvedValue(envelope(5, [ID_A]));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 6,
      action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').appliedThrough).toBe(6);

    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 7,
      action: 'added', messageIds: [ID_A, ID_B],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    expect(sessionState('ses_a').appliedThrough).toBe(7);
    // Another session's state is untouched by this session's broadcast.
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_B)).toBe(false);
  });

  it('a broadcast does not settle an in-flight transaction; its own response does', async () => {
    let release!: (value: unknown) => void;
    api.addSessionBookmark.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(sessionState('ses_a').transactions[ID_A]?.desired).toBe(true);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 1,
      action: 'added', messageIds: [ID_A],
    });
    // The broadcast proves server truth but cannot prove it is this request's
    // ack: the transaction stays in flight until its own response lands.
    expect(sessionState('ses_a').transactions[ID_A]).toBeDefined();
    release(mutation(1, [ID_A]));
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
  it('a late GET must not clobber a newer broadcast', async () => {
    // Baseline at rev 1; a forced re-read is in flight when rev 2 (adding
    // ID_A) is broadcast.  The late rev-1 snapshot must FOLD per-id instead
    // of clobbering the newer delta.
    api.fetchSessionBookmarks.mockResolvedValueOnce(envelope(1, []));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a', true);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 2, action: 'added', messageIds: [ID_A],
    });
    gate.resolve(envelope(1, []));
    await load;
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').appliedThrough).toBe(2);
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
    expect(sessionState('ses_a').appliedThrough).toBe(4);
  });

  it('an out-of-order broadcast must not roll back newer state', async () => {
    api.fetchSessionBookmarks.mockResolvedValue(envelope(4, []));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 5, action: 'added', messageIds: [ID_A],
    });
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 4, action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').appliedThrough).toBe(5);
  });

  it('a stale request failure must not roll back a newer broadcast', async () => {
    api.fetchSessionBookmarks.mockResolvedValue(envelope(5, []));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
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

  it('a stale snapshot must fold in, never drop the bookmarks only it knows', async () => {
    // MA gate: the initial GET (rev 0, carrying old bookmark X) is in flight
    // while a WS rev 1 adds Y; the late snapshot must fold X in (X and Y both
    // present) instead of being discarded wholesale.
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 1, action: 'added', messageIds: [ID_A],
    });
    gate.resolve(envelope(0, [ID_B]));
    await load;
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
  });

  it('an out-of-order delta is a gap: buffered and reconciled by a real refetch', async () => {
    // MA gate: WS rev 2 (adds B) arrives before rev 1 (adds A) and before any
    // baseline snapshot.  rev 2 sits above the watermark (2 > 0 + 1): it is a
    // GAP, so it buffers and asks for ONE coalesced real refetch — never
    // dropped, never applied blind.  The late rev 1 applies contiguously and
    // its drain releases the buffered rev 2, so A and B both stand.
    api.fetchSessionBookmarks.mockResolvedValue(envelope(2, [ID_A, ID_B]));
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 2, action: 'added', messageIds: [ID_B],
    });
    expect(api.fetchSessionBookmarks).toHaveBeenCalledTimes(1);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 1, action: 'added', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    expect(sessionState('ses_a').appliedThrough).toBe(2);
    // The coalesced refetch folds as a complete snapshot at rev 2; nothing
    // is rolled back and no further refetch is issued.
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    expect(api.fetchSessionBookmarks).toHaveBeenCalledTimes(1);
  });

  it('an old complete snapshot never resurrects what a newer complete snapshot proved absent', async () => {
    // MA gate (round 3): a stale cold GET (rev 4, still carrying ID_B) is in
    // flight while another-item mutation returns the COMPLETE rev-5 snapshot
    // without ID_B.  That absence proof is real evidence: the late rev-4
    // snapshot must not re-add ID_B even though no delta ever mentioned it.
    const gate = deferred<ReturnType<typeof envelope>>();
    api.fetchSessionBookmarks.mockReturnValue(gate.promise);
    const load = useBookmarkStore.getState().loadBookmarks('ses_a');
    api.addSessionBookmark.mockResolvedValue(mutation(5, [ID_A]));
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    gate.resolve(envelope(4, [ID_B]));
    await load;
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(false);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(sessionState('ses_a').snapshotRevision).toBe(5);
  });

  it('a delta removal tombstone survives a stale snapshot carrying the id', async () => {
    api.fetchSessionBookmarks.mockResolvedValue(envelope(5, [ID_A]));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 6, action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    // A complete snapshot older than the removal still lists ID_A: the
    // tombstone wins, the stale truth may not revert it.
    api.fetchSessionBookmarks.mockResolvedValue(envelope(4, [ID_A]));
    await useBookmarkStore.getState().loadBookmarks('ses_a', true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').appliedThrough).toBe(6);
  });

  it('a newer complete snapshot outruns old deltas: they neither add nor revert', async () => {
    // A complete rev-5 snapshot lands (via the mutation response).  Old
    // deltas (rev 2 adds ID_B, rev 3 removes ID_A) arriving later sit below
    // the covered domain and must not invent or revert anything, while a
    // genuinely newer delta (rev 6, contiguous) still applies.
    api.addSessionBookmark.mockResolvedValue(mutation(5, [ID_A]));
    await useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 2, action: 'added', messageIds: [ID_B],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(false);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 3, action: 'removed', messageIds: [ID_A],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 6, action: 'added', messageIds: [ID_B],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    expect(sessionState('ses_a').appliedThrough).toBe(6);
  });

  it('an out-of-contract success without a revision never overrides newer WS state', async () => {
    api.fetchSessionBookmarks.mockResolvedValue(envelope(0, []));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    api.fetchSessionBookmarks.mockClear();
    api.fetchSessionBookmarks.mockResolvedValue(envelope(2, []));
    const gate = deferred<ReturnType<typeof mutation>>();
    api.addSessionBookmark.mockReturnValue(gate.promise);
    const add = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 1, action: 'removed', messageIds: [ID_A],
    });
    gate.resolve({ ok: true, changed: true } as never);   // no revision envelope
    expect(await add).toBe(true);
    // The unversioned success must not invent authority: the newer WS value
    // stands and a real reconciliation was requested.
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').transactions[ID_A]).toBeUndefined();
    expect(api.fetchSessionBookmarks).toHaveBeenCalledTimes(1);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    // The reconciliation folds the real snapshot (rev 2) without rollback.
    expect(sessionState('ses_a').appliedThrough).toBe(2);
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
    api.fetchSessionBookmarks.mockResolvedValue(envelope(5, []));
    await useBookmarkStore.getState().loadBookmarks('ses_a');
    const gate = deferred<ReturnType<typeof mutation>>();
    api.addSessionBookmark.mockReturnValue(gate.promise);
    const add = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 6, action: 'removed', messageIds: [ID_A],
    });
    gate.resolve(mutation(4, [ID_A]));   // older than the broadcast
    expect(await add).toBe(true);
    expect(sessionState('ses_a').transactions[ID_A]).toBeUndefined();
    // The rev-4 envelope loses per-id against the rev-6 evidence: the stale
    // success cannot resurrect ID_A.
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(false);
    expect(sessionState('ses_a').appliedThrough).toBe(6);
  });
});
