// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Job } from '@/types/jobs';
import { selectRunningJobCount, useJobStore } from '@/stores/jobStore';
import * as api from '@/services/api';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchJobs: vi.fn(),
  };
});

const job = (overrides: Partial<Job> = {}): Job => ({
  jobId: 'job-1',
  kind: 'session-message',
  status: 'pending',
  name: 'Reminder',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
  ...overrides,
}) as Job;

const resetStore = () => useJobStore.setState({
  jobs: [], loaded: false, loading: false, error: null, pinMetadataUnavailable: false,
});

describe('jobStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetStore();
  });

  it('replaces the list from the authoritative REST load and records pin metadata state', async () => {
    useJobStore.setState({ jobs: [job({ jobId: 'stale' })] });
    vi.mocked(api.fetchJobs).mockImplementation(async (onPinStatus) => {
      onPinStatus?.('unavailable');
      return [job({ jobId: 'fresh' })];
    });

    await useJobStore.getState().loadJobs();

    const state = useJobStore.getState();
    expect(state.jobs.map((j) => j.jobId)).toEqual(['fresh']);
    expect(state.loaded).toBe(true);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
    expect(state.pinMetadataUnavailable).toBe(true);
  });

  it('keeps the previous list and surfaces the error when the load fails', async () => {
    useJobStore.setState({ jobs: [job({ jobId: 'kept' })], loaded: true });
    vi.mocked(api.fetchJobs).mockRejectedValue(new Error('boom'));

    await useJobStore.getState().loadJobs();

    const state = useJobStore.getState();
    expect(state.jobs.map((j) => j.jobId)).toEqual(['kept']);
    expect(state.loading).toBe(false);
    expect(state.error).toBe('boom');
  });

  it('merges lifecycle updates while preserving Pan-owned pin metadata', async () => {
    vi.mocked(api.fetchJobs).mockResolvedValue([
      job({ jobId: 'a', pinned: true, pinMetadataStatus: 'available' }),
    ]);
    await useJobStore.getState().loadJobs();

    // A lifecycle broadcast omits pinned/pinMetadataStatus: both must survive.
    useJobStore.getState().upsertJob(job({ jobId: 'a', status: 'running', updatedAt: 2 }));
    let row = useJobStore.getState().jobs[0]!;
    expect(row).toMatchObject({ status: 'running', pinned: true, pinMetadataStatus: 'available', updatedAt: 2 });

    // An explicit pin event overrides; an explicit unavailable report clears the pin.
    useJobStore.getState().upsertJob(job({ jobId: 'a', pinned: false }));
    row = useJobStore.getState().jobs[0]!;
    expect(row.pinned).toBe(false);

    useJobStore.getState().upsertJob(job({ jobId: 'a', pinned: true, pinMetadataStatus: 'unavailable' }));
    row = useJobStore.getState().jobs[0]!;
    expect(row.pinned).toBeNull();
    expect(useJobStore.getState().pinMetadataUnavailable).toBe(true);
  });

  it('appends unknown jobs and removes only the requested id', () => {
    useJobStore.setState({ jobs: [job({ jobId: 'a' }), job({ jobId: 'b' })] });

    useJobStore.getState().upsertJob(job({ jobId: 'c' }));
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['a', 'b', 'c']);

    useJobStore.getState().removeJob('b');
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['a', 'c']);

    useJobStore.getState().removeJob('missing');
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['a', 'c']);
  });

  it('counts only running jobs across the whole list', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'a', status: 'running' }),
        job({ jobId: 'b', status: 'running' }),
        job({ jobId: 'c', status: 'starting' }),
        job({ jobId: 'd', status: 'pending' }),
        job({ jobId: 'e', status: 'completed' }),
        job({ jobId: 'f', status: 'failed' }),
      ],
    });

    expect(selectRunningJobCount(useJobStore.getState())).toBe(2);

    useJobStore.getState().upsertJob(job({ jobId: 'a', status: 'completed' }));
    expect(selectRunningJobCount(useJobStore.getState())).toBe(1);

    useJobStore.getState().removeJob('b');
    expect(selectRunningJobCount(useJobStore.getState())).toBe(0);
  });
});
