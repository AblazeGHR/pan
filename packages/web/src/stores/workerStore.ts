import { create } from 'zustand';
import type { WorkerInfo, SettingsBody, ApiGenericResponse } from '@/types';
import {
  spawnWorker,
  killWorker,
  restartWorker,
  workerSettings,
  interruptWorker,
  steerWorker,
  takeoverWorker,
  listWorkers,
} from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';

/** Resolve the worker whose id equals currentWorkerId. `workers` is keyed by
 *  sessionId (one worker per session), so indexing `workers[currentWorkerId]`
 *  (a workerId) would always miss — scan the values instead.
 *
 *  NOTE: this must be maintained as explicit state (not a Zustand getter):
 *  v5 `setState` rebuilds the state object via Object.assign, which copies the
 *  getter's *value from the previous state* and then freezes it — a getter
 *  never sees fresh state after the first set. */
function findWorker(
  workers: Record<string, WorkerInfo>,
  workerId: string | null,
): WorkerInfo | null {
  if (!workerId) return null;
  for (const w of Object.values(workers)) {
    if (w.id === workerId) return w;
  }
  return null;
}

interface WorkerStore {
  workers: Record<string, WorkerInfo>;
  currentWorkerId: string | null;

  // Derived
  currentWorker: WorkerInfo | null;

  // Actions
  startWorker: (sessionId: string, settings?: SettingsBody) => Promise<void>;
  killCurrent: (workerId: string) => Promise<void>;
  interrupt: (workerId: string) => Promise<void>;
  steer: (workerId: string, text: string) => Promise<void>;
  restart: (workerId: string, settings?: SettingsBody) => Promise<void>;
  takeover: (workerId: string) => Promise<ApiGenericResponse>;
  updateWorker: (
    sessionId: string,
    workerId: string | null,
    status: string | null,
  ) => void;
  updateNativeStatus: (
    sessionId: string,
    workerId: string | null | undefined,
    nativeStatus: WorkerInfo['nativeStatus'],
  ) => void;
  updateNativeUsage: (
    sessionId: string,
    workerId: string | null | undefined,
    nativeUsage: WorkerInfo['nativeUsage'],
  ) => void;
  updateNativeRateLimits: (
    sessionId: string,
    workerId: string | null | undefined,
    nativeRateLimits: WorkerInfo['nativeRateLimits'],
  ) => void;
  syncToSession: (sessionId: string | null) => void;
  refresh: () => Promise<void>;
}

