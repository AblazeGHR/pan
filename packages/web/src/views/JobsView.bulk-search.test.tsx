// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './JobsView';
import type { Job, JobKind } from '@/types/jobs';

const api = vi.hoisted(() => ({
  fetchJobs: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobKinds: vi.fn(),
  fetchJobRuns: vi.fn(),
  fetchCompletedJobRetentionSettings: vi.fn(),
  updateCompletedJobRetentionSettings: vi.fn(),
  patchJob: vi.fn(),
  deleteJob: vi.fn(),
}));
const wsHandlers = vi.hoisted(() => new Map<string, (event: unknown) => void>());

vi.mock('@/services/api', () => ({
  fetchJobs: api.fetchJobs,
  fetchJob: api.fetchJob,
  fetchJobKinds: api.fetchJobKinds,
  fetchJobRuns: api.fetchJobRuns,
  fetchCompletedJobRetentionSettings: api.fetchCompletedJobRetentionSettings,
  updateCompletedJobRetentionSettings: api.updateCompletedJobRetentionSettings,
  patchJob: api.patchJob,
  deleteJob: api.deleteJob,
  createJob: vi.fn(),
  runJobNow: vi.fn(),
}));
vi.mock('@/services/ws', () => ({
  wsClient: {
    on: (name: string, callback: (event: unknown) => void) => {
      wsHandlers.set(name, callback);
      return () => wsHandlers.delete(name);
    },
  },
}));
vi.mock('@/stores/uiStore', () => ({
  useUIStore: (selector: (state: { showToast: () => void }) => unknown) =>
    selector({ showToast: vi.fn() }),
}));
vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: (
    selector: (state: { sessions: never[]; loadSessions: () => Promise<void> }) => unknown,
  ) => selector({ sessions: [], loadSessions: async () => {} }),
}));

const job = (overrides: Partial<Job> = {}): Job => ({
  jobId: 'job-1',
  kind: 'session-message',
  status: 'pending',
  name: 'Reminder',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  schedule: { type: 'legacy-weekly' },
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
  ...overrides,
});

const scheduledJob = (overrides: Partial<Job> = {}): Job =>
  job({
    kind: 'scheduled-task',
    status: 'scheduled',
    schedule: [
      {
        id: 'entry-1',
        kind: 'interval',
        intervalSec: 3600,
        enabled: true,
        nextFireAt: '2026-09-27T09:00:00',
      },
    ],
    ...overrides,
  });

function renderView() {
  return render(
    <MemoryRouter>
      <JobsView />
    </MemoryRouter>,
  );
}

function enterSelectionMode() {
  fireEvent.click(screen.getByRole('button', { name: 'Select jobs' }));
}

function selectAllVisible() {
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible jobs' }));
}

const defaultRetentionRules = (): Record<MockRetentionRule, { enabled: boolean; days: number | null }> => ({
  completed: { enabled: false, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: false, days: null },
  cancelled: { enabled: false, days: null },
  logs: { enabled: false, days: null },
});

type MockRetentionRule = 'completed' | 'failed' | 'timed_out' | 'cancelled' | 'logs';
type MockRetentionRun = {
  scannedAt: string;
  scanned: number;
  deleted: number;
  skipped: number;
  errorCount: number;
  errors: string[];
} | null;

const defaultRetentionRuns = (): Record<MockRetentionRule, MockRetentionRun> => ({
  completed: null,
  failed: null,
  timed_out: null,
  cancelled: null,
  logs: null,
});

function retentionResponse(
  rules = defaultRetentionRules(),
  lastRuns: Record<MockRetentionRule, MockRetentionRun> = defaultRetentionRuns(),
) {
  return {
    settings: rules.completed,
    rules,
    configValid: true,
    configValidity: {
      completed: true,
      failed: true,
      timed_out: true,
      cancelled: true,
      logs: true,
    },
    lastRun: lastRuns.completed,
    lastRuns,
  };
}

