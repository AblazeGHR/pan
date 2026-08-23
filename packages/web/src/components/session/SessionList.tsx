import { useEffect, useMemo } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { SessionItem } from './SessionItem';
import type { Session } from '@/types';
import { FolderOpen } from 'lucide-react';

interface SessionListProps {
  onSessionClick?: (id: string) => void;
  onSessionMenu?: (e: React.MouseEvent, id: string) => void;
}

function stripPrefix(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/$/, '');
}

// ── Manager grouping (tree) ──

interface ManagerNode {
  session: Session;
  children: ManagerNode[];
}

/**
 * Build a forest from sessions by `managedBy`. Each node's children are the
 * sessions it manages. Cycles (degenerate data) are broken by promoting the
 * offending node to a root so rendering never recurses infinitely.
 */
function buildManagerTree(sessions: Session[]): ManagerNode[] {
  const nodeMap = new Map<string, ManagerNode>();
  for (const s of sessions) {
    nodeMap.set(s.id, { session: s, children: [] });
  }

  // Parent edge per session (only when the manager exists in this list).
  const parentOf = new Map<string, string>();
  for (const s of sessions) {
    const p = s.managedBy;
    if (p && p !== s.id && nodeMap.has(p)) {
      parentOf.set(s.id, p);
    }
  }

  // Break cycles: if walking parent links from a node revisits it, the node
  // is on a managedBy cycle — drop its parent edge so it becomes a root.
  for (const id of [...parentOf.keys()]) {
    const seen = new Set<string>();
    let cur: string | undefined = id;
    while (cur) {
      if (seen.has(cur)) {
        parentOf.delete(id);
        break;
      }
      seen.add(cur);
      cur = parentOf.get(cur);
    }
  }

  for (const s of sessions) {
    const p = parentOf.get(s.id);
    if (p) {
      const parent = nodeMap.get(p);
      if (parent) parent.children.push(nodeMap.get(s.id)!);
    }
  }

  return sessions
    .filter((s) => !parentOf.has(s.id))
    .map((s) => nodeMap.get(s.id)!);
}

/** All descendant ids of a tree node (excluding the node itself). */
function collectDescendantIds(node: ManagerNode): string[] {
  const ids: string[] = [];
  const walk = (n: ManagerNode) => {
    for (const c of n.children) {
      ids.push(c.session.id);
      walk(c);
    }
  };
  walk(node);
  return ids;
}

