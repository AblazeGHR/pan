import { create } from 'zustand';
import { fetchPluginPanels } from '@/services/api';
import type { PluginPanel } from '@/types/pluginPanels';

interface State {
  panels: PluginPanel[];
  loading: boolean;
  loaded: boolean;
  error: string | null;
  load: (force?: boolean) => Promise<void>;
}

export const usePluginPanelStore = create<State>((set, get) => ({
  panels: [], loading: false, loaded: false, error: null,
  load: async (force = false) => {
    if (get().loading || (get().loaded && !force)) return;
    set({ loading: true, error: null });
    try { set({ panels: await fetchPluginPanels(), loaded: true }); }
    catch (error) { set({ error: error instanceof Error ? error.message : '无法加载插件面板' }); }
    finally { set({ loading: false }); }
  },
}));
