// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, act } from '@testing-library/react';
import { ChatMessages } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Message, Session, TerminalIdentity } from '@/types';
import { markDurableRow } from '@/stores/messageOrdering';
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

function terminal(taskId: string, taskSeq: number, tailOffset: number): TerminalIdentity {
  return {
    taskId, taskSeq, resultCursor: taskSeq, terminalKey: `terminal:${taskId}`,
    status: 'done', historyEpoch: EPOCH, historyRevision: tailOffset + 1,
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

/**
 * Seed the transcript exactly as a loaded history page would: every durable
 * row is marked with its absolute offset and owned by the window, and runtime
 * rows (a still-streaming tail) stay outside it.
 */
function setTranscript(sessionId: string, durable: Message[], extra: Partial<Session> = {}, runtime: Message[] = [], windowSize: { total: number; revision: number } = { total: durable.length, revision: durable.length }): void {
  const rows = new Map<number, Message>();
  durable.forEach((row, index) => {
    rows.set(index, row);
    markDurableRow(row, index);
  });
  useSessionStore.setState((state) => ({
    currentSessionId: sessionId,
    currentMessages: [...durable, ...runtime],
    sessions: [session(sessionId, extra)],
    sessionTranscripts: {
      ...state.sessionTranscripts,
      [sessionId]: {
        window: {
          rows, start: 0, end: durable.length, total: windowSize.total,
          epoch: EPOCH, revision: windowSize.revision,
        },
        runtime,
        anchorOffset: durable.length,
        serverEpoch: null,
      },
    },
  }));
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

/** The report entry lives in the shared block menu now (opened from the
 *  row's left gutter).  Scan the rows in DOM order, open each row's menu,
 *  and collect the report binding it offers. */
function buttonKeys(): string[] {
  const keys: string[] = [];
  for (const gutter of [...document.querySelectorAll<HTMLButtonElement>('[data-message-bookmark-gutter]')]) {
    fireEvent.click(gutter);
    const item = document.querySelector<HTMLElement>('[data-manual-report]');
    if (item) keys.push(item.getAttribute('data-manual-report')!);
    fireEvent.keyDown(window, { key: 'Escape' });
  }
  return keys;
}

/** Open block menus in row order until one offers the requested binding, then
 *  click it — the real user path for reporting one task. */
function clickReportFor(key: string): void {
  for (const gutter of [...document.querySelectorAll<HTMLButtonElement>('[data-message-bookmark-gutter]')]) {
    fireEvent.click(gutter);
    const item = document.querySelector<HTMLElement>('[data-manual-report]');
    if (item?.getAttribute('data-manual-report') === key) {
      fireEvent.click(item);
      return;
    }
    fireEvent.keyDown(window, { key: 'Escape' });
  }
  throw new Error(`no report entry for ${key}`);
}

describe('ChatMessages per-task manual report wiring', () => {
  it('shows one action per completed task, each on its own tail block', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3), terminal('task-3', 3, 5)],
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
    ], {
      workerStatus: 'running',
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3)],
    }, [
      assistant('pan:a3', 'partial', { streaming: true }),
    ]);
    render(<ChatMessages />);
    // A running newest task no longer hides the older completed tasks; the
    // streaming runtime tail has no canonical offset and gets no action.
    expect(buttonKeys()).toEqual(['ses_ta:task-1:1', 'ses_ta:task-2:2']);
  });

  it('reports the clicked task, not the newest one', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3), terminal('task-3', 3, 5)],
    });
    render(<ChatMessages />);
    clickReportFor('ses_ta:task-1:1');
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-1', 1, 'pan:a1', 1);
  });

  it('adds the action only for tasks with a durable terminal', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-2', 2, 3)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-2:2']);
  });

  it('keeps the action visible when the manager is unsubscribed', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      managedBy: 'ses_mgr',
      reportsToManager: false,
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3), terminal('task-3', 3, 5)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toHaveLength(3);
  });

  it('hides every action without a manager', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      managedBy: null,
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3), terminal('task-3', 3, 5)],
    });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual([]);
  });

  it('re-binds to the newly selected session on a switch', () => {
    setTranscript('ses_ta', THREE_TASKS, {
      terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3), terminal('task-3', 3, 5)],
    });
    const view = render(<ChatMessages />);
    expect(buttonKeys()).toHaveLength(3);

    act(() => {
      setTranscript('ses_tb', [
        assignRow('pan:v1', 'task-b1'),
        assistant('pan:b1', 'b answer'),
      ], { terminalResults: [terminal('task-b1', 7, 1)] });
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
      terminalResults: [terminal('task-r', 1, 1), terminal('task-r', 2, 3)],
    });
    render(<ChatMessages />);
    // Two runs of one taskId: canonical offsets prove each block belongs to its
    // own run, so both actions appear and neither can report the other run.
    expect(buttonKeys()).toEqual(['ses_ta:task-r:1', 'ses_ta:task-r:2']);
  });

  it('binds the older run, never the newer one, when the window is truncated', () => {
    setTranscript('ses_ta', [
      assignRow('pan:u1', 'task-w'),
      assistant('pan:a1', 'old run answer'),
    ], {
      // The full history holds the newer run's rows too, but this window does
      // not contain them: only the older run's boundary is recoverable here.
      terminalResults: [
        terminal('task-w', 1, 1),
        { ...terminal('task-w', 2, 4), historyRevision: 5 },
      ],
    }, [], { total: 5, revision: 5 });
    render(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-w:1']);
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
      terminalResults: [terminal('task-1', 1, 1)],
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
        terminalResults: [terminal('task-1', 1, 1), terminal('task-2', 2, 3)],
      });
    });
    view.rerender(<ChatMessages />);
    expect(buttonKeys()).toEqual(['ses_ta:task-1:1', 'ses_ta:task-2:2']);

    clickReportFor('ses_ta:task-2:2');
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', 2, 'pan:a2', 2);
  });
});
