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
    expect(sessionState('ses_a').pending).toEqual({});
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
    expect(sessionState('ses_a').revision).toBe(9);

    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 10,
      action: 'added', messageIds: [ID_A, ID_B],
    });
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_A)).toBe(true);
    expect(useBookmarkStore.getState().isBookmarked('ses_a', ID_B)).toBe(true);
    // Another session's state is untouched by this session's broadcast.
    expect(useBookmarkStore.getState().isBookmarked('ses_b', ID_B)).toBe(false);
  });

  it('clears a pending mark when its broadcast arrives', async () => {
    let release!: (value: unknown) => void;
    api.addSessionBookmark.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = useBookmarkStore.getState().addBookmark('ses_a', ID_A);
    expect(sessionState('ses_a').pending).toEqual({ [ID_A]: true });
    useBookmarkStore.getState().applyBookmarkEvent({
      type: 'bookmarks.changed', sessionId: 'ses_a', revision: 2,
      action: 'added', messageIds: [ID_A],
    });
    expect(sessionState('ses_a').pending).toEqual({});
    release({ ok: true, changed: true });
    await pending;
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
