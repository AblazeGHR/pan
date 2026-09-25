import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { ALL_WORKSPACES, UNGROUPED_WORKSPACES } from '@/utils/sessionFilters';

/**
 * Resolve membership after persisted app settings have hydrated. The default
 * argument snapshots active scope synchronously, before the first await, so a
 * Workspace switch while the settings request is pending cannot retarget the
 * creation action.
 */
export async function getCreationWorkspaceIds(
  activeWorkspaceId = useUIStore.getState().activeWorkspaceId,
): Promise<string[]> {
  await useAppSettingsStore.getState().ensureSettingsLoaded();
  if (!useAppSettingsStore.getState().defaultNewSessionToCurrentWorkspace) {
    return [];
  }
  if (
    !activeWorkspaceId ||
    activeWorkspaceId === ALL_WORKSPACES ||
    activeWorkspaceId === UNGROUPED_WORKSPACES
  ) {
    return [];
  }
  return [activeWorkspaceId];
}
