import { create } from 'zustand';
import type {
  AdapterConfig,
  AdapterInfo,
  ApiCliStatusResponse,
  ApiGenericResponse,
  CliDiagnostic,
  Session,
  SyncedSettings,
  SettingsBody,
} from '@/types';
import {
  fetchAdapterConfig,
  fetchAdapters,
  fetchCliStatus,
  patchSession,
} from '@/services/api';

interface AdapterStore {
  // State
  adapters: AdapterInfo[];
  cliStatus: ApiCliStatusResponse | null;
  cliStatusLoading: boolean;
  cliStatusError: string | null;
  adapterConfigs: Record<string, AdapterConfig>;
  currentAdapter: string;
  configReady: boolean;
  lastSyncedSettings: SyncedSettings | null;

  // Actions
  loadAdapterList: () => Promise<void>;
  loadCliStatus: () => Promise<void>;
  loadConfig: (adapter: string) => Promise<void>;
  setCurrentAdapter: (adapter: string) => void;
  getConfig: () => AdapterConfig | null;
  applySettings: (
    sessionId: string,
    settings?: SettingsBody,
  ) => Promise<Session | ApiGenericResponse>;
  updateSyncedSettings: (settings: SyncedSettings) => void;
  hasPendingChanges: (current: SyncedSettings) => boolean;
}

/** The CLI diagnostics endpoint is the source of truth for selectable adapters. */
export function getAvailableCliAdapters(
  status: ApiCliStatusResponse | null,
): CliDiagnostic[] {
  return status?.adapters.filter((adapter) => adapter.available) ?? [];
}

export const useAdapterStore = create<AdapterStore>((set, get) => ({
  adapters: [],
  cliStatus: null,
  cliStatusLoading: false,
  cliStatusError: null,
  adapterConfigs: {},
  currentAdapter: 'cbc',
  configReady: false,
  lastSyncedSettings: null,

  loadAdapterList: async () => {
    try {
      const data = await fetchAdapters();
      set({ adapters: data.adapters || [] });
    } catch {
      // Keep the last known registered list. Never invent cbc availability
      // when the registry request fails.
    }
  },

  loadCliStatus: async () => {
    set({ cliStatusLoading: true, cliStatusError: null });
    try {
      const cliStatus = await fetchCliStatus();
      set({ cliStatus, cliStatusLoading: false });
    } catch (error: unknown) {
      set({
        cliStatus: null,
        cliStatusLoading: false,
        cliStatusError:
          error instanceof Error ? error.message : '无法检测 Agent CLI 可用性',
      });
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

  applySettings: async (sessionId, settings) => {
    if (!settings) return {} as ApiGenericResponse;
    return await patchSession(sessionId, settings);
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
