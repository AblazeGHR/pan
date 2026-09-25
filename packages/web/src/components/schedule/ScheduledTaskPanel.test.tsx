// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ScheduledTaskPanel } from './ScheduledTaskPanel';
import { useSessionStore } from '@/stores/sessionStore';
import { useScheduleStore } from '@/stores/scheduleStore';
import { useUIStore } from '@/stores/uiStore';
import type { ScheduledTask, Session } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchScheduledTasks: vi.fn(),
  createScheduledTask: vi.fn(),
  updateScheduledTask: vi.fn(),
  deleteScheduledTask: vi.fn(async () => ({ ok: true })),
  pauseScheduledTask: vi.fn(),
  resumeScheduledTask: vi.fn(),
  runScheduledTaskNow: vi.fn(async () => ({ ok: true })),
  fetchTaskRuns: vi.fn(async () => [] as unknown[]),
  fetchSchedulerNext: vi.fn(async () => [] as unknown[]),
  fetchSchedulerStatus: vi.fn(async () => ({ running: true })),
  fetchSessions: vi.fn(async () => [] as unknown[]),
}));

vi.mock('@/services/api', () => apiMock);

function mkTask(extra?: Partial<ScheduledTask>): ScheduledTask {
  return {
    id: 'sch_1',
    name: 'Daily report',
    targetSessionId: 'ses_1',
    text: 'summarise yesterday',
    enabled: false,
    paused: false,
    schedule: { kind: 'cron', cron: '0 9 * * 1-5', timezone: 'Asia/Shanghai' },
    nextFireAt: '2026-09-16T09:00:00',
    lastFireAt: null,
    lastStatus: null,
    runCount: 0,
    maxRuns: null,
    misfirePolicy: 'fire_now',
    ...extra,
  };
}

function mkSession(id: string, name: string): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [] };
}

let toastSpy: ReturnType<typeof vi.fn>;

