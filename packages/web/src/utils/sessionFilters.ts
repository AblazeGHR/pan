import type { Session } from '@/types';

/**
 * Special (non-text) session-list filters, rendered as checkboxes next to the
 * search box. Each filter is judged on real Session fields — never on
 * display text.
 */
export type SpecialFilterId = 'subagent' | 'metaagent';

export interface SpecialFilterOption {
  id: SpecialFilterId;
  /** List-label shown in the filter menu (English UI, consistent with the app). */
  label: string;
  /** One-line hint rendered under the label. */
  description: string;
}

export const SPECIAL_FILTERS: SpecialFilterOption[] = [
  {
    id: 'subagent',
    label: 'Has subagent',
    description: 'Session manages at least one other session',
  },
  {
    id: 'metaagent',
    label: 'Is MetaAgent',
    description: 'Session mounts the pan MCP server',
  },
];

/**
 * True when the session manages at least one other session ("has subagent").
 *
 * Primary signal is the `managed` id array. As a fallback for payloads that
 * omit it (e.g. a placeholder merged locally or an older summary), we derive
 * the same relation from `managedBy` back-references — the exact source the
 * manager-group tree uses — so the filter stays correct either way.
 */
export function hasSubagents(session: Session, allSessions: Session[]): boolean {
  if (session.managed && session.managed.length > 0) return true;
  if (allSessions.some((s) => s.id !== session.id && s.managedBy === session.id)) {
    return true;
  }
  return false;
}

/**
 * True when the session mounts the Pan MCP server and that MCP is effective
 * ("is MetaAgent").
 *
 * `mcpServers` lists currently-enabled server names. A template locked to
 * `mcp_mode: "never"` (mcpLockReason === "never") disables MCP entirely, so a
 * stale server entry must not count as an active MetaAgent mount.
 */
export function isMetaAgent(session: Session): boolean {
  if (!session.mcpServers?.includes('pan')) return false;
  if (session.mcpLockReason === 'never') return false;
  return true;
}

/** True when the session matches the shared Session-list search input. */
export function matchesSessionSearch(session: Session, query: string): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return true;

  return [session.name, session.id, session.cliSessionId, session.workdir, session.adapter]
    .some((value) => String(value ?? '').toLowerCase().includes(normalizedQuery));
}

/** True when the session passes every active special filter. */
export function matchesSpecialFilters(
  session: Session,
  allSessions: Session[],
  filters: Set<SpecialFilterId>,
): boolean {
  if (filters.size === 0) return true;
  if (filters.has('subagent') && !hasSubagents(session, allSessions)) return false;
  if (filters.has('metaagent') && !isMetaAgent(session)) return false;
  return true;
}

/** Workspace scope key: 'all' / 'ungrouped' / a workspace id. */
export const ALL_WORKSPACES = 'all';
export const UNGROUPED_WORKSPACES = 'ungrouped';

/** Resolve a session's workspace from its manager chain; corrupt chains fail closed. */
export function effectiveWorkspaceIds(session: Session, sessions: Session[]): string[] {
  const byId = new Map(sessions.map((item) => [item.id, item]));
  const seen = new Set<string>();
  let current: Session | undefined = session;
  while (current) {
    if (seen.has(current.id)) return [];
    seen.add(current.id);
    if (!current.managedBy) return (current.workspaceIds ?? []).slice(0, 1);
    current = byId.get(current.managedBy);
  }
  return [];
}

/**
 * Sessions belonging to the active workspace scope.
 *   - 'all' / missing      → every session (the historical, unscoped list);
 *   - 'ungrouped'          → sessions without any membership;
 *   - a workspace id       → members of that workspace only.
 *
 * The workspace scope is the OUTERMOST filter: search, special filters and
 * select-all all run on top of it, so no list-level operation can ever reach
 * across workspaces.
 */
export function scopeSessionsByWorkspace(
  sessions: Session[],
  activeWorkspaceId?: string | null,
): Session[] {
  if (!activeWorkspaceId || activeWorkspaceId === ALL_WORKSPACES) return sessions;
  if (activeWorkspaceId === UNGROUPED_WORKSPACES) {
    return sessions.filter((session) => effectiveWorkspaceIds(session, sessions).length === 0);
  }
  return sessions.filter((session) => effectiveWorkspaceIds(session, sessions).includes(activeWorkspaceId));
}

/**
 * Return the sessions that SessionList can operate on for the current view.
 * Select mode deliberately includes sessions hidden from the normal list,
 * while search and special filters keep their usual meaning. Grouping and
 * collapsed groups are intentionally not inputs: selection covers the full
 * filtered result, not only cards currently present in the DOM.
 */
export function getSessionListCandidates(
  sessions: Session[],
  options: {
    multiSelectMode: boolean;
    hiddenSessionIds: Set<string>;
    searchQuery: string;
    specialFilters: Set<SpecialFilterId>;
    /** Active workspace scope ('all' keeps the historical full list). */
    activeWorkspaceId?: string | null;
  },
): Session[] {
  const inScope = scopeSessionsByWorkspace(sessions, options.activeWorkspaceId);
  const base = options.multiSelectMode
    ? inScope
    : inScope.filter((session) => !options.hiddenSessionIds.has(session.id));

  let candidates = base.filter((session) =>
    matchesSpecialFilters(session, sessions, options.specialFilters),
  );
  if (options.searchQuery.trim()) {
    candidates = candidates.filter((session) =>
      matchesSessionSearch(session, options.searchQuery),
    );
  }
  return candidates;
}
