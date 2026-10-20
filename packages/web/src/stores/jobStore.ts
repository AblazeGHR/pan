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
interface JobEventCursor { epoch?: string; seq?: number }

function jobTime(value: Job['updatedAt']): number {
  if (typeof value === 'number') return value * 1000;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : -Infinity;
}

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
  /** Last server live-event cursor accepted per Job. */
  eventSeqByJob: Record<string, JobEventCursor>;

  loadJobs: () => Promise<void>;
  /** Insert or merge one job. Lifecycle broadcasts omit Pan-owned pin metadata. */
  upsertJob: (job: Job, cursor?: JobEventCursor) => void;
  removeJob: (jobId: string, cursor?: JobEventCursor) => void;
}

export const useJobStore = create<JobStoreState>((set, get) => ({
  jobs: [],
  loaded: false,
  loading: false,
  error: null,
  pinMetadataUnavailable: false,
  loadSeq: 0,
  mutationSeq: 0,
  eventSeqByJob: {},

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

  upsertJob: (job, cursor) => {
    set((state) => {
      const previous = state.jobs.find((candidate) => candidate.jobId === job.jobId);
      const seen = state.eventSeqByJob[job.jobId];
      if (cursor?.epoch && cursor.epoch === seen?.epoch
          && typeof cursor.seq === 'number' && typeof seen.seq === 'number'
          && cursor.seq <= seen.seq) return state;
      // A retry can broadcast an older persisted snapshot with a newer transport
      // cursor. updatedAt belongs to the Job, and survives server restarts.
      const olderLifecycle = previous && jobTime(job.updatedAt) < jobTime(previous.updatedAt);
      // Pin metadata has an independent writer and does not advance Job time.
      // A pin response captured before a lifecycle update still owns its pin,
      // while its old status/target/schedule must not replace newer Job facts.
      if (olderLifecycle && job.pinned === undefined && !job.pinMetadataStatus) return state;
      const merged = {
        ...(olderLifecycle ? previous : job),
        pinned: job.pinMetadataStatus === 'unavailable' ? null : job.pinned ?? previous?.pinned,
        pinMetadataStatus: job.pinMetadataStatus ?? previous?.pinMetadataStatus,
      };
      return {
        mutationSeq: state.mutationSeq + 1,
        jobs: previous
          ? state.jobs.map((j) => j.jobId === job.jobId ? merged : j)
          : [...state.jobs, merged],
        eventSeqByJob: cursor?.epoch && typeof cursor.seq === 'number'
          ? { ...state.eventSeqByJob, [job.jobId]: cursor } : state.eventSeqByJob,
        ...(job.pinMetadataStatus
          ? { pinMetadataUnavailable: job.pinMetadataStatus === 'unavailable' } : {}),
      };
    });
  },

  removeJob: (jobId, cursor) => {
    set((state) => {
      const seen = state.eventSeqByJob[jobId];
      if (cursor?.epoch && cursor.epoch === seen?.epoch
          && typeof cursor.seq === 'number' && typeof seen.seq === 'number'
          && cursor.seq <= seen.seq) return state;
      return {
        mutationSeq: state.mutationSeq + 1,
        jobs: state.jobs.filter((j) => j.jobId !== jobId),
        // Retain the tombstone so replay cannot resurrect a deleted Job.
        eventSeqByJob: cursor?.epoch && typeof cursor.seq === 'number'
          ? { ...state.eventSeqByJob, [jobId]: cursor } : state.eventSeqByJob,
      };
    });
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

/**
 * Aggregated Job indicator for one Session's info area (bottom-right rail):
 * - `'running'`   — at least one targeted Job is actually running (blue J);
 * - `'scheduled'` — targeted Jobs are armed and waiting for their next
 *                   scheduled fire (yellow J);
 * - `null`        — nothing to show: no targeted Job, or every targeted Job is
 *                   starting / paused / disabled / terminal.
 *
 * Status semantics per kind (backend state machine is the source of truth):
 * - `background-process`: `starting` (runner spawn claim) → `running` →
 *   terminal. `starting` is neither a pending schedule nor yet running — the
 *   same strict reading as `selectRunningJobCount` — so it shows nothing.
 * - `session-message` / `session-broadcast`: `pending` (created, waiting for
 *   the first fire) and `scheduled` (recurring, between fires) are both armed;
 *   `running` is an in-flight delivery.
 * - `scheduled-task`: `scheduled` means enabled with a next fire; `pending`
 *   means no next fire (exhausted), which is not a pending schedule. A paused
 *   task keeps `scheduled` but is skipped at dispatch, and a disabled task
 *   closes as `completed` — both are filtered here instead of misreported.
 *
 * Targeting uses the authoritative `target` mapping only (single target
 * `sessionId`; broadcast `sessionIds`). `creatorSessionId` / `source` are
 * ownership metadata and never imply a Session indicator. The return value is
 * a primitive, so memoized SessionItems re-render only on real changes and
 * no per-Session polling or subscription is introduced.
 */
export function selectSessionJobIndicator(
  state: JobStoreState,
  sessionId: string,
): 'running' | 'scheduled' | null {
  let scheduled = false;
  for (const job of state.jobs) {
    const target = job.target;
    if (!target) continue;
    if (target.sessionId !== sessionId && !(target.sessionIds?.includes(sessionId))) {
      continue;
    }
    if (job.status === 'running') return 'running';
    // Paused / disabled Jobs keep a scheduled-looking status but never fire.
    if (job.paused === true || job.enabled === false) continue;
    if (job.status === 'scheduled') {
      scheduled = true;
    } else if (
      job.status === 'pending'
      && (job.kind === 'session-message' || job.kind === 'session-broadcast')
    ) {
      // Message Jobs wait in `pending` for their first fire; scheduler tasks
      // use `pending` for "no next fire", which is not a pending schedule.
      scheduled = true;
    }
  }
  return scheduled ? 'scheduled' : null;
}
