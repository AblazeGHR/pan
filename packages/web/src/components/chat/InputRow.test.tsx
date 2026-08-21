// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, screen, cleanup } from '@testing-library/react';
import { InputRow } from './InputRow';
import { useSessionStore } from '@/stores/sessionStore';
import { useQueueStore } from '@/stores/queueStore';
import { useUIStore } from '@/stores/uiStore';

vi.mock('@/services/ws', () => ({
  wsClient: {
    send: vi.fn(() => true),
    isOpen: true,
  },
}));

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
  useQueueStore.setState({ queues: {}, edits: {}, batchSend: {}, sendingId: null, panelOpen: false });
  useUIStore.setState({ toastQueue: [] });
});

afterEach(cleanup);

describe('InputRow send queue wiring', () => {
  it('enqueues when worker busy, clears input, and shows badge + panel row', () => {
    setBusySession();
    render(<InputRow />);

    const textarea = screen.getByPlaceholderText(/Type a message/);
    fireEvent.change(textarea, { target: { value: 'queued msg' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    // 入队成功、输入框清空
    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(1);
    expect(q[0]?.text).toBe('queued msg');
    expect((textarea as HTMLTextAreaElement).value).toBe('');
    // 不上屏
    expect(useSessionStore.getState().currentMessages.length).toBe(0);

    // ^ 按钮角标显示 1（面板头部的计数也在 DOM 中，用 getAllByText）
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);

    // 点击 ^ 展开面板 → 显示队列项
    fireEvent.click(screen.getByLabelText('发送队列'));
    expect(screen.getByText('queued msg')).toBeTruthy();
    expect(screen.getByText('待发送')).toBeTruthy();
  });

  it('still sends directly when worker is idle', () => {
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

    const q = useQueueStore.getState().queues['s1'] ?? [];
    expect(q.length).toBe(0);
    expect(useSessionStore.getState().currentMessages.length).toBe(1);
    expect(useSessionStore.getState().currentMessages[0]?.content).toBe('direct msg');
  });
});