describe('ScheduledTaskPanel', () => {
  // No vitest globals → RTL auto-cleanup is off; clean up manually.
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    toastSpy = vi.fn();
    useScheduleStore.setState({
      tasks: [],
      loaded: false,
      loading: false,
      busyId: null,
      status: null,
    });
    useSessionStore.setState({
      sessions: [mkSession('ses_1', 'Boss'), mkSession('ses_2', 'Worker')],
      loadSessions: vi.fn(async () => {}),
    });
    useUIStore.setState({ showToast: toastSpy });
    apiMock.fetchScheduledTasks.mockResolvedValue([mkTask()]);
  });

  it('lists fetched tasks with their schedule and next fire time', async () => {
    render(<ScheduledTaskPanel />);

    expect(await screen.findByText('Daily report')).toBeTruthy();
    expect(screen.getByText(/Cron · 0 9 \* \* 1-5 · Asia\/Shanghai/)).toBeTruthy();
    expect(screen.getByText(/next 2026-09-16 09:00/)).toBeTruthy();
    // Engine health comes from GET /api/scheduler/status.
    expect(await screen.findByText('engine running')).toBeTruthy();
  });

  it('toggles the enabled switch through PATCH', async () => {
    apiMock.updateScheduledTask.mockResolvedValue(mkTask({ enabled: true }));
    render(<ScheduledTaskPanel />);

    const toggle = await screen.findByRole('switch', { name: 'Enable Daily report' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(apiMock.updateScheduledTask).toHaveBeenCalledWith('sch_1', { enabled: true }),
    );
  });

  it('surfaces a failed toggle as an error toast and keeps the switch off', async () => {
    apiMock.updateScheduledTask.mockRejectedValue(new Error('engine not running'));
    render(<ScheduledTaskPanel />);

    const toggle = await screen.findByRole('switch', { name: 'Enable Daily report' });
    fireEvent.click(toggle);
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('engine not running', 'error'));
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('creates a cron task from a session, text and a preset expression', async () => {
    apiMock.createScheduledTask.mockResolvedValue(mkTask({ name: 'Morning', enabled: true }));
    render(<ScheduledTaskPanel />);
    await screen.findByText('Daily report');

    // The 5-field editor (kind buttons + presets) lives behind advanced mode.
    fireEvent.click(screen.getByRole('button', { name: '高级模式' }));
    fireEvent.change(screen.getByLabelText('Task name'), { target: { value: 'Morning' } });
    fireEvent.change(screen.getByLabelText('Target session'), { target: { value: 'ses_2' } });
    fireEvent.change(screen.getByLabelText('Task text'), { target: { value: 'do the thing' } });
    fireEvent.click(screen.getByRole('button', { name: /Cron/ }));
    fireEvent.click(screen.getByTitle('0 9 * * 1-5'));

    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));

    await waitFor(() =>
      expect(apiMock.createScheduledTask).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Morning',
          targetSessionId: 'ses_2',
          text: 'do the thing',
          enabled: true,
          maxRuns: null,
          schedule: expect.objectContaining({
            kind: 'cron',
            cron: '0 9 * * 1-5',
            timezone: 'Asia/Shanghai',
          }),
        }),
      ),
    );
  });

  it('defaults to simple mode and writes the generated cron', async () => {
    apiMock.createScheduledTask.mockResolvedValue(mkTask({ name: 'Weekly standup' }));
    render(<ScheduledTaskPanel />);
    await screen.findByText('Daily report');

    // Simple mode is the default and starts on "每天 09:00".
    expect(screen.getByLabelText('Frequency')).toHaveProperty('value', 'daily');
    expect(screen.getByLabelText('Fire time')).toHaveProperty('value', '09:00');
    expect(screen.getByText('0 9 * * *')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Task name'), { target: { value: 'Weekly standup' } });
    fireEvent.change(screen.getByLabelText('Target session'), { target: { value: 'ses_1' } });
    fireEvent.change(screen.getByLabelText('Task text'), { target: { value: 'standup notes' } });

    // Weekly + Wednesday/Friday + 09:30 → "30 9 * * 3,5".
    fireEvent.change(screen.getByLabelText('Frequency'), { target: { value: 'weekly' } });
    fireEvent.change(screen.getByLabelText('Fire time'), { target: { value: '09:30' } });
    // Weekly starts with Monday selected; add Wed + Fri, then drop Monday.
    fireEvent.click(screen.getByRole('button', { name: '星期三' }));
    fireEvent.click(screen.getByRole('button', { name: '星期五' }));
    fireEvent.click(screen.getByRole('button', { name: '星期一' }));
    expect(screen.getByText('30 9 * * 3,5')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() =>
      expect(apiMock.createScheduledTask).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Weekly standup',
          schedule: expect.objectContaining({ kind: 'cron', cron: '30 9 * * 3,5' }),
        }),
      ),
    );
  });

  it('generates interval and monthly cron from simple mode', async () => {
    render(<ScheduledTaskPanel />);
    await screen.findByText('Daily report');

    fireEvent.change(screen.getByLabelText('Frequency'), { target: { value: 'minutes' } });
    fireEvent.click(screen.getByRole('button', { name: '30 分钟' }));
    expect(screen.getByText('*/30 * * * *')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Frequency'), { target: { value: 'hours' } });
    fireEvent.change(screen.getByLabelText('Every N hours'), { target: { value: '4' } });
    expect(screen.getByText('0 */4 * * *')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Frequency'), { target: { value: 'monthly' } });
    fireEvent.change(screen.getByLabelText('Day of month'), { target: { value: '15' } });
    expect(screen.getByText('0 9 15 * *')).toBeTruthy();

    // Every selection keeps the existing next-5 preview alive.
    expect(screen.getByText('Next 5 fires')).toBeTruthy();
  });

  it('opens a round-trippable task back in simple mode', async () => {
    apiMock.fetchScheduledTasks.mockResolvedValue([
      mkTask({ schedule: { kind: 'cron', cron: '30 9 * * 1,3,5', timezone: 'Asia/Shanghai' } }),
    ]);
    render(<ScheduledTaskPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Frequency')).toHaveProperty('value', 'weekly');
    expect(screen.getByLabelText('Fire time')).toHaveProperty('value', '09:30');
    expect(screen.getByRole('button', { name: '星期一' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '星期三' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '星期五' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '星期二' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('falls back to advanced mode for a cron simple mode cannot express', async () => {
    apiMock.fetchScheduledTasks.mockResolvedValue([
      mkTask({ schedule: { kind: 'cron', cron: '0 9,18 * * 1-5', timezone: 'Asia/Shanghai' } }),
    ]);
    render(<ScheduledTaskPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    // The raw expression is preserved verbatim in the advanced editor.
    expect(screen.getByLabelText('Cron expression')).toHaveProperty('value', '0 9,18 * * 1-5');
    expect(screen.queryByLabelText('Frequency')).toBeNull();
    // Simple mode stays disabled rather than silently rewriting the cron.
    expect(screen.getByRole('button', { name: '简单模式' })).toHaveProperty('disabled', true);
  });

  it('refuses to submit without a name and reports it as a toast', async () => {
    render(<ScheduledTaskPanel />);
    await screen.findByText('Daily report');

    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    expect(toastSpy).toHaveBeenCalledWith('Name is required', 'error');
    expect(apiMock.createScheduledTask).not.toHaveBeenCalled();
  });

  it('pauses, resumes and dispatches a task immediately', async () => {
    apiMock.pauseScheduledTask.mockResolvedValue(mkTask({ paused: true }));
    apiMock.resumeScheduledTask.mockResolvedValue(mkTask());
    // Each mutation reloads the list; drive the reloads so the row reflects
    // the new paused state between the two clicks.
    apiMock.fetchScheduledTasks
      .mockResolvedValueOnce([mkTask()])
      .mockResolvedValueOnce([mkTask({ paused: true })])
      .mockResolvedValue([mkTask()]);
    render(<ScheduledTaskPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(apiMock.pauseScheduledTask).toHaveBeenCalledWith('sch_1'));

    fireEvent.click(await screen.findByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(apiMock.resumeScheduledTask).toHaveBeenCalledWith('sch_1'));

    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(apiMock.runScheduledTaskNow).toHaveBeenCalledWith('sch_1'));
  });

  it('loads and renders the run history of a task', async () => {
    apiMock.fetchTaskRuns.mockResolvedValue([
      {
        runId: 'r1',
        taskId: 'sch_1',
        fireAt: '2026-09-16T09:00:00',
        actualAt: '2026-09-16T09:00:02',
        status: 'dispatched',
      },
    ]);
    render(<ScheduledTaskPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'History' }));
    await waitFor(() => expect(apiMock.fetchTaskRuns).toHaveBeenCalledWith('sch_1'));
    expect(await screen.findByText('dispatched')).toBeTruthy();
    expect(screen.getByText('2026-09-16 09:00')).toBeTruthy();
  });

  it('deletes a task after confirmation', async () => {
    vi.stubGlobal('confirm', () => true);
    render(<ScheduledTaskPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(apiMock.deleteScheduledTask).toHaveBeenCalledWith('sch_1'));
    vi.unstubAllGlobals();
  });

  it('keeps the raw once / interval editors available in advanced mode', async () => {
    render(<ScheduledTaskPanel />);
    await screen.findByText('Daily report');

    fireEvent.click(screen.getByRole('button', { name: '高级模式' }));
    fireEvent.click(screen.getByRole('button', { name: /Interval/ }));
    fireEvent.click(screen.getByRole('button', { name: '1 小时' }));
    expect(screen.getByLabelText('Interval seconds')).toHaveProperty('value', '3600');

    fireEvent.click(screen.getByRole('button', { name: /Once/ }));
    expect(screen.getByLabelText('Fire at')).toBeTruthy();
  });
});
