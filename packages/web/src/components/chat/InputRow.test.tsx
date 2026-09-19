// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/react';
import { InputRow } from './InputRow';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import {
  enqueueSessionMessage,
  fetchDirectories,
  sendSession,
  spawnWorker,
  patchSession,
  steerSessionWorker,
  uploadSessionAttachment,
  registerServerFileAttachment,
} from '@/services/api';
import { wsClient } from '@/services/ws';
import { useWorkerStore } from '@/stores/workerStore';
import type { AdapterConfig } from '@/types';
import { ATTACHMENT_DRAG_MIME } from '@/utils/attachmentDrag';

vi.mock('@/services/ws', () => ({
  wsClient: {
    send: vi.fn(() => true),
    isOpen: true,
    // queueStore 模块加载时会注册 wsClient.on('open', ...) 联动
    on: vi.fn(),
  },
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    patchSession: vi.fn(async () => ({})),
    fetchSessions: vi.fn(async () => []),
    fetchDirectories: vi.fn(async () => ({
      current: 'D:\\attachments',
      parent: 'D:\\',
      entries: [{ name: 'report.txt', path: 'D:\\attachments\\report.txt', isDirectory: false }],
    })),
    uploadSessionAttachment: vi.fn(async (_sessionId: string, file: File) => ({
      ok: true,
      filename: file.name,
      displayName: file.name,
      storageFilename: `upload_${'a'.repeat(32)}${file.name.includes('.') ? `.${file.name.split('.').pop()}` : ''}`,
      href: `/api/attachments/upload_${'a'.repeat(32)}${file.name.includes('.') ? `.${file.name.split('.').pop()}` : ''}?session_id=s1`,
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
    enqueueSessionMessage: vi.fn(async (_sessionId: string, text: string) => ({
      item: {
        id: `q-${text.replace(/\s+/g, '-')}`,
        queueItemId: `q-${text.replace(/\s+/g, '-')}`,
        text,
        source: 'user',
        kind: 'task',
        createdAt: '2026-09-01T00:00:00Z',
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    })),
    sendSession: vi.fn(async () => ({ status: 'queued' })),
    spawnWorker: vi.fn(async () => ({ workerId: 'w-new' })),
    steerSessionWorker: vi.fn(async () => ({ workerId: 'w-live', status: 'steer sent' })),
  };
});

function setBusySession() {
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [
      {
        id: 's1',
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

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
    inputDrafts: {},
  });
  useWorkerStore.setState({ workers: {}, currentWorkerId: null, currentWorker: null });
  useQueueStore.setState({
    queues: {},
    edits: {},
    batchSend: {},
    sendingId: null,
    panelOpen: false,
  });
  useUIStore.setState({ toastQueue: [], chatAttachmentRequests: [] });
  useAdapterStore.setState({
    adapters: [],
    adapterConfigs: {},
    currentAdapter: 'cbc',
    configReady: false,
  });
  vi.mocked(patchSession).mockClear();
  vi.mocked(sendSession).mockClear();
  vi.mocked(enqueueSessionMessage).mockClear();
  vi.mocked(uploadSessionAttachment).mockClear();
  vi.mocked(registerServerFileAttachment).mockClear();
  vi.mocked(spawnWorker).mockClear();
  vi.mocked(steerSessionWorker).mockClear();
  vi.mocked(wsClient.send).mockReset().mockReturnValue(true);
  Object.defineProperty(wsClient, 'isOpen', { value: true, configurable: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, 'caretRangeFromPoint');
  Reflect.deleteProperty(document, 'caretPositionFromPoint');
  window.history.replaceState({}, '', '/');
});

describe('InputRow send queue wiring', () => {
  it('keeps Codex Steer visible and routes by sessionId when the session summary is stale', async () => {
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1',
          name: 'Test',
          adapter: 'codex',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          // Simulate a stale /api/sessions summary racing with worker refresh.
          workerStatus: 'offline',
          workerId: null,
          history: [],
        },
      ],
    });
    useWorkerStore.setState({
      workers: { s1: { id: 'w-live', sessionId: 's1', status: 'running' } },
      currentWorkerId: 'w-live',
      currentWorker: { id: 'w-live', sessionId: 's1', status: 'running' },
    });

    render(<InputRow />);
    expect(screen.getByRole('button', { name: 'Steer' })).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'continue with the latest result' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Steer' }));

    await waitFor(() =>
      expect(steerSessionWorker).toHaveBeenCalledWith('s1', 'continue with the latest result'),
    );
    await waitFor(() => expect(
      useSessionStore.getState().currentMessages.map((message) => message.content),
    ).toEqual(['continue with the latest result']));
    expect(useSessionStore.getState().sessions[0]?.history.map((message) => message.content))
      .toEqual(['continue with the latest result']);
  });

  it('does not put a delayed Steer response into a newly selected Session', async () => {
    const pending = deferred<{ workerId: string; status: string }>();
    vi.mocked(steerSessionWorker).mockReturnValueOnce(pending.promise);
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1', name: 'A', adapter: 'codex', model: null, permissionMode: null,
          alwaysThinkingEnabled: false, effort: '', workerStatus: 'running', workerId: 'w1',
          history: [],
        },
        {
          id: 's2', name: 'B', adapter: 'codex', model: null, permissionMode: null,
          alwaysThinkingEnabled: false, effort: '', workerStatus: 'running', workerId: 'w2',
          history: [],
        },
      ],
    });
    useWorkerStore.setState({
      workers: { s1: { id: 'w1', sessionId: 's1', status: 'running' } },
      currentWorkerId: 'w1',
      currentWorker: { id: 'w1', sessionId: 's1', status: 'running' },
    });

    render(<InputRow />);
    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'steer A' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Steer' }));
    await waitFor(() => expect(steerSessionWorker).toHaveBeenCalledWith('s1', 'steer A'));

    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    pending.resolve({ workerId: 'w1', status: 'steer sent' });
    await waitFor(() => expect(
      useSessionStore.getState().sessions.find((session) => session.id === 's1')?.history
        .map((message) => message.content),
    ).toEqual(['steer A']));

    expect(useSessionStore.getState().currentSessionId).toBe('s2');
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('does not let a delayed Steer success clear a newer draft in the same Session', async () => {
    const pending = deferred<{ workerId: string; status: string }>();
    vi.mocked(steerSessionWorker).mockReturnValueOnce(pending.promise);
    setBusySession();
    useSessionStore.setState({
      sessions: [{
        id: 's1', name: 'Test', adapter: 'codex', model: null, permissionMode: null,
        alwaysThinkingEnabled: false, effort: '', workerStatus: 'running', workerId: 'w1',
        history: [],
      }],
    });
    useWorkerStore.setState({
      workers: { s1: { id: 'w1', sessionId: 's1', status: 'running' } },
      currentWorkerId: 'w1',
      currentWorker: { id: 'w1', sessionId: 's1', status: 'running' },
    });

    render(<InputRow />);
    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'steer A' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Steer' }));
    await waitFor(() => expect(steerSessionWorker).toHaveBeenCalledWith('s1', 'steer A'));

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'new draft after request' },
    });
    pending.resolve({ workerId: 'w1', status: 'steer sent' });

    await waitFor(() => expect(
      (screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement).value,
    ).toBe('new draft after request'));
    expect(useSessionStore.getState().inputDrafts.s1).toBe('new draft after request');
    expect(useSessionStore.getState().currentMessages.map((message) => message.content))
      .toEqual(['steer A']);
  });

  it('selects server files, renders attachment chips, and enqueues standard Markdown links', async () => {
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    expect(screen.queryByTestId('directory-input-panel')?.closest('.modal-card')).toBeTruthy();
    expect(
      screen.queryByLabelText('Server attachment browser')?.closest('[data-testid="input-row"]'),
    ).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'report.txt' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'report.txt' }));
    await waitFor(() =>
      expect(registerServerFileAttachment).toHaveBeenCalledWith(
        's1',
        'D:\\attachments\\report.txt',
      ),
    );

    expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt');
    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: '请阅读' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        '请阅读 [report.txt](/api/fs/read?session_id=s1&path=D%3A%5Cattachments%5Creport.txt&download=1)',
        expect.any(String),
        expect.any(Array),
      ),
    );
    await waitFor(() => expect(screen.queryByTestId('server-attachments')).toBeNull());
  });

  it('drops a message attachment into the editor and queues it at the text caret', async () => {
    setBusySession();
    render(<InputRow />);
    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: '请先 后续' } });
    const editor = screen.getByTestId('rich-text-composer');
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 3);
    range.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => range),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1',
    };
    const dataTransfer = {
      getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
      dropEffect: 'copy',
    } as unknown as DataTransfer;

    fireEvent.dragOver(editor, { dataTransfer, clientX: 40, clientY: 12 });
    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
    fireEvent.drop(editor, { dataTransfer, clientX: 40, clientY: 12 });

    expect(screen.getByRole('group', { name: '附件 接口说明.md' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        '请先 [接口说明.md](/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1)后续',
        expect.any(String),
        expect.any(Array),
      ),
    );
  });

  it('accepts a cross-Session editor payload and keeps its line range in text and parts', async () => {
    setBusySession();
    render(<InputRow />);
    const editor = screen.getByTestId('rich-text-composer');
    const payload = {
      displayName: 'guide.md',
      href: '/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1',
      serverAttachmentId: 'resource-guide',
      source: 'editor',
      sourceSessionId: 'other-session',
      location: { line: 42, endLine: 48 },
    };
    fireEvent.drop(editor, {
      dataTransfer: {
        getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
      },
    });

    expect(screen.getByRole('group', { name: '附件 guide.md' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalled());
    const call = vi.mocked(enqueueSessionMessage).mock.calls.at(-1);
    expect(call?.[1]).toBe(
      '[guide.md](/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1#L42-L48)',
    );
    expect(call?.[3]).toEqual([
      expect.objectContaining({
        type: 'attachment',
        attachmentId: 'resource-guide',
        location: { line: 42, endLine: 48 },
      }),
    ]);
  });

  it('keeps separate occurrences when the same resource is inserted twice', async () => {
    setBusySession();
    render(<InputRow />);
    const editor = screen.getByTestId('rich-text-composer');
    const payload = {
      displayName: 'same.md',
      href: '/api/attachments/upload_cccccccccccccccccccccccccccccccc.md?session_id=s1',
      serverAttachmentId: 'resource-same',
      source: 'message' as const,
    };
    const dataTransfer = {
      getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
      dropEffect: 'copy',
    } as unknown as DataTransfer;
    fireEvent.drop(editor, { dataTransfer });
    fireEvent.drop(editor, { dataTransfer });

    expect(screen.getAllByRole('group', { name: '附件 same.md' })).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalled());
    const parts = vi.mocked(enqueueSessionMessage).mock.calls.at(-1)?.[3] || [];
    expect(parts.filter((part) => part.type === 'attachment')).toHaveLength(2);
    expect(
      parts.filter((part) => part.type === 'attachment').map((part) => part.attachmentId),
    ).toEqual(['resource-same', 'resource-same']);
  });

  it('removes an embedded attachment when native select-all Backspace removes its node', async () => {
    setBusySession();
    render(<InputRow />);
    const editor = screen.getByTestId('rich-text-composer');
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1',
    };
    fireEvent.drop(editor, {
      dataTransfer: {
        getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
      },
    });
    expect(screen.getByRole('group', { name: '附件 接口说明.md' })).toBeTruthy();

    // jsdom does not implement native Ctrl+A editing. This is the DOM shape
    // Chromium leaves after that command; the real mouse/keyboard path is
    // covered by e2e/attachment-dnd.mock.mjs.
    editor.replaceChildren(document.createElement('br'));
    fireEvent.input(editor);

    await waitFor(() => expect(editor.querySelector('[data-composer-attachment]')).toBeNull());
    expect(screen.queryByTestId('server-attachments')).toBeNull();
  });

  it('keeps an unembedded attachment chip when native select-all clears only editor text', async () => {
    setBusySession();
    useUIStore.getState().requestChatAttachment('s1', 'D:\\attachments\\report.txt');
    render(<InputRow />);
    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt'),
    );

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'only editor text' } });
    const editor = screen.getByTestId('rich-text-composer');
    editor.replaceChildren(document.createElement('br'));
    fireEvent.input(editor);

    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt'),
    );
    expect(screen.queryByRole('group', { name: '附件 report.txt' })).toBeTruthy();
  });

  it('simulates direct client upload in mock mode, then reuses its chip as an inline node', async () => {
    window.history.pushState({}, '', '/?mock=1');
    setBusySession();
    render(<InputRow />);
    const file = new File(['demo upload'], 'direct.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });

    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('上传中'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成'),
    );
    const chip = screen.getByTestId('draggable-attachment-chip');
    expect(chip.textContent).toContain('direct.txt');

    const data = new Map<string, string>();
    const dataTransfer = {
      getData: (type: string) => data.get(type) || '',
      setData: (type: string, value: string) => data.set(type, value),
      effectAllowed: 'copy',
      dropEffect: 'copy',
    } as unknown as DataTransfer;
    fireEvent.dragStart(chip, { dataTransfer });
    expect(dataTransfer.effectAllowed).toBe('move');
    expect(JSON.parse(data.get(ATTACHMENT_DRAG_MIME) || '{}')).toMatchObject({
      displayName: 'direct.txt',
      attachmentId: expect.any(String),
      source: 'attachment-chip',
      sourceSessionId: 's1',
    });
    fireEvent.drop(screen.getByTestId('rich-text-composer'), { dataTransfer });

    expect(screen.getByRole('group', { name: '附件 direct.txt' })).toBeTruthy();
    expect(screen.queryByTestId('draggable-attachment-chip')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        expect.stringMatching(
          /^\[direct\.txt\]\(\/api\/attachments\/upload_[a-z0-9]{32}\.txt\?session_id=s1\)$/,
        ),
        expect.any(String),
        expect.any(Array),
      ),
    );
    expect(uploadSessionAttachment).not.toHaveBeenCalled();
    window.history.pushState({}, '', '/');
  });

  it('deduplicates repeated files within one mock selection and keeps distinct files', async () => {
    window.history.pushState({}, '', '/?mock=1');
    setBusySession();
    render(<InputRow />);
    const first = new File(['first'], 'first.txt', { type: 'text/plain' });
    const second = new File(['second'], 'second.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), {
      target: { files: [first, first, second] },
    });

    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成'),
    );
    expect(screen.getAllByTestId('draggable-attachment-chip')).toHaveLength(2);
    expect(screen.getByTestId('server-attachments').textContent).toContain('first.txt');
    expect(screen.getByTestId('server-attachments').textContent).toContain('second.txt');
    expect(uploadSessionAttachment).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [first] } });
    await waitFor(() => expect(screen.getAllByTestId('draggable-attachment-chip')).toHaveLength(2));
    expect(uploadSessionAttachment).not.toHaveBeenCalled();
  });

  it('allows cancelling an in-flight mock file without restoring its chip after completion', async () => {
    window.history.pushState({}, '', '/?mock=1');
    setBusySession();
    render(<InputRow />);
    const file = new File(['cancel me'], 'cancel.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });

    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('上传中'),
    );
    fireEvent.click(screen.getByRole('button', { name: '取消附件 cancel.txt' }));
    expect(screen.queryByTestId('server-attachments')).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 220));
    expect(screen.queryByTestId('server-attachments')).toBeNull();
    expect(uploadSessionAttachment).not.toHaveBeenCalled();
  });

  it('aborts an in-flight real upload when its pending chip is cancelled', async () => {
    setBusySession();
    let uploadSignal!: AbortSignal;
    vi.mocked(uploadSessionAttachment).mockImplementationOnce(
      async (_sessionId, _file, _onProgress, signal) => {
        uploadSignal = signal!;
        return new Promise(() => {});
      },
    );
    render(<InputRow />);
    const file = new File(['cancel real'], 'cancel-real.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });

    await waitFor(() => expect(uploadSessionAttachment).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '取消附件 cancel-real.txt' }));
    expect(uploadSignal.aborted).toBe(true);
    expect(screen.queryByTestId('server-attachments')).toBeNull();
  });

  it('restores each session draft and its inline attachment structure independently', async () => {
    setBusySession();
    useSessionStore.setState((state) => ({
      inputDrafts: { s1: 'first draft', s2: 'second draft' },
      sessions: [
        ...state.sessions,
        {
          id: 's2',
          name: 'Second',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'idle',
          workerId: null,
          history: [],
        },
      ],
    }));
    render(<InputRow />);
    const editor = screen.getByTestId('rich-text-composer');
    const payload = {
      displayName: 'session.txt',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt?session_id=s1',
    };
    fireEvent.drop(editor, {
      dataTransfer: {
        getData: (type: string) => (type === ATTACHMENT_DRAG_MIME ? JSON.stringify(payload) : ''),
      },
    });
    expect(screen.getByRole('group', { name: '附件 session.txt' })).toBeTruthy();

    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    await waitFor(() =>
      expect((screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement).value).toBe(
        'second draft',
      ),
    );
    expect(screen.queryByRole('group', { name: '附件 session.txt' })).toBeNull();
    expect(screen.getByTestId('rich-text-composer').textContent).toBe('second draft');

    useSessionStore.setState({ currentSessionId: 's1', currentMessages: [] });
    await waitFor(() => {
      expect(screen.getByTestId('rich-text-composer').textContent).toContain('session.txt');
      expect(screen.getByRole('group', { name: '附件 session.txt' })).toBeTruthy();
    });
    expect(screen.getByTestId('rich-text-composer').textContent).toContain('first draft');
  });

  it('does not let an old Session attachment registration update the newly selected Session', async () => {
    setBusySession();
    useSessionStore.setState((state) => ({
      sessions: [
        ...state.sessions,
        {
          id: 's2',
          name: 'Second',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'idle',
          workerId: null,
          history: [],
        },
      ],
    }));
    const registration = deferred<Awaited<ReturnType<typeof registerServerFileAttachment>>>();
    vi.mocked(registerServerFileAttachment).mockReturnValueOnce(registration.promise);
    useUIStore.getState().requestChatAttachment('s1', 'D:\\attachments\\old-session.txt');
    render(<InputRow />);
    await waitFor(() =>
      expect(registerServerFileAttachment).toHaveBeenCalledWith(
        's1',
        'D:\\attachments\\old-session.txt',
      ),
    );

    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    await waitFor(() =>
      expect(screen.queryByRole('group', { name: '附件 old-session.txt' })).toBeNull(),
    );
    registration.resolve({
      ok: true,
      attachmentId: `att_${'b'.repeat(32)}`,
      displayName: 'old-session.txt',
      mimeType: 'text/plain',
      size: 1,
      path: 'D:\\attachments\\old-session.txt',
      href: '/api/fs/read?session_id=s1&path=old-session.txt&download=1',
    });
    await Promise.resolve();
    expect(screen.queryByRole('group', { name: '附件 old-session.txt' })).toBeNull();
  });

  it('consumes an editor request through the existing server attachment and queue path', async () => {
    setBusySession();
    vi.mocked(fetchDirectories).mockResolvedValueOnce({
      current: 'D:\\project\\src',
      parent: 'D:\\project',
      entries: [{ name: 'main.ts', path: 'D:\\project\\src\\main.ts', isDirectory: false }],
    });
    useUIStore.getState().requestChatAttachment('s1', 'D:\\project\\src\\main.ts');
    render(<InputRow />);

    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('main.ts'),
    );
    expect(useUIStore.getState().chatAttachmentRequests).toEqual([]);
    fireEvent.change(screen.getByPlaceholderText(/Type a message/), { target: { value: '审阅' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        '审阅 [main.ts](/api/fs/read?session_id=s1&path=D%3A%5Cproject%5Csrc%5Cmain.ts&download=1)',
        expect.any(String),
        expect.any(Array),
      ),
    );
    expect(fetchDirectories).toHaveBeenCalledWith('D:\\project\\src', true);
  });

  it('revalidates a selected server attachment before enqueue and cancels on a stale path', async () => {
    setBusySession();
    vi.mocked(fetchDirectories)
      .mockResolvedValueOnce({
        current: 'D:\\attachments',
        parent: 'D:\\',
        entries: [{ name: 'report.txt', path: 'D:\\attachments\\report.txt', isDirectory: false }],
      })
      .mockResolvedValueOnce({ current: 'D:\\attachments', parent: 'D:\\', entries: [] });
    render(<InputRow />);
    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => screen.getByRole('button', { name: 'report.txt' }));
    fireEvent.click(screen.getByRole('button', { name: 'report.txt' }));
    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.at(-1)?.message).toBe('当前目录非法'),
    );
    expect(enqueueSessionMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId('server-attachments')).toBeTruthy();
  });

  it('closes the server browser with its close button and backdrop', async () => {
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => expect(screen.getByTestId('directory-input-panel')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('directory-input-panel')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => expect(screen.getByTestId('directory-input-panel')).toBeTruthy());
    fireEvent.click(document.body.querySelector('.modal-overlay')!);
    expect(screen.queryByTestId('directory-input-panel')).toBeNull();
  });

  it('keeps attachments after a failed enqueue and allows cancelling one', async () => {
    setBusySession();
    vi.mocked(enqueueSessionMessage).mockRejectedValueOnce(new Error('offline'));
    render(<InputRow />);
    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => screen.getByRole('button', { name: 'report.txt' }));
    fireEvent.click(screen.getByRole('button', { name: 'report.txt' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByTestId('server-attachments')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /取消附件/ }));
    expect(screen.queryByTestId('server-attachments')).toBeNull();
  });

  it('uploads client files and combines them with server attachments on send', async () => {
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => screen.getByRole('button', { name: 'report.txt' }));
    fireEvent.click(screen.getByRole('button', { name: 'report.txt' }));
    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt'),
    );

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: '客户端附件' }));
    const file = new File(['client'], 'client.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });
    await waitFor(() =>
      expect(uploadSessionAttachment).toHaveBeenCalledWith(
        's1',
        file,
        expect.any(Function),
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('server-attachments').textContent).toContain('client.txt'),
    );

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: '合并发送' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        '合并发送 [report.txt](/api/fs/read?session_id=s1&path=D%3A%5Cattachments%5Creport.txt&download=1) [client.txt](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt?session_id=s1)',
        expect.any(String),
        expect.any(Array),
      ),
    );
  });

  it('shows deterministic aggregate progress and blocks send until upload completes', async () => {
    setBusySession();
    let finishUpload!: (value: Awaited<ReturnType<typeof uploadSessionAttachment>>) => void;
    vi.mocked(uploadSessionAttachment).mockImplementationOnce(
      async (_sessionId, file, onProgress) => {
        onProgress?.(4, file.size);
        return new Promise((resolve) => {
          finishUpload = resolve;
        });
      },
    );
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: '客户端附件' }));
    const file = new File(['12345678'], 'progress.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('50%');
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('上传中');
    });
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
    expect(enqueueSessionMessage).not.toHaveBeenCalled();

    finishUpload({
      ok: true,
      filename: file.name,
      path: 'D:\\attachments\\progress.txt',
      size: file.size,
    });
    await waitFor(() => {
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('100%');
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成');
    });
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('keeps failed uploads visible with a retry action', async () => {
    setBusySession();
    vi.mocked(uploadSessionAttachment)
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({
        ok: true,
        filename: 'retry.txt',
        path: 'D:\\attachments\\retry.txt',
        size: 5,
      });
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: '客户端附件' }));
    const file = new File(['retry'], 'retry.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });
    await waitFor(() => {
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('失败');
      expect(screen.getByRole('button', { name: '重试上传 retry.txt' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: '重试上传 retry.txt' }));
    await waitFor(() =>
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成'),
    );
  });

  it('enqueues through the server when worker busy, then shows the pending row', async () => {
    setBusySession();
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'queued msg' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(useQueueStore.getState().queues['s1']?.[0]?.text).toBe('queued msg'),
    );
    expect((textarea as HTMLTextAreaElement).value).toBe('');
    // Successful enqueue is visible immediately, correlated by the server
    // queue id; a later delivery event must merge idempotently.
    expect(useSessionStore.getState().currentMessages).toEqual([{
      role: 'user', content: 'queued msg', queueItemIds: ['q-queued-msg'],
    }]);

    // ^ 按钮角标显示 1（面板头部的计数也在 DOM 中，用 getAllByText）
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);

    // 点击 ^ 展开面板 → 显示队列项（排队消息的唯一 UI 呈现处）
    fireEvent.click(screen.getByLabelText('发送队列'));
    expect(screen.getByText('queued msg')).toBeTruthy();
  });

  it('blocks button, Enter, and imperative send paths while a queue item is being edited', async () => {
    setBusySession();
    useQueueStore.setState({
      queues: {
        s1: [{
          id: 'q-edit-send',
          queueItemId: 'q-edit-send',
          text: 'queued original',
          source: 'user',
          kind: 'task',
          createdAt: '2026-09-01T00:00:00Z',
          meta: { dispatchState: 'queued', revision: 1 },
        }],
      },
    });
    useQueueStore.getState().startEdit('q-edit-send');
    render(<InputRow />);

    const send = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'must not send while editing' },
    });
    fireEvent.keyDown(screen.getByPlaceholderText(/Type a message/), { key: 'Enter' });
    fireEvent.keyDown(screen.getByTestId('rich-text-composer'), { key: 'Enter' });
    expect(enqueueSessionMessage).not.toHaveBeenCalled();
    expect(useSessionStore.getState().currentMessages).toEqual([]);

    // Cancel is local-only; it reopens the ordinary send path without
    // changing the queue item's projection or adding an edit row.
    useQueueStore.getState().cancelEdit();
    await waitFor(() => expect(send.disabled).toBe(false));
    fireEvent.click(send);
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledTimes(1));
  });

  it('uses the current Session runtime worker, not the stale summary, to suppress optimistic history', async () => {
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1',
          name: 'Test',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'offline',
          workerId: null,
          history: [],
        },
      ],
    });
    useWorkerStore.setState({
      workers: { s1: { id: 'w-runtime', sessionId: 's1', status: 'running' } },
      currentWorkerId: 'w-runtime',
      currentWorker: { id: 'w-runtime', sessionId: 's1', status: 'running' },
    });
    render(<InputRow />);

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'live path msg' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('live path msg'),
    );
    expect(enqueueSessionMessage).toHaveBeenCalledWith(
      's1',
      'live path msg',
      expect.any(String),
      expect.any(Array),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it.each([
    ['idle runtime', { s1: { id: 'w-idle', sessionId: 's1', status: 'idle' as const } }],
    ['offline runtime', { s1: { id: 'w-offline', sessionId: 's1', status: 'offline' as const } }],
    ['no runtime entry', {}],
    [
      'another Session runtime',
      { s2: { id: 'w-other', sessionId: 's2', status: 'running' as const } },
    ],
    [
      'mismatched runtime entry',
      { s1: { id: 'w-other', sessionId: 's2', status: 'running' as const } },
    ],
  ])('keeps one optimistic row for %s', async (_name, workers) => {
    // The summary deliberately says running in every case; only the
    // current-session runtime registry is authoritative for this decision.
    setBusySession();
    useWorkerStore.setState({ workers });
    render(<InputRow />);

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'fallback path msg' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('fallback path msg'),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([{
      role: 'user', content: 'fallback path msg', queueItemIds: ['q-fallback-path-msg'],
    }]);
  });

  it('does not render a stale localStorage queue after a page reload', async () => {
    // Legacy localStorage is not a business source of truth.
    localStorage.setItem(
      'pan.sendQueue.s1',
      JSON.stringify([{ id: 'q1', text: 'survivor msg', createdAt: 1, status: 'pending' }]),
    );
    setBusySession();
    // 内存镜像为空（模拟刷新后 store 初始化）
    useQueueStore.setState({ queues: {}, edits: {}, batchSend: {} });
    render(<InputRow />);

    fireEvent.click(screen.getByLabelText('发送队列'));
    await waitFor(() => expect(screen.queryByText('survivor msg')).toBeNull());
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('also queues when worker is idle; Provider delivery is not a UI ack', async () => {
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1',
          name: 'Test',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'idle',
          workerId: 'w1',
          history: [],
        },
      ],
    });
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'direct msg' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(useQueueStore.getState().queues['s1']?.[0]?.text).toBe('direct msg'),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([{
      role: 'user', content: 'direct msg', queueItemIds: ['q-direct-msg'],
    }]);
  });

  it('uses the durable HTTP enqueue path when WS is unavailable', async () => {
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1',
          name: 'Test',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: null,
          workerId: null,
          history: [],
        },
      ],
    });
    Object.defineProperty(wsClient, 'isOpen', { value: false, configurable: true });
    vi.mocked(wsClient.send).mockReturnValue(false);
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'survive reconnect' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        'survive reconnect',
        expect.any(String),
        expect.any(Array),
      ),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([{
      role: 'user', content: 'survive reconnect', queueItemIds: ['q-survive-reconnect'],
    }]);
  });

  it('clears optimistically and restores the complete draft when enqueue fails', async () => {
    setBusySession();
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'retry this message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(textarea.value).toBe('');

    request.reject(new Error('network result unknown'));
    await waitFor(() => expect(textarea.value).toBe('retry this message'));
    expect(enqueueSessionMessage).toHaveBeenCalledTimes(1);
  });

  it('does not let a pending send overwrite input typed after the optimistic clear', async () => {
    setBusySession();
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'first' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.change(textarea, { target: { value: 'new input while waiting' } });
    request.resolve({
      item: {
        id: 'q-first',
        queueItemId: 'q-first',
        text: 'first',
        source: 'user',
        kind: 'task',
        createdAt: 1,
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    });

    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledTimes(1));
    expect(textarea.value).toBe('new input while waiting');
  });

  it('suppresses the optimistic row if the runtime becomes running before enqueue acknowledgement', async () => {
    setBusySession();
    useWorkerStore.setState({
      workers: { s1: { id: 'w-idle', sessionId: 's1', status: 'idle' } },
      currentWorkerId: 'w-idle',
      currentWorker: { id: 'w-idle', sessionId: 's1', status: 'idle' },
    });
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'transition to live' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    useWorkerStore.getState().updateWorker('s1', 'w-running', 'running');
    request.resolve({
      item: {
        id: 'q-transition-to-live',
        queueItemId: 'q-transition-to-live',
        text: 'transition to live',
        source: 'user',
        kind: 'task',
        createdAt: 1,
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    });

    await waitFor(() =>
      expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('transition to live'),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('keeps a send that started in the live path suppressed if the runtime becomes idle before acknowledgement', async () => {
    setBusySession();
    useWorkerStore.setState({
      workers: { s1: { id: 'w-running', sessionId: 's1', status: 'running' } },
      currentWorkerId: 'w-running',
      currentWorker: { id: 'w-running', sessionId: 's1', status: 'running' },
    });
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), {
      target: { value: 'started while live' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    useWorkerStore.getState().updateWorker('s1', 'w-running', 'idle');
    request.resolve({
      item: {
        id: 'q-started-while-live',
        queueItemId: 'q-started-while-live',
        text: 'started while live',
        source: 'user',
        kind: 'task',
        createdAt: 1,
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    });

    await waitFor(() =>
      expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('started while live'),
    );
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('keeps a new Session draft isolated while the old Session send completes', async () => {
    setBusySession();
    useSessionStore.setState((state) => ({
      sessions: [
        ...state.sessions,
        {
          id: 's2',
          name: 'Second',
          adapter: 'cbc',
          model: null,
          permissionMode: null,
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'idle',
          workerId: null,
          history: [],
        },
      ],
    }));
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'belongs to s1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
    await waitFor(() => expect(textarea.value).toBe(''));
    fireEvent.change(textarea, { target: { value: 'belongs to s2' } });
    request.resolve({
      item: {
        id: 'q-s1',
        queueItemId: 'q-s1',
        text: 'belongs to s1',
        source: 'user',
        kind: 'task',
        createdAt: 1,
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    });

    await waitFor(() =>
      expect(enqueueSessionMessage).toHaveBeenCalledWith(
        's1',
        'belongs to s1',
        expect.any(String),
        expect.any(Array),
      ),
    );
    expect(textarea.value).toBe('belongs to s2');
    expect(useSessionStore.getState().currentMessages).toEqual([]);
    expect(useQueueStore.getState().queues.s2).toBeUndefined();
    expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('belongs to s1');
  });

  it('does not duplicate a submission on repeated clicks while its request is pending', async () => {
    setBusySession();
    const request = deferred<Awaited<ReturnType<typeof enqueueSessionMessage>>>();
    vi.mocked(enqueueSessionMessage).mockReturnValueOnce(request.promise);
    render(<InputRow />);
    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'once' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledTimes(1));
    request.resolve({
      item: {
        id: 'q-once',
        queueItemId: 'q-once',
        text: 'once',
        source: 'user',
        kind: 'task',
        createdAt: 1,
        meta: { dispatchState: 'queued', revision: 1 },
      },
      queueRevision: 1,
    });
    await waitFor(() => expect(useQueueStore.getState().queues.s1?.[0]?.text).toBe('once'));
  });

  it('supports consecutive reset/send transactions without retaining stale text', async () => {
    setBusySession();
    vi.mocked(enqueueSessionMessage)
      .mockResolvedValueOnce({
        item: {
          id: 'q-one',
          queueItemId: 'q-one',
          text: 'one',
          source: 'user',
          kind: 'task',
          createdAt: 1,
          meta: { dispatchState: 'queued', revision: 1 },
        },
        queueRevision: 1,
      })
      .mockResolvedValueOnce({
        item: {
          id: 'q-two',
          queueItemId: 'q-two',
          text: 'two',
          source: 'user',
          kind: 'task',
          createdAt: 2,
          meta: { dispatchState: 'queued', revision: 2 },
        },
        queueRevision: 2,
      });
    render(<InputRow />);
    const textarea = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'one' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(textarea.value).toBe(''));
    fireEvent.change(textarea, { target: { value: 'two' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledTimes(2));
    expect(textarea.value).toBe('');
    expect(useQueueStore.getState().queues.s1?.map((entry) => entry.text)).toEqual(['one', 'two']);
  });
});

describe('InputRow responsive composer controls', () => {
  it('renders the desktop resize handle and changes height by pointer drag', async () => {
    setBusySession();
    render(<InputRow />);
    const handle = await waitFor(() => screen.getByTestId('desktop-composer-resize'));
    const root = screen.getByTestId('input-row');
    expect(root.getAttribute('style')).toContain('height: 180px');

    handle.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientY: 500 }));
    await waitFor(() => expect(handle.className).toContain('bg-accent/50'));
    const move = new MouseEvent('pointermove', { bubbles: true, clientY: 400 });
    handle.dispatchEvent(move);
    await waitFor(() => expect(root.getAttribute('style')).toContain('height: 280px'));
    document.dispatchEvent(new Event('pointerup'));
  });

  it('opens the desktop queue above a short composer without changing its height', async () => {
    setBusySession();
    render(<InputRow />);
    const root = screen.getByTestId('input-row');
    const handle = screen.getByTestId('desktop-composer-resize');

    handle.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientY: 500 }));
    document.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientY: 560 }));
    await waitFor(() => expect(root.getAttribute('style')).toContain('height: 120px'));

    fireEvent.click(screen.getByLabelText('发送队列'));
    const anchor = screen.getByTestId('send-queue-anchor');
    expect(anchor.className).toContain('absolute');
    expect(anchor.className).toContain('bottom-full');
    expect(anchor.className).toContain('z-20');
    expect(root.getAttribute('style')).toContain('height: 120px');
    expect(screen.getByPlaceholderText(/Type a message/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    document.dispatchEvent(new Event('pointerup'));
  });

  it('shows only the mobile fullscreen control and enters/exits with click or Escape', async () => {
    mockMatchMedia(true);
    setBusySession();
    render(<InputRow />);

    await waitFor(() => expect(screen.getByTestId('mobile-input-fullscreen')).toBeTruthy());
    expect(screen.queryByTestId('desktop-composer-resize')).toBeNull();
    const root = screen.getByTestId('input-row');
    const enter = screen.getByRole('button', { name: '全屏输入' });

    fireEvent.click(enter);
    expect(root.className).toContain('fixed');
    expect(screen.getByRole('button', { name: '退出全屏输入' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(root.className).not.toContain('fixed');

    fireEvent.click(screen.getByRole('button', { name: '全屏输入' }));
    expect(root.className).toContain('fixed');
    fireEvent.click(screen.getByRole('button', { name: '退出全屏输入' }));
    expect(root.className).not.toContain('fixed');
  });

  it('uses a viewport-filling modal for server attachments on mobile', async () => {
    mockMatchMedia(true);
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => expect(screen.getByTestId('directory-input-panel')).toBeTruthy());

    const overlay = document.body.querySelector('.modal-overlay')!;
    const card = document.body.querySelector('.modal-card')!;
    expect(overlay.className).toContain('p-0 md:p-4');
    expect(card.className).toContain('max-md:h-[100dvh]');
    expect(card.className).toContain('max-md:max-h-[100dvh]');
    expect(card.className).toContain('max-md:rounded-none');
  });

  it('opens the ordinary mobile queue above the composer', () => {
    mockMatchMedia(true);
    setBusySession();
    render(<InputRow />);

    const anchor = screen.getByTestId('send-queue-anchor');
    expect(anchor.className).toContain('absolute');
    expect(anchor.className).toContain('bottom-full');
  });

  it('keeps the queue in fullscreen mobile flow so it stays inside the viewport', () => {
    mockMatchMedia(true);
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '全屏输入' }));
    const anchor = screen.getByTestId('send-queue-anchor');
    const root = screen.getByTestId('input-row');
    expect(anchor.className).toContain('shrink-0');
    expect(anchor.className).not.toContain('absolute');
    expect(root.className).toContain('fixed');
    expect(root.className).toContain('overflow-hidden');
    expect(screen.getByPlaceholderText(/Type a message/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
  });
});

// ── ModelPill 搜索过滤（复用 ModelSelect）──

const OPENCODE_CONFIG: AdapterConfig = {
  models: [
    'opencode/big-pickle',
    'opencode/mimo-v2.5-free',
    'siliconflow-cn/deepseek-ai/DeepSeek-R1',
    'siliconflow-cn/Qwen/Qwen3-14B',
  ],
  defaultModel: 'opencode/big-pickle',
  effortValues: [],
  permissionModes: [],
  defaultPermissionMode: '',
  supportedSettings: ['model'],
};

const CODEX_CONFIG: AdapterConfig = {
  models: ['gpt-5-codex'],
  defaultModel: 'gpt-5-codex',
  effortValues: [],
  permissionModes: [
    { value: 'read-only', label: 'read-only (auto)' },
    { value: 'workspace-write', label: 'workspace-write (auto)' },
  ],
  defaultPermissionMode: 'read-only',
  supportedSettings: ['permissionMode'],
};

const MODEL_AND_PERMISSION_CONFIG: AdapterConfig = {
  ...OPENCODE_CONFIG,
  permissionModes: CODEX_CONFIG.permissionModes,
  defaultPermissionMode: CODEX_CONFIG.defaultPermissionMode,
  supportedSettings: ['model', 'permissionMode', 'thinking'],
};

function setModelSession() {
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [
      {
        id: 's1',
        name: 'Test',
        adapter: 'opencode',
        model: null,
        permissionMode: null,
        alwaysThinkingEnabled: false,
        effort: '',
        workerStatus: 'idle',
        workerId: 'w1',
        history: [],
      },
    ],
  });
  useAdapterStore.setState({
    currentAdapter: 'opencode',
    adapterConfigs: { opencode: OPENCODE_CONFIG },
  });
}

