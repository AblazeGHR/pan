import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { SessionItem } from './SessionItem';
import { matchesSpecialFilters } from '@/utils/sessionFilters';
import { resolveDropZone, decideManagerDrop, DRAG_START_THRESHOLD_PX } from './sessionDrag';
import type { DropZone } from './sessionDrag';
import { isMockMode, applyMockSessionUpdate } from '@/demo/mockBackend';
import { claimSession, unclaimSession, reorderSessions } from '@/services/api';
import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { FolderOpen, Loader2 } from 'lucide-react';

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

/** Full desired session order after an EDGE drop: the dragged session is
 *  removed and re-inserted right next to the target card (before/after), every
 *  session that is not visible in the DOM (e.g. collapsed/hidden) keeps its
 *  current relative position after the visible ones. */
function buildDropOrderIds(
  allSessions: Session[],
  listEl: HTMLElement | null,
  fallback: Session[],
  dragId: string,
  targetId: string,
  zone: DropZone,
): string[] {
  const isRealId = (id: string) => id.length > 0 && !id.startsWith('__pending_');
  const domCards = listEl?.querySelectorAll<HTMLElement>('[data-session-card-id]');
  const domIds = domCards
    ? [...domCards].map((el) => el.dataset.sessionCardId ?? '').filter(isRealId)
    : fallback.map((s) => s.id).filter(isRealId);
  const ids = domIds.filter((x) => x !== dragId);
  const tIdx = ids.indexOf(targetId);
  if (tIdx >= 0) {
    ids.splice(zone === 'before' ? tIdx : tIdx + 1, 0, dragId);
    for (const s of allSessions) {
      if (isRealId(s.id) && !ids.includes(s.id)) ids.push(s.id);
    }
  }
  return ids;
}

