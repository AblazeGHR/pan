// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './JobsView';
import type { Job } from '@/types/jobs';

const api = vi.hoisted(() => ({
  fetchJobs: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobKinds: vi.fn(async () => []),
  fetchJobRuns: vi.fn(async () => []),
  patchJob: vi.fn(),
}));
const wsHandlers = vi.hoisted(() => new Map<string, (event: unknown) => void>());

vi.mock('@/services/api', () => ({
  fetchJobs: api.fetchJobs,
  fetchJob: api.fetchJob,
  fetchJobKinds: api.fetchJobKinds,
  fetchJobRuns: api.fetchJobRuns,
  patchJob: api.patchJob,
  createJob: vi.fn(),
  deleteJob: vi.fn(),
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

const job = (overrides: Record<string, unknown> = {}): Job =>
  ({
    jobId: 'job_message',
    kind: 'session-message',
    status: 'pending',
    name: 'Reminder',
    description: '',
    source: { type: 'agent' },
    target: { sessionId: 'session-1' },
    paused: false,
    schedule: { type: 'weekly', weekday: 2, time: '09:30' },
    runCount: 0,
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  }) as unknown as Job;

function renderView() {
  return render(
    <MemoryRouter>
      <JobsView />
    </MemoryRouter>,
  );
}

describe('JobsView schedule payload compatibility', () => {
  afterEach(() => {
    cleanup();
    wsHandlers.clear();
    vi.clearAllMocks();
  });

  it('reproduces a historical object schedule from the initial REST list', async () => {
    api.fetchJobs.mockResolvedValue([job()]);
    renderView();
    expect(await screen.findByText('Reminder')).toBeTruthy();
  });

  it('opens details for object, missing, null, and scalar schedules safely', async () => {
    const records = [
      job({ jobId: 'object', name: 'Object schedule' }),
      job({
        jobId: 'missing',
        kind: 'background-process',
        name: 'Missing schedule',
        schedule: undefined,
      }),
      job({ jobId: 'null', kind: 'session-broadcast', name: 'Null schedule', schedule: null }),
      job({ jobId: 'scalar', name: 'Scalar schedule', schedule: 'legacy schedule value' }),
    ];
    api.fetchJobs.mockResolvedValue(records);
    api.fetchJob.mockImplementation(async (id: string) => records.find((j) => j.jobId === id));
    renderView();

    fireEvent.click(await screen.findByText('Object schedule'));
    expect(await screen.findByRole('dialog', { name: 'Job detail' })).toBeTruthy();
    expect(screen.getByText(/weekly/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByText('Missing schedule'));
    expect(await screen.findByRole('dialog', { name: 'Job detail' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByText('Null schedule'));
    expect(await screen.findByRole('dialog', { name: 'Job detail' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByText('Scalar schedule'));
    expect(await screen.findByText('legacy schedule value')).toBeTruthy();
  });

  it('renders object schedule returned from a REST action without changing its shape', async () => {
    const record = job();
    api.fetchJobs.mockResolvedValue([record]);
    api.fetchJob.mockResolvedValue(record);
    api.patchJob.mockResolvedValue({ ...record, paused: true });
    renderView();
    fireEvent.click(await screen.findByText('Reminder'));
    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));

    await waitFor(() => expect(api.patchJob).toHaveBeenCalledWith('job_message', { paused: true }));
    expect(screen.getByText(/weekly/)).toBeTruthy();
  });

  it('renders scheduled-task entry arrays and keeps their controls available', async () => {
    const scheduled = job({
      jobId: 'scheduled',
      kind: 'scheduled-task',
      name: 'Array schedule',
      status: 'scheduled',
      schedule: [
        {
          id: 'entry-1',
          kind: 'cron',
          cron: '0 9 * * *',
          enabled: true,
          misfirePolicy: 'skip',
          nextFireAt: '2026-09-27T09:00:00',
        },
      ],
    });
    api.fetchJobs.mockResolvedValue([scheduled]);
    api.fetchJob.mockResolvedValue(scheduled);
    api.patchJob.mockImplementation(
      async (_id: string, patch: { schedule: Array<Record<string, unknown>> }) => ({
        ...scheduled,
        schedule: patch.schedule.map((entry) => ({ ...entry, id: 'entry-1', nextFireAt: null })),
      }),
    );
    renderView();
    fireEvent.click(await screen.findByText('Array schedule'));
    expect(await screen.findByText('0 9 * * *')).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'Schedule entry entry-1 enabled' }));
    await waitFor(() =>
      expect(api.patchJob).toHaveBeenCalledWith(
        'scheduled',
        expect.objectContaining({
          schedule: [
            expect.objectContaining({
              kind: 'cron',
              cron: '0 9 * * *',
              enabled: false,
              misfirePolicy: 'skip',
            }),
          ],
        }),
      ),
    );
  });

  it('accepts object schedule updates arriving over WebSocket', async () => {
    api.fetchJobs.mockResolvedValue([]);
    renderView();
    await waitFor(() => expect(wsHandlers.has('job.updated')).toBe(true));
    wsHandlers.get('job.updated')?.({ job: job({ name: 'WS object schedule' }) });
    expect(await screen.findByText('WS object schedule')).toBeTruthy();
  });
});