function setModelAndPermissionSession() {
  useSessionStore.setState({
    currentSessionId: 's1',
    currentMessages: [],
    sessions: [
      {
        id: 's1',
        name: 'Test',
        adapter: 'opencode',
        model: 'opencode/big-pickle',
        permissionMode: 'read-only',
        alwaysThinkingEnabled: true,
        effort: '',
        workerStatus: 'idle',
        workerId: 'w1',
        history: [],
      },
    ],
  });
  useAdapterStore.setState({
    currentAdapter: 'opencode',
    adapterConfigs: { opencode: MODEL_AND_PERMISSION_CONFIG },
  });
}

describe('InputRow pill visibility', () => {
  it('shows the model pill on mobile and keeps the permission pill desktop-only', () => {
    setModelAndPermissionSession();
    render(<InputRow />);

    const modelPill = document.querySelector('[data-model-pill]');
    const permissionPill = document.querySelector('[data-perm-pill]');

    expect(modelPill).toBeTruthy();
    expect(modelPill?.parentElement?.className).not.toContain('hidden');
    expect(permissionPill).toBeTruthy();
    expect(permissionPill?.parentElement?.className).toContain('hidden md:flex');
    expect(screen.getByRole('button', { name: /opencode\/big-pickle/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /read-only/ })).toBeTruthy();
  });
});

