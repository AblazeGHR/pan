// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/react';
import { InputRow } from './InputRow';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { enqueueSessionMessage, sendSession, spawnWorker, patchSession, uploadSessionAttachment } from '@/services/api';
import { wsClient } from '@/services/ws';
import type { AdapterConfig } from '@/types';

vi.mock('@/services/ws', () => ({
  wsClient: {
    send: vi.fn(() => true),
    isOpen: true,
    // queueStore 模块加载时会注册 wsClient.on('open', ...) 联动
    on: vi.fn(),
  },
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    patchSession: vi.fn(async () => ({})),
    fetchSessions: vi.fn(async () => []),
    fetchDirectories: vi.fn(async () => ({
      current: 'D:\\attachments',
      parent: 'D:\\',
      entries: [
        { name: 'report.txt', path: 'D:\\attachments\\report.txt', isDirectory: false },
      ],
    })),
    uploadSessionAttachment: vi.fn(async (_sessionId: string, file: File) => ({
      ok: true,
      filename: file.name,
      path: `D:\\attachments\\uploaded\\${file.name}`,
      size: file.size,
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
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
  });
  useQueueStore.setState({ queues: {}, edits: {}, batchSend: {}, sendingId: null, panelOpen: false });
  useUIStore.setState({ toastQueue: [] });
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
  vi.mocked(spawnWorker).mockClear();
  vi.mocked(wsClient.send).mockReset().mockReturnValue(true);
  Object.defineProperty(wsClient, 'isOpen', { value: true, configurable: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('InputRow send queue wiring', () => {
  it('selects server files, renders attachment chips, and enqueues formatted paths', async () => {
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    expect(screen.queryByTestId('directory-browser')?.closest('.modal-card')).toBeTruthy();
    expect(screen.queryByLabelText('Server attachment browser')?.closest('[data-testid="input-row"]')).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'report.txt' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'report.txt' }));

    expect(screen.getByTestId('server-attachments').textContent).toContain('report.txt');
    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: '请阅读' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledWith(
      's1', '请阅读 @"D:\\attachments\\report.txt"', expect.any(String),
    ));
    await waitFor(() => expect(screen.queryByTestId('server-attachments')).toBeNull());
  });

  it('closes the server browser with its close button and backdrop', async () => {
    setBusySession();
    render(<InputRow />);

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => expect(screen.getByTestId('directory-browser')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('directory-browser')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: /服务端附件$/ }));
    await waitFor(() => expect(screen.getByTestId('directory-browser')).toBeTruthy());
    fireEvent.click(document.body.querySelector('.modal-overlay')!);
    expect(screen.queryByTestId('directory-browser')).toBeNull();
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

    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    fireEvent.click(screen.getByRole('button', { name: '客户端附件' }));
    const file = new File(['client'], 'client.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('client-attachment-input'), { target: { files: [file] } });
    await waitFor(() => expect(uploadSessionAttachment).toHaveBeenCalledWith('s1', file, expect.any(Function)));
    await waitFor(() => expect(screen.getByTestId('server-attachments').textContent).toContain('client.txt'));

    fireEvent.change(screen.getByPlaceholderText(/Type a message/), { target: { value: '合并发送' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledWith(
      's1', '合并发送 @"D:\\attachments\\report.txt" @"D:\\attachments\\uploaded\\client.txt"', expect.any(String),
    ));
  });

  it('shows deterministic aggregate progress and blocks send until upload completes', async () => {
    setBusySession();
    let finishUpload!: (value: Awaited<ReturnType<typeof uploadSessionAttachment>>) => void;
    vi.mocked(uploadSessionAttachment).mockImplementationOnce(async (_sessionId, file, onProgress) => {
      onProgress?.(4, file.size);
      return new Promise((resolve) => { finishUpload = resolve; });
    });
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

    finishUpload({ ok: true, filename: file.name, path: 'D:\\attachments\\progress.txt', size: file.size });
    await waitFor(() => {
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('100%');
      expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成');
    });
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('keeps failed uploads visible with a retry action', async () => {
    setBusySession();
    vi.mocked(uploadSessionAttachment)
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ ok: true, filename: 'retry.txt', path: 'D:\\attachments\\retry.txt', size: 5 });
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
    await waitFor(() => expect(screen.getByTestId('attachment-upload-progress').textContent).toContain('已完成'));
  });

  it('enqueues through the server when worker busy, then shows the pending row', async () => {
    setBusySession();
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'queued msg' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(useQueueStore.getState().queues['s1']?.[0]?.text).toBe('queued msg'));
    expect((textarea as HTMLTextAreaElement).value).toBe('');
    // 排队消息不上屏：它不在服务端 history 中，伪装进聊天会在刷新后凭空消失
    expect(useSessionStore.getState().currentMessages).toEqual([]);

    // ^ 按钮角标显示 1（面板头部的计数也在 DOM 中，用 getAllByText）
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);

    // 点击 ^ 展开面板 → 显示队列项（排队消息的唯一 UI 呈现处）
    fireEvent.click(screen.getByLabelText('发送队列'));
    expect(screen.getByText('queued msg')).toBeTruthy();
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

    await waitFor(() => expect(useQueueStore.getState().queues['s1']?.[0]?.text).toBe('direct msg'));
    expect(useSessionStore.getState().currentMessages).toEqual([]);
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

    await waitFor(() => expect(enqueueSessionMessage).toHaveBeenCalledWith(
      's1', 'survive reconnect', expect.any(String),
    ));
    expect(useSessionStore.getState().currentMessages).toEqual([]);
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
    await waitFor(() => expect(screen.getByTestId('directory-browser')).toBeTruthy());

    const overlay = document.body.querySelector('.modal-overlay')!;
    const card = document.body.querySelector('.modal-card')!;
    expect(overlay.className).toContain('p-0 md:p-4');
    expect(card.className).toContain('max-md:h-[100dvh]');
    expect(card.className).toContain('max-md:max-h-[100dvh]');
    expect(card.className).toContain('max-md:rounded-none');
  });

  it('keeps the queue in the mobile composer flow instead of using desktop positioning', () => {
    mockMatchMedia(true);
    setBusySession();
    render(<InputRow />);

    const anchor = screen.getByTestId('send-queue-anchor');
    expect(anchor.className).toContain('shrink-0');
    expect(anchor.className).not.toContain('absolute');
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
    expect(
      screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B' }),
    ).toBeTruthy();
    expect(
      screen.queryByRole('option', { name: 'opencode/big-pickle' }),
    ).toBeNull();
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



