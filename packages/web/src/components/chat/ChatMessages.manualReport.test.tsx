// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, act } from '@testing-library/react';
import { ChatMessages } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Message, Session, TerminalIdentity } from '@/types';
import { reportTaskManually as reportTaskManuallyApi } from '@/services/api';

// The real virtualizer needs layout/ResizeObserver, which jsdom lacks. This
// stub renders every row so the per-task bindings can be observed on the exact
// rows the real list would paint.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (options: { count?: number }) => {
    const count = options.count ?? 0;
    return {
      getTotalSize: () => count * 100,
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({
        index, start: index * 100, size: 100, key: index,
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
  return {
    ...actual,
    reportTaskManually: vi.fn(async () => ({
      ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false,
    })),
  };
});

const reportMock = vi.mocked(reportTaskManuallyApi);

class TestResizeObserver {
  constructor(_callback: ResizeObserverCallback) {}
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function assignRow(messageId: string, taskId: string): Message {
  return {
    role: 'user', content: `do ${taskId}`, messageId,
    source: 'agent', taskIdSource: 'assign', taskId, ts: '2026-10-06T11:00:00',
  };
}

function assistant(messageId: string, content: string, extra: Partial<Message> = {}): Message {
  return { role: 'assistant', content, messageId, ts: '2026-10-06T12:00:00', ...extra };
}

const EPOCH = 'epoch-1';

function terminal(taskId: string, taskSeq: number): TerminalIdentity {
  return {
    taskId, taskSeq, resultCursor: taskSeq, terminalKey: `terminal:${taskId}`,
    status: 'done', historyEpoch: EPOCH, historyRevision: taskSeq,
  };
}

function session(id: string, extra: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workerStatus: 'idle',
    managedBy: 'ses_mgr',
    historyEpoch: EPOCH,
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

const THREE_TASKS: Message[] = [
  assignRow('pan:u1', 'task-1'),
  assistant('pan:a1', 'answer 1'),
  assignRow('pan:u2', 'task-2'),
  assistant('pan:a2', 'answer 2'),
  assignRow('pan:u3', 'task-3'),
  assistant('pan:a3', 'answer 3'),
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

function buttonKeys(): string[] {
  return reportButtons().map((button) => button.getAttribute('data-manual-report')!);
}

describe('ChatMessages per-task manual report wiring', () => {
  it('shows one action per completed task, each on its own tail block', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual([
      'ses_ta:task-1:1', 'ses_ta:task-2:2', 'ses_ta:task-3:3',
    ]);
  });

  it('keeps earlier tasks reportable while the newest task is running', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-1'),
      assistant('pan:a1', 'answer 1'),
      assignRow('pan:u2', 'task-2'),
      assistant('pan:a2', 'answer 2'),
      assignRow('pan:u3', 'task-3'),
      assistant('pan:a3', 'partial', { streaming: true }),
    ], {
      workerStatus: 'running',
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2)],
    });
    render(<ChatMessages />);
    // A running newest task no longer hides the older completed tasks.
    expect(buttonKeys()).toEqual(['ses_ta:task-1:1', 'ses_ta:task-2:2']);
  });

  it('reports the clicked task, not the newest one', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    });
    render(<ChatMessages />);
    const older = reportButtons().find(
      (button) => button.getAttribute('data-manual-report') === 'ses_ta:task-1:1')!;
    fireEvent.click(older);
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-1', 1, 'pan:a1');
  });

  it('adds the action only for tasks with a durable terminal', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-2', 2)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-2:2']);
  });

  it('keeps the action visible when the manager is unsubscribed', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      managedBy: 'ses_mgr',
      reportsToManager: false,
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toHaveLength(3);
  });

  it('hides every action without a manager', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      managedBy: null,
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual([]);
  });

  it('re-binds to the newly selected session on a switch', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    });
    const view = render(<ChatMessages />);
    expect(buttonKeys()).toHaveLength(3);

    act(() => {
      setTranscript('ses_tb', [
        assignRow('pan:v1', 'task-b1'),
        assistant('pan:b1', 'b answer'),
      ], { terminalResults: [terminal('task-b1', 7)] });
    });
    view.rerender(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_tb:task-b1:7']);
  });

  it('binds nothing when one taskId was dispatched twice', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-r'),
      assistant('pan:a1', 'first run'),
      assignRow('pan:u2', 'task-r'),
      assistant('pan:a2', 'second run'),
    ], {
      terminalResults: [terminal('task-r', 1), terminal('task-r', 2)],
    });
    render(<ChatMessages />);
    // Two candidate blocks and two runs: binding either to the newest terminal
    // would report the wrong run's result, so no action appears.
    expect(buttonKeys()).toEqual([]);
  });

  it('binds nothing when the window holds only the older run', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-w'),
      assistant('pan:a1', 'old run answer'),
    ], {
      terminalResults: [terminal('task-w', 1), terminal('task-w', 2)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual([]);
  });

  it('binds nothing when the run boundary cannot be proven', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-1'),
      assistant('pan:a1', 'answer 1'),
    ], {
      historyEpoch: EPOCH,
      terminalResults: [{ taskId: 'task-1', taskSeq: 1, resultCursor: 1, terminalKey: 't', status: 'done' }],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual([]);
  });

  it('adds a late-completed task action without disturbing older ones', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-1'),
      assistant('pan:a1', 'answer 1'),
      assignRow('pan:u2', 'task-2'),
      assistant('pan:a2', 'answer 2', { streaming: true }),
    ], {
      workerStatus: 'running',
      terminalResults: [terminal('task-1', 1)],
    });
    const view = render(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-1:1']);

    // task-2 finishes: its terminal arrives and its block settles.
    act(() => {
      setTranscript('ses_ta', [
        assignRow('pan:u1', 'task-1'),
        assistant('pan:a1', 'answer 1'),
        assignRow('pan:u2', 'task-2'),
        assistant('pan:a2', 'answer 2'),
      ], {
        workerStatus: 'idle',
        terminalResults: [terminal('task-1', 1), terminal('task-2', 2)],
      });
    });
    view.rerender(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-1:1', 'ses_ta:task-2:2']);

    fireEvent.click(reportButtons()[1]!);
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', 2, 'pan:a2');
  });
});
