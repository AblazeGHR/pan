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

    set((s) => ({
      workers: { ...s.workers, [sessionId]: now },
      currentWorkerId:
        s.currentWorkerId === null && workerId
          ? workerId
          : s.currentWorkerId,
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
    } catch {
      // ignore
    }
  },
}));