describe('InputRow control row layout contract', () => {
  it('anchors the queue count badge to the Queue button, including the desktop busy hint', () => {
    mockMatchMedia(false);
    setBusySession();
    useQueueStore.setState({
      queues: {
        s1: [{
          id: 'q-1',
          queueItemId: 'q-1',
          kind: 'task',
          text: 'queued',
          createdAt: 1,
          meta: { dispatchState: 'queued', revision: 1 },
        }],
      },
    });
    render(<InputRow />);

    const queueButton = screen.getByRole('button', { name: /发送队列（1 条待发）/ });
    const badge = screen.getByTestId('queue-count-badge');
    expect(queueButton.className).toContain('relative');
    expect(badge.parentElement).toBe(queueButton);
    expect(badge.className).toContain('absolute');
    expect(badge.className).toContain('pointer-events-none');
  });

  it('uses a wider Queue control and right-aligns desktop attachment', () => {
    mockMatchMedia(false);
    setModelAndPermissionSession();
    render(<InputRow />);
    expect(screen.getByText('Queue')).toBeTruthy();
    expect(screen.getByText('Queue').closest('button')?.className).toContain('md:w-auto');
    expect(screen.getByRole('button', { name: '添加附件' }).parentElement?.className).toContain(
      'md:ml-auto',
    );
  });

  it('shows both attachment choices outside the clipped control row', () => {
    setModelAndPermissionSession();
    render(<InputRow />);
    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    const menu = screen.getByTestId('attachment-menu');
    expect(menu).toBeTruthy();
    expect(menu.parentElement).toBe(document.body);
    expect(screen.getByRole('button', { name: /服务端附件$/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: '客户端附件' })).toBeTruthy();
  });

  it('keeps settings, model and permission menus visible outside the composer clipping contexts', async () => {
    setModelAndPermissionSession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: 'Session settings' }));
    expect(screen.getByText('Permission Mode', { selector: 'label' })).toBeTruthy();
    expect(
      screen.getByText('Permission Mode', { selector: 'label' }).closest('[data-settings-popover]')
        ?.parentElement,
    ).toBe(document.body);

    fireEvent.click(
      screen.getByTestId('input-control-row').querySelector('button[title="opencode/big-pickle"]')!,
    );
    await waitFor(() =>
      expect(
        screen.getByPlaceholderText('筛选模型…').closest('[data-model-select-menu]')?.parentElement,
      ).toBe(document.body),
    );
    fireEvent.click(document.body.querySelector('[data-perm-pill] button')!);
    await waitFor(() => expect(document.querySelector('[data-permission-menu]')).toBeTruthy());
    expect(document.querySelector('[data-permission-menu]')?.parentElement).toBe(document.body);
  });

  it('keeps desktop attachment in the control row, separate from textarea and Send', () => {
    mockMatchMedia(false);
    setModelAndPermissionSession();
    render(<InputRow />);
    const controls = screen.getByTestId('input-control-row');
    expect(controls.className).toContain('flex-nowrap');
    expect(controls.querySelector('[data-perm-pill]')).toBeTruthy();
    expect(controls.querySelector('button[aria-label="添加附件"]')).toBeTruthy();
    expect(
      controls.compareDocumentPosition(screen.getByRole('button', { name: 'Send' })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '添加附件' }).closest('[data-testid="input-control-row"]'),
    ).toBe(controls);
  });

  it('orders mobile settings, queue, model, effort, attachment and fullscreen, hiding Thinking first', () => {
    mockMatchMedia(true);
    setModelAndPermissionSession();
    render(<InputRow />);
    const controls = screen.getByTestId('input-control-row');
    const labels = Array.from(controls.querySelectorAll('button, select')).map(
      (node) =>
        node.getAttribute('aria-label') || node.getAttribute('title') || node.textContent?.trim(),
    );
    expect(labels.findIndex((label) => label === 'Session settings')).toBeLessThan(
      labels.findIndex((label) => label === '发送队列'),
    );
    const modelIndex = labels.findIndex(
      (label) => label?.includes('模型') || label?.includes('opencode'),
    );
    expect(labels.findIndex((label) => label === '发送队列')).toBeLessThan(modelIndex);
    const effortIndex = labels.findIndex((label) => label === 'Effort');
    if (effortIndex >= 0) {
      expect(modelIndex).toBeLessThan(effortIndex);
      expect(effortIndex).toBeLessThan(labels.findIndex((label) => label === '添加附件'));
    }
    expect(labels.findIndex((label) => label === '添加附件')).toBeLessThan(
      labels.findIndex((label) => label === '全屏输入'),
    );
    expect(controls.className).toContain('flex-nowrap');
    expect(
      screen.getByRole('button', { name: 'Send' }).closest('[data-testid="input-control-row"]'),
    ).toBeNull();
    expect(controls.querySelector('[data-testid="thinking-toggle"]')).toBeNull();
  });
});

