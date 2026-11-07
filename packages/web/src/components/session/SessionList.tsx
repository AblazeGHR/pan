import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { SessionItem } from './SessionItem';
import { ALL_WORKSPACES, UNGROUPED_WORKSPACES, effectiveWorkspaceIds, getSessionListCandidates, scopeSessionsByWorkspace } from '@/utils/sessionFilters';
import { useHistorySearchViewStore } from '@/stores/historySearchViewStore';
import { CREATE_WORKSPACE_DROP_TARGET_ID, useWorkspaceStore } from '@/stores/workspaceStore';
import { resolveDropZone, decideManagerDrop, DRAG_START_THRESHOLD_PX, buildManagerEdges, collectDescendants, movePinnedSessionId, orderPinnedWithinGroups, sessionPinGroupKey } from './sessionDrag';
import type { DropZone } from './sessionDrag';
import { isMockMode, applyMockSessionUpdate } from '@/demo/mockBackend';
import { claimSession, unclaimSession, reorderSessions } from '@/services/api';
import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { FolderOpen, Loader2 } from 'lucide-react';
import { getAutoScrollDelta, findScrollableAncestor } from './sessionDragAutoScroll';
import { confirmWorkspaceManagerChange } from '@/utils/workspaceMoveConfirmation';

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

/** Full desired session order after an EDGE drop: the dragged session is
 *  removed and re-inserted right next to the target card (before/after), every
 *  session that is not visible in the DOM (e.g. collapsed/hidden) keeps its
 *  current relative position after the visible ones. */
function buildDropOrderIds(
  allSessions: Session[],
  listEl: HTMLElement | null,
  fallback: Session[],
  dragIds: string[],
  targetId: string,
  zone: DropZone,
): string[] {
  const isRealId = (id: string) => id.length > 0 && !id.startsWith('__pending_');
  const dragged = new Set(dragIds);
  const domCards = listEl?.querySelectorAll<HTMLElement>('[data-session-card-id]');
  const domIds = domCards
    ? [...domCards].map((el) => el.dataset.sessionCardId ?? '').filter(isRealId)
    : fallback.map((s) => s.id).filter(isRealId);
  // Keep the selection's visible order: the whole selection lands as one
  // contiguous block at the boundary the user saw.
  const insertIds = domIds.filter((id) => dragged.has(id));
  const ids = domIds.filter((id) => !dragged.has(id));
  const tIdx = ids.indexOf(targetId);
  if (tIdx >= 0) {
    ids.splice(zone === 'before' ? tIdx : tIdx + 1, 0, ...insertIds);
    for (const s of allSessions) {
      if (isRealId(s.id) && !ids.includes(s.id)) ids.push(s.id);
    }
  }
  return ids;
}

/** Selected ids that are actually rendered, in the list's visual order. */
function visibleSelectionDragIds(
  listEl: HTMLElement | null,
  selectedIds: Set<string>,
): string[] {
  const cards = listEl?.querySelectorAll<HTMLElement>('[data-session-card-id]');
  const ids: string[] = [];
  for (const el of cards ?? []) {
    const id = el.dataset.sessionCardId;
    if (id && selectedIds.has(id) && !id.startsWith('__pending_')) ids.push(id);
  }
  return ids;
}

interface RealDropMember {
  id: string;
  name: string;
  /** Current manager of this Session (null = top-level root). */
  oldManager: string | null;
  /** Manager this Session should have after the drop. */
  newManager: string | null;
}

interface RealDropParams {
  /** Every dragged Session (one for a normal drag, N for a selection drag). */
  members: RealDropMember[];
  targetName: string;
  /** 'center' → manage-only (no reorder); edge zones also reorder. */
  zone: DropZone;
  /** Full desired order (only meaningful for edge drops). */
  orderIds: string[];
}

/**
 * Real-backend persistence for a finished drag. Called ONLY outside mock mode.
 *
 * Local state is mutated only AFTER the server confirms, so a failed drop
 * never fakes success: on error we toast the server message and let the
 * authoritative list refresh reconcile any partial server-side effect
 * (e.g. unclaim succeeded but the follow-up claim failed).
 *
 * A selection drag runs the exact same per-member rules sequentially; the
 * first rejection aborts the remaining members and reconciles from the
 * server instead of fabricating success.
 */
