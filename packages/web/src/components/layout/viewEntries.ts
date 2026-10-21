import { useMemo } from 'react';
import { Code, ListChecks, MessageSquare, TerminalSquare, PanelsTopLeft } from 'lucide-react';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import type { PluginPanel } from '@/types/pluginPanels';

export type ViewEntry = {
  /** Stable identity used by shortcut settings; never an array position. */
  id: string;
  to: string;
  label: string;
  icon: typeof MessageSquare;
  /** Exact-match only (only `/` needs it among the current entries). */
  end?: boolean;
  /** Present only for plugin panels; `id` is the composite stable identity. */
  pluginId?: string;
  panelId?: string;
};

// All view entries belong here; every three entries form one page.
// Single source of truth shared by the expanded entry strip
// (ViewEntryGroups) and the collapsed desktop nav rail (ViewEntryRail),
// so both surfaces always cover exactly the same routes.
export const entries: ViewEntry[] = [
  { id: 'builtin:chat', to: '/', label: 'Chat', icon: MessageSquare, end: true },
  { id: 'builtin:editor', to: '/editor', label: 'Editor', icon: Code },
  { id: 'builtin:jobs', to: '/jobs', label: 'Jobs', icon: ListChecks },
  { id: 'builtin:terminals', to: '/terminals', label: 'Terminal', icon: TerminalSquare },
  { id: 'builtin:actions', to: '/actions', label: 'Actions', icon: PanelsTopLeft },
  { id: 'builtin:automation', to: '/automation', label: 'Automation', icon: ListChecks },
];

/**
 * Merge built-in views with the live plugin-panel catalog.
 *
 * Plugin panel ids are only unique inside one plugin, so the stable identity is
 * the composite `plugin:<pluginId>/<panelId>`.  Invalid/duplicate entries are
 * skipped instead of breaking the whole navigation/shortcut page.  The first
 * occurrence wins, matching the catalog's own registration order.
 */
export function mergeViewEntries(
  base: readonly ViewEntry[],
  panels: readonly Partial<PluginPanel>[],
): ViewEntry[] {
  const result = [...base];
  const seen = new Set(base.map((entry) => entry.id));
  const routes = new Set(base.map((entry) => entry.to));
  for (const panel of panels) {
    if (!panel || typeof panel !== 'object') continue;
    const { pluginId, id, title, path } = panel;
    if (
      typeof pluginId !== 'string' || !pluginId ||
      typeof id !== 'string' || !id ||
      typeof path !== 'string' || !path
    ) {
      continue;
    }
    const entryId = `plugin:${pluginId}/${id}`;
    if (seen.has(entryId) || routes.has(path)) continue;
    seen.add(entryId);
    routes.add(path);
    result.push({
      id: entryId,
      to: path,
      label: typeof title === 'string' && title.trim() ? title : id,
      icon: PanelsTopLeft,
      pluginId,
      panelId: id,
    });
  }
  return result;
}

export function useViewEntries(): ViewEntry[] {
  const panels = usePluginPanelStore((state) => state.panels);
  return useMemo(() => mergeViewEntries(entries, panels), [panels]);
}

/** Non-reactive snapshot for the global keydown dispatcher. */
export function getViewEntriesSnapshot(): ViewEntry[] {
  return mergeViewEntries(entries, usePluginPanelStore.getState().panels);
}