export const useWorkerStore = create<WorkerStore>((set) => ({
  workers: {},
  currentWorkerId: null,
  currentWorker: null,

  startWorker: async (sessionId, settings) => {
    try {
      const result = await spawnWorker(sessionId, settings);
      const workerId = result.workerId;
      if (workerId) {
        set((s) => {
          const worker: WorkerInfo = { id: workerId, sessionId, status: 'idle' };
          const workers = { ...s.workers, [sessionId]: worker };
          return {
            currentWorkerId: workerId,
            workers,
            currentWorker: findWorker(workers, workerId),
          };
        });
      }
    } catch (e) {
      throw e;
    }
  },

  killCurrent: async (workerId) => {
    try {
      await killWorker(workerId);
      set({ currentWorkerId: null, currentWorker: null });
    } catch (e) {
      throw e;
    }
  },

  interrupt: async (workerId) => {
    try {
      await interruptWorker(workerId);
    } catch (e) {
      throw e;
    }
  },

  steer: async (workerId, text) => {
    await steerWorker(workerId, text);
  },

  restart: async (workerId, settings) => {
    if (settings) {
      await workerSettings(workerId, settings);
    } else {
      await restartWorker(workerId);
    }
  },

  takeover: async (workerId) => {
    const result = await takeoverWorker(workerId);
    return result;
  },

  updateWorker: (sessionId, workerId, status) => {
    if (!sessionId) return;

    const previous = useWorkerStore.getState().workers[sessionId];
    const now: WorkerInfo = {
      id: workerId || '',
      sessionId,
      status: (status as WorkerInfo['status']) || 'offline',
      ...(status === 'idle' || status === null || status === undefined
        ? {}
        : previous?.nativeStatus
          ? { nativeStatus: previous.nativeStatus }
          : {}),
      ...(status === 'idle' || status === null || status === undefined
        ? {}
        : previous?.nativeUsage
          ? { nativeUsage: previous.nativeUsage }
          : {}),
      ...(status !== null && status !== undefined && previous?.id === workerId && previous?.nativeRateLimits
        ? { nativeRateLimits: previous.nativeRateLimits }
        : {}),
    };

    set((s) => {
      // currentWorkerId tracks the worker of the *currently selected*
      // session — set/clear it only for events belonging to that session,
      // otherwise leave it untouched (another session's worker must not
      // hijack the toolbar buttons).
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const isCurrentSession = sessionId === currentSessionId;
      const workers = { ...s.workers, [sessionId]: now };
      const currentWorkerId = isCurrentSession
        ? workerId || null
        : s.currentWorkerId;
      return {
        workers,
        currentWorkerId,
        currentWorker: findWorker(workers, currentWorkerId),
      };
    });
  },

  updateNativeStatus: (sessionId, workerId, nativeStatus) => {
    if (!sessionId) return;
    set((s) => {
      const previous = s.workers[sessionId];
      if (!previous && !workerId) return s;
      const worker: WorkerInfo = previous
        ? { ...previous, nativeStatus }
        : {
            id: workerId || '',
            sessionId,
            status: 'running',
            nativeStatus,
          };
      const workers = { ...s.workers, [sessionId]: worker };
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const currentWorkerId = sessionId === currentSessionId
        ? worker.id || s.currentWorkerId
        : s.currentWorkerId;
      return {
        workers,
        currentWorkerId,
        currentWorker: findWorker(workers, currentWorkerId),
      };
    });
  },

  updateNativeUsage: (sessionId, workerId, nativeUsage) => {
    if (!sessionId) return;
    set((s) => {
      const previous = s.workers[sessionId];
      if (!previous && !workerId) return s;
      const worker: WorkerInfo = previous
        ? { ...previous, nativeUsage }
        : {
            id: workerId || '',
            sessionId,
            status: 'running',
            nativeUsage,
          };
      const workers = { ...s.workers, [sessionId]: worker };
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const currentWorkerId = sessionId === currentSessionId
        ? worker.id || s.currentWorkerId
        : s.currentWorkerId;
      return {
        workers,
        currentWorkerId,
        currentWorker: findWorker(workers, currentWorkerId),
      };
    });
  },

  updateNativeRateLimits: (sessionId, workerId, nativeRateLimits) => {
    if (!sessionId) return;
    set((s) => {
      const previous = s.workers[sessionId];
      if (!previous && !workerId) return s;
      const worker: WorkerInfo = previous
        ? { ...previous, nativeRateLimits }
        : {
            id: workerId || '',
            sessionId,
            status: 'running',
            nativeRateLimits,
          };
      const workers = { ...s.workers, [sessionId]: worker };
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const currentWorkerId = sessionId === currentSessionId
        ? worker.id || s.currentWorkerId
        : s.currentWorkerId;
      return {
        workers,
        currentWorkerId,
        currentWorker: findWorker(workers, currentWorkerId),
      };
    });
  },

  syncToSession: (sessionId) => {
    set((s) => {
      const currentWorkerId = sessionId
        ? s.workers[sessionId]?.id ?? null
        : null;
      return {
        currentWorkerId,
        currentWorker: findWorker(s.workers, currentWorkerId),
      };
    });
  },

  refresh: async () => {
    try {
      const workers = await listWorkers();
      const map: Record<string, WorkerInfo> = {};
      for (const w of workers) {
        const previous = useWorkerStore.getState().workers[w.sessionId];
        const status = w.status as WorkerInfo['status'];
        map[w.sessionId] = {
          id: w.workerId,
          sessionId: w.sessionId,
          status,
          ...(status !== 'idle' && previous?.nativeStatus
            ? { nativeStatus: previous.nativeStatus }
            : {}),
          ...(status !== 'idle' && previous?.nativeUsage
            ? { nativeUsage: previous.nativeUsage }
            : {}),
          ...(previous?.id === w.workerId && previous?.nativeRateLimits
            ? { nativeRateLimits: previous.nativeRateLimits }
            : {}),
        };
      }
      // Pre-existing workers (spawned before this page loaded) never fire a
      // worker.spawned event — pick up the current session's worker here.
      const sid = useSessionStore.getState().currentSessionId;
      const currentWorkerId = sid ? map[sid]?.id ?? null : null;
      set({
        workers: map,
        currentWorkerId,
        currentWorker: findWorker(map, currentWorkerId),
      });
    } catch {
      // ignore
    }
  },
}));

// Keep currentWorkerId in lockstep with the selected session. Worker events
// and refresh() also sync, but the initial session selection (or switching)
// happens through sessionStore.selectSession — this subscription covers it.
useSessionStore.subscribe((state, prevState) => {
  if (state.currentSessionId !== prevState.currentSessionId) {
    useWorkerStore.getState().syncToSession(state.currentSessionId);
  }
});