async function persistSessionDrop(p: RealDropParams): Promise<void> {
  const sessionStore = useSessionStore.getState();
  const ui = useUIStore.getState();
  const { members, targetName, zone, orderIds } = p;
  const multi = members.length > 1;
  const changed = members.filter((member) => member.oldManager !== member.newManager);
  const findLabel = (id: string | null) =>
    id ? (sessionStore.sessions.find((s) => s.id === id)?.name ?? id) : null;

  const attachChanges = changed.filter((member) => member.newManager !== null);
  if (attachChanges.length > 0) {
    const first = attachChanges[0]!;
    const target = sessionStore.sessions.find((s) => s.id === first.id);
    const manager = sessionStore.sessions.find((s) => s.id === first.newManager);
    if (target && manager) {
      const currentWorkspace = effectiveWorkspaceIds(target, sessionStore.sessions)[0] ?? null;
      const destinationWorkspace = effectiveWorkspaceIds(manager, sessionStore.sessions)[0] ?? null;
      if (currentWorkspace !== destinationWorkspace && useAppSettingsStore.getState().notifications.confirmCrossWorkspaceManagement) {
        const edges = buildManagerEdges(sessionStore.sessions);
        const subtreeIds = new Set<string>();
        for (const member of members) {
          subtreeIds.add(member.id);
          for (const id of collectDescendants(edges, member.id)) subtreeIds.add(id);
        }
        const destinationName = useWorkspaceStore.getState().workspaces.find((w) => w.id === destinationWorkspace)?.name ?? '未分组';
        const accepted = await confirmWorkspaceManagerChange({
          changeType: 'attach',
          sessionName: multi ? `${first.name} 等 ${members.length} 个会话` : first.name,
          subtreeCount: multi ? subtreeIds.size : 1 + collectDescendants(edges, first.id).size,
          managerName: manager.name || manager.id,
          targetWorkspaceName: destinationName,
        });
        if (!accepted) return;
      }
    }
  }

  // 1) Management transition (B manage A / move between groups / leave group).
  //    Server enforces exclusivity, so changing managers = unclaim old first.
  for (const member of changed) {
    try {
      if (member.oldManager) await unclaimSession(member.oldManager, member.id);
      if (member.newManager) await claimSession(member.newManager, member.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '服务端拒绝';
      ui.showToast(
        multi ? `「${member.name}」管理关系更新失败：${msg}` : `管理关系更新失败：${msg}`,
        'error',
      );
      void sessionStore.loadSessions(); // reconcile any partial unclaim
      return;
    }
    sessionStore.updateSession(member.id, { managedBy: member.newManager });
  }

  if (changed.length === 1 && !multi) {
    const member = changed[0]!;
    if (member.newManager) {
      const managerName = zone === 'center' ? targetName : findLabel(member.newManager) ?? member.newManager;
      ui.showToast(
        zone === 'center'
          ? `「${targetName}」现在管理「${member.name}」`
          : `「${member.name}」已移入「${managerName}」的组`,
      );
    } else {
      ui.showToast(`「${member.name}」已移出「${findLabel(member.oldManager) ?? member.oldManager}」的管理`);
    }
  } else if (multi && changed.length > 0) {
    if (changed.every((member) => member.newManager !== null)) {
      const managerName = zone === 'center'
        ? targetName
        : findLabel(changed[0]!.newManager) ?? changed[0]!.newManager;
      ui.showToast(
        zone === 'center'
          ? `「${targetName}」现在管理 ${changed.length} 个会话`
          : `${changed.length} 个会话已移入「${managerName}」的组`,
      );
    } else if (changed.every((member) => member.newManager === null)) {
      ui.showToast(`${changed.length} 个会话已移出管理组`);
    } else {
      ui.showToast(`${changed.length} 个会话已更新管理关系`);
    }
  }

  // 2) Persist the display order (edge drops only) and adopt the custom sort
  //    mode, exactly like the mock demo. The returned order is authoritative.
  if (zone !== 'center') {
    try {
      const res = await reorderSessions(orderIds);
      const order = res.order && res.order.length > 0 ? res.order : orderIds;
      const u = useUIStore.getState();
      u.setCustomOrder(order);
      u.setSortBy('custom');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '排序失败';
      ui.showToast(`排序失败：${msg}`, 'error');
    }
  }

  // Reconcile the local list with the server snapshot (managed lists / order).
  void sessionStore.loadSessions();
}

export function SessionList({ onSessionClick, onSessionMenu }: SessionListProps) {
  const matchingSessionIds = useHistorySearchViewStore((state) => state.matchingSessionIds);
  const historySearchPreparing = useHistorySearchViewStore((state) => state.preparing);
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const multiSelectMode = useSessionStore((s) => s.multiSelectMode);
  const selectedIds = useSessionStore((s) => s.selectedIds);
  const workspaces = useWorkspaceStore((s) => s.workspaces);

  const {
    groupBy,
    searchQuery,
    sortBy,
    specialFilters,
    adapterFilters,
    hiddenSessionIds,
    collapsedGroups,
    customOrder,
    toggleGroupCollapse,
    pruneCollapsedGroups,
    pruneHiddenSessions,
    showToast,
    dragEnabled: dragPreference,
    activeWorkspaceId,
  } = useUIStore(useShallow((s) => ({
    groupBy: s.groupBy,
    searchQuery: s.searchQuery,
    sortBy: s.sortBy,
    specialFilters: s.specialFilters,
    adapterFilters: s.adapterFilters,
    hiddenSessionIds: s.hiddenSessionIds,
    collapsedGroups: s.collapsedGroups,
    customOrder: s.customOrder,
    toggleGroupCollapse: s.toggleGroupCollapse,
    pruneCollapsedGroups: s.pruneCollapsedGroups,
    pruneHiddenSessions: s.pruneHiddenSessions,
    showToast: s.showToast,
    dragEnabled: s.dragEnabled,
    activeWorkspaceId: s.activeWorkspaceId,
  })));
  const { defaultGroupBy, showGroupBy, settingsLoaded } = useAppSettingsStore(useShallow((s) => ({
    defaultGroupBy: s.defaultGroupBy,
    showGroupBy: s.showGroupBy,
    settingsLoaded: s.loaded,
  })));
  // Hiding the control also forces the list and drag grouping semantics to
  // Manager, even while settings are still hydrating or legacy UI state says
  // workdir/off. Keep this derived value synchronous to avoid a first-paint
  // flash of the old grouping.
  const effectiveGroupBy = showGroupBy ? groupBy : 'manager';

  // Default grouping: adopt the app-settings default as long as the user has
  // never manually picked a grouping (nothing persisted to pan:groupBy AND the
  // store is still at its untouched 'none'). Manual switches persist to
  // pan:groupBy and take precedence thereafter. The sync deliberately does NOT
  // persist, so each app entry re-reads the latest default until the user
  // groups manually.
  useLayoutEffect(() => {
    const ui = useUIStore.getState();
    if (!settingsLoaded) {
      // If a user opens Preferences before the initial settings GET settles,
      // showing the control still resumes from Manager instead of resurrecting
      // an old browser-local mode.
      if (showGroupBy && ui.groupBy !== 'manager') ui.setGroupBy('manager');
      return;
    }
    if (!showGroupBy) {
      if (ui.groupBy !== 'manager') ui.setGroupBy('manager');
      return;
    }
    if (localStorage.getItem('pan:groupBy') === null && ui.groupBy === 'none') {
      useUIStore.setState({ groupBy: defaultGroupBy });
    }
  }, [defaultGroupBy, showGroupBy, settingsLoaded]);

  // Keep collapsedGroups consistent with the live tree. The persisted set
  // holds BOTH key namespaces — manager mode keys are session ids, workdir
  // mode keys are normalized workdir paths — so the valid-key set here is the
  // UNION across modes: pruning by the current grouping only would delete the
  // other mode's saved collapse preferences on every groupBy switch. Guarded
  // on sessions.length > 0 (like pruneHiddenSessions below) so the pre-fetch
  // cold load never wipes persisted state. The valid set is built from the
  // full session store list — NOT the search/filter/workspace-filtered view —
  // so temporarily invisible sessions keep their collapse state; only keys
  // whose session no longer exists (stale `__pending_*` placeholders, deleted
  // sessions) or whose workdir has no remaining session are dropped.
  useEffect(() => {
    if (sessions.length === 0) return;
    const valid = new Set<string>();
    let hasNoWorkdir = false;
    for (const s of sessions) {
      valid.add(s.id);
      if (s.workdir) {
        valid.add(stripPrefix(s.workdir));
      } else {
        hasNoWorkdir = true;
      }
    }
    if (hasNoWorkdir) valid.add('__no_workdir');
    pruneCollapsedGroups(valid);
  }, [sessions, pruneCollapsedGroups]);

  // Keep hiddenSessionIds consistent with the live list: drop ids of deleted
  // sessions. Guarded on sessions.length > 0 so a fresh page load (empty list
  // before the first fetch resolves) never wipes persisted hides.
  useEffect(() => {
    if (sessions.length === 0) return;
    pruneHiddenSessions(new Set(sessions.map((s) => s.id)));
  }, [sessions, pruneHiddenSessions]);

  const { filtered, grouped, managerTree, allHidden } = useMemo(() => {
    // The candidate selector is shared with Sidebar's bulk-selection control.
    // It includes hidden sessions in select mode, but not sessions removed by
    // search/special filters; grouping/collapse only affects presentation.
    const filtered = [...getSessionListCandidates(sessions, {
      multiSelectMode,
      hiddenSessionIds,
      searchQuery,
      specialFilters,
      adapterFilters,
      activeWorkspaceId,
      matchingSessionIds,
    })];

    // Preview/status updates can rerun this sort during streaming. Build the
    // ranks and timestamps once per list instead of rescanning/parsing them
    // inside every comparator call.
    const customRanks = sortBy === 'custom'
      ? new Map(customOrder.map((id, index) => [id, index] as const))
      : null;
    const updatedTimes = sortBy === 'recent' ? new Map(filtered.map((session) => [
      session.id,
      session.updatedAt ? new Date(session.updatedAt).getTime() : 0,
    ] as const)) : null;

    filtered.sort((a, b) => {
      if (sortBy === 'custom') {
        // Manual drag order; ids missing from customOrder fall back to their
        // current relative order (ranked after every mapped id). Two unmapped
        // ids keep their input (= authoritative server) relative order instead
        // of re-sorting by updatedAt, so the server-persisted order survives a
        // refresh even when customOrder is partial/stale.
        const rank = (id: string) => customRanks?.get(id) ?? Number.MAX_SAFE_INTEGER;
        const diff = rank(a.id) - rank(b.id);
        if (diff !== 0) return diff;
        if (rank(a.id) === Number.MAX_SAFE_INTEGER) return 0;
      }
      if (sortBy === 'name') {
        return a.name.localeCompare(b.name);
      }
      const aTime = updatedTimes?.get(a.id) ?? 0;
      const bTime = updatedTimes?.get(b.id) ?? 0;
      if (aTime !== bTime) return bTime - aTime;
      return a.name.localeCompare(b.name);
    });
    const pinnedOrdered = orderPinnedWithinGroups(filtered, effectiveGroupBy);

    const groups: { key: string; label: string; sessions: Session[] }[] = [];
    if (effectiveGroupBy === 'workdir') {
      const map = new Map<string, Session[]>();
      const uncategorized: Session[] = [];

      for (const s of pinnedOrdered) {
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

    const managerTree = effectiveGroupBy === 'manager' ? buildManagerTree(pinnedOrdered) : [];

    // Normal mode, sessions exist IN THE ACTIVE WORKSPACE, but every one of
    // them is hidden. Scoped so a fully-hidden workspace still explains itself.
    const scopedSessions = scopeSessionsByWorkspace(sessions, activeWorkspaceId);
    const allHidden =
      !multiSelectMode &&
      scopedSessions.length > 0 &&
      scopedSessions.every((session) => hiddenSessionIds.has(session.id));

    return { filtered: pinnedOrdered, grouped: groups, managerTree, allHidden };
  }, [sessions, searchQuery, sortBy, customOrder, effectiveGroupBy, specialFilters, adapterFilters, hiddenSessionIds, multiSelectMode, activeWorkspaceId, matchingSessionIds]);

  // ── 稳定回调：SessionItem 已 React.memo，靠这些引用稳定才不触发无关卡片重渲染 ──
  // multiSelectMode / toggleSelection / selectSession 通过 getState() 读取最新值，
  // 避免把易变的状态放进依赖数组导致回调每渲染都变。
  //
  // 卡片主体点击（非拖动区 / checkbox / eye / chevron 区域）：
  //  - 普通模式：打开该 Session；
  //  - select 模式：选中/取消选中该 Session，绝不打开 Session。
  //    拖动区（drag gutter）由 SessionItem 吞掉 click，不进入这里。
  const handleSelect = useCallback(
    (id: string) => {
      // A click fired right after a drag release must not select the session
      // that was just dragged (the pointerup landed back on the gutter), and
      // the click that closes a sweep must not toggle the press-origin card.
      if (didDragRef.current || sweepSuppressClickRef.current) {
        didDragRef.current = false;
        sweepSuppressClickRef.current = false;
        if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
        if (sweepSuppressTimerRef.current) clearTimeout(sweepSuppressTimerRef.current);
        return;
      }
      if (id.startsWith('__pending_')) return;
      const store = useSessionStore.getState();
      if (store.multiSelectMode) {
        store.toggleSelection(id);
        return;
      }
      store.selectSession(id);
      onSessionClick?.(id);
    },
    [onSessionClick],
  );

  // Select-mode checkbox: toggle selection without opening the session.
  // Read via getState() so the callback reference stays stable (React.memo).
  const handleToggleSelect = useCallback((id: string) => {
    useSessionStore.getState().toggleSelection(id);
  }, []);

  const handleMenu = useCallback(
    (e: React.MouseEvent, id: string) => {
      onSessionMenu?.(e, id);
    },
    [onSessionMenu],
  );

  // Select-mode eye button: toggle the hidden state, read live via getState()
  // so the callback reference stays stable (React.memo cards don't re-render).
  const handleToggleHidden = useCallback((id: string) => {
    const store = useUIStore.getState();
    store.setSessionHidden(id, !store.hiddenSessionIds.has(id));
  }, []);

  // ── Drag-to-reorder / drag-to-manage (demo) ──
  // Pointer-events based so it works for mouse AND touch. During a drag the
  // original card stays in place (nothing moves); feedback is a floating
  // ghost near the cursor plus per-card highlight (center band → manage,
  // edge band → insert line). Drop executes a local mock action.
  const [dragId, setDragId] = useState<string | null>(null);
  // Full drag payload: the pressed Session plus (in select mode) every selected
  // Session rendered in the list. A plain drag has exactly one member.
  const [dragIds, setDragIds] = useState<string[]>([]);
  const dragIdsRef = useRef<string[]>([]);
  const [dragMode, setDragMode] = useState<'session' | 'pin' | null>(null);
  const dragModeRef = useRef<'session' | 'pin' | null>(null);
  const [centerTargetId, setCenterTargetId] = useState<string | null>(null);
  const [insertTarget, setInsertTarget] = useState<{
    id: string;
    zone: 'before' | 'after';
  } | null>(null);
  // Latest hit-test result, read on pointerup without re-subscribing handlers.
  const dropTargetRef = useRef<{ id: string; zone: 'before' | 'center' | 'after' } | null>(null);
  /** Workspace-tab drop target while the pointer is over the rail. */
  const railTargetRef = useRef<string | null>(null);
  const [railZone, setRailZone] = useState(false);
  const [railTarget, setRailTarget] = useState<string | null>(null);
  const railZoneRef = useRef(false);
  // 'all' and the virtual 'ungrouped' tab both mean "no membership"; neither
  // resolves to a workspace name.
  const railTargetName = railTarget && railTarget !== ALL_WORKSPACES && railTarget !== UNGROUPED_WORKSPACES
    ? workspaces.find((w) => w.id === railTarget)?.name ?? '工作区'
    : null;
  /** Currently highlighted rail tab element (Tailwind ring utilities). */
  const railHighlightRef = useRef<HTMLElement | null>(null);
  const highlightRailTab = (el: HTMLElement | null) => {
    if (railHighlightRef.current === el) return;
    railHighlightRef.current?.classList.remove('ring-2', 'ring-accent');
    if (el) el.classList.add('ring-2', 'ring-accent');
    railHighlightRef.current = el;
  };
  const railExpandTimerRef = useRef<number | null>(null);
  const railAutoExpandedRef = useRef(false);
  const mobileRailAutoExpandedRef = useRef(false);

  /**
   * Desktop drops target the rail at the right edge of the session list. On
   * mobile, the attached rail opens with the Sidebar when a drag reaches its
   * collapsed handle, or the screen's left edge while the drawer is closed.
   * Returns true when the pointer is inside that zone.
   */
  const updateRailHover = useCallback((clientX: number, clientY: number): boolean => {
    const listRight = listRef.current?.getBoundingClientRect().right ?? 0;
    const isMobileViewport = typeof window.matchMedia === 'function'
      && window.matchMedia('(max-width: 767px)').matches;
    const ui = useUIStore.getState();
    const mobileCollapsedHandle = document.querySelector<HTMLElement>(
      '[data-testid="mobile-workspace-rail-collapsed"]',
    );
    const mobileExpandedRail = document.querySelector<HTMLElement>(
      '[data-testid="mobile-workspace-rail-expanded"]',
    );
    const mobileHandleRect = mobileCollapsedHandle?.getBoundingClientRect();
    const enteringMobileRail = !!mobileHandleRect
      && clientX >= mobileHandleRect.left - 4
      && clientX <= mobileHandleRect.right + 4
      && clientY >= mobileHandleRect.top - 4
      && clientY <= mobileHandleRect.bottom + 4;
    const expandedRailRect = mobileExpandedRail?.getBoundingClientRect();
    const insideMobileExpandedRail = !!expandedRailRect
      && clientX >= expandedRailRect.left
      && clientX <= expandedRailRect.right
      && clientY >= expandedRailRect.top
      && clientY <= expandedRailRect.bottom;
    const openingClosedMobileDrawer = isMobileViewport
      && !ui.mobileSidebarOpen
      && clientX <= 24;
    const inZone = insideMobileExpandedRail
      || enteringMobileRail
      || openingClosedMobileDrawer
      || (!isMobileViewport && listRight > 0 && clientX >= listRight - 4);
    if (!inZone) {
      if (railExpandTimerRef.current !== null) {
        window.clearTimeout(railExpandTimerRef.current);
        railExpandTimerRef.current = null;
      }
      if (railTargetRef.current !== null) { railTargetRef.current = null; setRailTarget(null); }
      railZoneRef.current = false;
      setRailZone(false);
      return false;
    }
    railZoneRef.current = true;
    setRailZone(true);
    if (openingClosedMobileDrawer) {
      mobileRailAutoExpandedRef.current = true;
      window.dispatchEvent(new Event('pan:workspace-rail-open-for-session-drop'));
      return true;
    }
    if (enteringMobileRail && !mobileExpandedRail) {
      mobileRailAutoExpandedRef.current = true;
      window.dispatchEvent(new Event('pan:workspace-rail-open-for-session-drop'));
      return true;
    }
    if (insideMobileExpandedRail) {
      railAutoExpandedRef.current = false;
    }
    if (!isMobileViewport && !mobileExpandedRail && !mobileCollapsedHandle && !ui.railExpanded) {
      if (railExpandTimerRef.current === null) {
        railExpandTimerRef.current = window.setTimeout(() => {
          railExpandTimerRef.current = null;
          railAutoExpandedRef.current = true;
          ui.setRailExpanded(true);
        }, 320);
      }
      return true;
    }
    let found: string | null = null;
    let foundEl: HTMLElement | null = null;
    for (const el of document.querySelectorAll<HTMLElement>('[data-workspace-tab-id]')) {
      const rect = el.getBoundingClientRect();
      if (clientY >= rect.top && clientY < rect.bottom && clientX >= rect.left && clientX <= rect.right) {
        found = el.dataset.workspaceTabId ?? null;
        foundEl = el;
        break;
      }
    }
    highlightRailTab(foundEl);
    if (found !== railTargetRef.current) { railTargetRef.current = found; setRailTarget(found); }
    return true;
  }, []);
  const dragIdRef = useRef<string | null>(null);
  // Press tracking for the drag-start threshold: a press must move beyond
  // DRAG_START_THRESHOLD_PX before it becomes a real drag; otherwise the
  // pointerup is treated as a plain click (select) on the session.
  const pressRef = useRef<{
    id: string;
    /** Whole drag payload; [id] unless the press starts inside a selection. */
    ids: string[];
    x: number;
    y: number;
    dragging: boolean;
    mode: 'session' | 'pin';
  } | null>(null);
  // True once a real drag ran; the click that follows a drag release must not
  // select the session. Cleared on the next press / after a short delay.
  const didDragRef = useRef(false);
  const didDragClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // ── Select-mode sweep selection (press on a card body, then move) ──
  // Strictly separate from the drag engine: the gesture starts on a card body
  // (the gutter stops pointerdown), adds every crossed Session to the current
  // selection and never creates a drag/ghost or a sort request.
  const sweepPressRef = useRef<{
    x: number;
    y: number;
    lastY: number;
    id: string;
    active: boolean;
  } | null>(null);
  const sweepSuppressClickRef = useRef(false);
  const sweepSuppressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ghostRef = useRef<HTMLDivElement | null>(null);
  // Last pointer position, re-applied when the ghost mounts mid-drag.
  const ghostPosRef = useRef<{ x: number; y: number } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const autoScrollFrameRef = useRef<number | null>(null);
  const autoScrollContainerRef = useRef<HTMLElement | null>(null);
  const autoScrollPointerYRef = useRef(0);
  const autoScrollTickRef = useRef<() => void>(() => {});
  // Latest visible (filtered+sorted) order, for computing the insert result.
  const filteredRef = useRef<Session[]>([]);
  useEffect(() => {
    filteredRef.current = filtered;
  }, [filtered]);

  const clearDragFeedback = useCallback(() => {
    setCenterTargetId(null);
    setInsertTarget(null);
    dropTargetRef.current = null;
  }, []);

  // Keep the event listeners registered on window stable while dispatching to
  // the latest render's handlers. Directly registering ref.current captures an
  // obsolete pointerup closure for the entire drag.
  const onPointerMoveRef = useRef<(e: PointerEvent) => void>(() => {});
  const onPointerUpRef = useRef<(e: PointerEvent) => void>(() => {});
  const onPointerCancelRef = useRef<(e: PointerEvent) => void>(() => {});
  const windowPointerMove = useCallback((e: PointerEvent) => onPointerMoveRef.current(e), []);
  const windowPointerUp = useCallback((e: PointerEvent) => onPointerUpRef.current(e), []);
  const windowPointerCancel = useCallback((e: PointerEvent) => onPointerCancelRef.current(e), []);

  const positionGhost = useCallback((x: number, y: number) => {
    ghostPosRef.current = { x, y };
    if (ghostRef.current) {
      ghostRef.current.style.transform = `translate(${x + 14}px, ${y + 14}px)`;
    }
  }, []);

  // Callback ref: apply the last pointer position the moment the ghost mounts
  // (positionGhost may have run before the ghost existed on pointerdown).
  const ghostMountRef = useCallback(
    (el: HTMLDivElement | null) => {
      ghostRef.current = el;
      if (el && ghostPosRef.current) {
        el.style.transform = `translate(${ghostPosRef.current.x + 14}px, ${ghostPosRef.current.y + 14}px)`;
      }
    },
    [],
  );

  /**
   * Every real (non-placeholder) Session card the vertical segment
   * (x, fromY)→(x, toY) crosses. Sampling the whole segment — not just the
   * latest point — keeps fast pointer moves from skipping rows, so a sweep
   * really selects every Session along the path.
   */
  const collectCardIdsAlong = useCallback((x: number, fromY: number, toY: number) => {
    const ids: string[] = [];
    const lo = Math.min(fromY, toY);
    const hi = Math.max(fromY, toY);
    const cards = listRef.current?.querySelectorAll<HTMLElement>('[data-session-card-id]');
    for (const el of cards ?? []) {
      const id = el.dataset.sessionCardId;
      if (!id || id.startsWith('__pending_')) continue;
      const rect = el.getBoundingClientRect();
      if (rect.height === 0) continue;
      if (x < rect.left || x > rect.right) continue;
      if (rect.bottom < lo || rect.top > hi) continue;
      ids.push(id);
    }
    return ids;
  }, []);

  /** Additive selection update: sweep only ever adds, never toggles off. */
  const addToSelection = useCallback((ids: string[]) => {
    for (const id of ids) {
      const store = useSessionStore.getState();
      if (!store.multiSelectMode) return;
      if (!store.selectedIds.has(id)) store.toggleSelection(id);
    }
  }, []);

  const onSweepMoveRef = useRef<(e: PointerEvent) => void>(() => {});
  const onSweepEndRef = useRef<() => void>(() => {});
  const windowSweepMove = useCallback((e: PointerEvent) => onSweepMoveRef.current(e), []);
  const windowSweepEnd = useCallback(() => onSweepEndRef.current(), []);

  const endSweep = useCallback(() => {
    window.removeEventListener('pointermove', windowSweepMove);
    window.removeEventListener('pointerup', windowSweepEnd);
    window.removeEventListener('pointercancel', windowSweepEnd);
    const press = sweepPressRef.current;
    sweepPressRef.current = null;
    if (!press?.active) return;
    // A sweep ends with a click on the press-origin card; that click must not
    // toggle it back off. Keep the guard briefly, like the drag-release guard.
    if (sweepSuppressTimerRef.current) clearTimeout(sweepSuppressTimerRef.current);
    sweepSuppressTimerRef.current = setTimeout(() => {
      sweepSuppressClickRef.current = false;
    }, 600);
  }, [windowSweepMove, windowSweepEnd]);

  onSweepEndRef.current = endSweep;
  onSweepMoveRef.current = (e) => {
    const press = sweepPressRef.current;
    if (!press) return;
    if (!press.active) {
      if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < DRAG_START_THRESHOLD_PX) return;
      press.active = true;
      sweepSuppressClickRef.current = true;
    }
    addToSelection([press.id, ...collectCardIdsAlong(e.clientX, press.lastY, e.clientY)]);
    press.lastY = e.clientY;
  };

  /**
   * Select-mode sweep start. Only a left-button press on a card body starts
   * it: interactive controls and the dedicated drag gutter are excluded, so a
   * gutter press stays a sort drag and a checkbox press stays a single toggle.
   */
  const handleListPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!useSessionStore.getState().multiSelectMode) return;
      // Left-button gesture only; touch keeps native scrolling.
      if (e.pointerType === 'touch') return;
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const target = e.target as HTMLElement | null;
      if (!target || target.closest('button, input, label, a, [data-testid="drag-handle"]')) return;
      const cardEl = target.closest<HTMLElement>('[data-session-card-id]');
      const id = cardEl?.dataset.sessionCardId;
      if (!id || id.startsWith('__pending_')) return;
      if (sweepSuppressTimerRef.current) {
        clearTimeout(sweepSuppressTimerRef.current);
        sweepSuppressTimerRef.current = null;
      }
      sweepSuppressClickRef.current = false;
      sweepPressRef.current = {
        x: e.clientX,
        y: e.clientY,
        lastY: e.clientY,
        id,
        active: false,
      };
      window.addEventListener('pointermove', windowSweepMove);
      window.addEventListener('pointerup', windowSweepEnd);
      window.addEventListener('pointercancel', windowSweepEnd);
    },
    [windowSweepMove, windowSweepEnd],
  );

  const hitTest = useCallback((clientY: number) => {
    const dragCurrent = dragIdRef.current;
    const cards = listRef.current?.querySelectorAll<HTMLElement>('[data-session-card-id]');
    if (!dragCurrent || !cards) return;
    const dragged = new Set(dragIdsRef.current.length > 0 ? dragIdsRef.current : [dragCurrent]);
    for (const el of cards) {
      // A member of the dragged selection is never a drop target for itself.
      if (el.dataset.sessionCardId && dragged.has(el.dataset.sessionCardId)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.height === 0) continue;
      if (clientY >= rect.top && clientY < rect.bottom) {
        const id = el.dataset.sessionCardId!;
        let zone = resolveDropZone(rect.top, rect.height, clientY);
        if (dragModeRef.current === 'pin' && zone === 'center') {
          zone = clientY < rect.top + rect.height / 2 ? 'before' : 'after';
        }
        dropTargetRef.current = { id, zone };
        setCenterTargetId(zone === 'center' ? id : null);
        setInsertTarget(
          zone === 'center' ? null : { id, zone },
        );
        return;
      }
    }
    clearDragFeedback();
  }, [clearDragFeedback]);

  const stopAutoScroll = useCallback(() => {
    if (autoScrollFrameRef.current !== null) {
      cancelAnimationFrame(autoScrollFrameRef.current);
      autoScrollFrameRef.current = null;
    }
    autoScrollContainerRef.current = null;
  }, []);

  // One animation loop owns scrolling for the whole drag. Pointer moves only
  // update the latest Y, so no unbounded timer is created on touch devices.
  autoScrollTickRef.current = () => {
    const container = autoScrollContainerRef.current;
    if (!container || !dragIdRef.current) {
      stopAutoScroll();
      return;
    }
    const delta = getAutoScrollDelta(
      autoScrollPointerYRef.current,
      container.getBoundingClientRect(),
    );
    if (delta === 0) {
      stopAutoScroll();
      return;
    }
    const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
    const next = Math.max(0, Math.min(maxScrollTop, container.scrollTop + delta));
    if (next !== container.scrollTop) container.scrollTop = next;
    hitTest(autoScrollPointerYRef.current);
    autoScrollFrameRef.current = requestAnimationFrame(autoScrollTickRef.current);
  };

  const updateAutoScroll = useCallback((clientY: number) => {
    autoScrollPointerYRef.current = clientY;
    if (!autoScrollContainerRef.current) {
      autoScrollContainerRef.current = findScrollableAncestor(listRef.current);
    }
    if (autoScrollContainerRef.current && autoScrollFrameRef.current === null) {
      autoScrollFrameRef.current = requestAnimationFrame(autoScrollTickRef.current);
    }
  }, []);

  const finishDrag = useCallback(() => {
    window.removeEventListener('pointermove', windowPointerMove);
    window.removeEventListener('pointerup', windowPointerUp);
    window.removeEventListener('pointercancel', windowPointerCancel);
    stopAutoScroll();
    setDragId(null);
    dragIdRef.current = null;
    setDragIds([]);
    dragIdsRef.current = [];
    setDragMode(null);
    dragModeRef.current = null;
    pressRef.current = null;
    clearDragFeedback();
    // Rail drop state must not survive the drag either.
    if (railExpandTimerRef.current !== null) {
      window.clearTimeout(railExpandTimerRef.current);
      railExpandTimerRef.current = null;
    }
    highlightRailTab(null);
    railTargetRef.current = null;
    setRailTarget(null);
    railZoneRef.current = false;
    setRailZone(false);
    if (mobileRailAutoExpandedRef.current) {
      mobileRailAutoExpandedRef.current = false;
      window.dispatchEvent(new Event('pan:workspace-rail-close-after-session-drop'));
    }
    // Keep didDrag set briefly so the click fired right after a drag release
    // is still suppressed, but never swallow a later genuine click.
    if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
    didDragClearTimerRef.current = setTimeout(() => {
      didDragRef.current = false;
    }, 600);
  }, [clearDragFeedback, stopAutoScroll, windowPointerMove, windowPointerUp, windowPointerCancel]);

  // Listener wrappers live in refs so add/remove always target the same
  // function instances across mounts.
  onPointerMoveRef.current = (e) => {
    const press = pressRef.current;
    if (!press) return;
    if (!press.dragging) {
      // Drag-start threshold: small movements are still a click.
      if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < DRAG_START_THRESHOLD_PX) {
        return;
      }
      press.dragging = true;
      didDragRef.current = true;
      dragIdRef.current = press.id;
      dragIdsRef.current = press.ids;
      dragModeRef.current = press.mode;
      setDragId(press.id);
      setDragIds(press.ids);
      setDragMode(press.mode);
    }
    positionGhost(e.clientX, e.clientY);
    if (updateRailHover(e.clientX, e.clientY)) {
      // Over the Workspace rail: card-level hints must not linger, and the
      // list's auto-scroll must not fight the rail's own scrolling.
      clearDragFeedback();
      return;
    }
    hitTest(e.clientY);
    updateAutoScroll(e.clientY);
  };
  onPointerUpRef.current = () => {
    const press = pressRef.current;
    // A press released without crossing the threshold is a plain click: let
    // the browser's click select the session; nothing else to do here.
    if (!press?.dragging) {
      finishDrag();
      return;
    }
    const dragCurrent = dragIdRef.current;
    const target = dropTargetRef.current;
    if (!dragCurrent) {
      finishDrag();
      return;
    }
    if (press.mode === 'pin') {
      const state = useSessionStore.getState();
      const dragged = state.sessions.find((session) => session.id === dragCurrent);
      const droppedOnRail = railZoneRef.current;
      let targetSession = target
        ? state.sessions.find((session) => session.id === target.id)
        : undefined;
      let zone: 'before' | 'after' = target?.zone === 'before' ? 'before' : 'after';
      if (!targetSession && !droppedOnRail && dragged) {
        const groupKey = sessionPinGroupKey(dragged, effectiveGroupBy);
        const siblings = filteredRef.current.filter(
          (session) => sessionPinGroupKey(session, effectiveGroupBy) === groupKey,
        );
        targetSession = siblings.filter((session) => session.id !== dragged.id).at(-1);
        zone = 'after';
      }
      finishDrag();
      if (!dragged || dragged.id.startsWith('__pending_')) return;
      const rejectCrossGroup = () => {
        showToast(`${isMockMode() ? '[Mock] ' : ''}Pinned Sessions can only be reordered within the same group`, 'error');
      };
      if (droppedOnRail) {
        rejectCrossGroup();
        return;
      }
      if (!targetSession) return;
      const currentGroupBy = effectiveGroupBy;
      if (sessionPinGroupKey(dragged, currentGroupBy) !== sessionPinGroupKey(targetSession, currentGroupBy)) {
        rejectCrossGroup();
        return;
      }
      const groupKey = sessionPinGroupKey(dragged, currentGroupBy);
      const pinnedIds = filteredRef.current
        .filter((session) => session.pinned && sessionPinGroupKey(session, currentGroupBy) === groupKey)
        .map((session) => session.id);
      const nextIds = movePinnedSessionId(
        pinnedIds,
        dragged.id,
        targetSession.id,
        zone,
        targetSession.pinned === true,
      );
      if (nextIds.length === pinnedIds.length && nextIds.every((id, index) => id === pinnedIds[index])) return;
      void state.reorderPinned(nextIds).catch((error) => {
        showToast(error instanceof Error ? error.message : 'Pinned Session reorder failed', 'error');
      });
      return;
    }
    // Workspace drop: over a rail tab → move (single membership + cascade).
    // Over the rail but not on a tab → cancel, and re-collapse the rail when
    // this drag was the thing that auto-expanded it. Every dragged member
    // (the whole selection in select mode) moves; moveSessions owns the
    // root/dedup/cascade rules and reports which ids actually changed.
    if (railZoneRef.current) {
      const railTargetId = railTargetRef.current;
      const sessionState = useSessionStore.getState();
      const members = dragIdsRef.current
        .map((id) => sessionState.sessions.find((s) => s.id === id))
        .filter((s): s is Session => !!s && !s.id.startsWith('__pending_'));
      const dragged = members.find((s) => s.id === dragCurrent) ?? members[0];
      finishDrag();
      if (!railTargetId || !dragged) {
        if (railAutoExpandedRef.current) {
          railAutoExpandedRef.current = false;
          useUIStore.getState().setRailExpanded(false);
        }
        return;
      }
      railAutoExpandedRef.current = false;
      const multi = members.length > 1;
      if (railTargetId === CREATE_WORKSPACE_DROP_TARGET_ID) {
        void useWorkspaceStore.getState()
          .createWorkspaceForSession(dragged.id)
          .then(async (workspace) => {
            if (!workspace) return;
            const rest = members.filter((s) => s.id !== dragged.id).map((s) => s.id);
            if (rest.length > 0) await useWorkspaceStore.getState().moveSessions(rest, workspace.id);
            showToast(
              multi
                ? `已创建「${workspace.name}」并移入 ${members.length} 个会话`
                : `已创建「${workspace.name}」并移入「${dragged.name}」`,
            );
          })
          .catch((err) => {
            showToast(err instanceof Error ? err.message : '创建工作区失败', 'error');
          });
        return;
      }
      // The virtual scopes ('all' / 'ungrouped') are not workspace ids: both
      // map to `null`, which moveSessions turns into the existing "no
      // membership" request (`workspaceIds: []`). A sentinel must never be
      // sent to the backend as a real workspace.
      const clearsMembership = railTargetId === ALL_WORKSPACES || railTargetId === UNGROUPED_WORKSPACES;
      void useWorkspaceStore.getState()
        .moveSessions(members.map((s) => s.id), clearsMembership ? null : railTargetId)
        .then((changed) => {
          if (changed.length === 0) {
            showToast(
              multi
                ? '所选会话均未发生工作区变更'
                : clearsMembership ? '该会话当前未归属任何工作区' : '该会话已在该工作区',
              'error',
            );
            return;
          }
          const followed = Math.max(0, changed.length - members.length);
          const label = clearsMembership
            ? null
            : useWorkspaceStore.getState().workspaces.find((w) => w.id === railTargetId)?.name ?? '工作区';
          if (multi) {
            if (!label) showToast(`已将 ${members.length} 个会话移出工作区（未分组）`);
            else showToast(`已将 ${members.length} 个会话移入「${label}」${followed > 0 ? `（含 ${followed} 个子孙会话）` : ''}`);
            return;
          }
          if (!label) showToast(`已将「${dragged.name}」移出工作区（未分组）`);
          else if (followed > 0) showToast(`已将「${dragged.name}」及其 ${followed} 个子孙会话移入「${label}」`);
          else showToast(`已将「${dragged.name}」移入「${label}」`);
        })
        .catch((err) => {
          showToast(err instanceof Error ? err.message : '移动失败', 'error');
        });
      return;
    }
    if (!target) {
      finishDrag();
      return;
    }
    const state = useSessionStore.getState();
    const members = dragIdsRef.current
      .map((id) => state.sessions.find((s) => s.id === id))
      .filter((s): s is Session => !!s && !s.id.startsWith('__pending_'));
    const targetSession = state.sessions.find((s) => s.id === target.id);
    // A pending placeholder (mid-creation) has no server identity — dropping
    // onto/from one would submit an unknown id to /api/claim or the order API.
    if (members.length === 0 || !targetSession || targetSession.id.startsWith('__pending_')) {
      finishDrag();
      return;
    }

    // Manager-tree semantics: decide where this drop wants every dragged
    // Session to live (center → target manages it; edge slot → adopt that
    // slot's group context). The whole drop is rejected up front when ANY
    // member would create a management cycle — a selection never partially
    // bypasses the existing prohibition.
    const decisions = members.map((member) => ({
      member,
      ...decideManagerDrop(filteredRef.current, member.id, targetSession.id, target.zone),
    }));
    const blocked = decisions.find((decision) => decision.blockedByCycle);
    if (blocked) {
      const prefix = isMockMode() ? '[Mock] ' : '';
      showToast(
        `${prefix}禁止：不能把「${blocked.member.name}」移入自己或其下级的组内（会形成递归管理）`,
        'error',
      );
      finishDrag();
      return;
    }

    const memberPayload = decisions.map((decision) => ({
      id: decision.member.id,
      name: decision.member.name || 'Untitled',
      oldManager: decision.member.managedBy ?? null,
      newManager: decision.newManager,
    }));
    const changedMembers = memberPayload.filter((member) => member.oldManager !== member.newManager);
    const dragMemberIds = members.map((member) => member.id);

    // ── 非 mock 模式：真实持久化。拖到两张卡片之间 → POST /api/sessions/order；
    //    拖到卡片中心（B manage A）或拖出/移入管理组 → POST /api/unclaim 与
    //    /api/claim。只在服务端确认成功后更新本地状态；失败仅弹错误 toast，绝不
    //    假装成功。mock demo（?mock=1）走下方原本地分支，保持行为不变。
    if (!isMockMode()) {
      // center 落点但所有成员都已由 target 管理 → 与 mock 分支一致的静默 no-op。
      if (target.zone === 'center' && changedMembers.length === 0) {
        finishDrag();
        return;
      }
      const orderIds = buildDropOrderIds(
        state.sessions,
        listRef.current,
        filteredRef.current,
        dragMemberIds,
        targetSession.id,
        target.zone,
      );
      finishDrag();
      void persistSessionDrop({
        members: memberPayload,
        targetName: targetSession.name || targetSession.id,
        zone: target.zone,
        orderIds,
      });
      return;
    }

    if (target.zone === 'center') {
      // Mock "B manage A": every member becomes managed by B (local state only).
      for (const member of changedMembers) {
        state.updateSession(member.id, { managedBy: targetSession.id });
        applyMockSessionUpdate(member.id, { managedBy: targetSession.id });
      }
      if (changedMembers.length === 1) {
        showToast(`[Mock] 「${targetSession.name}」现在管理「${changedMembers[0]!.name}」`);
      } else if (changedMembers.length > 1) {
        showToast(`[Mock] 「${targetSession.name}」现在管理 ${changedMembers.length} 个会话`);
      }
      // Already managed by the target → silent no-op (no toast for that).
      finishDrag();
      return;
    }

    // Edge drop: (optionally) move the dragged sessions between groups, then
    // insert them at the exact boundary the user saw. The authoritative
    // "visible order" is the DOM card order (flat list, or preorder of the
    // manager tree). Entering a group = adopt that manager; landing on a
    // top-level slot = leave the current group (managedBy → null).
    for (const member of changedMembers) {
      state.updateSession(member.id, { managedBy: member.newManager });
      applyMockSessionUpdate(member.id, { managedBy: member.newManager });
    }

    const ids = buildDropOrderIds(
      state.sessions,
      listRef.current,
      filteredRef.current,
      dragMemberIds,
      targetSession.id,
      target.zone,
    );
    if (ids.includes(targetSession.id)) {
      useUIStore.getState().setCustomOrder(ids);
      useUIStore.getState().setSortBy('custom');

      // Toast ONLY when this drop changes a management relationship; a pure
      // position move reorders silently.
      if (changedMembers.length === 1) {
        const member = changedMembers[0]!;
        const newManagerName = member.newManager
          ? state.sessions.find((s) => s.id === member.newManager)?.name ?? member.newManager
          : null;
        if (member.newManager !== null) {
          showToast(`[Mock] 「${member.name}」已移入「${newManagerName}」的组`);
        } else if (member.oldManager !== null) {
          showToast(
            `[Mock] 「${member.name}」已移出「${
              state.sessions.find((s) => s.id === member.oldManager)?.name ?? member.oldManager
            }」的管理`,
          );
        }
      } else if (changedMembers.length > 1) {
        if (decisions.every((decision) => decision.newManager !== null)) {
          showToast(`[Mock] ${changedMembers.length} 个会话已移入「${targetSession.name}」所在的组`);
        } else {
          showToast(`[Mock] ${changedMembers.length} 个会话已更新管理关系`);
        }
      }
    }
    finishDrag();
  };
  onPointerCancelRef.current = () => finishDrag();

  const handleDragPointerDown = useCallback(
    (e: React.PointerEvent, id: string) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
      didDragRef.current = false;
      const store = useSessionStore.getState();
      const session = store.sessions.find((item) => item.id === id);
      // Selection drag: pressing the gutter of a selected Session drags the
      // whole rendered selection. Pressing an unselected Session keeps the
      // normal single-Session drag and leaves the selection untouched.
      const selectedDrag = store.multiSelectMode && store.selectedIds.has(id);
      const ids = selectedDrag ? visibleSelectionDragIds(listRef.current, store.selectedIds) : [];
      pressRef.current = {
        id,
        ids: ids.length > 0 ? ids : [id],
        x: e.clientX,
        y: e.clientY,
        dragging: false,
        // Pin reorder is a normal-mode affordance; select mode always uses the
        // standard drag pipeline so the whole selection follows one rule.
        mode: store.multiSelectMode ? 'session' : session?.pinned ? 'pin' : 'session',
      };
      window.addEventListener('pointermove', windowPointerMove);
      window.addEventListener('pointerup', windowPointerUp);
      window.addEventListener('pointercancel', windowPointerCancel);
    },
    [windowPointerMove, windowPointerUp, windowPointerCancel],
  );

  // Safety net: unmount mid-drag removes the window listeners.
  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', windowPointerMove);
      window.removeEventListener('pointerup', windowPointerUp);
      window.removeEventListener('pointercancel', windowPointerCancel);
      window.removeEventListener('pointermove', windowSweepMove);
      window.removeEventListener('pointerup', windowSweepEnd);
      window.removeEventListener('pointercancel', windowSweepEnd);
      if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
      if (sweepSuppressTimerRef.current) clearTimeout(sweepSuppressTimerRef.current);
      stopAutoScroll();
    };
  }, [stopAutoScroll, windowPointerMove, windowPointerUp, windowPointerCancel, windowSweepMove, windowSweepEnd]);

  const dragSession = dragId ? sessions.find((s) => s.id === dragId) : null;
  // Drag works in the flat list AND the manager tree (same semantics:
  // center → manage, edge → sibling slot at the target row's level).
  // Grouped-by-dir lists stay non-draggable. The drag preference also applies
  // in select mode: the dedicated gutter remains the only drag entry point.
  const dragEnabled = dragPreference;

  // Per-card drag props for memoized SessionItem (stable refs + primitives
  // keep unrelated cards from re-rendering).
  const dragPropsFor = useCallback(
    (session: Session) =>
      dragEnabled && (session.pinned || effectiveGroupBy === 'none' || effectiveGroupBy === 'manager')
        ? {
            dragEnabled: true,
            onDragHandlePointerDown: handleDragPointerDown,
            isDragSource: dragIds.includes(session.id),
            isCenterTarget: session.id === centerTargetId,
            insertZone: insertTarget?.id === session.id ? insertTarget.zone : null,
          }
        : {},
    [dragEnabled, effectiveGroupBy, handleDragPointerDown, dragIds, centerTargetId, insertTarget],
  );

  // Ghost feedback: as the dragged session hovers a drop zone, preview on the
  // floating mini-card whether the drop will (a) change a management relation
  // (「管理」 lights), (b) only sort to a sibling slot (「排序」, the right chip,
  // lights), or (c) BOTH when the slot lies in a different group's level and
  // the session must also adopt that group's manager. Blocked (cycle) drops
  // light nothing.
  const ghostFeedback = useMemo(() => {
    if (!dragId) return null;
    const targetId = centerTargetId ?? insertTarget?.id ?? null;
    if (!targetId) return null;
    const zone: DropZone = insertTarget ? insertTarget.zone : 'center';
    const draggedS = sessions.find((s) => s.id === dragId);
    const targetS = sessions.find((s) => s.id === targetId);
    if (!draggedS || !targetS) return null;
    const payload = dragIds.length > 0 ? dragIds : [dragId];
    // Pin reorder has no manage semantics at all (single normal-mode drag).
    if (dragMode === 'pin') return { blocked: false, manage: false, order: true };
    // Any member whose drop would create a cycle blocks the whole selection.
    let blocked = false;
    let manageChange = false;
    for (const id of payload) {
      const member = sessions.find((s) => s.id === id);
      if (!member) continue;
      const { newManager, blockedByCycle } = decideManagerDrop(filtered, id, targetId, zone);
      if (blockedByCycle) blocked = true;
      const currentManager = member.managedBy ?? null;
      if (zone === 'center' ? targetId !== currentManager : newManager !== currentManager) {
        manageChange = true;
      }
    }
    const orderChange = zone !== 'center';
    return {
      blocked,
      manage: manageChange && !blocked,
      order: orderChange && !blocked,
    };
  }, [dragId, dragIds, dragMode, centerTargetId, insertTarget, sessions, filtered]);

  const ghostEl = dragEnabled && dragSession && (
    <div
      ref={ghostMountRef}
      data-drag-ghost
      data-drag-count={Math.max(1, dragIds.length)}
      data-ghost-manage-lit={ghostFeedback?.manage ? '1' : '0'}
      data-ghost-order-lit={ghostFeedback?.order ? '1' : '0'}
      aria-hidden="true"
      className="fixed left-0 top-0 z-[60] w-64 pointer-events-none rounded border border-dashed border-accent bg-bg-secondary/95 px-3 py-2 shadow-panel"
      style={{ willChange: 'transform' }}
    >
      <div className="flex items-center gap-2">
        <WorkerDot status={dragSession.workerStatus} illegalOffline={dragSession.illegalOffline} />
        <span className="text-sm text-text-primary font-medium truncate min-w-0 flex-1">
          {dragIds.length > 1
            ? `${dragIds.length} sessions`
            : dragSession.name || 'Untitled'}
        </span>
        {dragMode === 'pin' ? (
          <span className="shrink-0 rounded px-1.5 py-px text-[10px] leading-tight bg-accent text-white font-medium">
            Pin order
          </span>
        ) : <>
        {/* Outcome preview chips: 「管理」left, 「排序」right. */}
        <span
          data-ghost-manage
          data-lit={ghostFeedback?.manage ? '1' : '0'}
          className={`shrink-0 rounded px-1.5 py-px text-[10px] leading-tight transition-colors ${
            ghostFeedback?.manage
              ? 'bg-accent text-white font-medium'
              : 'text-text-tertiary/70 border border-border-default'
          }`}
        >
          管理
        </span>
        <span
          data-ghost-order
          data-lit={ghostFeedback?.order ? '1' : '0'}
          className={`shrink-0 rounded px-1.5 py-px text-[10px] leading-tight transition-colors ${
            ghostFeedback?.order
              ? 'bg-accent text-white font-medium'
              : 'text-text-tertiary/70 border border-border-default'
          }`}
        >
          排序
        </span>
        <span
          data-ghost-workspace
          data-lit={railZone ? '1' : '0'}
          className={`shrink-0 rounded px-1.5 py-px text-[10px] leading-tight transition-colors ${
            railZone
              ? 'bg-accent text-white font-medium'
              : 'text-text-tertiary/70 border border-border-default'
          }`}
        >
          工作区
        </span>
        </>}
      </div>
      <div className="mt-1 text-[10px] text-text-tertiary">
        {dragMode === 'pin'
          ? 'Pinned Sessions reorder within the current group'
          : railZone
          ? (railTarget === CREATE_WORKSPACE_DROP_TARGET_ID
            ? `新建「${dragSession?.name || 'Untitled'}」工作区（重名自动编号）`
            : railTarget === ALL_WORKSPACES || railTarget === UNGROUPED_WORKSPACES
            ? '放到「全部」或「无工作区」= 移出工作区（未分组）'
            : railTarget
              ? `移入「${railTargetName}」（管理者会话的子孙会跟随）`
              : '放到某个工作区标签上')
          : `${dragIds.length > 1 ? `同时移动 ${dragIds.length} 个会话 · ` : ''}中心 = 交给管理 · 边缘 = 同级排序`}
      </div>
    </div>
  );

  // Per-node collapse toggle: expanding a node only clears ITS OWN key, so
  // every level keeps its individual collapsed state — e.g. after
  // "collapse all", re-expanding an outer group leaves its children collapsed
  // (each child's key is still in the set) until expanded themselves.
  const handleToggleManagerNode = useCallback(
    (node: ManagerNode) => {
      toggleGroupCollapse(node.session.id);
    },
    [toggleGroupCollapse],
  );

  // Initial list fetch in flight + nothing to show yet → spinner, so an account
  // that does have sessions never flashes the "No sessions yet" empty state.
  // When sessions are already populated (a background refresh), keep rendering
  // the list instead of replacing it with a spinner.
  if (sessionsLoading && sessions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 gap-3">
        <Loader2 size={24} className="animate-spin text-text-tertiary" />
        <p className="text-sm text-text-tertiary">Loading sessions...</p>
      </div>
    );
  }

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

  // Every session is hidden (normal mode): only Select mode can surface them.
  if (allHidden) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 gap-2">
        <p className="text-sm text-text-tertiary">All sessions hidden</p>
        <p className="text-xs text-text-tertiary">Enable Select mode to show hidden sessions again</p>
      </div>
    );
  }

  // An active global search with zero visible hits is a no-match state too
  // (the sidebar banner already reports "still searching…" while preparing).
  if (filtered.length === 0 && (searchQuery.trim() || specialFilters.size > 0 || adapterFilters.size > 0
    || (matchingSessionIds !== null && !historySearchPreparing))) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 gap-2">
        <p className="text-sm text-text-tertiary">No matching sessions</p>
        <p className="text-xs text-text-tertiary">Try a different search term or clear filters</p>
      </div>
    );
  }

  if (effectiveGroupBy === 'manager' && managerTree.length > 0) {
    return (
      <div className="flex flex-col" ref={listRef} onPointerDown={handleListPointerDown}>
        {managerTree.map((node) => (
          <ManagerNodeView
            key={node.session.id}
            node={node}
            currentSessionId={currentSessionId}
            selectedIds={selectedIds}
            multiSelectMode={multiSelectMode}
            collapsedGroups={collapsedGroups}
            hiddenIds={hiddenSessionIds}
            onSelect={handleSelect}
            onToggleSelect={handleToggleSelect}
            onMenu={handleMenu}
            onToggle={handleToggleManagerNode}
            onToggleHidden={multiSelectMode ? handleToggleHidden : undefined}
            dragPropsFor={dragPropsFor}
          />
        ))}
        {ghostEl}
      </div>
    );
  }

  if (effectiveGroupBy === 'workdir' && grouped.length > 0) {
    return (
      <div className="flex flex-col" ref={listRef} onPointerDown={handleListPointerDown}>
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
                isHidden={hiddenSessionIds.has(session.id)}
                onToggleHidden={multiSelectMode ? handleToggleHidden : undefined}
                onSelect={handleSelect}
                onToggleSelect={handleToggleSelect}
                onMenu={handleMenu}
                {...dragPropsFor(session)}
              />
            ))}
          </GroupSection>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col" ref={listRef} onPointerDown={handleListPointerDown}>
      {filtered.map((session) => (
        <SessionItem
          key={session.id}
          session={session}
          isActive={session.id === currentSessionId}
          isSelected={selectedIds.has(session.id)}
          multiSelectMode={multiSelectMode}
          isHidden={hiddenSessionIds.has(session.id)}
          onToggleHidden={multiSelectMode ? handleToggleHidden : undefined}
          onSelect={handleSelect}
          onToggleSelect={handleToggleSelect}
          onMenu={handleMenu}
          {...dragPropsFor(session)}
        />
      ))}
      {ghostEl}
    </div>
  );
}

