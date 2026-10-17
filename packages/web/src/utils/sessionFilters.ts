import type { Session, Workspace } from '@/types';

/**
 * Special (non-text) session-list filters, rendered as checkboxes next to the
 * search box. Each filter is judged on real Session fields — never on
 * display text.
 */
export type SpecialFilterId = 'subagent' | 'metaagent' | 'running' | 'illegal' | 'unread' | 'queue';

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
  {
    id: 'running',
    label: 'Running state',
    description: 'Actual Worker runtime status is running',
  },
  {
    id: 'illegal',
    label: 'Illegal state',
    description: 'Legal state is running, but actual Worker status is not',
  },
  {
    id: 'unread',
    label: 'Has unread',
    description: 'Unread done count is greater than zero',
  },
  {
    id: 'queue',
    label: 'Has queued messages',
    description: 'Pending queue count is greater than zero',
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
  const coreEnabled = session.pluginBindings
    ? session.pluginBindings.some((binding) => binding.plugin_id === 'pan' && binding.enabled)
    : session.mcpServers?.includes('pan');
  if (!coreEnabled) return false;
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
  if (filters.has('running') && session.workerStatus !== 'running') return false;
  if (
    filters.has('illegal') &&
    !(session.lastLegalWorkerState === 'running' && session.workerStatus !== 'running')
  ) return false;
  if (
    filters.has('unread') &&
    !(typeof session.unreadDoneCount === 'number' && session.unreadDoneCount > 0)
  ) return false;
  if (
    filters.has('queue') &&
    !(typeof session.queuePendingCount === 'number' && session.queuePendingCount > 0)
  ) return false;
  return true;
}

/** Workspace scope key: 'all' / 'ungrouped' / a workspace id. */
export const ALL_WORKSPACES = 'all';
export const UNGROUPED_WORKSPACES = 'ungrouped';

/**
 * Ids of every Session effectively belonging to a Workspace, resolved through
 * the manager chain exactly like {@link scopeSessionsByWorkspace} (descendants
 * inherit the root's membership; corrupt chains fail closed). Ids are returned
 * in input order; the keyword/status filters deliberately do NOT participate —
 * this is the workspace's real membership, not the visible filtered subset.
 */
export function workspaceMemberIds(sessions: Session[], workspaceId: string): Set<string> {
  const ids = new Set<string>();
  const resolve = createWorkspaceResolver(sessions);
  for (const session of sessions) {
    if (resolve(session).includes(workspaceId)) ids.add(session.id);
  }
  return ids;
}

/**
 * A session's effective workspace ownership AND whether it is provable.
 *
 * The ids alone cannot tell "a root with no workspace" apart from "a broken
 * manager chain", yet the difference matters: only the former is a *provable*
 * absence of membership. A dangling manager reference or a cycle is
 * 'indeterminate' — the caller must not treat it as ungrouped.
 */
export type WorkspaceOwnership =
  /** Determinate root that carries no workspace id: provably ungrouped. */
  | { kind: 'none' }
  /** Determinate root that carries a workspace id. */
  | { kind: 'workspace'; id: string }
  /** A dangling manager reference or a cycle: ownership cannot be proven. */
  | { kind: 'indeterminate' };

/** Resolve a session's workspace from its manager chain; corrupt chains fail closed. */
export function effectiveWorkspaceIds(session: Session, sessions: Session[]): string[] {
  return createWorkspaceResolver(sessions)(session);
}

/** Effective ownership WITH provenance (see {@link WorkspaceOwnership}). */
export function resolveWorkspaceOwnership(session: Session, sessions: Session[]): WorkspaceOwnership {
  return createOwnershipResolver(sessions)(session);
}

/**
 * One index and path cache per derivation; never retained across store
 * snapshots. The single chain walk: descendants inherit the root's membership;
 * a dangling manager or a cycle is 'indeterminate'.
 */
export function createOwnershipResolver(sessions: Session[]): (session: Session) => WorkspaceOwnership {
  const byId = new Map(sessions.map((item) => [item.id, item]));
  const cache = new Map<Session, WorkspaceOwnership>();
  return (session) => {
    const path: Session[] = [];
    const seen = new Set<string>();
    let current: Session | undefined = session;
    let result: WorkspaceOwnership = { kind: 'indeterminate' };
    while (current) {
      const known = cache.get(current);
      if (known) { result = known; break; }
      if (seen.has(current.id)) { result = { kind: 'indeterminate' }; break; }
      seen.add(current.id);
      path.push(current);
      if (!current.managedBy) {
        const [id] = current.workspaceIds ?? [];
        result = id ? { kind: 'workspace', id } : { kind: 'none' };
        break;
      }
      current = byId.get(current.managedBy);
    }
    path.forEach(item => cache.set(item, result));
    return result;
  };
}

/**
 * Ids-only view of {@link createOwnershipResolver}: a determinate root's
 * workspace id, or `[]` for anything else (ungrouped OR an unprovable chain —
 * the historical, fail-closed behavior the session-list scope relies on).
 */
