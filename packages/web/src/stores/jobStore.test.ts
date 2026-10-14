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

type PinStatus = 'available' | 'unavailable';
type PinCallback = (status: PinStatus) => void;

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
  loadSeq: 0, mutationSeq: 0,
});

/** A manually resolved fetchJobs response, for deterministic race ordering. */
function gatedJobs() {
  const gates = [
    { resolve: undefined as undefined | ((jobs: Job[]) => void), pin: undefined as PinCallback | undefined },
    { resolve: undefined as undefined | ((jobs: Job[]) => void), pin: undefined as PinCallback | undefined },
  ];
  let call = 0;
  vi.mocked(api.fetchJobs).mockImplementation((onPinStatus) => {
    const gate = gates[call]!;
    gate.pin = onPinStatus;
    call += 1;
    return new Promise<Job[]>((resolve) => { gate.resolve = resolve; });
  });
  return gates;
}

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

  it('discards a REST snapshot that resolves after a WS mutation landed', async () => {
    const gates = gatedJobs();
    const loadPromise = useJobStore.getState().loadJobs();

    // job.updated lands while the load is in flight (useWebSocket -> store).
    useJobStore.getState().upsertJob(job({ jobId: 'live', status: 'running' }));
    gates[0]!.resolve!([job({ jobId: 'stale-snapshot', status: 'completed' })]);
    await loadPromise;

    const state = useJobStore.getState();
    // The snapshot predates the event: adopting it would overwrite the
    // newer running state, so it is discarded and the mutation survives.
    expect(state.jobs.map((j) => j.jobId)).toEqual(['live']);
    expect(state.loaded).toBe(false);
    // The request is finished: it must release its loading claim.
    expect(state.loading).toBe(false);
  });

  it('does not resurrect a job deleted while a load was pending', async () => {
    useJobStore.setState({ jobs: [job({ jobId: 'a' }), job({ jobId: 'b' })], loaded: true });
    const gates = gatedJobs();
    const loadPromise = useJobStore.getState().loadJobs();

    useJobStore.getState().removeJob('a');
    gates[0]!.resolve!([job({ jobId: 'a' }), job({ jobId: 'b' })]);
    await loadPromise;

    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['b']);
    expect(useJobStore.getState().loading).toBe(false);
  });

  it('lets the newer load win when two loads resolve out of order', async () => {
    const gates = gatedJobs();
    const first = useJobStore.getState().loadJobs();
    const second = useJobStore.getState().loadJobs();

    gates[1]!.resolve!([job({ jobId: 'new', status: 'running' })]);
    await second;
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['new']);

    gates[0]!.resolve!([job({ jobId: 'old' })]);
    await first;
    // The older request's snapshot must not overwrite the newer one.
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual(['new']);
    expect(useJobStore.getState().loading).toBe(false);
  });

  it('ignores a stale pin-metadata callback from a superseded load', async () => {
    const gates = gatedJobs();
    const first = useJobStore.getState().loadJobs();
    const second = useJobStore.getState().loadJobs();

    gates[0]!.pin?.('unavailable');
    expect(useJobStore.getState().pinMetadataUnavailable).toBe(false);
    gates[1]!.pin?.('unavailable');
    expect(useJobStore.getState().pinMetadataUnavailable).toBe(true);

    gates[1]!.resolve!([]);
    await second;
    gates[0]!.resolve!([job({ jobId: 'late' })]);
    await first;
    // The late stale snapshot neither applies nor resets the pin state.
    expect(useJobStore.getState().jobs.map((j) => j.jobId)).toEqual([]);
    expect(useJobStore.getState().pinMetadataUnavailable).toBe(true);
  });

  it('still applies a load that started after the mutation it observes', async () => {
    // Green path for the gating: a mutation BEFORE the load starts is part of
    // the snapshot's baseline and must not cause a discard.
    useJobStore.getState().upsertJob(job({ jobId: 'pre-existing', status: 'running' }));
    vi.mocked(api.fetchJobs).mockImplementation(async (onPinStatus) => {
      onPinStatus?.('available');
      return [job({ jobId: 'pre-existing', status: 'completed' })];
    });

    await useJobStore.getState().loadJobs();

    const state = useJobStore.getState();
    expect(state.jobs.map((j) => j.jobId)).toEqual(['pre-existing']);
    expect(state.loaded).toBe(true);
    expect(state.loading).toBe(false);
  });
});