interface ManagerNodeViewProps {
  node: ManagerNode;
  currentSessionId: string | null;
  selectedIds: Set<string>;
  multiSelectMode: boolean;
  collapsedGroups: Set<string>;
  /** Select-mode hidden ids (eye button source). */
  hiddenIds: Set<string>;
  onSelect: (id: string) => void;
  onToggleSelect?: (id: string) => void;
  onMenu?: (e: React.MouseEvent, id: string) => void;
  onToggle: (node: ManagerNode) => void;
  onToggleHidden?: (id: string) => void;
  /** Drag demo: per-session drag props factory (stable identity). */
  dragPropsFor: (session: Session) => Record<string, unknown>;
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
  hiddenIds,
  onSelect,
  onToggleSelect,
  onMenu,
  onToggle,
  onToggleHidden,
  dragPropsFor,
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
        isHidden={hiddenIds.has(session.id)}
        onToggleHidden={onToggleHidden}
        expandable={hasChildren}
        expanded={!collapsed}
        onToggleChildren={(e) => {
          e.stopPropagation();
          onToggle(node);
        }}
        onSelect={onSelect}
        onToggleSelect={onToggleSelect}
        onMenu={onMenu}
        {...dragPropsFor(session)}
      />
      {hasChildren && !collapsed && (
        <div className="ml-0" data-tree-children>
          {node.children.map((child, idx) => (
            <ManagerChildView
              key={child.session.id}
              child={child}
              isLast={idx === node.children.length - 1}
              currentSessionId={currentSessionId}
              selectedIds={selectedIds}
              multiSelectMode={multiSelectMode}
              collapsedGroups={collapsedGroups}
              hiddenIds={hiddenIds}
              onSelect={onSelect}
              onToggleSelect={onToggleSelect}
              onMenu={onMenu}
              onToggle={onToggle}
              onToggleHidden={onToggleHidden}
              dragPropsFor={dragPropsFor}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface ManagerChildViewProps {
  child: ManagerNode;
  /** True when this child is the last sibling — its vertical guide ends with a
   *  └ corner instead of continuing to the next sibling (├). */
  isLast: boolean;
  currentSessionId: string | null;
  selectedIds: Set<string>;
  multiSelectMode: boolean;
  collapsedGroups: Set<string>;
  /** Select-mode hidden ids (eye button source). */
  hiddenIds: Set<string>;
  onSelect: (id: string) => void;
  onToggleSelect?: (id: string) => void;
  onMenu?: (e: React.MouseEvent, id: string) => void;
  onToggle: (node: ManagerNode) => void;
  onToggleHidden?: (id: string) => void;
  /** Drag demo: per-session drag props factory (stable identity). */
  dragPropsFor: (session: Session) => Record<string, unknown>;
}

/**
 * One manager child rendered as a classic tree corner (├ / └):
 *  - A vertical guide segment at `left-0` (= the children container's left
 *    edge). Non-last children draw it across the WHOLE root div (row + their
 *    own deeper subtree) so the guide stays continuous through nested levels
 *    down to the next sibling; the last child draws it only down to its row
 *    center, forming the └ corner.
 *  - A horizontal tick at the row's vertical center, turning right from the
 *    guide into the card (`top-1/2 -translate-y-1/2`, row wrapper is relative
 *    and wraps ONLY this row, so the centering holds at any nesting depth).
 *
 * The row wrapper's `pl-3` indents the card 12px past the guide, so the tick
 * (w-3 = 12px) spans guide → card left edge without covering the WorkerDot
 * (which sits 12px further inside the card's own px-3 padding).
 */
function ManagerChildView({
  child,
  isLast,
  currentSessionId,
  selectedIds,
  multiSelectMode,
  collapsedGroups,
  hiddenIds,
  onSelect,
  onToggleSelect,
  onMenu,
  onToggle,
  onToggleHidden,
  dragPropsFor,
}: ManagerChildViewProps) {
  const session = child.session;
  const hasChildren = child.children.length > 0;
  const collapsed = collapsedGroups.has(session.id);

  return (
    <div className="relative">
      {/* Vertical guide: non-last children continue below their subtree. */}
      {!isLast && (
        <span
          aria-hidden
          data-tree-guide
          className="pointer-events-none absolute left-0 top-0 bottom-0 w-px bg-text-secondary/70"
        />
      )}
      {/* Own row + corner connector */}
      <div className="relative pl-3">
        {/* Last child: └ corner — guide stops at this row's center. */}
        {isLast && (
          <span
            aria-hidden
            data-tree-guide
            className="pointer-events-none absolute left-0 top-0 h-1/2 w-px bg-text-secondary/70"
          />
        )}
        {/* Horizontal tick (corner turn) at the row's vertical center. */}
        <span
          aria-hidden
          data-tree-tick
          className="pointer-events-none absolute left-0 top-1/2 -translate-y-1/2 h-px w-3 bg-text-secondary/70"
        />
        <SessionItem
          session={session}
          isActive={session.id === currentSessionId}
          isSelected={selectedIds.has(session.id)}
          multiSelectMode={multiSelectMode}
          isHidden={hiddenIds.has(session.id)}
          onToggleHidden={onToggleHidden}
          expandable={hasChildren}
          expanded={!collapsed}
          onToggleChildren={(e) => {
            e.stopPropagation();
            onToggle(child);
          }}
          onSelect={onSelect}
          onToggleSelect={onToggleSelect}
          onMenu={onMenu}
          {...dragPropsFor(session)}
        />
      </div>
      {/* The child's own children */}
      {hasChildren && !collapsed && (
        <div className="ml-3" data-tree-children>
          {child.children.map((grandchild, idx) => (
            <ManagerChildView
              key={grandchild.session.id}
              child={grandchild}
              isLast={idx === child.children.length - 1}
              currentSessionId={currentSessionId}
              selectedIds={selectedIds}
              multiSelectMode={multiSelectMode}
              collapsedGroups={collapsedGroups}
              hiddenIds={hiddenIds}
              onSelect={onSelect}
              onToggleSelect={onToggleSelect}
              onMenu={onMenu}
              onToggle={onToggle}
              onToggleHidden={onToggleHidden}
              dragPropsFor={dragPropsFor}
            />
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
