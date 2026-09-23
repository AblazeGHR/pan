import { create } from 'zustand';
import type { Workspace } from '@/types';
import * as api from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { buildManagerEdges, collectDescendants } from '@/components/session/sessionDrag';

/**
 * Durable Workspace (session group) metadata + membership mutations.
 *
 * Product rules implemented here:
 *  - A Session may belong to multiple workspaces. Moving it to a different
 *    workspace replaces its memberships (`[]` = ungrouped); deleting one
 *    workspace removes only that membership.
 *  - MANAGER CASCADE: moving a session that manages others moves every
 *    descendant with it (recursively, cycle-safe).
 *
 * Membership lives on the sessions themselves (`Session.workspaceIds`), so all
 * session mutations are applied optimistically through sessionStore and later
 * reconciled by the WebSocket membership snapshots.
 */
interface WorkspaceStoreState {
  workspaces: Workspace[];
  loaded: boolean;
  loading: boolean;
  error: string | null;

  loadWorkspaces: () => Promise<void>;
  createWorkspace: (name: string) => Promise<Workspace>;
  renameWorkspace: (id: string, name: string) => Promise<void>;
  deleteWorkspace: (id: string) => Promise<void>;
  /** Persist a full display order (drag-reorder of the rail tabs). */
  reorderWorkspaces: (orderedIds: string[]) => Promise<void>;
  /**
   * Move sessions (plus their managed descendants) into `workspaceId`, or out
   * of every workspace when null. Returns the ids that actually changed.
   */
  moveSessions: (sessionIds: string[], workspaceId: string | null) => Promise<string[]>;
  /** Workspace event snapshot: the complete member list of one workspace. */
  applyMembership: (workspaceId: string, memberIds: string[]) => void;
  /** session.workspaceUpdated snapshot for one session. */
  applySessionMembership: (sessionId: string, workspaceIds: string[]) => void;
  reset: () => void;
}

/** Backend ordering: explicit order first, then creation time, then id. */
function sortWorkspaces(a: Workspace, b: Workspace): number {
  const aOrdered = a.order !== null && a.order !== undefined;
  const bOrdered = b.order !== null && b.order !== undefined;
  if (aOrdered !== bOrdered) return aOrdered ? -1 : 1;
  if (aOrdered && bOrdered && a.order !== b.order) return (a.order as number) - (b.order as number);
  const created = String(a.createdAt ?? '').localeCompare(String(b.createdAt ?? ''));
  if (created !== 0) return created;
  return a.id.localeCompare(b.id);
}

/** Ids of the given sessions plus every managed descendant (cycle-safe). */
function withManagedDescendants(sessionIds: string[]): string[] {
  const sessions = useSessionStore.getState().sessions;
  const edges = buildManagerEdges(sessions);
  const out = new Set<string>();
  for (const id of sessionIds) {
    out.add(id);
    for (const descendant of collectDescendants(edges, id)) out.add(descendant);
  }
  return [...out];
}

export const useWorkspaceStore = create<WorkspaceStoreState>((set) => ({
  workspaces: [],
  loaded: false,
  loading: false,
  error: null,

  loadWorkspaces: async () => {
    set({ loading: true });
    try {
      const workspaces = await api.fetchWorkspaces();
      set({ workspaces: [...workspaces].sort(sortWorkspaces), loaded: true, loading: false, error: null });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : 'Failed to load workspaces',
      });
    }
  },

  createWorkspace: async (name) => {
    const workspace = await api.createWorkspace(name);
    set((s) => ({
      workspaces: [...s.workspaces.filter((w) => w.id !== workspace.id), workspace].sort(sortWorkspaces),
    }));
    return workspace;
  },

  renameWorkspace: async (id, name) => {
    const workspace = await api.renameWorkspace(id, name);
    set((s) => ({
      workspaces: s.workspaces.map((w) => (w.id === id ? { ...w, ...workspace } : w)),
    }));
  },

  deleteWorkspace: async (id) => {
    await api.deleteWorkspace(id);
    set((s) => ({ workspaces: s.workspaces.filter((w) => w.id !== id) }));
    // The server strips the membership; mirror it locally so the list and the
    // badges stop showing a workspace that no longer exists.
    const sessionStore = useSessionStore.getState();
    for (const session of sessionStore.sessions) {
      if ((session.workspaceIds ?? []).includes(id)) {
        sessionStore.updateSession(session.id, {
          workspaceIds: (session.workspaceIds ?? []).filter((workspaceId) => workspaceId !== id),
        });
      }
    }
  },

  reorderWorkspaces: async (orderedIds) => {
    const previous = useWorkspaceStore.getState().workspaces;
    const byId = new Map(previous.map((w) => [w.id, w]));
    const next = orderedIds.map((id) => byId.get(id)).filter((w): w is Workspace => !!w);
    if (next.length !== orderedIds.length) return;
    set({ workspaces: next });   // optimistic: the rail must not jump while dragging
    try {
      await api.saveWorkspaceOrder(orderedIds);
    } catch (e) {
      set({ workspaces: previous, error: e instanceof Error ? e.message : 'Reorder failed' });
      throw e;
    }
  },

  moveSessions: async (sessionIds, workspaceId) => {
    const targets = withManagedDescendants(sessionIds);
    const membership = workspaceId ? [workspaceId] : [];
    const sessionStore = useSessionStore.getState();
    const changed: string[] = [];
    for (const id of targets) {
      const session = sessionStore.sessions.find((s) => s.id === id);
      const current = session?.workspaceIds ?? [];
      // Selecting a workspace the session already belongs to must not discard
      // any of its other memberships. Explicitly moving elsewhere still
      // replaces the membership set with the selected workspace.
      const same = workspaceId === null
        ? current.length === 0
        : current.includes(workspaceId);
      if (same) continue;   // no-op moves stay silent
      await api.setSessionWorkspaces(id, membership);
      sessionStore.updateSession(id, { workspaceIds: [...membership] });
      changed.push(id);
    }
    return changed;
  },

  applyMembership: (workspaceId, memberIds) => {
    const members = new Set(memberIds);
    const sessionStore = useSessionStore.getState();
    for (const session of sessionStore.sessions) {
      const has = (session.workspaceIds ?? []).includes(workspaceId);
      const should = members.has(session.id);
      if (has === should) continue;
      const next = should
        ? [...(session.workspaceIds ?? []), workspaceId]
        : (session.workspaceIds ?? []).filter((id) => id !== workspaceId);
      sessionStore.updateSession(session.id, { workspaceIds: next });
    }
  },

  applySessionMembership: (sessionId, workspaceIds) => {
    useSessionStore.getState().updateSession(sessionId, { workspaceIds: [...workspaceIds] });
  },

  reset: () => set({ workspaces: [], loaded: false, loading: false, error: null }),
}));
