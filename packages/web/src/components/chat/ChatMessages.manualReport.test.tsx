// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, act } from '@testing-library/react';
import { ChatMessages } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Message, Session } from '@/types';
import { reportTaskManually as reportTaskManuallyApi } from '@/services/api';

// The real virtualizer needs layout/ResizeObserver, which jsdom lacks. This
// stub renders every row so the manual-report binding can be observed on the
// exact row the real list would paint.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (options: { count?: number }) => {
    const count = options.count ?? 0;
    return {
      getTotalSize: () => count * 100,
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({
        index,
        start: index * 100,
        size: 100,
        key: index,
      })),
      measureElement: () => {},
      scrollToIndex: () => {},
      measure: () => {},
      options,
    };
  },
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, reportTaskManually: vi.fn(async () => ({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false })) };
});

const reportMock = vi.mocked(reportTaskManuallyApi);

class TestResizeObserver {
  constructor(_callback: ResizeObserverCallback) {}
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function assistant(messageId: string, content: string): Message {
  return { role: 'assistant', content, messageId, ts: '2026-10-06T12:00:00' };
}

function assign(messageId: string, taskId: string, content: string): Message {
  return { role: 'user', content, messageId, source: 'agent', taskIdSource: 'assign', taskId, ts: '2026-10-06T11:00:00' };
}

function session(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    ...extra,
  } as Session;
}

function setTranscript(sessionId: string, messages: Message[], extra: Partial<Session> = {}): void {
  useSessionStore.setState({
    currentSessionId: sessionId,
    currentMessages: messages,
    sessions: [session(sessionId, extra)],
  });
}

const TA_TRANSCRIPT: Message[] = [
  assign('pan:u1', 'task-1', 'do task 1'),
  assistant('pan:a1', 'answer 1'),
  assign('pan:u2', 'task-2', 'do task 2'),
  assistant('pan:a2', 'answer 2'),
];

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    const timer = setTimeout(() => cb(Date.now()), 0);
    return timer as unknown as number;
  }) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>)) as typeof cancelAnimationFrame;
  reportMock.mockClear();
  useSessionStore.setState({ manualTaskReports: {} });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

function reportButtons(): HTMLButtonElement[] {
  return [...document.querySelectorAll<HTMLButtonElement>('button[data-manual-report]')];
}

describe('ChatMessages manual report wiring', () => {
  it('shows the action on the latest task tail block only', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    render(<ChatMessages />);
    const buttons = reportButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.getAttribute('data-manual-report')).toBe('ses_ta:pan:a2');
    expect(buttons[0]!.textContent).toContain('汇报');
  });

  it('keeps the action visible when the manager is unsubscribed', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      reportsToManager: false,
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    render(<ChatMessages />);
    expect(reportButtons()).toHaveLength(1);
  });

  it('hides the action while the worker is running', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      workerStatus: 'running',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    render(<ChatMessages />);
    expect(reportButtons()).toHaveLength(0);
  });

  it('hides the action without a manager', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    render(<ChatMessages />);
    expect(reportButtons()).toHaveLength(0);
  });

  it('re-binds to the new tail block when a late task completes', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    const view = render(<ChatMessages />);
    expect(reportButtons()[0]!.getAttribute('data-manual-report')).toBe('ses_ta:pan:a2');

    // A newer task finishes while the view stays open: the binding must move to
    // the new tail block, never stay on the stale one.
    act(() => {
      setTranscript('ses_ta', [
        ...TA_TRANSCRIPT,
        assign('pan:u3', 'task-3', 'do task 3'),
        assistant('pan:a3', 'answer 3'),
      ], {
        managedBy: 'ses_mgr',
        lastResult: { status: 'done', result: 'answer 3', taskId: 'task-3' },
      });
    });
    view.rerender(<ChatMessages />);
    const buttons = reportButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.getAttribute('data-manual-report')).toBe('ses_ta:pan:a3');
  });

  it('re-binds across a session switch', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    const view = render(<ChatMessages />);
    expect(reportButtons()[0]!.getAttribute('data-manual-report')).toBe('ses_ta:pan:a2');

    act(() => {
      setTranscript('ses_tb', [assistant('pan:b1', 'b answer')], {
        managedBy: 'ses_mgr2',
        lastResult: { status: 'done', result: 'b answer', taskId: null },
      });
    });
    view.rerender(<ChatMessages />);
    const buttons = reportButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.getAttribute('data-manual-report')).toBe('ses_tb:pan:b1');
  });

  it('click reports the bound session/task/message identity', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    render(<ChatMessages />);
    fireEvent.click(reportButtons()[0]!);
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', 'pan:a2');
  });

  it('click after a late update reports the new task, not the stale one', () => {
    setTranscript('ses_ta', TA_TRANSCRIPT, {
      managedBy: 'ses_mgr',
      lastResult: { status: 'done', result: 'answer 2', taskId: 'task-2' },
    });
    const view = render(<ChatMessages />);
    act(() => {
      setTranscript('ses_ta', [
        ...TA_TRANSCRIPT,
        assign('pan:u3', 'task-3', 'do task 3'),
        assistant('pan:a3', 'answer 3'),
      ], {
        managedBy: 'ses_mgr',
        lastResult: { status: 'done', result: 'answer 3', taskId: 'task-3' },
      });
    });
    view.rerender(<ChatMessages />);
    fireEvent.click(reportButtons()[0]!);
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-3', 'pan:a3');
  });
});