describe('JobsView search, status filters, and bulk actions', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    wsHandlers.clear();
    api.fetchJobs.mockResolvedValue([]);
    api.fetchJob.mockResolvedValue(undefined);
    api.fetchJobKinds.mockResolvedValue([
      { kind: 'session-message', label: '定时消息', hasSchedule: true, hasProcess: false },
      { kind: 'main-lifecycle', label: '服务生命周期', hasSchedule: false, hasProcess: false },
      { kind: 'scheduled-task', label: '定时任务', hasSchedule: true, hasProcess: false },
    ]);
    api.fetchJobRuns.mockResolvedValue([]);
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse());
    api.updateCompletedJobRetentionSettings.mockImplementation(async (rules) =>
      retentionResponse(rules),
    );
  });

  afterEach(() => {
    cleanup();
    wsHandlers.clear();
  });

  it('matches Completed and Timeout only to their persisted API statuses', async () => {
    const records = [
      job({
        jobId: 'completed',
        name: 'Completed lifecycle',
        status: 'completed',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'timeout',
        name: 'Timed out lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'ordinary-failure',
        name: 'Ordinary failure',
        status: 'failed',
        kind: 'main-lifecycle',
      }),
    ];
    api.fetchJobs.mockResolvedValue(records);
    renderView();

    expect(await screen.findByText('Completed lifecycle')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Completed' }));
    expect(screen.getByText('Completed lifecycle')).toBeTruthy();
    expect(screen.queryByText('Timed out lifecycle')).toBeNull();
    expect(screen.queryByText('Ordinary failure')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Timeout' }));
    expect(screen.getByText('Timed out lifecycle')).toBeTruthy();
    expect(screen.getByText('timed_out').className).toContain('text-danger');
    expect(screen.queryByText('Ordinary failure')).toBeNull();
    expect(screen.queryByText('Completed lifecycle')).toBeNull();
  });

  it('loads retention settings from the server and saves the edited values', async () => {
    const loadedRules = defaultRetentionRules();
    loadedRules.completed = { enabled: true, days: 12 };
    const loadedRuns = defaultRetentionRuns();
    loadedRuns.completed = {
        scannedAt: '2026-09-27T01:00:00+00:00',
        scanned: 9,
        deleted: 4,
        skipped: 1,
        errorCount: 0,
        errors: [],
      };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(
      retentionResponse(loadedRules, loadedRuns),
    );
    const savedRules = { ...loadedRules, completed: { enabled: true, days: 14 } };
    const savedRuns = defaultRetentionRuns();
    savedRuns.completed = {
        scannedAt: '2026-09-27T01:00:00+00:00',
        scanned: 9,
        deleted: 4,
        skipped: 1,
        errorCount: 0,
        errors: [],
      };
    api.updateCompletedJobRetentionSettings.mockResolvedValue(
      retentionResponse(savedRules, savedRuns),
    );
    renderView();
    expect(api.fetchCompletedJobRetentionSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    const daysInput = await screen.findByRole('spinbutton', { name: 'Keep Completed Jobs for days' });
    expect((daysInput as HTMLInputElement).value).toBe('12');
    expect((screen.getByRole('checkbox', { name: 'Enable Completed Jobs cleanup' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/deleted 4/).textContent).toContain('deleted 4');
    expect(api.fetchCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);

    fireEvent.change(daysInput, { target: { value: '14' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith(savedRules));
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('loads blank retention days from server defaults', async () => {
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    for (const label of [
      'Completed Jobs',
      'Failed Jobs',
      'Timed out Jobs',
      'Cancelled Jobs',
      'Job log files',
    ]) {
      const input = await screen.findByRole('spinbutton', {
        name: `Keep ${label} for days`,
      });
      expect((input as HTMLInputElement).value).toBe('');
    }
  });

  it('loads and saves failed, timeout, cancelled, and log retention independently', async () => {
    const rules = defaultRetentionRules();
    rules.failed = { enabled: true, days: 11 };
    rules.timed_out = { enabled: false, days: 12 };
    rules.cancelled = { enabled: true, days: 13 };
    rules.logs = { enabled: false, days: 14 };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse(rules));
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    for (const label of ['Failed Jobs', 'Timed out Jobs', 'Cancelled Jobs', 'Job log files']) {
      expect(await screen.findByRole('spinbutton', { name: `Keep ${label} for days` })).toBeTruthy();
      expect(screen.getByRole('checkbox', { name: `Enable ${label} cleanup` })).toBeTruthy();
    }
    expect(screen.getByText(/Job record may remain after its expired log is removed/)).toBeTruthy();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Enable Timed out Jobs cleanup' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Keep Job log files for days' }), {
      target: { value: '21' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    const expected = {
      ...rules,
      timed_out: { enabled: true, days: 12 },
      logs: { enabled: false, days: 21 },
    };
    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith(expected));
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('keeps a cleared day count blank when saving an enabled rule', async () => {
    const rules = defaultRetentionRules();
    rules.completed = { enabled: true, days: 14 };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse(rules));
    api.updateCompletedJobRetentionSettings.mockImplementation(async (savedRules) =>
      retentionResponse(savedRules),
    );
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    const daysInput = await screen.findByRole('spinbutton', {
      name: 'Keep Completed Jobs for days',
    });
    fireEvent.change(daysInput, { target: { value: '' } });
    expect((daysInput as HTMLInputElement).value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith({
      ...rules,
      completed: { enabled: true, days: null },
    }));
    expect((daysInput as HTMLInputElement).value).toBe('');
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('shows invalid persisted rule feedback and keeps that switch off', async () => {
    const response = retentionResponse();
    response.configValid = false;
    response.configValidity.failed = false;
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(response);
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Failed Jobs');
    expect((screen.getByRole('checkbox', {
      name: 'Enable Failed Jobs cleanup',
    }) as HTMLInputElement).checked).toBe(false);
  });

  it('shows settings load and save errors', async () => {
    api.fetchCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('settings unavailable'));
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('settings unavailable');

    api.fetchCompletedJobRetentionSettings.mockResolvedValueOnce(retentionResponse());
    api.updateCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('save failed'));
    cleanup();
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByRole('spinbutton', { name: 'Keep Completed Jobs for days' });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('save failed');
  });

  it('combines case-insensitive name, ID, and description search with status and kind filters', async () => {
    api.fetchJobs.mockResolvedValue([
      job({
        jobId: 'job_timeout_alpha',
        name: 'Alpha lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'job_timeout_beta',
        name: 'Beta lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
        description: 'Needle detail',
      }),
      job({
        jobId: 'job_timeout_task',
        name: 'Alpha task',
        status: 'timed_out',
        kind: 'scheduled-task',
      }),
      job({
        jobId: 'job_failed_alpha',
        name: 'Alpha failed',
        status: 'failed',
        kind: 'main-lifecycle',
      }),
    ]);
    renderView();
    expect(await screen.findByText('Alpha lifecycle')).toBeTruthy();

    enterSelectionMode();
    selectAllVisible();
    expect(screen.getByText('4 selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Timeout' }));
    expect(screen.getByText('0 selected')).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), {
      target: { value: 'main-lifecycle' },
    });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'BETA' },
    });
    expect(screen.getByText('Beta lifecycle')).toBeTruthy();
    expect(screen.queryByText('Alpha lifecycle')).toBeNull();
    expect(screen.queryByText('Alpha task')).toBeNull();
    expect(screen.queryByText('Alpha failed')).toBeNull();

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'needle' },
    });
    expect(screen.getByText('Beta lifecycle')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('1 selected')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'missing-query' },
    });
    expect(screen.getByText('0 selected')).toBeTruthy();
    expect(screen.queryByText('Beta lifecycle')).toBeNull();
    expect(screen.getByText('No jobs match')).toBeTruthy();
  });

  it('keeps controls and counts outside the dedicated row scroll container', async () => {
    api.fetchJobs.mockResolvedValue([job()]);
    renderView();
    expect(await screen.findByText('Reminder')).toBeTruthy();
    const scroll = screen.getByTestId('jobs-list-scroll');
    expect(scroll.className).toContain('overflow-y-auto');
    expect(scroll.contains(screen.getByRole('button', { name: 'Completed' }))).toBe(false);
    expect(scroll.contains(screen.getByRole('searchbox', { name: 'Search jobs' }))).toBe(false);
    expect(scroll.contains(screen.getByText('1 of 1 jobs'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'New Job' }));
    expect(screen.queryByTestId('jobs-list-scroll')).toBeNull();
    expect(screen.getByRole('button', { name: 'New Job' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(screen.getByRole('textbox', { name: 'Name' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Create job' })).toBeTruthy();
  });

  it('selects and deselects visible results without opening a detail drawer; row menus stay isolated', async () => {
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'job-a', name: 'Job A' }),
      job({ jobId: 'job-b', name: 'Job B' }),
    ]);
    renderView();
    expect(await screen.findByText('Job A')).toBeTruthy();
    enterSelectionMode();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select job Job A' }));
    expect(screen.queryByRole('dialog', { name: 'Job detail' })).toBeNull();
    expect(screen.getByText('1 selected')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('2 selected')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('0 selected')).toBeTruthy();

    const row = screen.getByText('Job A').closest('.rounded.border') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Job actions' }));
    expect(await screen.findByRole('menu')).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Job detail' })).toBeNull();
  });

  it('pauses unpaused jobs with paused=true, reports partial failure, and retries only failures', async () => {
    const jobs = [
      scheduledJob({ jobId: 'pause-ok', name: 'Pause OK', paused: false }),
      scheduledJob({ jobId: 'pause-fail', name: 'Pause Fail', paused: false }),
      scheduledJob({ jobId: 'already-paused', name: 'Already Paused', paused: true }),
    ];
    api.fetchJobs.mockResolvedValue(jobs);
    api.patchJob.mockImplementation(async (id: string) => {
      if (
        id === 'pause-fail' &&
        api.patchJob.mock.calls.filter(([called]) => called === id).length === 1
      ) {
        throw new Error('temporary pause failure');
      }
      return { ...jobs.find((item) => item.jobId === id)!, paused: true };
    });
    renderView();
    expect(await screen.findByText('Pause OK')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Pause scheduled tasks' }));

    expect(await screen.findByText(/Pause: 2 succeeded, 1 failed/)).toBeTruthy();
    expect(api.patchJob).toHaveBeenCalledTimes(2);
    expect(api.patchJob).toHaveBeenCalledWith('pause-ok', { paused: true });
    expect(api.patchJob).toHaveBeenCalledWith('pause-fail', { paused: true });
    expect(screen.getByText('1 selected')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry failed (1)' }));
    await waitFor(() => expect(api.patchJob).toHaveBeenCalledTimes(3));
    expect(api.patchJob.mock.calls[2]).toEqual(['pause-fail', { paused: true }]);
    expect(await screen.findByText(/Pause: 1 succeeded, 0 failed/)).toBeTruthy();
    expect(screen.getByText('0 selected')).toBeTruthy();
  });

  it('blocks duplicate bulk submission while per-job pause requests are pending', async () => {
    const pendingJob = scheduledJob({ jobId: 'pending-pause', name: 'Pending pause' });
    let resolvePatch: ((value: Job) => void) | undefined;
    api.fetchJobs.mockResolvedValue([pendingJob]);
    api.patchJob.mockImplementation(
      () =>
        new Promise<Job>((resolve) => {
          resolvePatch = resolve;
        }),
    );
    renderView();
    expect(await screen.findByText('Pending pause')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    const pauseButton = screen.getByRole('button', { name: 'Pause scheduled tasks' });
    fireEvent.click(pauseButton);
    fireEvent.click(pauseButton);

    expect(await screen.findByText('Pausing selected jobs…')).toBeTruthy();
    expect(api.patchJob).toHaveBeenCalledTimes(1);
    resolvePatch?.({ ...pendingJob, paused: true });
    expect(await screen.findByText(/Pause: 1 succeeded, 0 failed/)).toBeTruthy();
  });

  it('cancels batch delete without requests and confirms the displayed quantity', async () => {
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'delete-a', name: 'Delete A' }),
      job({ jobId: 'delete-b', name: 'Delete B' }),
    ]);
    renderView();
    expect(await screen.findByText('Delete A')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (2)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete selected jobs' });
    expect(dialog.textContent).toContain('Delete 2 selected jobs?');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Delete selected jobs' })).toBeNull();
    expect(api.deleteJob).not.toHaveBeenCalled();
    expect(screen.getByText('2 selected')).toBeTruthy();
  });

  it('removes WS-deleted selection before confirmation and retries only a failed DELETE', async () => {
    const jobs = [
      job({ jobId: 'delete-ws', name: 'Deleted elsewhere' }),
      job({ jobId: 'delete-ok', name: 'Delete OK' }),
      job({ jobId: 'delete-retry', name: 'Delete Retry' }),
    ];
    api.fetchJobs.mockResolvedValue(jobs);
    api.deleteJob.mockImplementation(async (id: string) => {
      if (
        id === 'delete-retry' &&
        api.deleteJob.mock.calls.filter(([called]) => called === id).length === 1
      ) {
        throw new Error('temporary delete failure');
      }
      return { deleted: true };
    });
    renderView();
    expect(await screen.findByText('Deleted elsewhere')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (3)' }));

    await waitFor(() => expect(wsHandlers.has('job.deleted')).toBe(true));
    act(() => wsHandlers.get('job.deleted')?.({ jobId: 'delete-ws' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete selected jobs' });
    expect(dialog.textContent).toContain('Delete 2 selected jobs?');

    fireEvent.click(screen.getByRole('button', { name: 'Delete 2 jobs' }));
    expect(await screen.findByText(/Delete: 1 succeeded, 1 failed/)).toBeTruthy();
    expect(api.deleteJob).toHaveBeenCalledTimes(2);
    expect(api.deleteJob).toHaveBeenCalledWith('delete-ok');
    expect(api.deleteJob).toHaveBeenCalledWith('delete-retry');
    expect(screen.getByText('Delete Retry: temporary delete failure')).toBeTruthy();
    expect(screen.getByText('1 selected')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry failed (1)' }));
    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledTimes(3));
    expect(api.deleteJob.mock.calls[2]).toEqual(['delete-retry']);
    expect(await screen.findByText(/Delete: 1 succeeded, 0 failed/)).toBeTruthy();
    expect(screen.getByText('0 selected')).toBeTruthy();
  });

  it('supports searching by kind payload ID and description while retaining kind filters', async () => {
    const payloadKind: JobKind = 'main-lifecycle';
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'id-search-hit', name: 'Lifecycle A', status: 'timed_out', kind: payloadKind }),
      job({
        jobId: 'no-hit',
        name: 'Lifecycle B',
        description: 'find me in description',
        status: 'timed_out',
        kind: payloadKind,
      }),
    ]);
    renderView();
    expect(await screen.findByText('Lifecycle A')).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), {
      target: { value: payloadKind },
    });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'ID-SEARCH' },
    });
    expect(screen.getByText('Lifecycle A')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'FIND ME' },
    });
    expect(screen.getByText('Lifecycle B')).toBeTruthy();
  });
});
