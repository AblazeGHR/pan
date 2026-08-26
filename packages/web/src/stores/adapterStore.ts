import { create } from 'zustand';
import type {
  AdapterConfig,
  AdapterInfo,
  ApiGenericResponse,
  Session,
  SyncedSettings,
  SettingsBody,
} from '@/types';
import {
  fetchAdapterConfig,
  fetchAdapters,
  patchSession,
  workerSettings,
} from '@/services/api';

interface AdapterStore {
  // State
  adapters: AdapterInfo[];
  adapterConfigs: Record<string, AdapterConfig>;
  currentAdapter: string;
  configReady: boolean;
  lastSyncedSettings: SyncedSettings | null;

  // Actions
  loadAdapterList: () => Promise<void>;
  loadConfig: (adapter: string) => Promise<void>;
  setCurrentAdapter: (adapter: string) => void;
  getConfig: () => AdapterConfig | null;
  applySettings: (
    sessionId: string,
    workerId?: string | null,
    settings?: SettingsBody,
  ) => Promise<Session | ApiGenericResponse>;
  updateSyncedSettings: (settings: SyncedSettings) => void;
  hasPendingChanges: (current: SyncedSettings) => boolean;
}

export const useAdapterStore = create<AdapterStore>((set, get) => ({
  adapters: [],
  adapterConfigs: {},
  currentAdapter: 'cbc',
  configReady: false,
  lastSyncedSettings: null,

  loadAdapterList: async () => {
    try {
      const data = await fetchAdapters();
      set({ adapters: data.adapters || [] });
    } catch {
      set({ adapters: [{ name: 'cbc', defaultModel: '', supportsResume: false, supportsFork: false }] });
    }
  },

  loadConfig: async (adapter) => {
    try {
      const config = await fetchAdapterConfig(adapter);
      set((s) => ({
        adapterConfigs: { ...s.adapterConfigs, [adapter]: config },
        currentAdapter: adapter,
        configReady: true,
      }));
    } catch {
      // retry on next settings open
    }
  },

  setCurrentAdapter: (adapter) => {
    set({ currentAdapter: adapter });
  },

  getConfig: () => {
    const { currentAdapter, adapterConfigs } = get();
    return adapterConfigs[currentAdapter] ?? null;
  },

  applySettings: async (sessionId, workerId, settings) => {
    if (!settings) return {} as ApiGenericResponse;

    if (workerId) {
      return await workerSettings(workerId, settings);
    } else {
      return await patchSession(sessionId, settings);
    }
  },

  updateSyncedSettings: (settings) => {
    set({ lastSyncedSettings: settings });
  },

  hasPendingChanges: (current) => {
    const { lastSyncedSettings: last } = get();
    if (!last) return false;
    if (current.model !== last.model) return true;
    if (current.permissionMode !== last.permissionMode) return true;
    if (current.alwaysThinkingEnabled !== last.alwaysThinkingEnabled) return true;
    if (current.effort !== last.effort) return true;
    return false;
  },
}));
