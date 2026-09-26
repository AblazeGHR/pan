import { useMemo } from 'react';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { effectiveWorkspaceIds } from '@/utils/sessionFilters';

export interface SessionWorkspaceInfo {
  /** Effective Workspace id, or null when ungrouped or not yet loaded. */
  workspaceId: string | null;
  workspaceName?: string;
  /** Shared absolute directories of that Workspace (empty when none). */
  workspaceDirs: string[];
}

/**
 * Resolve the Workspace a Session effectively belongs to (following manager
 * inheritance) and its shared directories. Returns null id until the Workspace
 * metadata is loaded, so callers can disable workspace-scoped actions safely.
 */
export function useSessionWorkspace(session: Session | null): SessionWorkspaceInfo {
  const sessions = useSessionStore((s) => s.sessions);
  const workspaces = useWorkspaceStore((s) => s.workspaces);

  return useMemo(() => {
    if (!session) return { workspaceId: null, workspaceDirs: [] };
    const effectiveId = effectiveWorkspaceIds(session, sessions)[0] ?? null;
    const workspace = effectiveId ? workspaces.find((w) => w.id === effectiveId) ?? null : null;
    if (!workspace) return { workspaceId: null, workspaceDirs: [] };
    return {
      workspaceId: workspace.id,
      workspaceName: workspace.name,
      workspaceDirs: workspace.dirs ?? [],
    };
  }, [session, sessions, workspaces]);
}
