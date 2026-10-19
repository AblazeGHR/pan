// @vitest-environment jsdom
//
// Store-level guarantees behind the route-unmount restore: the recorded draft
// must outlive the component, survive a failed save or a CAS conflict without
// being clobbered, and never be turned into a tombstone by a read.
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getSessionDraft: vi.fn(),
  putSessionDraft: vi.fn(),
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    getSessionDraft: api.getSessionDraft,
    putSessionDraft: api.putSessionDraft,
  };
});

const files = vi.hoisted(() => ({
  ensure: vi.fn(async () => {}),
  prune: vi.fn(async () => {}),
  load: vi.fn(async () => undefined),
}));
vi.mock('./draftFiles', () => ({
  ensureDraftFiles: files.ensure,
  pruneDraftFiles: files.prune,
  loadDraftFile: files.load,
  draftFile: () => undefined,
  rememberDraftFile: () => {},
}));

import {
  flushDraft,
  loadDraft,
  localDraftError,
  recordDraft,
  resetDraftStateForTests,
  restorableLocalDraft,
  type ApiSessionDraft,
} from './composerDraftStore';

const draft: ApiSessionDraft = {
  text: 'keep me',
  parts: [
    { type: 'text', value: 'keep me ' },
    { type: 'attachment', attachmentId: 'occ-1', occurrenceId: 'occ-1' },
  ],
  attachments: [
    {
      occurrenceId: 'occ-1',
      displayName: 'pending.txt',
      localFileId: 's:occ-1',
      source: 'upload',
    },
  ],
};

beforeEach(() => {
  resetDraftStateForTests();
  api.getSessionDraft.mockReset().mockResolvedValue({ revision: 0, draft: null, updatedAt: null });
  api.putSessionDraft.mockReset().mockImplementation(async (_id, value) => ({
    revision: 1,
    draft: value,
    updatedAt: null,
  }));
  files.ensure.mockClear();
  files.prune.mockClear();
  files.load.mockClear();
});

describe('recorded composer draft across unmount', () => {
  it('exposes the last recorded payload and forgets it only on an explicit clear', () => {
    expect(restorableLocalDraft('s')).toBeNull();
    recordDraft('s', draft);
    expect(restorableLocalDraft('s')).toEqual(draft);
    recordDraft('s', null);
    expect(restorableLocalDraft('s')).toBeNull();
  });

  it('keeps the local payload when the save fails, and a read cannot replace it', async () => {
    const serverDraft: ApiSessionDraft = { text: 'server copy', parts: [], attachments: [] };
    api.getSessionDraft.mockResolvedValue({ revision: 3, draft: serverDraft, updatedAt: null });
    recordDraft('s', draft);
    // A cold read landing while the tab holds a local draft must not swap it in.
    expect(await loadDraft('s')).toBeNull();
    expect(restorableLocalDraft('s')).toEqual(draft);

    api.putSessionDraft.mockRejectedValue(new Error('offline'));
    recordDraft('s', { ...draft, text: 'typed again' });
    flushDraft('s');
    await vi.waitFor(() => expect(localDraftError('s')).toBe('offline'));
    expect(restorableLocalDraft('s')?.text).toBe('typed again');
  });

  it('keeps the local payload after a CAS conflict and does not auto-retry identical content', async () => {
    const { DraftConflictError } = await import('@/services/api');
    const other: ApiSessionDraft = { text: 'other tab', parts: [], attachments: [] };
    api.putSessionDraft.mockRejectedValue(new DraftConflictError(other, 9));
    recordDraft('s', draft);
    flushDraft('s');
    await vi.waitFor(() => expect(localDraftError('s')).toContain('another tab'));
    expect(restorableLocalDraft('s')).toEqual(draft);
    const calls = api.putSessionDraft.mock.calls.length;
    // A remount re-records the same content (composer sync). That must not
    // silently resolve the conflict by overwriting the other tab's draft.
    recordDraft('s', draft);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(api.putSessionDraft.mock.calls.length).toBe(calls);
    expect(restorableLocalDraft('s')).toEqual(draft);
    // The user's actual edit clears the conflict and saves on the adopted base.
    recordDraft('s', { ...draft, text: 'keep me edited' });
    flushDraft('s');
    await vi.waitFor(() => expect(api.putSessionDraft).toHaveBeenCalledTimes(calls + 1));
    expect(restorableLocalDraft('s')?.text).toBe('keep me edited');
  });

  it('never prunes bytes for a content save; only an acknowledged tombstone clears them', async () => {
    // jsdom has no IndexedDB; the store only prunes when the browser has one.
    vi.stubGlobal('indexedDB', {});
    recordDraft('s', draft);
    flushDraft('s');
    await vi.waitFor(() => expect(api.putSessionDraft).toHaveBeenCalledWith('s', draft, 0));
    expect(files.prune).not.toHaveBeenCalled();
    recordDraft('s', null);
    flushDraft('s');
    await vi.waitFor(() => expect(files.prune).toHaveBeenCalled());
    vi.unstubAllGlobals();
  });
});
