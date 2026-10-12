// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import {
  reportTaskManually as reportTaskManuallyApi,
  ApiRequestError,
  ManualReportRejectedError,
} from '@/services/api';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    reportTaskManually: vi.fn(),
  };
});

const reportMock = vi.mocked(reportTaskManuallyApi);

const TARGET = { taskId: 'task-2', taskSeq: 2, messageId: 'pan:a2' } as const;
const OTHER_TASK = { taskId: 'task-1', taskSeq: 1, messageId: 'pan:a1' } as const;
const KEY = 'ses_ta:task-2:2';

function entry(key: string = KEY) {
  return useSessionStore.getState().manualTaskReports[key]!;
}

beforeEach(() => {
  reportMock.mockReset();
  useSessionStore.setState({ manualTaskReports: {} });
});

describe('reportTaskManually store action', () => {
  it('records sent state with the server queue item on success', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', 2, 'pan:a2');
    expect(entry()).toMatchObject({ state: 'sent', queueItemId: 'q_1', taskId: 'task-2', taskSeq: 2 });
  });

  it('forwards a null sequence unchanged', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', {
        taskId: 'task-2', taskSeq: null, messageId: 'pan:a2',
      });
    });
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', null, 'pan:a2');
    expect(entry('ses_ta:task-2:-')).toMatchObject({ state: 'sent', taskSeq: null });
  });

  it('surfaces an idempotent replay as already reported', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: true });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry().state).toBe('sent');
  });

  it('tracks each task and each block independently', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
      await useSessionStore.getState().reportTaskManually('ses_ta', OTHER_TASK);
      await useSessionStore.getState().reportTaskManually('ses_other', TARGET);
    });
    const state = useSessionStore.getState().manualTaskReports;
    expect(Object.keys(state).sort()).toEqual([
      'ses_other:task-2:2', 'ses_ta:task-1:1', 'ses_ta:task-2:2',
    ]);
    expect(state['ses_ta:task-1:1']?.state).toBe('sent');
    expect(state['ses_ta:task-2:2']?.state).toBe('sent');
    expect(state['ses_other:task-2:2']?.state).toBe('sent');
    expect(reportMock).toHaveBeenCalledTimes(3);
  });

  it('ignores a second click while one report is in flight', async () => {
    let resolve!: (value: unknown) => void;
    reportMock.mockReturnValue(new Promise((r) => { resolve = r; }) as never);
    const first = useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    expect(entry().state).toBe('sending');
    const second = useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    expect(reportMock).toHaveBeenCalledTimes(1);
    resolve({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => { await Promise.all([first, second]); });
    expect(entry().state).toBe('sent');
    expect(reportMock).toHaveBeenCalledTimes(1);
  });

  it('does not block a different task while one report is in flight', async () => {
    let resolve!: (value: unknown) => void;
    reportMock.mockReturnValue(new Promise((r) => { resolve = r; }) as never);
    const first = useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    const second = useSessionStore.getState().reportTaskManually('ses_ta', OTHER_TASK);
    expect(reportMock).toHaveBeenCalledTimes(2);
    resolve({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => { await Promise.all([first, second]); });
    expect(entry('ses_ta:task-2:2').state).toBe('sent');
    expect(entry('ses_ta:task-1:1').state).toBe('sent');
  });

  it('ignores further clicks after a successful report', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(reportMock).toHaveBeenCalledTimes(1);
    expect(entry().state).toBe('sent');
  });

  it('re-arms after a definite server rejection and retries as a fresh attempt', async () => {
    reportMock.mockRejectedValueOnce(new ManualReportRejectedError('该任务的终态结果不可用，无法汇报', false));
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry()).toMatchObject({ state: 'failed', error: '该任务的终态结果不可用，无法汇报' });

    reportMock.mockResolvedValueOnce({ ok: true, queueItemId: 'q_2', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(reportMock).toHaveBeenCalledTimes(2);
    expect(entry()).toMatchObject({ state: 'sent', queueItemId: 'q_2' });
  });

  it('marks an HTTP error as uncertain and still allows a retry', async () => {
    reportMock.mockRejectedValueOnce(new ApiRequestError(500, 'HTTP 500: boom'));
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry().state).toBe('uncertain');
    expect(entry().error).toContain('汇报结果不确定');

    reportMock.mockResolvedValueOnce({ ok: true, queueItemId: 'q_3', managerId: 'ses_mgr', alreadyReported: true });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry().state).toBe('sent');
    expect(reportMock).toHaveBeenCalledTimes(2);
  });

  it('marks a transport failure as uncertain', async () => {
    reportMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry().state).toBe('uncertain');
  });
});

describe('terminal identity mirroring', () => {
  it('appends a live terminal so a just-finished task becomes reportable', async () => {
    useSessionStore.setState({
      currentSessionId: 'ses_ta',
      sessions: [{
        id: 'ses_ta',
        name: 'TA',
        alwaysThinkingEnabled: false,
        effort: '',
        history: [],
        managedBy: 'ses_mgr',
        terminalResults: [
          { taskId: 'task-1', taskSeq: 1, resultCursor: 1, terminalKey: 't1', status: 'done' },
        ],
      }],
      currentMessages: [],
    });
    await act(async () => {
      useSessionStore.getState().reconcileWorkerResult(
        'ses_ta',
        { status: 'done', result: 'answer 2', terminalCoverage: { historyEpoch: 'epoch', historyRevision: 5 } },
        { serverEpoch: 'epoch', workerId: 'w1', generation: 1, taskId: 'task-2',
          taskSeq: 2, terminalKey: 't2', resultCursor: 2 },
      );
    });
    const session = useSessionStore.getState().sessions[0]!;
    expect(session.terminalResults).toEqual([
      { taskId: 'task-1', taskSeq: 1, resultCursor: 1, terminalKey: 't1', status: 'done' },
      { status: 'done', taskId: 'task-2', taskSeq: 2, terminalKey: 't2', resultCursor: 2,
        historyEpoch: 'epoch', historyRevision: 5 },
    ]);
  });

  it('updates a replayed terminal instead of duplicating it', async () => {
    useSessionStore.setState({
      currentSessionId: 'ses_ta',
      sessions: [{
        id: 'ses_ta',
        name: 'TA',
        alwaysThinkingEnabled: false,
        effort: '',
        history: [],
        managedBy: 'ses_mgr',
        terminalResults: [
          { taskId: 'task-2', taskSeq: 2, resultCursor: 2, terminalKey: 't2', status: 'done' },
        ],
      }],
      currentMessages: [],
    });
    await act(async () => {
      useSessionStore.getState().reconcileWorkerResult(
        'ses_ta',
        { status: 'done', result: 'answer 2', terminalCoverage: { historyEpoch: 'epoch', historyRevision: 5 } },
        { serverEpoch: 'epoch', workerId: 'w1', generation: 1, taskId: 'task-2',
          taskSeq: 2, terminalKey: 't2', resultCursor: 2 },
      );
    });
    const session = useSessionStore.getState().sessions[0]!;
    expect(session.terminalResults).toHaveLength(1);
    expect(session.terminalResults?.[0]?.terminalKey).toBe('t2');
  });
});
