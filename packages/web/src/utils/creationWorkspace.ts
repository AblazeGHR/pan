import { useUIStore } from '@/stores/uiStore';
import { ALL_WORKSPACES, UNGROUPED_WORKSPACES } from '@/utils/sessionFilters';

/**
 * Snapshot the active scope immediately before a new Session request.
 * Special scopes stay ungrouped. The server validates concrete ids atomically
 * with creation, so a stale/deleted id cannot create an incorrectly grouped
 * Session and existing imported Sessions can still be reimported unchanged.
 */
export function getCreationWorkspaceIds(): string[] {
  const activeWorkspaceId = useUIStore.getState().activeWorkspaceId;
  if (
    !activeWorkspaceId ||
    activeWorkspaceId === ALL_WORKSPACES ||
    activeWorkspaceId === UNGROUPED_WORKSPACES
  ) {
    return [];
  }
  return [activeWorkspaceId];
}
