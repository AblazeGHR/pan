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

const TARGET = { taskId: 'task-2', messageId: 'pan:a2' } as const;
const KEY = `ses_ta:${TARGET.messageId}`;

function entry() {
  return useSessionStore.getState().manualTaskReports[KEY]!;
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
    expect(reportMock).toHaveBeenCalledWith('ses_ta', 'task-2', 'pan:a2');
    expect(entry()).toMatchObject({ state: 'sent', queueItemId: 'q_1', taskId: 'task-2' });
  });

  it('surfaces an idempotent replay as already reported', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: true });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry().state).toBe('sent');
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
    reportMock.mockRejectedValueOnce(new ManualReportRejectedError('任务已变化，请刷新后重试', false));
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
    });
    expect(entry()).toMatchObject({ state: 'failed', error: '任务已变化，请刷新后重试' });

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

  it('tracks reports per session and message identity', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', TARGET);
      await useSessionStore.getState().reportTaskManually('ses_ta', { taskId: 'task-2', messageId: 'pan:a9' });
      await useSessionStore.getState().reportTaskManually('ses_other', TARGET);
    });
    const state = useSessionStore.getState().manualTaskReports;
    expect(Object.keys(state).sort()).toEqual(['ses_other:pan:a2', 'ses_ta:pan:a2', 'ses_ta:pan:a9']);
    expect(state['ses_ta:pan:a2']?.state).toBe('sent');
    expect(state['ses_ta:pan:a9']?.state).toBe('sent');
    expect(state['ses_other:pan:a2']?.state).toBe('sent');
    expect(reportMock).toHaveBeenCalledTimes(3);
  });

  it('forwards a null taskId for a plain turn', async () => {
    reportMock.mockResolvedValue({ ok: true, queueItemId: 'q_1', managerId: 'ses_mgr', alreadyReported: false });
    await act(async () => {
      await useSessionStore.getState().reportTaskManually('ses_ta', { taskId: null, messageId: 'pan:a2' });
    });
    expect(reportMock).toHaveBeenCalledWith('ses_ta', null, 'pan:a2');
    expect(entry()).toMatchObject({ state: 'sent', taskId: null });
  });
});
