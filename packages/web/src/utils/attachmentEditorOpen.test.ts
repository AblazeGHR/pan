import { afterEach, expect, it, vi } from 'vitest';
import { foregroundActivity } from '@/services/foregroundActivity';
import { openComposerAttachmentInEditor } from './attachmentEditorOpen';

const stores = vi.hoisted(() => ({
  sessionId: 'ses_selected',
  openFile: vi.fn(async () => true),
  setRoot: vi.fn(),
  showToast: vi.fn(),
}));
vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: { getState: () => ({ currentSessionId: stores.sessionId }) },
}));
vi.mock('@/stores/editorStore', () => ({
  useEditorStore: {
    getState: () => ({
      sessionId: 'ses_selected',
      workdir: '/fixture',
      openFile: stores.openFile,
      setRoot: stores.setRoot,
    }),
  },
}));
vi.mock('@/stores/uiStore', () => ({
  useUIStore: { getState: () => ({ showToast: stores.showToast }) },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  stores.sessionId = 'ses_selected';
});

it.each(['success', 'failure', 'switch'] as const)(
  'yields prefetch through metadata body parsing and releases on %s',
  async (outcome) => {
    let resolve!: (body: object) => void;
    let reject!: (error: Error) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: () =>
          new Promise((yes, no) => {
            resolve = yes;
            reject = no;
          }),
      })),
    );
    const navigate = vi.fn();
    const pending = openComposerAttachmentInEditor(
      { serverAttachmentId: 'att_fixture' },
      { sessionId: 'ses_selected', workdir: '/fixture', navigate },
    );
    await Promise.resolve();
    expect(foregroundActivity().requests).toBe(1);
    if (outcome === 'failure') reject(new Error('body failed'));
    else {
      if (outcome === 'switch') stores.sessionId = 'ses_other';
      resolve({ path: '/fixture/file.txt' });
    }
    await pending;
    expect(foregroundActivity().requests).toBe(0);
    if (outcome === 'success') expect(navigate).toHaveBeenCalledWith('/editor');
    else expect(navigate).not.toHaveBeenCalled();
    if (outcome === 'failure') expect(stores.showToast).toHaveBeenCalledOnce();
    if (outcome === 'switch') expect(stores.openFile).not.toHaveBeenCalled();
  },
);