describe('InputRow ModelPill search', () => {
  it('opens a searchable dropdown and filters models by keyword', () => {
    setModelSession();
    render(<InputRow />);

    // pill 按钮显示当前模型（session 未设置时回退 defaultModel）
    const pill = screen.getByRole('button', { name: /opencode\/big-pickle/ });
    fireEvent.click(pill);

    // 展开后有过滤输入框 + 全部模型
    const search = screen.getByPlaceholderText('筛选模型…');
    expect(search).toBeTruthy();
    for (const m of OPENCODE_CONFIG.models) {
      expect(screen.getByRole('option', { name: m })).toBeTruthy();
    }

    // 输入关键字后只剩匹配项
    fireEvent.change(search, { target: { value: 'qwen' } });
    expect(screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'opencode/big-pickle' })).toBeNull();
    expect(
      screen.queryByRole('option', {
        name: 'siliconflow-cn/deepseek-ai/DeepSeek-R1',
      }),
    ).toBeNull();
  });

  it('applies the selected model immediately and closes the dropdown', () => {
    setModelSession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: /opencode\/big-pickle/ }));
    fireEvent.click(
      screen.getByRole('option', {
        name: 'siliconflow-cn/Qwen/Qwen3-14B',
      }),
    );

    expect(patchSession).toHaveBeenCalledWith('s1', {
      model: 'siliconflow-cn/Qwen/Qwen3-14B',
    });
    // 选中后下拉关闭
    expect(screen.queryByPlaceholderText('筛选模型…')).toBeNull();
  });

  it('closes the dropdown when clicking outside', () => {
    setModelSession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: /opencode\/big-pickle/ }));
    expect(screen.getByPlaceholderText('筛选模型…')).toBeTruthy();

    fireEvent.mouseDown(screen.getByPlaceholderText(/Type a message/));
    expect(screen.queryByPlaceholderText('筛选模型…')).toBeNull();
  });
});

describe('InputRow PermissionPill', () => {
  it('keeps the collapsed Codex permission pill to the short label', () => {
    useSessionStore.setState({
      currentSessionId: 's1',
      currentMessages: [],
      sessions: [
        {
          id: 's1',
          name: 'Test',
          adapter: 'codex',
          model: null,
          permissionMode: 'read-only',
          alwaysThinkingEnabled: false,
          effort: '',
          workerStatus: 'idle',
          workerId: 'w1',
          history: [],
        },
      ],
    });
    useAdapterStore.setState({
      currentAdapter: 'codex',
      adapterConfigs: { codex: CODEX_CONFIG },
    });

    render(<InputRow />);

    const pill = document.querySelector('[data-perm-pill] button')!;
    expect(pill.textContent).toBe('read-only');
    fireEvent.click(pill);
    expect(screen.getByText('read-only (auto)')).toBeTruthy();
  });
});
