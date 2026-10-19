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
 * Per-snapshot relation index used by the list and workspace projections.
 * Building the reverse managed-by relation once keeps the subagent filter
 * linear in the number of Sessions instead of rescanning the full list for
 * every candidate.
 */
export interface SessionFilterContext {
  sessionsWithSubagents: ReadonlySet<string>;
}

export function createSessionFilterContext(sessions: Session[]): SessionFilterContext {
  const sessionsWithSubagents = new Set<string>();
  for (const session of sessions) {
    if (session.managed?.length) sessionsWithSubagents.add(session.id);
    if (session.managedBy && session.managedBy !== session.id) {
      sessionsWithSubagents.add(session.managedBy);
    }
  }
  return { sessionsWithSubagents };
}

/**
 * True when the session manages at least one other session ("has subagent").
 *
 * Primary signal is the `managed` id array. As a fallback for payloads that
 * omit it (e.g. a placeholder merged locally or an older summary), we derive
 * the same relation from `managedBy` back-references — the exact source the
 * manager-group tree uses — so the filter stays correct either way.
 */
export function hasSubagents(
  session: Session,
  allSessions: Session[],
  context?: SessionFilterContext,
): boolean {
  if (session.managed && session.managed.length > 0) return true;
  if (context) return context.sessionsWithSubagents.has(session.id);
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
  context?: SessionFilterContext,
): boolean {
  if (filters.size === 0) return true;
  if (filters.has('subagent') && !hasSubagents(session, allSessions, context)) return false;
  if (filters.has('metaagent') && !isMetaAgent(session)) return false;
  if (filters.has('running') && session.workerStatus !== 'running') return false;
  if (
    filters.has('illegal') &&
    session.illegalOffline !== true
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

/**
 * Adapter-group filter ids are the Sessions' own adapter names, collected from
 * the live session list — never a hardcoded registry that can go stale.
 */
export type AdapterFilterId = string;

/**
 * Distinct adapter names present on the given sessions, in stable order.
 *
 * Derived from the real list being filtered, so an option always has at least
 * one session behind it and a newly appearing adapter shows up on its own.
 * Blank/missing adapters are skipped: they cannot be expressed as a selection.
 */
export function collectAdapterOptions(sessions: Session[]): AdapterFilterId[] {
  const names = new Set<string>();
  for (const session of sessions) {
    const adapter = session.adapter?.trim();
    if (adapter) names.add(adapter);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

/**
 * True when the session passes the Adapter group.
 *
 * Selections inside the group are OR-ed (any selected adapter matches). An
 * empty — or absent, for legacy callers — selection filters nothing. A session
 * with no adapter can never match an active selection; a stale selection id
 * (an adapter that no longer exists) simply matches nothing.
 */
export function matchesAdapterFilter(
  session: Session,
  selectedAdapters?: Set<AdapterFilterId> | null,
): boolean {
  if (!selectedAdapters || selectedAdapters.size === 0) return true;
  const adapter = session.adapter?.trim();
  if (!adapter) return false;
  return selectedAdapters.has(adapter);
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
  context?: SessionFilterContext,
): boolean {
  if (filters.size === 0) return true;
  for (const filter of filters) {
    const single = new Set<SpecialFilterId>([filter]);
    if (!members.some((session) => matchesSpecialFilters(session, allSessions, single, context))) return false;
  }
  return true;
}

/**
 * Workspace-level Adapter aggregation, mirroring
 * {@link workspaceMatchesSpecialFilters}: the group is ONE condition with
 * OR semantics inside it, so the Workspace matches when ANY selected adapter
 * is carried by at least one effective member. An empty selection imposes no
 * constraint (the historical, unfiltered rail).
 */
export function workspaceMatchesAdapterFilter(
  members: Session[],
  selectedAdapters?: Set<AdapterFilterId> | null,
): boolean {
  if (!selectedAdapters || selectedAdapters.size === 0) return true;
  return members.some((session) => matchesAdapterFilter(session, selectedAdapters));
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
 *   members, then AND — never derived from the keyword-filtered Session list)
 *   AND the Adapter group ({@link workspaceMatchesAdapterFilter}: any selected
 *   adapter carried by some member; cross-group AND with the status filters).
 * - Virtual scopes ignore the workspace keyword (they have no workspace name).
 *   Their existing per-Session status matching stays unchanged: 'all' covers
 *   every session, 'ungrouped' only sessions without membership.
 * - Collapsed, or with no keyword and no status/adapter filter active, every
 *   row keeps its original visibility.
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
    /** Adapter-group selections (OR within the group, AND across groups). */
    adapterFilters?: Set<AdapterFilterId> | null;
    /** Effective member sessions per workspace id (inheritance applied). */
    membersByWorkspace: Map<string, Session[]>;
    /** Sessions with no effective workspace membership. */
    ungroupedMembers: Session[];
  },
): WorkspaceRowVisibility {
  const everything: WorkspaceRowVisibility = { workspaces, showAll: true, showUngrouped: true };
  if (!options.expanded) return everything;
  if (!options.searchQuery.trim() && options.specialFilters.size === 0 && !options.adapterFilters?.size) {
    return everything;
  }

  const filterContext = options.specialFilters.has('subagent')
    ? createSessionFilterContext(sessions)
    : undefined;

  const visibleWorkspaces = workspaces.filter(
    (workspace) =>
      matchesWorkspaceSearch(workspace, options.searchQuery) &&
      workspaceMatchesSpecialFilters(
        options.membersByWorkspace.get(workspace.id) ?? [],
        sessions,
        options.specialFilters,
        filterContext,
      ) &&
      workspaceMatchesAdapterFilter(
        options.membersByWorkspace.get(workspace.id) ?? [],
        options.adapterFilters,
      ),
  );
  const sessionMatches = (session: Session) =>
    matchesSpecialFilters(session, sessions, options.specialFilters, filterContext) &&
    matchesAdapterFilter(session, options.adapterFilters);
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
 *
 * Filter combination semantics (shared by every caller):
 *   - Adapter group: OR inside the group (any selected adapter);
 *   - State group (special filters): the existing AND semantics, unchanged;
 *   - Across groups (and with the keyword/workspace scope): AND.
 * `adapterFilters` is optional so legacy callers without the option keep
 * their exact previous behavior (no adapter filtering).
 */
export function getSessionListCandidates(
  sessions: Session[],
  options: {
    multiSelectMode: boolean;
    hiddenSessionIds: Set<string>;
    searchQuery: string;
    specialFilters: Set<SpecialFilterId>;
    /** Adapter-group selections; omitted/empty filters nothing. */
    adapterFilters?: Set<AdapterFilterId> | null;
    /** Active workspace scope ('all' keeps the historical full list). */
    activeWorkspaceId?: string | null;
    matchingSessionIds?: Set<string> | null;
  },
): Session[] {
  const inScope = scopeSessionsByWorkspace(sessions, options.activeWorkspaceId);
  const filterContext = options.specialFilters.has('subagent')
    ? createSessionFilterContext(sessions)
    : undefined;
  const base = options.multiSelectMode
    ? inScope
    : inScope.filter((session) => !options.hiddenSessionIds.has(session.id));

  let candidates = base.filter((session) =>
    matchesSpecialFilters(session, sessions, options.specialFilters, filterContext) &&
    matchesAdapterFilter(session, options.adapterFilters),
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
