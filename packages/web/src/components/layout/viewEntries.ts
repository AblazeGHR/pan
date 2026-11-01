import { useMemo } from 'react';
import { Code, ListChecks, MessageSquare, TerminalSquare, PanelsTopLeft } from 'lucide-react';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { usePlatformStore } from '@/stores/platformStore';
import { capabilityAllowed, type CapabilityMap } from '@/utils/platformCapabilities';
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
  /**
   * Server capability that must not be explicitly `false` for this entry to
   * be offered. Unknown capabilities keep the entry (Windows baseline).
   */
  capability?: string;
};

// All view entries belong here; every three entries form one page.
// Single source of truth shared by the expanded entry strip
// (ViewEntryGroups) and the collapsed desktop nav rail (ViewEntryRail),
// so both surfaces always cover exactly the same routes.
export const entries: ViewEntry[] = [
  { id: 'builtin:chat', to: '/', label: 'Chat', icon: MessageSquare, end: true },
  { id: 'builtin:editor', to: '/editor', label: 'Editor', icon: Code },
  { id: 'builtin:terminals', to: '/terminals', label: 'Terminal', icon: TerminalSquare, capability: 'terminal.native' },
  { id: 'builtin:locker', to: '/locker', label: 'Locker', icon: PanelsTopLeft },
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

/**
 * Drop entries whose capability the server explicitly denied.
 *
 * Ordering is applied afterwards, so a hidden entry does not occupy a slot in
 * the three-per-page strip. Persisted order keeps its id: once the capability
 * comes back the entry returns to its saved position.
 */
export function filterAvailableViewEntries(
  list: readonly ViewEntry[],
  capabilities: CapabilityMap | null | undefined,
): ViewEntry[] {
  return list.filter((entry) => !entry.capability || capabilityAllowed(capabilities, entry.capability));
}

export function useViewEntries(): ViewEntry[] {
  const panels = usePluginPanelStore((state) => state.panels);
  const order = useAppSettingsStore((state) => state.viewEntryOrder);
  const capabilities = usePlatformStore((state) => state.capabilities);
  return useMemo(
    () => applyViewEntryOrder(
      filterAvailableViewEntries(mergeViewEntries(entries, panels), capabilities),
      order,
    ),
    [panels, order, capabilities],
  );
}

/** Non-reactive snapshot for the global keydown dispatcher. */
export function getViewEntriesSnapshot(): ViewEntry[] {
  return applyViewEntryOrder(
    filterAvailableViewEntries(
      mergeViewEntries(entries, usePluginPanelStore.getState().panels),
      usePlatformStore.getState().capabilities,
    ),
    useAppSettingsStore.getState().viewEntryOrder,
  );
}

/** Merge persisted ids with the current catalog: stale ids are dropped and new entries append. */
export function applyViewEntryOrder(all: ViewEntry[], saved: unknown): ViewEntry[] {
  if (!Array.isArray(saved)) return all;
  const byId = new Map(all.map((entry) => [entry.id, entry]));
  const result: ViewEntry[] = [];
  const seen = new Set<string>();
  for (const id of saved) {
    if (typeof id !== 'string' || seen.has(id)) continue;
    const entry = byId.get(id);
    if (entry) { result.push(entry); seen.add(id); }
  }
  for (const entry of all) if (!seen.has(entry.id)) result.push(entry);
  return result;
}

export function reorderViewEntryIds(ids: readonly string[], draggedId: string, targetId: string, zone: 'before' | 'after'): string[] {
  if (draggedId === targetId || !ids.includes(draggedId) || !ids.includes(targetId)) return [...ids];
  const next = ids.filter((id) => id !== draggedId);
  const index = next.indexOf(targetId);
  next.splice(index + (zone === 'after' ? 1 : 0), 0, draggedId);
  return next;
}

export function paginateViewEntries<T>(items: readonly T[], size = 3): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, group) =>
    Array.from({ length: size }, (_, slot) => items[group * size + slot]).filter((item): item is T => item !== undefined),
  );
}
