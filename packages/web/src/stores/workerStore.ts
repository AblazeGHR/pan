import { create } from 'zustand';
import type { WorkerInfo, SettingsBody, ApiGenericResponse } from '@/types';
import {
  spawnWorker,
  killWorker,
  restartWorker,
  workerSettings,
  interruptWorker,
  takeoverWorker,
  listWorkers,
} from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';

interface WorkerStore {
  workers: Record<string, WorkerInfo>;
  currentWorkerId: string | null;

  // Derived
  currentWorker: WorkerInfo | null;

  // Actions
  startWorker: (sessionId: string, settings?: SettingsBody) => Promise<void>;
  killCurrent: (workerId: string) => Promise<void>;
  interrupt: (workerId: string) => Promise<void>;
  restart: (workerId: string, settings?: SettingsBody) => Promise<void>;
  takeover: (workerId: string) => Promise<ApiGenericResponse>;
  updateWorker: (
    sessionId: string,
    workerId: string | null,
    status: string | null,
  ) => void;
  syncToSession: (sessionId: string | null) => void;
  refresh: () => Promise<void>;
}

export const useWorkerStore = create<WorkerStore>((set, get) => ({
  workers: {},
  currentWorkerId: null,

  get currentWorker() {
    const { currentWorkerId, workers } = get();
    return currentWorkerId ? workers[currentWorkerId] ?? null : null;
  },

  startWorker: async (sessionId, settings) => {
    try {
      const result = await spawnWorker(sessionId, settings);
      const workerId = result.workerId;
      if (workerId) {
        set({ currentWorkerId: workerId });
        set((s) => ({
          workers: {
            ...s.workers,
            [sessionId]: {
              id: workerId,
              sessionId,
              status: 'idle',
            },
          },
        }));
      }
    } catch (e) {
      throw e;
    }
  },

  killCurrent: async (workerId) => {
    try {
      await killWorker(workerId);
      set({ currentWorkerId: null });
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

    const now: WorkerInfo = {
      id: workerId || '',
      sessionId,
      status: (status as WorkerInfo['status']) || 'offline',
    };

    set((s) => {
      // currentWorkerId tracks the worker of the *currently selected*
      // session — set/clear it only for events belonging to that session,
      // otherwise leave it untouched (another session's worker must not
      // hijack the toolbar buttons).
      const currentSessionId = useSessionStore.getState().currentSessionId;
      const isCurrentSession = sessionId === currentSessionId;
      return {
        workers: { ...s.workers, [sessionId]: now },
        currentWorkerId: isCurrentSession
          ? workerId || null
          : s.currentWorkerId,
      };
    });
  },

  syncToSession: (sessionId) => {
    set((s) => ({
      currentWorkerId: sessionId ? s.workers[sessionId]?.id ?? null : null,
    }));
  },

  refresh: async () => {
    try {
      const workers = await listWorkers();
      const map: Record<string, WorkerInfo> = {};
      for (const w of workers) {
        map[w.sessionId] = {
          id: w.workerId,
          sessionId: w.sessionId,
          status: w.status as WorkerInfo['status'],
        };
      }
      set({ workers: map });
      // Pre-existing workers (spawned before this page loaded) never fire a
      // worker.spawned event — pick up the current session's worker here.
      const sid = useSessionStore.getState().currentSessionId;
      if (sid) get().syncToSession(sid);
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
