import { create } from 'zustand';
import type { Job } from '@/types/jobs';
import { fetchJobs } from '@/services/api';

/**
 * Unified Job registry state — the single source of truth for every surface.
 *
 * The Jobs page list, the nav running-count badge and any future consumer read
 * this one list. Updates arrive through the existing global WebSocket pipeline
 * (job.created / job.updated / job.deleted, wired in useWebSocket) plus an
 * authoritative REST load on socket open; there are no page-local copies, no
 * extra subscriptions and no polling.
 *
 * Staleness follows the queueStore request-generation pattern: every load
 * captures the current load and mutation generations, and a response may only
 * apply while it is still the newest load AND no list mutation (WS event or
 * page action) happened since it started. Otherwise the snapshot predates
 * newer state and would overwrite it or resurrect deleted jobs, so it is
 * discarded — never papered over with a duplicate fetch.
 */
interface JobStoreState {
  jobs: Job[];
  loaded: boolean;
  loading: boolean;
  error: string | null;
  /** GET /api/jobs reported Pan-owned pin metadata as unavailable. */
  pinMetadataUnavailable: boolean;
  /** Monotonic generation, bumped by every loadJobs start. */
  loadSeq: number;
  /** Monotonic generation, bumped by every list mutation (upsert/remove). */
  mutationSeq: number;

  loadJobs: () => Promise<void>;
  /** Insert or merge one job. Lifecycle broadcasts omit Pan-owned pin metadata. */
  upsertJob: (job: Job) => void;
  removeJob: (jobId: string) => void;
}

export const useJobStore = create<JobStoreState>((set, get) => ({
  jobs: [],
  loaded: false,
  loading: false,
  error: null,
  pinMetadataUnavailable: false,
  loadSeq: 0,
  mutationSeq: 0,

  loadJobs: async () => {
    const requestSeq = get().loadSeq + 1;
    const mutationSeqAtRequest = get().mutationSeq;
    set({ loadSeq: requestSeq, loading: true, error: null });
    try {
      const jobs = await fetchJobs((status) => {
        // The pin status callback belongs to its own request: a stale
        // request's late callback must not pollute the current state.
        if (get().loadSeq === requestSeq) {
          set({ pinMetadataUnavailable: status === 'unavailable' });
        }
      });
      if (get().loadSeq !== requestSeq) return; // a newer load owns the list
      if (get().mutationSeq !== mutationSeqAtRequest) {
        // A WS mutation landed while this request was in flight: the snapshot
        // was taken before it and would overwrite/resurrect newer state.
        // Discard it; this request is finished, so release its loading claim.
        set({ loading: false });
        return;
      }
      set({ jobs, loaded: true, loading: false, error: null });
    } catch (e) {
      if (get().loadSeq !== requestSeq) return;
      set({ loading: false, error: e instanceof Error ? e.message : 'Request failed' });
    }
  },

  upsertJob: (job) => {
    set((state) => ({
      mutationSeq: state.mutationSeq + 1,
      jobs: state.jobs.some((j) => j.jobId === job.jobId)
        ? state.jobs.map((j) => (j.jobId === job.jobId ? {
          ...job,
          pinned: job.pinMetadataStatus === 'unavailable' ? null : job.pinned ?? j.pinned,
          pinMetadataStatus: job.pinMetadataStatus ?? j.pinMetadataStatus,
        } : j))
        : [...state.jobs, job],
      ...(job.pinMetadataStatus
        ? { pinMetadataUnavailable: job.pinMetadataStatus === 'unavailable' }
        : {}),
    }));
  },

  removeJob: (jobId) => {
    set((state) => ({
      mutationSeq: state.mutationSeq + 1,
      jobs: state.jobs.filter((j) => j.jobId !== jobId),
    }));
  },
}));

/**
 * Count of jobs in the running state across ALL jobs (never a page filter).
 * `starting` is deliberately excluded: the Jobs list treats it as a separate
 * status and the badge reports strictly running work.
 */
export function selectRunningJobCount(state: JobStoreState): number {
  let count = 0;
  for (const job of state.jobs) {
    if (job.status === 'running') count += 1;
  }
  return count;
}