export function createWorkspaceResolver(sessions: Session[]): (session: Session) => string[] {
  const resolveOwnership = createOwnershipResolver(sessions);
  return (session) => {
    const ownership = resolveOwnership(session);
    return ownership.kind === 'workspace' ? [ownership.id] : [];
  };
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
  const resolve = createWorkspaceResolver(sessions);
  if (activeWorkspaceId === UNGROUPED_WORKSPACES) {
    return sessions.filter((session) => resolve(session).length === 0);
  }
  return sessions.filter((session) => resolve(session).includes(activeWorkspaceId));
}

/**
 * True when the Workspace name matches the shared Session-list search input.
 *
 * Same normalization as {@link matchesSessionSearch} (trim + case-insensitive
 * substring; an empty query matches everything), but judged on the Workspace
 * name only — the rail reuses the existing search box instead of adding one.
 */
export function matchesWorkspaceSearch(workspace: Pick<Workspace, 'name'>, query: string): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return true;
  return String(workspace.name ?? '').toLowerCase().includes(normalizedQuery);
}

/**
 * True when a Workspace satisfies every active special filter at the Workspace
 * level: EACH selected filter must be met by at least one of the Workspace's
 * effective members, but a single Session need not satisfy them all. Selecting
 * `running` + `unread` therefore matches a Workspace whose member A is running
 * and whose member B has unread — exactly the Workspace-level AND of the
 * per-filter any() results.
 *
 * Every filter is judged through the existing single-filter predicate
 * {@link matchesSpecialFilters} rather than re-deriving its state definition.
 * `members` must be the workspace's EFFECTIVE member sessions — the caller
 * resolves them through the manager chain ({@link effectiveWorkspaceIds}), so
 * descendants inherit the root's workspace. Each member is judged on its
 * ACTUAL state: the aggregation never runs over the keyword-filtered Session
 * list, so a session hidden from that list by the search keyword still
 * contributes its real state here. The Workspace result is therefore computed
 * independently from the Session-list result while both share the same filter
 * conditions. The keyword never participates in this aggregation.
 */
export function workspaceMatchesSpecialFilters(
  members: Session[],
  allSessions: Session[],
  filters: Set<SpecialFilterId>,
): boolean {
  if (filters.size === 0) return true;
  for (const filter of filters) {
    const single = new Set<SpecialFilterId>([filter]);
    if (!members.some((session) => matchesSpecialFilters(session, allSessions, single))) return false;
  }
  return true;
}

/** Which workspace-rail rows stay visible under its name query and shared filters. */
export interface WorkspaceRowVisibility {
  /** Real workspace rows to render, in store order. */
  workspaces: Workspace[];
  /** Virtual 'all' scope row (全部) stays visible. */
  showAll: boolean;
  /** Virtual 'ungrouped' scope row (无工作区) stays visible. */
  showUngrouped: boolean;
}

/**
 * Compute workspace-rail visibility from its own name query and shared status filters:
 *
 * - A real workspace row shows when its NAME matches the keyword AND the
 *   workspace satisfies every active status filter ({@link
 *   workspaceMatchesSpecialFilters}: per-filter any() over the ACTUAL effective
 *   members, then AND — never derived from the keyword-filtered Session list).
 * - Virtual scopes ignore the workspace keyword (they have no workspace name).
 *   Their existing per-Session status matching stays unchanged: 'all' covers
 *   every session, 'ungrouped' only sessions without membership.
 * - Collapsed, or with no keyword and no status filter active, every row
 *   keeps its original visibility.
 *
 * `membersByWorkspace` / `ungroupedMembers` are the caller's already-resolved
 * effective memberships (see {@link effectiveWorkspaceIds}); passing them in
 * keeps this a single-pass derivation over the Session list.
 */
export function computeWorkspaceRowVisibility(
  workspaces: Workspace[],
  sessions: Session[],
  options: {
    /** Rail expanded (desktop panel or mobile drawer). Collapsed keeps every row. */
    expanded: boolean;
    searchQuery: string;
    specialFilters: Set<SpecialFilterId>;
    /** Effective member sessions per workspace id (inheritance applied). */
    membersByWorkspace: Map<string, Session[]>;
    /** Sessions with no effective workspace membership. */
    ungroupedMembers: Session[];
  },
): WorkspaceRowVisibility {
  const everything: WorkspaceRowVisibility = { workspaces, showAll: true, showUngrouped: true };
  if (!options.expanded) return everything;
  if (!options.searchQuery.trim() && options.specialFilters.size === 0) return everything;

  const visibleWorkspaces = workspaces.filter(
    (workspace) =>
      matchesWorkspaceSearch(workspace, options.searchQuery) &&
      workspaceMatchesSpecialFilters(
        options.membersByWorkspace.get(workspace.id) ?? [],
        sessions,
        options.specialFilters,
      ),
  );
  const sessionMatches = (session: Session) =>
    matchesSpecialFilters(session, sessions, options.specialFilters);
  return {
    workspaces: visibleWorkspaces,
    showAll: sessions.some(sessionMatches),
    showUngrouped: options.ungroupedMembers.some(sessionMatches),
  };
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
    matchingSessionIds?: Set<string> | null;
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
  if (options.matchingSessionIds) {
    candidates = candidates.filter((session) => options.matchingSessionIds!.has(session.id));
  }
  return candidates;
}