interface RealDropParams {
  draggedId: string;
  draggedName: string;
  targetName: string;
  /** Current manager of the dragged session (null = top-level root). */
  oldManager: string | null;
  /** Manager the dragged session should have after the drop. */
  newManager: string | null;
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
 */
async function persistSessionDrop(p: RealDropParams): Promise<void> {
  const sessionStore = useSessionStore.getState();
  const ui = useUIStore.getState();
  const { draggedId, draggedName, targetName, oldManager, newManager, zone, orderIds } = p;
  const findLabel = (id: string | null) =>
    id ? (sessionStore.sessions.find((s) => s.id === id)?.name ?? id) : null;

  // 1) Management transition (B manage A / move between groups / leave group).
  //    Server enforces exclusivity, so changing managers = unclaim old first.
  if (oldManager !== newManager) {
    try {
      if (oldManager) await unclaimSession(oldManager, draggedId);
      if (newManager) await claimSession(newManager!, draggedId);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '服务端拒绝';
      ui.showToast(`管理关系更新失败：${msg}`, 'error');
      void sessionStore.loadSessions(); // reconcile any partial unclaim
      return;
    }
    sessionStore.updateSession(draggedId, { managedBy: newManager });
    if (newManager) {
      const managerName = zone === 'center' ? targetName : findLabel(newManager) ?? newManager;
      ui.showToast(
        zone === 'center'
          ? `「${targetName}」现在管理「${draggedName}」`
          : `「${draggedName}」已移入「${managerName}」的组`,
      );
    } else {
      ui.showToast(`「${draggedName}」已移出「${findLabel(oldManager) ?? oldManager}」的管理`);
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
  const sessions = useSessionStore((s) => s.sessions);
  const sessionsLoading = useSessionStore((s) => s.sessionsLoading);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const multiSelectMode = useSessionStore((s) => s.multiSelectMode);
  const selectedIds = useSessionStore((s) => s.selectedIds);

  const {
    groupBy,
    searchQuery,
    sortBy,
    specialFilters,
    hiddenSessionIds,
    collapsedGroups,
    customOrder,
    toggleGroupCollapse,
    addCollapsedGroups,
    removeCollapsedGroups,
    pruneCollapsedGroups,
    pruneHiddenSessions,
    showToast,
  } = useUIStore();
  const defaultGroupBy = useAppSettingsStore((s) => s.defaultGroupBy);

  // Default grouping: adopt the app-settings default as long as the user has
  // never manually picked a grouping (nothing persisted to pan:groupBy AND the
  // store is still at its untouched 'none'). Manual switches persist to
  // pan:groupBy and take precedence thereafter. The sync deliberately does NOT
  // persist, so each app entry re-reads the latest default until the user
  // groups manually.
  useLayoutEffect(() => {
    const ui = useUIStore.getState();
    if (localStorage.getItem('pan:groupBy') === null && ui.groupBy === 'none') {
      useUIStore.setState({ groupBy: defaultGroupBy });
    }
  }, [defaultGroupBy]);

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

  // Keep hiddenSessionIds consistent with the live list: drop ids of deleted
  // sessions. Guarded on sessions.length > 0 so a fresh page load (empty list
  // before the first fetch resolves) never wipes persisted hides.
  useEffect(() => {
    if (sessions.length === 0) return;
    pruneHiddenSessions(new Set(sessions.map((s) => s.id)));
  }, [sessions, pruneHiddenSessions]);

  const { filtered, grouped, managerTree, allHidden } = useMemo(() => {
    // Hidden sessions never take part in the normal list — they are excluded
    // up front so text search, special filters, sorting and grouping all
    // operate on the visible set. Select mode bypasses the exclusion so a
    // permanently hidden session is always reachable (recovery path).
    const base = multiSelectMode
      ? [...sessions]
      : sessions.filter((s) => !hiddenSessionIds.has(s.id));

    let filtered = base;

    if (specialFilters.size > 0) {
      filtered = filtered.filter((s) =>
        matchesSpecialFilters(s, sessions, specialFilters),
      );
    }

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
      if (sortBy === 'custom') {
        // Manual drag order; ids missing from customOrder fall back to their
        // current relative order (ranked after every mapped id). Two unmapped
        // ids keep their input (= authoritative server) relative order instead
        // of re-sorting by updatedAt, so the server-persisted order survives a
        // refresh even when customOrder is partial/stale.
        const rank = (id: string) => {
          const i = customOrder.indexOf(id);
          return i === -1 ? Number.MAX_SAFE_INTEGER : i;
        };
        const diff = rank(a.id) - rank(b.id);
        if (diff !== 0) return diff;
        if (rank(a.id) === Number.MAX_SAFE_INTEGER) return 0;
      }
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

    // Normal mode, sessions exist, but every one of them is hidden.
    const allHidden = !multiSelectMode && sessions.length > 0 && base.length === 0;

    return { filtered, grouped: groups, managerTree, allHidden };
  }, [sessions, searchQuery, sortBy, customOrder, groupBy, specialFilters, hiddenSessionIds, multiSelectMode]);

  // ── 稳定回调：SessionItem 已 React.memo，靠这些引用稳定才不触发无关卡片重渲染 ──
  // multiSelectMode / toggleSelection / selectSession 通过 getState() 读取最新值，
  // 避免把易变的状态放进依赖数组导致回调每渲染都变。
  const handleSelect = useCallback(
    (id: string) => {
      // A click fired right after a drag release must not select the session
      // that was just dragged (the pointerup landed back on the gutter).
      if (didDragRef.current) {
        didDragRef.current = false;
        if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
        return;
      }
      const store = useSessionStore.getState();
      if (store.multiSelectMode) {
        store.toggleSelection(id);
      } else if (!id.startsWith('__pending_')) {
        store.selectSession(id);
        onSessionClick?.(id);
      }
    },
    [onSessionClick],
  );

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
  const [centerTargetId, setCenterTargetId] = useState<string | null>(null);
  const [insertTarget, setInsertTarget] = useState<{
    id: string;
    zone: 'before' | 'after';
  } | null>(null);
  // Latest hit-test result, read on pointerup without re-subscribing handlers.
  const dropTargetRef = useRef<{ id: string; zone: 'before' | 'center' | 'after' } | null>(null);
  const dragIdRef = useRef<string | null>(null);
  // Press tracking for the drag-start threshold: a press must move beyond
  // DRAG_START_THRESHOLD_PX before it becomes a real drag; otherwise the
  // pointerup is treated as a plain click (select) on the session.
  const pressRef = useRef<{
    id: string;
    x: number;
    y: number;
    dragging: boolean;
  } | null>(null);
  // True once a real drag ran; the click that follows a drag release must not
  // select the session. Cleared on the next press / after a short delay.
  const didDragRef = useRef(false);
  const didDragClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ghostRef = useRef<HTMLDivElement | null>(null);
  // Last pointer position, re-applied when the ghost mounts mid-drag.
  const ghostPosRef = useRef<{ x: number; y: number } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
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

  const hitTest = useCallback((clientY: number) => {
    const dragCurrent = dragIdRef.current;
    const cards = listRef.current?.querySelectorAll<HTMLElement>('[data-session-card-id]');
    if (!dragCurrent || !cards) return;
    for (const el of cards) {
      if (el.dataset.sessionCardId === dragCurrent) continue;
      const rect = el.getBoundingClientRect();
      if (rect.height === 0) continue;
      if (clientY >= rect.top && clientY < rect.bottom) {
        const id = el.dataset.sessionCardId!;
        const zone = resolveDropZone(rect.top, rect.height, clientY);
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

  const finishDrag = useCallback(() => {
    window.removeEventListener('pointermove', onPointerMoveRef.current);
    window.removeEventListener('pointerup', onPointerUpRef.current);
    window.removeEventListener('pointercancel', onPointerCancelRef.current);
    setDragId(null);
    dragIdRef.current = null;
    pressRef.current = null;
    clearDragFeedback();
    // Keep didDrag set briefly so the click fired right after a drag release
    // is still suppressed, but never swallow a later genuine click.
    if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
    didDragClearTimerRef.current = setTimeout(() => {
      didDragRef.current = false;
    }, 600);
  }, [clearDragFeedback]);

  // Listener wrappers live in refs so add/remove always target the same
  // function instances across mounts.
  const onPointerMoveRef = useRef<(e: PointerEvent) => void>(() => {});
  const onPointerUpRef = useRef<(e: PointerEvent) => void>(() => {});
  const onPointerCancelRef = useRef<(e: PointerEvent) => void>(() => {});
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
      setDragId(press.id);
    }
    positionGhost(e.clientX, e.clientY);
    hitTest(e.clientY);
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
    if (!dragCurrent || !target) {
      finishDrag();
      return;
    }
    const state = useSessionStore.getState();
    const dragged = state.sessions.find((s) => s.id === dragCurrent);
    const targetSession = state.sessions.find((s) => s.id === target.id);
    if (!dragged || !targetSession) {
      finishDrag();
      return;
    }
    // A pending placeholder (mid-creation) has no server identity — dropping
    // onto/from one would submit an unknown id to /api/claim or the order API.
    if (dragged.id.startsWith('__pending_') || targetSession.id.startsWith('__pending_')) {
      finishDrag();
      return;
    }

    // Manager-tree semantics: decide where this drop wants the dragged
    // session to live (center → target manages it; edge slot → adopt that
    // slot's group context), and reject management cycles up front.
    const { newManager, blockedByCycle } = decideManagerDrop(
      filteredRef.current,
      dragged.id,
      targetSession.id,
      target.zone,
    );

    if (blockedByCycle) {
      const prefix = isMockMode() ? '[Mock] ' : '';
      showToast(
        `${prefix}禁止：不能把「${dragged.name}」移入自己或其下级的组内（会形成递归管理）`,
        'error',
      );
      finishDrag();
      return;
    }

    const oldManager = dragged.managedBy ?? null;
    const managerChanged = newManager !== oldManager;

    // ── 非 mock 模式：真实持久化。拖到两张卡片之间 → POST /api/sessions/order；
    //    拖到卡片中心（B manage A）或拖出/移入管理组 → POST /api/unclaim 与
    //    /api/claim。只在服务端确认成功后更新本地状态；失败仅弹错误 toast，绝不
    //    假装成功。mock demo（?mock=1）走下方原本地分支，保持行为不变。
    if (!isMockMode()) {
      // center 落点但 A 已由 target 管理 → 与 mock 分支一致的静默 no-op。
      if (target.zone === 'center' && !managerChanged) {
        finishDrag();
        return;
      }
      const orderIds = buildDropOrderIds(
        state.sessions,
        listRef.current,
        filteredRef.current,
        dragged.id,
        targetSession.id,
        target.zone,
      );
      finishDrag();
      void persistSessionDrop({
        draggedId: dragged.id,
        draggedName: dragged.name || 'Untitled',
        targetName: targetSession.name || targetSession.id,
        oldManager,
        newManager,
        zone: target.zone,
        orderIds,
      });
      return;
    }

    if (target.zone === 'center') {
      // Mock "B manage A": A becomes managed by B (local state only).
      const already = dragged.managedBy === targetSession.id;
      if (!already) {
        state.updateSession(dragged.id, { managedBy: targetSession.id });
        if (isMockMode()) {
          applyMockSessionUpdate(dragged.id, { managedBy: targetSession.id });
        }
        showToast(`[Mock] 「${targetSession.name}」现在管理「${dragged.name}」`);
      }
      // Already managed by the target → silent no-op (no toast for that).
      finishDrag();
      return;
    }

    // Edge drop: (optionally) move the dragged session between groups, then
    // insert it at the exact boundary the user saw. The authoritative
    // "visible order" is the DOM card order (flat list, or preorder of the
    // manager tree). Entering a group = adopt that manager; landing on a
    // top-level slot = leave the current group (managedBy → null).
    // (oldManager / managerChanged already computed above, shared with the
    //  real-backend branch.)
    if (managerChanged) {
      state.updateSession(dragged.id, { managedBy: newManager });
      if (isMockMode()) {
        applyMockSessionUpdate(dragged.id, { managedBy: newManager });
      }
    }

    const domCards = listRef.current?.querySelectorAll<HTMLElement>('[data-session-card-id]');
    const domIds = domCards
      ? [...domCards].map((el) => el.dataset.sessionCardId ?? '').filter(Boolean)
      : filteredRef.current.map((s) => s.id);
    const ids = domIds.filter((x) => x !== dragged.id);
    const tIdx = ids.indexOf(targetSession.id);
    if (tIdx >= 0) {
      ids.splice(target.zone === 'before' ? tIdx : tIdx + 1, 0, dragged.id);
      for (const s of state.sessions) {
        if (!ids.includes(s.id)) ids.push(s.id);
      }
      useUIStore.getState().setCustomOrder(ids);
      useUIStore.getState().setSortBy('custom');

      // Toast ONLY when this drop changes a management relationship; a pure
      // position move reorders silently.
      if (managerChanged) {
        const newManagerName = newManager
          ? state.sessions.find((s) => s.id === newManager)?.name ?? newManager
          : null;
        if (newManager !== null) {
          showToast(`[Mock] 「${dragged.name}」已移入「${newManagerName}」的组`);
        } else if (oldManager !== null) {
          showToast(
            `[Mock] 「${dragged.name}」已移出「${
              state.sessions.find((s) => s.id === oldManager)?.name ?? oldManager
            }」的管理`,
          );
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
      pressRef.current = { id, x: e.clientX, y: e.clientY, dragging: false };
      window.addEventListener('pointermove', onPointerMoveRef.current);
      window.addEventListener('pointerup', onPointerUpRef.current);
      window.addEventListener('pointercancel', onPointerCancelRef.current);
    },
    [],
  );

  // Safety net: unmount mid-drag removes the window listeners.
  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', onPointerMoveRef.current);
      window.removeEventListener('pointerup', onPointerUpRef.current);
      window.removeEventListener('pointercancel', onPointerCancelRef.current);
      if (didDragClearTimerRef.current) clearTimeout(didDragClearTimerRef.current);
    };
  }, []);

  const dragSession = dragId ? sessions.find((s) => s.id === dragId) : null;
  // Drag works in the flat list AND the manager tree (same semantics:
  // center → manage, edge → insert into the visible order). Grouped-by-dir
  // lists stay non-draggable.
  const dragEnabled = (groupBy === 'none' || groupBy === 'manager') && !multiSelectMode;

  // Per-card drag props for memoized SessionItem (stable refs + primitives
  // keep unrelated cards from re-rendering).
  const dragPropsFor = useCallback(
    (session: Session) =>
      dragEnabled
        ? {
            dragEnabled: true,
            onDragHandlePointerDown: handleDragPointerDown,
            isDragSource: session.id === dragId,
            isCenterTarget: session.id === centerTargetId,
            insertZone: insertTarget?.id === session.id ? insertTarget.zone : null,
          }
        : {},
    [dragEnabled, handleDragPointerDown, dragId, centerTargetId, insertTarget],
  );

  const ghostEl = dragEnabled && dragSession && (
    <div
      ref={ghostMountRef}
      data-drag-ghost
      aria-hidden="true"
      className="fixed left-0 top-0 z-[60] w-56 pointer-events-none rounded border border-dashed border-accent bg-bg-secondary/95 px-3 py-2 shadow-panel"
      style={{ willChange: 'transform' }}
    >
      <div className="flex items-center gap-2">
        <WorkerDot status={dragSession.workerStatus} />
        <span className="text-sm text-text-primary font-medium truncate">
          {dragSession.name || 'Untitled'}
        </span>
      </div>
      <div className="mt-1 text-[10px] text-text-tertiary">
        中心 = 交给管理 · 边缘 = 插入排序
      </div>
    </div>
  );

  // Recursive collapse/expand: collapsing a manager node also collapses every
  // descendant; expanding it expands the whole subtree (same for un-collapse).
  const handleToggleManagerNode = useCallback(
    (node: ManagerNode) => {
      const ids = [node.session.id, ...collectDescendantIds(node)];
      const isCollapsed = collapsedGroups.has(node.session.id);
      if (isCollapsed) {
        removeCollapsedGroups(ids);
      } else {
        addCollapsedGroups(ids);
      }
    },
    [collapsedGroups, removeCollapsedGroups, addCollapsedGroups],
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

  if (filtered.length === 0 && (searchQuery.trim() || specialFilters.size > 0)) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 gap-2">
        <p className="text-sm text-text-tertiary">No matching sessions</p>
        <p className="text-xs text-text-tertiary">Try a different search term or clear filters</p>
      </div>
    );
  }

  if (groupBy === 'manager' && managerTree.length > 0) {
    return (
      <div className="flex flex-col" ref={listRef}>
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
                isHidden={hiddenSessionIds.has(session.id)}
                onToggleHidden={multiSelectMode ? handleToggleHidden : undefined}
                onSelect={handleSelect}
                onMenu={handleMenu}
              />
            ))}
          </GroupSection>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col" ref={listRef}>
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
        expandable={hasChildren && !multiSelectMode}
        expanded={!collapsed}
        onToggleChildren={(e) => {
          e.stopPropagation();
          onToggle(node);
        }}
        onSelect={onSelect}
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
          expandable={hasChildren && !multiSelectMode}
          expanded={!collapsed}
          onToggleChildren={(e) => {
            e.stopPropagation();
            onToggle(child);
          }}
          onSelect={onSelect}
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
