import { create } from 'zustand';

/** Facts from existing panel requests; Search never mounts or fetches hidden panels. */
export const useSettingsAvailability = create<{
  qqPlugins: boolean; qqSelected: boolean; snowLuma: boolean; localPanels: boolean;
  pluginSources: Array<{ id: string; label: string }>;
}>(() => ({ qqPlugins: false, qqSelected: false, snowLuma: false, localPanels: false, pluginSources: [] }));
