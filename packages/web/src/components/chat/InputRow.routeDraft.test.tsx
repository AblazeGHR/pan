// @vitest-environment jsdom
//
// Route-unmount regression for the ordinary composer draft.
//
// Switching to Editor / Jobs / Terminal / a plugin panel unmounts the whole
// Chat route. The mounted InputRow keeps its rich draft (occurrence order,
// URL nodes, pending File bytes) in per-component refs, so anything that only
// lives there is destroyed by that navigation. These tests drive a real
// react-router link/unlink cycle — not a manual unmount — and assert the
// composer comes back with the same text, structure, occurrence identity and
// real File bytes. The network layer stays mocked (and, where noted, broken)
// on purpose: a route switch inside one tab must not depend on a server round
// trip to keep its own draft.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor, act } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { InputRow } from './InputRow';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useWorkerStore } from '@/stores/workerStore';
import {
  DraftConflictError,
  getSessionDraft,
  putSessionDraft,
  uploadSessionAttachment,
  sendSession,
  enqueueSessionMessage,
  spawnWorker,
  steerSessionWorker,
  fetchDirectories,
  patchSession,
  registerServerFileAttachment,
} from '@/services/api';
import { wsClient } from '@/services/ws';
import { ATTACHMENT_DRAG_MIME } from '@/utils/attachmentDrag';
import { resetDraftStateForTests, localDraftError } from '@/stores/composerDraftStore';

// The bytes live in memory for this test (the real IndexedDB transaction is
// exercised by the isolated Chromium run). Keeping this boundary mocked makes
// the assertion about *which* File object survives explicit.
const draftFiles = vi.hoisted(() => ({
  files: new Map<string, File>(),
  pruned: [] as { sessionId: string; keep: string[] }[],
}));

vi.mock('@/stores/draftFiles', () => ({
  rememberDraftFile: (key: string, file: File) => { draftFiles.files.set(key, file); },
  draftFile: (key: string) => draftFiles.files.get(key),
  ensureDraftFiles: async () => {},
  loadDraftFile: async (key: string) => draftFiles.files.get(key),
  pruneDraftFiles: async (sessionId: string, keep: () => string[]) => {
    draftFiles.pruned.push({ sessionId, keep: keep() });
    for (const key of [...draftFiles.files.keys()]) {
      if (key.startsWith(`${sessionId}:`) && !keep().includes(key)) draftFiles.files.delete(key);
    }
  },
}));

vi.mock('@/services/ws', () => ({
  wsClient: { send: vi.fn(() => true), isOpen: true, on: vi.fn() },
}));

const queueApi = vi.hoisted(() => ({
  acquireSessionQueueItemEdit: vi.fn(),
  fetchSessionQueue: vi.fn(),
  releaseSessionQueueItemEdit: vi.fn(),
  updateSessionQueueItem: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    ...queueApi,
    getSessionDraft: vi.fn(async () => ({ revision: 0, draft: null, updatedAt: null })),
    putSessionDraft: vi.fn(async (_id: string, draft: unknown) => ({ revision: 1, draft, updatedAt: null })),
    patchSession: vi.fn(async () => ({})),
    fetchSessions: vi.fn(async () => []),
    fetchDirectories: vi.fn(async () => ({ current: 'D:\\attachments', parent: 'D:\\', entries: [] })),
    uploadSessionAttachment: vi.fn(async (_sessionId: string, file: File) => ({
      ok: true,
      filename: file.name,
      displayName: file.name,
      storageFilename: 'upload_' + 'a'.repeat(32) + '.txt',
      href: `/api/attachments/upload_${'a'.repeat(32)}.txt?session_id=s1`,
      path: `D:\\attachments\\uploaded\\${file.name}`,
      size: file.size,
    })),
    registerServerFileAttachment: vi.fn(async (_sessionId: string, path: string) => ({
      ok: true,
      attachmentId: `att_${'b'.repeat(32)}`,
      displayName: path.split('\\').at(-1) || 'attachment.txt',
      mimeType: 'text/plain',
      size: 12,
      path,
      href: `/api/fs/read?session_id=s1&path=${encodeURIComponent(path)}&download=1`,
    })),
    enqueueSessionMessage: vi.fn(async () => ({ item: { id: 'q1' }, queueRevision: 1 })),
    sendSession: vi.fn(async () => ({ status: 'queued' })),
    spawnWorker: vi.fn(async () => ({ workerId: 'w-new' })),
    steerSessionWorker: vi.fn(async () => ({ workerId: 'w-live', status: 'steer sent' })),
  };
});

