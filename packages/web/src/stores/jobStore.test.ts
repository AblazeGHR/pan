// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Job } from '@/types/jobs';
import { selectRunningJobCount, selectSessionJobIndicator, useJobStore } from '@/stores/jobStore';
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
  loadSeq: 0, mutationSeq: 0, eventSeqByJob: {},
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
    expect(row.status).toBe('running');
    expect(row.updatedAt).toBe(2);

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

describe('selectSessionJobIndicator', () => {
  const SESSION = 'session-1';

  beforeEach(() => {
    vi.clearAllMocks();
    resetStore();
  });

  const indicator = () => selectSessionJobIndicator(useJobStore.getState(), SESSION);

  it('is null with no jobs at all', () => {
    expect(indicator()).toBeNull();
  });

  it('reports running for a targeted background-process job', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'p1', kind: 'background-process', status: 'running', target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBe('running');
  });

  it('reports scheduled for an armed session-message job in pending (first fire)', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'm1', kind: 'session-message', status: 'pending', target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBe('scheduled');
  });

  it('reports scheduled for a recurring session-message job between fires', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'm2', kind: 'session-message', status: 'scheduled', target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBe('scheduled');
  });

  it('reports scheduled for an enabled scheduled-task job with a next fire', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 's1', kind: 'scheduled-task', status: 'scheduled', enabled: true, paused: false, target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBe('scheduled');
  });

  it('is null for a scheduled-task job in pending (no next fire)', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 's2', kind: 'scheduled-task', status: 'pending', target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBeNull();
  });

  it('is null while a background-process job is only starting (spawn claim)', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'p2', kind: 'background-process', status: 'starting', target: { sessionId: SESSION } })],
    });
    expect(indicator()).toBeNull();
  });

  it('is null for every terminal status', () => {
    for (const status of ['completed', 'failed', 'cancelled', 'timed_out'] as const) {
      useJobStore.setState({
        jobs: [job({ jobId: `t-${status}`, status, target: { sessionId: SESSION } })],
      });
      expect(indicator()).toBeNull();
    }
  });

  it('aggregates many jobs for one session into a single value with running priority', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'a', kind: 'session-message', status: 'scheduled', target: { sessionId: SESSION } }),
        job({ jobId: 'b', kind: 'session-message', status: 'pending', target: { sessionId: SESSION } }),
      ],
    });
    expect(indicator()).toBe('scheduled');

    // A running job arriving later (any position) flips the aggregate to blue.
    useJobStore.getState().upsertJob(
      job({ jobId: 'c', kind: 'background-process', status: 'running', target: { sessionId: SESSION } }),
    );
    expect(indicator()).toBe('running');

    // Running first in the list also wins.
    useJobStore.setState({
      jobs: [
        job({ jobId: 'd', kind: 'background-process', status: 'running', target: { sessionId: SESSION } }),
        job({ jobId: 'e', kind: 'session-message', status: 'scheduled', target: { sessionId: SESSION } }),
      ],
    });
    expect(indicator()).toBe('running');
  });

  it('matches a broadcast job through target.sessionIds without dropping the first id', () => {
    useJobStore.setState({
      jobs: [job({
        jobId: 'b1', kind: 'session-broadcast', status: 'running',
        target: { sessionId: SESSION, sessionIds: [SESSION, 'session-2'] },
      })],
    });
    expect(indicator()).toBe('running');
    // The other broadcast member sees the same indicator.
    expect(selectSessionJobIndicator(useJobStore.getState(), 'session-2')).toBe('running');
  });

  it('is null for a session that is not a job target', () => {
    useJobStore.setState({
      jobs: [job({ jobId: 'x1', status: 'running', target: { sessionId: 'other-session' } })],
    });
    expect(indicator()).toBeNull();
  });

  it('never infers a target from the creator/source session', () => {
    useJobStore.setState({
      jobs: [job({
        jobId: 'c1', kind: 'background-process', status: 'running',
        source: { type: 'agent', sessionId: SESSION },
        target: { sessionId: 'other-session' },
      })],
    });
    // The creator session shows nothing; only the real target does.
    expect(indicator()).toBeNull();
    expect(selectSessionJobIndicator(useJobStore.getState(), 'other-session')).toBe('running');
  });

  it('does not misreport paused or disabled scheduled tasks', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'pa', kind: 'scheduled-task', status: 'scheduled', paused: true, target: { sessionId: SESSION } }),
        job({ jobId: 'pb', kind: 'scheduled-task', status: 'scheduled', enabled: false, target: { sessionId: SESSION } }),
      ],
    });
    expect(indicator()).toBeNull();
  });

  it('tolerates jobs without any target (main-lifecycle) and legacy shapes', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'ml', kind: 'main-lifecycle', status: 'running', target: { sessionId: null } }),
        job({ jobId: 'lg', kind: 'session-message', status: 'scheduled', target: undefined as unknown as Job['target'] }),
      ],
    });
    expect(indicator()).toBeNull();
  });

  it('tracks the authoritative snapshot and WS mutations without polling', async () => {
    // First snapshot (socket open REST hydration) arms the indicator.
    vi.mocked(api.fetchJobs).mockResolvedValue([
      job({ jobId: 'w1', kind: 'session-message', status: 'scheduled', target: { sessionId: SESSION } }),
    ]);
    await useJobStore.getState().loadJobs();
    expect(indicator()).toBe('scheduled');

    // job.updated: the message fires and runs.
    useJobStore.getState().upsertJob(
      job({ jobId: 'w1', kind: 'session-message', status: 'running', target: { sessionId: SESSION } }),
    );
    expect(indicator()).toBe('running');

    // job.updated: terminal state removes the indicator.
    useJobStore.getState().upsertJob(
      job({ jobId: 'w1', kind: 'session-message', status: 'completed', target: { sessionId: SESSION } }),
    );
    expect(indicator()).toBeNull();

    // job.created: a new process job targeting the session arms blue again.
    useJobStore.getState().upsertJob(
      job({ jobId: 'w2', kind: 'background-process', status: 'running', target: { sessionId: SESSION } }),
    );
    expect(indicator()).toBe('running');

    // job.deleted: the registry row disappears and the indicator clears.
    useJobStore.getState().removeJob('w2');
    expect(indicator()).toBeNull();
  });

  it('returns a stable primitive so memoized cards skip unrelated re-renders', () => {
    useJobStore.setState({
      jobs: [
        job({ jobId: 'k1', status: 'running', target: { sessionId: SESSION } }),
        job({ jobId: 'k2', status: 'running', target: { sessionId: 'other' } }),
      ],
    });
    const first = selectSessionJobIndicator(useJobStore.getState(), SESSION);
    const second = selectSessionJobIndicator(useJobStore.getState(), SESSION);
    expect(Object.is(first, second)).toBe(true);
    // An unrelated job update keeps the selected value identical.
    useJobStore.getState().upsertJob(job({ jobId: 'k3', status: 'running', target: { sessionId: 'third' } }));
    expect(Object.is(selectSessionJobIndicator(useJobStore.getState(), SESSION), first)).toBe(true);
  });
});
