// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/react';
import { InputRow } from './InputRow';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { fetchSessionQueue, sendSession, spawnWorker, patchSession } from '@/services/api';
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
    fetchSessionQueue: vi.fn(async () => []),
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

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    currentSessionId: null,
    currentMessages: [],
    sessions: [],
  });
  useQueueStore.setState({
    queues: {}, edits: {}, batchSend: {}, sendingId: null, panelOpen: false,
    agentQueues: {}, agentQueueLoadSeq: {},
  });
  useUIStore.setState({ toastQueue: [] });
  useAdapterStore.setState({
    adapters: [],
    adapterConfigs: {},
    currentAdapter: 'cbc',
    configReady: false,
  });
  vi.mocked(patchSession).mockClear();
  vi.mocked(sendSession).mockClear();
  vi.mocked(fetchSessionQueue).mockReset().mockResolvedValue([]);
  vi.mocked(spawnWorker).mockClear();
  vi.mocked(wsClient.send).mockReset().mockReturnValue(true);
  Object.defineProperty(wsClient, 'isOpen', { value: true, configurable: true });
});

afterEach(cleanup);

describe('InputRow send queue wiring', () => {
  it('persists through the server queue when worker busy, without interrupting it', async () => {
    setBusySession();
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'queued msg' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(sendSession).toHaveBeenCalledWith(
      's1', 'queued msg', expect.any(String),
    ));
    // 正常输入不再写入 localStorage queue
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(0);
    expect((textarea as HTMLTextAreaElement).value).toBe('');
    expect(useSessionStore.getState().currentMessages[0]?.content).toBe('queued msg');

    // 点击 ^ 展开面板；服务端队列由 GET /queue 镜像，local queue 为空
    fireEvent.click(screen.getByLabelText('发送队列'));
    expect(screen.getByText('待发送队列为空')).toBeTruthy();
  });

  it('shows frontend tasks, reports, and QQ reminders in one server FIFO list', async () => {
    setBusySession();
    vi.mocked(fetchSessionQueue).mockResolvedValue([
      { id: 'q-task', kind: 'task', text: 'frontend input', createdAt: 0, source: 'user' },
      { id: 'q-report', kind: 'report', text: 'agent result', createdAt: 0, source: 'report' },
      { id: 'q-qq', kind: 'qq', text: 'QQ reminder', createdAt: 0, source: 'qq' },
    ]);
    render(<InputRow />);

    fireEvent.click(screen.getByLabelText('发送队列'));
    await waitFor(() => {
      expect(screen.getByText('统一服务端待发送队列（FIFO）')).toBeTruthy();
    });
    expect(screen.getByText('user task')).toBeTruthy();
    expect(screen.getByText('agent report')).toBeTruthy();
    expect(screen.getByText('agent qq')).toBeTruthy();
    expect(screen.getByText('frontend input')).toBeTruthy();
    expect(screen.getByText('agent result')).toBeTruthy();
    expect(screen.getByText('QQ reminder')).toBeTruthy();
    expect(screen.queryByText('服务端待发送队列（FIFO）')).toBeNull();
  });

  it('still renders queued messages from localStorage after a page reload', () => {
    // 预置上一页留下的队列（localStorage 持久化）
    localStorage.setItem(
      'pan.sendQueue.s1',
      JSON.stringify([{ id: 'q1', text: 'survivor msg', createdAt: 1, status: 'pending' }]),
    );
    setBusySession();
    // 内存镜像为空（模拟刷新后 store 初始化）
    useQueueStore.setState({ queues: {}, edits: {}, batchSend: {} });
    render(<InputRow />);

    // SendQueuePanel mount effect loadForSession → 从 localStorage 恢复并渲染
    fireEvent.click(screen.getByLabelText('发送队列'));
    expect(screen.getByText('survivor msg')).toBeTruthy();
    // 聊天历史（服务端拉取）中不含它
    expect(useSessionStore.getState().currentMessages).toEqual([]);
  });

  it('uses the same durable server queue when worker is idle', async () => {
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

    await waitFor(() => expect(sendSession).toHaveBeenCalledWith(
      's1', 'direct msg', expect.any(String),
    ));
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(0);
    expect(useSessionStore.getState().currentMessages.length).toBe(1);
    expect(useSessionStore.getState().currentMessages[0]?.content).toBe('direct msg');
  });

  it('uses durable HTTP fallback when WS is unavailable during first spawn', async () => {
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

    await waitFor(() => {
      expect(sendSession).toHaveBeenCalledWith('s1', 'survive reconnect', expect.any(String));
    });
    expect(spawnWorker).not.toHaveBeenCalled();
    expect(useSessionStore.getState().currentMessages.map((m) => m.content)).toEqual([
      'survive reconnect',
    ]);
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