function setSession(id = 's1') {
  useSessionStore.setState({
    currentSessionId: id,
    currentMessages: [],
    inputDrafts: {},
    sessions: [
      {
        id,
        name: 'Test',
        adapter: 'cbc',
        model: null,
        permissionMode: null,
        alwaysThinkingEnabled: false,
        effort: '',
        workerStatus: 'running',
        workerId: 'w1',
        history: [],
      },
    ],
  });
}

function AppHarness() {
  return (
    <>
      <Link to="/editor">go-editor</Link>
      <Link to="/jobs">go-jobs</Link>
      <Link to="/terminals">go-terminals</Link>
      <Link to="/plugins/demo/panel">go-plugins</Link>
      <Link to="/">go-chat</Link>
      <Routes>
        <Route path="/" element={<InputRow />} />
        <Route path="/editor" element={<p>editor-route</p>} />
        <Route path="/jobs" element={<p>jobs-route</p>} />
        <Route path="/terminals" element={<p>terminals-route</p>} />
        <Route path="/plugins/demo/panel" element={<p>plugin-route</p>} />
      </Routes>
    </>
  );
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AppHarness />
    </MemoryRouter>,
  );
}

async function leaveChat(link = 'go-jobs', marker = 'jobs-route') {
  fireEvent.click(screen.getByText(link));
  await waitFor(() => expect(screen.queryByTestId('input-row')).toBeNull());
  expect(screen.getByText(marker)).toBeTruthy();
}

async function returnToChat() {
  fireEvent.click(screen.getByText('go-chat'));
  await waitFor(() => expect(screen.getByTestId('input-row')).toBeTruthy());
}

function addPendingFile(name: string, bytes: string) {
  const file = new File([bytes], name, { type: 'text/plain' });
  fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });
  return file;
}

/** jsdom's File does not implement Blob.text(); read via FileReader. */
function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

beforeEach(() => {
  localStorage.clear();
  resetDraftStateForTests();
  draftFiles.files.clear();
  draftFiles.pruned.length = 0;
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
    inputDrafts: {},
  });
  useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null });
  useQueueStore.setState({ queues: {}, edits: {}, batchSend: {}, sendingId: null, panelOpen: false });
  useUIStore.setState({ toastQueue: [], chatAttachmentRequests: [] });
  useAdapterStore.setState({
    adapters: [],
    adapterConfigs: {},
    currentAdapter: 'cbc',
    configReady: false,
  });
  queueApi.acquireSessionQueueItemEdit.mockReset();
  queueApi.fetchSessionQueue.mockReset().mockRejectedValue(new Error('offline'));
  queueApi.releaseSessionQueueItemEdit.mockReset().mockResolvedValue(undefined);
  queueApi.updateSessionQueueItem.mockReset();
  vi.mocked(getSessionDraft).mockReset().mockResolvedValue({ revision: 0, draft: null, updatedAt: null });
  vi.mocked(putSessionDraft).mockReset().mockImplementation(async (_id, draft) => ({
    revision: 1,
    draft: draft as never,
    updatedAt: null,
  }));
  vi.mocked(uploadSessionAttachment).mockReset().mockImplementation(async (_sessionId, file) => ({
    ok: true,
    filename: file.name,
    displayName: file.name,
    storageFilename: `upload_${'a'.repeat(32)}.txt`,
    href: `/api/attachments/upload_${'a'.repeat(32)}.txt?session_id=s1`,
    path: `D:\\attachments\\uploaded\\${file.name}`,
    size: file.size,
  }));
  vi.mocked(registerServerFileAttachment).mockClear();
  vi.mocked(sendSession).mockReset().mockResolvedValue({ status: 'queued' });
  vi.mocked(enqueueSessionMessage).mockReset().mockResolvedValue({ item: { id: 'q1' }, queueRevision: 1 } as never);
  vi.mocked(spawnWorker).mockClear();
  vi.mocked(steerSessionWorker).mockClear();
  vi.mocked(patchSession).mockClear();
  vi.mocked(fetchDirectories).mockClear();
  vi.mocked(wsClient.send).mockReset().mockReturnValue(true);
  Object.defineProperty(wsClient, 'isOpen', { value: true, configurable: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});

