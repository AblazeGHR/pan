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