export function SessionList({ onSessionClick, onSessionMenu }: SessionListProps) {
  const sessions = useSessionStore((s) => s.sessions);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const multiSelectMode = useSessionStore((s) => s.multiSelectMode);
  const selectedIds = useSessionStore((s) => s.selectedIds);
  const selectSession = useSessionStore((s) => s.selectSession);
  const toggleSelection = useSessionStore((s) => s.toggleSelection);

  const { groupBy, searchQuery, sortBy, collapsedGroups, toggleGroupCollapse, addCollapsedGroups, removeCollapsedGroups, pruneCollapsedGroups } =
    useUIStore();

  // Keep collapsedGroups consistent with the live tree: drop stale keys left
  // behind by session placeholders (`__pending_*`) or deleted sessions so a
  // newly-joined manager group toggles immediately without a refresh.
  useEffect(() => {
    if (groupBy !== 'manager' && groupBy !== 'workdir') return;
    const valid = new Set<string>();
    if (groupBy === 'manager') {
      for (const s of sessions) valid.add(s.id);
    } else {
      for (const s of sessions) {
        if (s.workdir) valid.add(stripPrefix(s.workdir));
      }
      valid.add('__no_workdir');
    }
    pruneCollapsedGroups(valid);
  }, [sessions, groupBy, pruneCollapsedGroups]);

  const { filtered, grouped, managerTree } = useMemo(() => {
    let filtered = [...sessions];

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.workdir?.toLowerCase().includes(q) ||
          s.adapter?.toLowerCase().includes(q),
      );
    }

    filtered.sort((a, b) => {
      if (sortBy === 'name') {
        return a.name.localeCompare(b.name);
      }
      const aTime = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
      const bTime = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
      if (aTime !== bTime) return bTime - aTime;
      return a.name.localeCompare(b.name);
    });

    const groups: { key: string; label: string; sessions: Session[] }[] = [];
    if (groupBy === 'workdir') {
      const map = new Map<string, Session[]>();
      const uncategorized: Session[] = [];

      for (const s of filtered) {
        if (s.workdir) {
          const dir = stripPrefix(s.workdir);
          const existing = map.get(dir);
          if (existing) {
            existing.push(s);
          } else {
            map.set(dir, [s]);
          }
        } else {
          uncategorized.push(s);
        }
      }

      const dirs = [...map.entries()].sort((a, b) => {
        const diff = b[1].length - a[1].length;
        return diff !== 0 ? diff : a[0].localeCompare(b[0]);
      });

      for (const [dir, sess] of dirs) {
        groups.push({ key: dir, label: dir, sessions: sess });
      }

      if (uncategorized.length > 0) {
        groups.push({ key: '__no_workdir', label: 'No working directory', sessions: uncategorized });
      }
    }

    const managerTree = groupBy === 'manager' ? buildManagerTree(filtered) : [];

    return { filtered, grouped: groups, managerTree };
  }, [sessions, searchQuery, sortBy, groupBy]);

  // Recursive collapse/expand: collapsing a manager node also collapses every
  // descendant; expanding it expands the whole subtree (same for un-collapse).
  const handleToggleManagerNode = (node: ManagerNode) => {
    const ids = [node.session.id, ...collectDescendantIds(node)];
    const isCollapsed = collapsedGroups.has(node.session.id);
    if (isCollapsed) {
      removeCollapsedGroups(ids);
    } else {
      addCollapsedGroups(ids);
    }
  };

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 gap-3">
        <div className="text-text-tertiary opacity-50">
          <FolderOpen size={40} />
        </div>
        <p className="text-sm text-text-tertiary">No sessions yet</p>
        <p className="text-xs text-text-tertiary text-center">
          Click <span className="text-accent">+ New</span> to create your first session
        </p>
      </div>
    );
  }

  if (filtered.length === 0 && searchQuery.trim()) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 gap-2">
        <p className="text-sm text-text-tertiary">No matching sessions</p>
        <p className="text-xs text-text-tertiary">Try a different search term</p>
      </div>
    );
  }

  if (groupBy === 'manager' && managerTree.length > 0) {
    return (
      <div className="flex flex-col">
        {managerTree.map((node) => (
          <ManagerNodeView
            key={node.session.id}
            node={node}
            currentSessionId={currentSessionId}
            selectedIds={selectedIds}
            multiSelectMode={multiSelectMode}
            collapsedGroups={collapsedGroups}
            onSelect={(id) => {
              if (multiSelectMode) {
                toggleSelection(id);
              } else if (!id.startsWith('__pending_')) {
                selectSession(id);
                onSessionClick?.(id);
              }
            }}
            onMenu={(e, id) => onSessionMenu?.(e, id)}
            onToggle={handleToggleManagerNode}
          />
        ))}
      </div>
    );
  }

  if (groupBy === 'workdir' && grouped.length > 0) {
    return (
      <div className="flex flex-col">
        {grouped.map((group) => (
          <GroupSection
            key={group.key}
            label={group.label}
            count={group.sessions.length}
            collapsed={collapsedGroups.has(group.key)}
            onToggle={() => toggleGroupCollapse(group.key)}
          >
            {group.sessions.map((session) => (
              <SessionItem
                key={session.id}
                session={session}
                isActive={session.id === currentSessionId}
                isSelected={selectedIds.has(session.id)}
                multiSelectMode={multiSelectMode}
                onSelect={() => {
                  if (multiSelectMode) {
                    toggleSelection(session.id);
                  } else if (!session.id.startsWith('__pending_')) {
                    selectSession(session.id);
                    onSessionClick?.(session.id);
                  }
                }}
                onMenu={(e) => onSessionMenu?.(e, session.id)}
              />
            ))}
          </GroupSection>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {filtered.map((session) => (
        <SessionItem
          key={session.id}
          session={session}
          isActive={session.id === currentSessionId}
          isSelected={selectedIds.has(session.id)}
          multiSelectMode={multiSelectMode}
          onSelect={() => {
            if (multiSelectMode) {
              toggleSelection(session.id);
            } else if (!session.id.startsWith('__pending_')) {
              selectSession(session.id);
              onSessionClick?.(session.id);
            }
          }}
          onMenu={(e) => onSessionMenu?.(e, session.id)}
        />
      ))}
    </div>
  );
}

interface ManagerNodeViewProps {
  node: ManagerNode;
  currentSessionId: string | null;
  selectedIds: Set<string>;
  multiSelectMode: boolean;
  collapsedGroups: Set<string>;
  onSelect: (id: string) => void;
  onMenu?: (e: React.MouseEvent, id: string) => void;
  onToggle: (node: ManagerNode) => void;
}

/**
 * Recursive render of one manager-group node. The node renders as a normal
 * SessionItem (its name + status dot double as the group header) plus a
 * collapse chevron when it manages children. Children render indented below.
 */
function ManagerNodeView({
  node,
  currentSessionId,
  selectedIds,
  multiSelectMode,
  collapsedGroups,
  onSelect,
  onMenu,
  onToggle,
}: ManagerNodeViewProps) {
  const session = node.session;
  const hasChildren = node.children.length > 0;
  const collapsed = collapsedGroups.has(session.id);

  return (
    <div>
      <SessionItem
        session={session}
        isActive={session.id === currentSessionId}
        isSelected={selectedIds.has(session.id)}
        multiSelectMode={multiSelectMode}
        expandable={hasChildren && !multiSelectMode}
        expanded={!collapsed}
        onToggleChildren={(e) => {
          e.stopPropagation();
          onToggle(node);
        }}
        onSelect={() => onSelect(session.id)}
        onMenu={(e) => onMenu?.(e, session.id)}
      />
      {hasChildren && !collapsed && (
        <div className="ml-3 border-l border-text-tertiary">
          {node.children.map((child) => (
            <div key={child.session.id} className="relative">
              {/* Tree connector: horizontal tick from the left guide into this
                  child row (├─ style), making the parent→child ownership
                  visually explicit. `top-1/2 -translate-y-1/2` centers it on
                  the card row (aligned with the WorkerDot, which is vertically
                  centered via SessionItem's `items-center`), instead of a
                  hardcoded offset. Color uses text-tertiary — visible in both
                  light (#8e96a1) and dark (#6e7681) themes, unlike the
                  near-invisible border-muted. */}
              <span
                aria-hidden
                className="pointer-events-none absolute -left-3 top-1/2 -translate-y-1/2 h-px w-6 bg-text-tertiary"
              />
              <ManagerNodeView
                node={child}
                currentSessionId={currentSessionId}
                selectedIds={selectedIds}
                multiSelectMode={multiSelectMode}
                collapsedGroups={collapsedGroups}
                onSelect={onSelect}
                onMenu={onMenu}
                onToggle={onToggle}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface GroupSectionProps {
  label: string;
  count: number;
  children: React.ReactNode;
  collapsed: boolean;
  onToggle: () => void;
}

function GroupSection({ label, count, children, collapsed, onToggle }: GroupSectionProps) {
  const shortLabel = useMemo(() => {
    const parts = label.replace(/\\/g, '/').split('/').filter(Boolean);
    if (parts.length <= 2) return label;
    return `…/${parts.slice(-2).join('/')}`;
  }, [label]);

  return (
    <div>
      <div
        className="flex items-center gap-1.5 px-3 py-1.5 bg-bg-tertiary/50 border-b border-border-muted sticky top-0 z-[1] cursor-pointer hover:bg-bg-hover/30 transition-colors select-none"
        onClick={onToggle}
      >
        <span className="text-[10px] text-text-tertiary transition-transform shrink-0" style={{ transform: collapsed ? '' : 'rotate(90deg)' }}>
          ▶
        </span>
        <FolderOpen size={11} className="text-text-tertiary shrink-0" />
        <span className="text-[11px] text-text-tertiary font-medium truncate" title={label}>
          {shortLabel}
        </span>
        <span className="text-[10px] text-text-tertiary bg-bg-tertiary rounded-full px-1.5 ml-auto shrink-0">
          {count}
        </span>
      </div>
      {!collapsed && children}
    </div>
  );
}
