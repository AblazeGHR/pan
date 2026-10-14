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
 */
interface JobStoreState {
  jobs: Job[];
  loaded: boolean;
  loading: boolean;
  error: string | null;
  /** GET /api/jobs reported Pan-owned pin metadata as unavailable. */
  pinMetadataUnavailable: boolean;

  loadJobs: () => Promise<void>;
  /** Insert or merge one job. Lifecycle broadcasts omit Pan-owned pin metadata. */
  upsertJob: (job: Job) => void;
  removeJob: (jobId: string) => void;
}

export const useJobStore = create<JobStoreState>((set) => ({
  jobs: [],
  loaded: false,
  loading: false,
  error: null,
  pinMetadataUnavailable: false,

  loadJobs: async () => {
    set({ loading: true, error: null });
    try {
      const jobs = await fetchJobs((status) =>
        set({ pinMetadataUnavailable: status === 'unavailable' }));
      set({ jobs, loaded: true, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Request failed' });
    }
  },

  upsertJob: (job) =>
    set((state) => ({
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
    })),

  removeJob: (jobId) =>
    set((state) => ({ jobs: state.jobs.filter((j) => j.jobId !== jobId) })),
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