describe('composer draft across a real route unmount', () => {
  it('restores text, URL node and a still-pending File with its real bytes', async () => {
    setSession();
    // The server save never completes: the draft may only come back from this
    // tab's own memory, exactly like an in-flight PUT during navigation.
    vi.mocked(putSessionDraft).mockImplementation(() => new Promise(() => {}));
    vi.mocked(uploadSessionAttachment).mockImplementation(() => new Promise(() => {}));
    renderApp();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'hello ' },
    });
    const editor = screen.getByTestId('rich-text-composer');
    const textNode = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(textNode!, textNode!.textContent!.length);
    range.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    fireEvent.paste(editor, {
      clipboardData: {
        files: [],
        items: [],
        types: ['text/plain'],
        getData: (type: string) => (type === 'text/plain' ? 'https://route.test/x' : ''),
      } as unknown as DataTransfer,
    });
    const urlId = editor.querySelector('[data-composer-url-id]')!.getAttribute('data-composer-url-id')!;
    addPendingFile('pending.txt', 'real pending bytes');
    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('上传中'),
    );

    await leaveChat();
    await returnToChat();

    await waitFor(() =>
      expect(screen.getByTestId('rich-text-composer').textContent).toContain('hello'),
    );
    expect(
      screen.getByTestId('rich-text-composer').querySelector('[data-composer-url-id]')
        ?.getAttribute('data-composer-url-id'),
    ).toBe(urlId);
    expect(screen.getByTestId('server-attachments').textContent).toContain('pending.txt');
    // Not pretend-ready: an interrupted upload is explicitly retryable, and
    // the retry carries the original File object, never an empty placeholder.
    const retry = screen.getByRole('button', { name: '重试上传 pending.txt' });
    vi.mocked(uploadSessionAttachment).mockResolvedValue({
      ok: true,
      filename: 'pending.txt',
      displayName: 'pending.txt',
      storageFilename: `upload_${'a'.repeat(32)}.txt`,
      href: `/api/attachments/upload_${'a'.repeat(32)}.txt?session_id=s1`,
      path: 'D:\\attachments\\uploaded\\pending.txt',
      size: 17,
    });
    fireEvent.click(retry);
    await waitFor(() => expect(uploadSessionAttachment).toHaveBeenCalled());
    const retried = vi.mocked(uploadSessionAttachment).mock.calls.at(-1)![1];
    expect(retried).toBeInstanceOf(File);
    expect(retried.size).toBe('real pending bytes'.length);
    expect(await readFile(retried)).toBe('real pending bytes');
  });

  it('keeps an uploaded inline attachment occurrence id and its document order', async () => {
    setSession();
    renderApp();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'before after' },
    });
    addPendingFile('ready.txt', 'ready bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());

    // Embed the ready chip the way a user would: drag it into the editor.
    const data = new Map<string, string>();
    const dataTransfer = {
      getData: (type: string) => data.get(type) || '',
      setData: (type: string, value: string) => data.set(type, value),
      effectAllowed: 'copy',
      dropEffect: 'copy',
    } as unknown as DataTransfer;
    fireEvent.dragStart(screen.getByTestId('draggable-attachment-chip'), { dataTransfer });
    expect(JSON.parse(data.get(ATTACHMENT_DRAG_MIME) || '{}')).toMatchObject({
      displayName: 'ready.txt',
      source: 'attachment-chip',
    });
    const editor = screen.getByTestId('rich-text-composer');
    fireEvent.drop(editor, { dataTransfer });
    const inline = editor.querySelector('[data-composer-attachment]');
    const occurrenceId = inline!.getAttribute('data-composer-attachment');
    expect(occurrenceId).toBeTruthy();

    await leaveChat();
    await returnToChat();

    await waitFor(() => expect(screen.getByTestId('rich-text-composer').textContent).toContain('before'));
    const restored = screen
      .getByTestId('rich-text-composer')
      .querySelector('[data-composer-attachment]');
    expect(restored?.getAttribute('data-composer-attachment')).toBe(occurrenceId);
    expect(screen.getByTestId('rich-text-composer').textContent).toContain('ready.txt');
    // Embedded nodes are not duplicated back into the chip row.
    expect(screen.queryByTestId('server-attachments')).toBeNull();
  });

  it('round-trips the draft through every panel route kind', async () => {
    setSession();
    renderApp();
    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'panel text' },
    });
    addPendingFile('panel.txt', 'panel bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());

    for (const [link, marker] of [
      ['go-editor', 'editor-route'],
      ['go-jobs', 'jobs-route'],
      ['go-terminals', 'terminals-route'],
      ['go-plugins', 'plugin-route'],
    ] as const) {
      await leaveChat(link, marker);
      await returnToChat();
      expect(screen.getByTestId('rich-text-composer').textContent).toContain('panel text');
      expect(screen.getByTestId('server-attachments').textContent).toContain('panel.txt');
    }
  });

  it('restores an attachment-only draft without waiting for a server read', async () => {
    setSession();
    const serverRead = new Promise<never>(() => {});
    vi.mocked(getSessionDraft).mockReturnValue(serverRead);
    renderApp();

    addPendingFile('only.txt', 'only bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());
    // No text at all: the plain-text projection is empty, so a route restore
    // that only consults inputDrafts would silently drop this draft.
    expect(useSessionStore.getState().inputDrafts.s1 ?? '').toBe('');

    await leaveChat();
    await returnToChat();

    expect(screen.getByTestId('server-attachments').textContent).toContain('only.txt');
  });

  it('restores the Session selected while the Chat route was unmounted only', async () => {
    setSession();
    useSessionStore.setState((state) => ({
      sessions: [...state.sessions, { ...state.sessions[0]!, id: 's2', name: 'Second' }],
    }));
    renderApp();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'first session text' },
    });
    addPendingFile('first.txt', 'first bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());

    await leaveChat();
    // The Session changes while Chat is unmounted (e.g. from the sidebar).
    act(() => {
      useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    });
    await returnToChat();

    expect(screen.getByTestId('rich-text-composer').textContent).not.toContain('first session text');
    expect(screen.queryByTestId('server-attachments')).toBeNull();

    act(() => {
      useSessionStore.setState({ currentSessionId: 's1', currentMessages: [] });
    });
    await waitFor(() =>
      expect(screen.getByTestId('rich-text-composer').textContent).toContain('first session text'),
    );
    expect(screen.getByTestId('server-attachments').textContent).toContain('first.txt');
  });

  it('does not let a late upload response overwrite the restored retryable chip', async () => {
    setSession();
    let resolveUpload!: (value: Awaited<ReturnType<typeof uploadSessionAttachment>>) => void;
    vi.mocked(uploadSessionAttachment).mockImplementationOnce(
      () => new Promise((resolve) => { resolveUpload = resolve; }),
    );
    renderApp();

    addPendingFile('late.txt', 'late bytes');
    await waitFor(() => expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('上传中'));
    await leaveChat();
    resolveUpload({
      ok: true,
      filename: 'late.txt',
      displayName: 'late.txt',
      storageFilename: `upload_${'c'.repeat(32)}.txt`,
      href: `/api/attachments/upload_${'c'.repeat(32)}.txt?session_id=s1`,
      path: 'D:\\attachments\\uploaded\\late.txt',
      size: 10,
    });
    await Promise.resolve();
    await returnToChat();

    expect(screen.getByTestId('server-attachments').textContent).toContain('late.txt');
    expect(screen.getByRole('button', { name: '重试上传 late.txt' })).toBeTruthy();
  });

  it('keeps local content when a late save reports a CAS conflict while unmounted', async () => {
    setSession();
    vi.mocked(putSessionDraft).mockRejectedValue(
      new DraftConflictError({ text: 'other tab', parts: [], attachments: [] }, 7),
    );
    renderApp();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'mine' },
    });
    addPendingFile('conflict.txt', 'conflict bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());

    await leaveChat();
    // Wait for the deferred flush to observe the conflict while unmounted.
    await waitFor(() => expect(putSessionDraft).toHaveBeenCalled());
    await waitFor(() => expect(localDraftError('s1')).toContain('another tab'));
    await returnToChat();

    expect(screen.getByTestId('rich-text-composer').textContent).toContain('mine');
    expect(screen.getByTestId('server-attachments').textContent).toContain('conflict.txt');
    // The standing conflict is reported once the composer is back, so the
    // preserved local copy is never mistaken for a durable one.
    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.at(-1)?.message).toContain('another tab'),
    );
  });

  it('does not resurrect a sent draft and prunes browser bytes only after an acknowledged clear', async () => {
    setSession();
    // jsdom has no IndexedDB; the draft store gates deletion on its presence.
    // The transaction itself is mocked above (and exercised in Chromium).
    vi.stubGlobal('indexedDB', {});
    renderApp();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'send me' },
    });
    addPendingFile('sent.txt', 'sent bytes');
    await waitFor(() => expect(screen.getByTestId('draggable-attachment-chip')).toBeTruthy());
    const localKeys = () => [...draftFiles.files.keys()].filter((key) => key.startsWith('s1:'));
    expect(localKeys().length).toBe(1);
    expect(draftFiles.pruned.length).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalled());
    // The successful send records a tombstone; only its acknowledgement may
    // delete the local bytes. The debounce plus the coalesced write can exceed
    // the default one-second wait, so allow the bounded scheduling window.
    await waitFor(
      () => expect(
        vi.mocked(putSessionDraft).mock.calls.some(([, payload]) => payload === null),
      ).toBe(true),
      { timeout: 4_000 },
    );
    await waitFor(() => expect(draftFiles.pruned.length).toBeGreaterThan(0), { timeout: 4_000 });
    expect(localKeys().length).toBe(0);

    await leaveChat();
    await returnToChat();
    expect(screen.getByTestId('rich-text-composer').textContent).not.toContain('send me');
    expect(screen.queryByTestId('server-attachments')).toBeNull();
  });
});
