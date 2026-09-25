import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronsLeft,
  ChevronsRight,
  Folder,
  Layers,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
} from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { CREATE_WORKSPACE_DROP_TARGET_ID, useWorkspaceStore } from '@/stores/workspaceStore';
import { ALL_WORKSPACES, effectiveWorkspaceIds } from '@/utils/sessionFilters';
import type { Workspace } from '@/types';

const RAIL_WIDTH = 172;
const DRAG_THRESHOLD_PX = 5;
const MOBILE_DRAG_HOLD_MS = 350;
const WORKSPACE_STATUS_RANK: Record<string, number> = { idle: 1, held: 2, running: 3 };
const WORKSPACE_COUNT_STATUS_CLASSES: Record<string, string> = {
  running: 'border-accent/30 bg-accent/10 text-accent',
  idle: 'border-success/30 bg-success/10 text-success',
  held: 'border-warning/40 bg-warning/10 text-warning',
};
/**
 * Handle width. Chosen to fit INSIDE the chat's existing left padding
 * (`MessageBubble` rows use `px-3 sm:px-6 lg:px-8`, and the rail only renders
 * at ≥768px → 24px there, 32px at lg), so the handle reuses that whitespace
 * instead of adding another inset to the conversation.
 */
const HANDLE_WIDTH = 20;

/**
 * Workspace rail: the vertical session-group switcher attached to the right of
 * the sidebar.
 *
 * - Collapsed (0 horizontal span) only the floating handle shows, pinned to the
 *   panel's right edge and vertically centered; it carries the active workspace
 *   name and toggles the panel.
 * - Expanded, the panel takes real layout width and pushes the chat area right
 *   (it never overlays the conversation).
 *
 * The workspace scope itself is applied by `getSessionListCandidates`, so the
 * list, the search box and Select-all all narrow to the same workspace.
 */
interface WorkspaceRailProps {
  /** Render as a handle attached to the mobile Sidebar, expanding beside it. */
  mobileDrawer?: boolean;
  mobileExpanded?: boolean;
  onMobileExpandedChange?: (expanded: boolean) => void;
}

export function WorkspaceRail({
  mobileDrawer = false,
  mobileExpanded = false,
  onMobileExpandedChange,
}: WorkspaceRailProps) {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const loaded = useWorkspaceStore((s) => s.loaded);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);
  const renameWorkspace = useWorkspaceStore((s) => s.renameWorkspace);
  const deleteWorkspace = useWorkspaceStore((s) => s.deleteWorkspace);
  const sessions = useSessionStore((s) => s.sessions);
  const expanded = useUIStore((s) => s.railExpanded);
  const setRailExpanded = useUIStore((s) => s.setRailExpanded);
  const isExpanded = mobileDrawer ? mobileExpanded : expanded;
  const activeWorkspaceId = useUIStore((s) => s.activeWorkspaceId);
  const setActiveWorkspace = useUIStore((s) => s.setActiveWorkspace);
  const showToast = useUIStore((s) => s.showToast);

  const [editing, setEditing] = useState<{ id: string | '__new__'; value: string } | null>(null);
  const [menuFor, setMenuFor] = useState<{ workspace: Workspace; x: number; y: number } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Workspace | null>(null);
  const [dropHint, setDropHint] = useState<{ id: string; place: 'before' | 'after' } | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const nameErrorShown = useRef(false);

  useEffect(() => {
    if (!loaded) void loadWorkspaces();
  }, [loaded, loadWorkspaces]);

  // The remembered workspace may have been deleted elsewhere → fall back.
  useEffect(() => {
    if (!loaded) return;
    if (activeWorkspaceId !== ALL_WORKSPACES && !workspaces.some((w) => w.id === activeWorkspaceId)) {
      setActiveWorkspace(ALL_WORKSPACES);
    }
  }, [loaded, workspaces, activeWorkspaceId, setActiveWorkspace]);

  const memberStats = useMemo(() => {
    const stats = new Map<string, { count: number; workerStatus: string | null }>();
    for (const session of sessions) {
      for (const id of effectiveWorkspaceIds(session, sessions)) {
        const current = stats.get(id) ?? { count: 0, workerStatus: null };
        current.count += 1;
        const candidate = session.workerStatus ?? '';
        if ((WORKSPACE_STATUS_RANK[candidate] ?? 0) > (WORKSPACE_STATUS_RANK[current.workerStatus ?? ''] ?? 0)) {
          current.workerStatus = candidate;
        }
        stats.set(id, current);
      }
    }
    return stats;
  }, [sessions]);

  const activeName = activeWorkspaceId === ALL_WORKSPACES
    ? '全部'
    : workspaces.find((w) => w.id === activeWorkspaceId)?.name ?? '全部';

  const switchTo = useCallback((id: string) => {
    if (id === activeWorkspaceId) return;
    // Selection must never survive a scope change (it would batch across workspaces).
    useSessionStore.setState({ selectedIds: new Set() });
    setActiveWorkspace(id);
  }, [activeWorkspaceId, setActiveWorkspace]);

  /** Mirrors the backend `_workspace_name_error` + name_taken rules. */
  const validateName = useCallback((raw: string, excludeId: string | null): string | null => {
    const name = raw.trim();
    if (!name) return '名称不能为空';
    if (name.length > 128) return '名称过长（最多 128 个字符）';
    if (workspaces.some((w) => w.name === name && w.id !== excludeId)) return '已存在同名工作区';
    return null;
  }, [workspaces]);

  const commitEditing = useCallback(async (opts: { keepEditingOnError: boolean }) => {
    const current = editing;
    if (!current) return;
    const isNew = current.id === '__new__';
    const name = current.value.trim();
    if (!isNew && workspaces.find((w) => w.id === current.id)?.name === name) {
      setEditing(null);   // unchanged → silent no-op
      return;
    }
    const error = validateName(current.value, isNew ? null : current.id);
    if (error) {
      if (!nameErrorShown.current || opts.keepEditingOnError) showToast(error, 'error');
      nameErrorShown.current = true;
      if (opts.keepEditingOnError) return;   // stay in the input
      setEditing(null);
      return;
    }
    nameErrorShown.current = false;
    setEditing(null);
    try {
      if (isNew) {
        const workspace = await createWorkspace(name);
        useSessionStore.setState({ selectedIds: new Set() });
        setActiveWorkspace(workspace.id);
        showToast(`已创建并切换到「${workspace.name}」`);
      } else {
        await renameWorkspace(current.id, name);
        showToast(`已重命名为「${name}」`);
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : '操作失败', 'error');
    }
  }, [editing, workspaces, validateName, showToast, createWorkspace, renameWorkspace, setActiveWorkspace]);

  const toggleReassign = (target: Workspace | null) => {
    setMenuFor(null);
    if (target) setDeleteTarget(target);
  };

  const confirmDelete = async () => {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (!target) return;
    try {
      await deleteWorkspace(target.id);
      if (activeWorkspaceId === target.id) switchTo(ALL_WORKSPACES);
      showToast(`已删除「${target.name}」`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '删除失败', 'error');
    }
  };

  /* ── tab drag-reorder (whole-row long press on touch; threshold on mouse) ── */
  const dragRef = useRef<{
    id: string;
    startX: number;
    startY: number;
    lastY: number;
    pointerType: string;
    active: boolean;
    scrollOnly: boolean;
    longPressReady: boolean;
    longPressTimer: number | null;
  } | null>(null);
  const tabScrollRef = useRef<HTMLDivElement>(null);
  const suppressTabClickRef = useRef(false);
  const suppressTabClickTimerRef = useRef<number | null>(null);
  const dropHintRef = useRef<typeof dropHint>(null);
  const lastYRef = useRef(0);
  const pointerUpHandlerRef = useRef<() => void>(() => {});
  const pointerCancelHandlerRef = useRef<() => void>(() => {});

  const setCurrentDropHint = useCallback((hint: typeof dropHint) => {
    dropHintRef.current = hint;
    setDropHint(hint);
  }, []);

  const updateDropHint = useCallback(() => {
    const drag = dragRef.current;
    if (!drag?.active) return;
    for (const el of document.querySelectorAll<HTMLElement>('[data-workspace-tab-id]')) {
      const id = el.dataset.workspaceTabId!;
      if (id === drag.id || id === ALL_WORKSPACES || id === CREATE_WORKSPACE_DROP_TARGET_ID) continue;
      const rect = el.getBoundingClientRect();
      if (lastYRef.current >= rect.top && lastYRef.current < rect.bottom) {
        setCurrentDropHint({ id, place: lastYRef.current < rect.top + rect.height / 2 ? 'before' : 'after' });
        return;
      }
    }
    setCurrentDropHint(null);
  }, [setCurrentDropHint]);

  const onDragMove = useCallback((e: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    if (drag.pointerType === 'touch' && !drag.active) {
      const deltaY = e.clientY - drag.lastY;
      const moved = Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) >= DRAG_THRESHOLD_PX;
      if (!drag.longPressReady && moved) {
        if (drag.longPressTimer !== null) window.clearTimeout(drag.longPressTimer);
        drag.longPressTimer = null;
        drag.scrollOnly = true;
      }
      if (drag.scrollOnly) {
        if (tabScrollRef.current) tabScrollRef.current.scrollTop -= deltaY;
        suppressTabClickRef.current = true;
      }
      drag.lastY = e.clientY;
      lastYRef.current = e.clientY;
      return;
    }
    if (!drag.active) {
      if (Math.abs(e.clientY - drag.startY) < DRAG_THRESHOLD_PX) return;
      drag.active = true;
      setDraggingId(drag.id);
      document.body.classList.add('select-none');
    }
    lastYRef.current = e.clientY;
    updateDropHint();
  }, [updateDropHint]);

  const finishTabDrag = useCallback((shouldReorder: boolean) => {
    window.removeEventListener('pointermove', onDragMove);
    window.removeEventListener('pointerup', pointerUpHandlerRef.current);
    window.removeEventListener('pointercancel', pointerCancelHandlerRef.current);
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.longPressTimer !== null && drag?.longPressTimer !== undefined) {
      window.clearTimeout(drag.longPressTimer);
    }
    if (drag?.active || drag?.scrollOnly) {
      suppressTabClickRef.current = true;
      if (suppressTabClickTimerRef.current !== null) window.clearTimeout(suppressTabClickTimerRef.current);
      suppressTabClickTimerRef.current = window.setTimeout(() => {
        suppressTabClickRef.current = false;
        suppressTabClickTimerRef.current = null;
      }, 0);
    }
    document.body.classList.remove('select-none');
    setDraggingId(null);
    const hint = dropHintRef.current;
    setCurrentDropHint(null);
    if (!shouldReorder || !drag?.active || !hint) return;
    // Read the store at pointerup time. The pointer listener can outlive the
    // render that installed it, so component state captured by that listener
    // may no longer represent either the current hint or workspace order.
    const current = useWorkspaceStore.getState();
    const ids = current.workspaces.map((w) => w.id).filter((id) => id !== drag.id);
    const index = ids.indexOf(hint.id);
    if (index === -1) return;
    ids.splice(hint.place === 'before' ? index : index + 1, 0, drag.id);
    const currentIds = current.workspaces.map((workspace) => workspace.id);
    if (ids.every((id, i) => id === currentIds[i])) return;
    void current.reorderWorkspaces(ids).catch((e) => {
      useUIStore.getState().showToast(e instanceof Error ? e.message : '排序失败', 'error');
    });
  }, [onDragMove, setCurrentDropHint]);

  const onDragUp = useCallback(() => finishTabDrag(true), [finishTabDrag]);
  const onDragCancel = useCallback(() => finishTabDrag(false), [finishTabDrag]);
  pointerUpHandlerRef.current = onDragUp;
  pointerCancelHandlerRef.current = onDragCancel;

  const startTabDrag = (e: React.PointerEvent, workspaceId: string) => {
    const pointerType = e.pointerType || (mobileDrawer ? 'touch' : 'mouse');
    if (pointerType === 'mouse' && typeof e.button === 'number' && e.button !== 0) return;
    if (suppressTabClickTimerRef.current !== null) window.clearTimeout(suppressTabClickTimerRef.current);
    suppressTabClickTimerRef.current = null;
    suppressTabClickRef.current = false;
    const drag = {
      id: workspaceId,
      startX: e.clientX,
      startY: e.clientY,
      lastY: e.clientY,
      pointerType,
      active: false,
      scrollOnly: false,
      longPressReady: false,
      longPressTimer: null as number | null,
    };
    dragRef.current = drag;
    if (mobileDrawer && pointerType === 'touch') {
      drag.longPressTimer = window.setTimeout(() => {
        if (dragRef.current !== drag) return;
        drag.longPressTimer = null;
        drag.longPressReady = true;
        drag.active = true;
        setDraggingId(workspaceId);
        document.body.classList.add('select-none');
      }, MOBILE_DRAG_HOLD_MS);
    }
    lastYRef.current = e.clientY;
    setCurrentDropHint(null);
    window.addEventListener('pointermove', onDragMove);
    window.addEventListener('pointerup', onDragUp);
    window.addEventListener('pointercancel', onDragCancel);
  };

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', onDragMove);
      window.removeEventListener('pointerup', onDragUp);
      window.removeEventListener('pointercancel', onDragCancel);
      if (dragRef.current?.longPressTimer !== null && dragRef.current?.longPressTimer !== undefined) {
        window.clearTimeout(dragRef.current.longPressTimer);
      }
      if (suppressTabClickTimerRef.current !== null) window.clearTimeout(suppressTabClickTimerRef.current);
      document.body.classList.remove('select-none');
    };
  }, [onDragMove, onDragUp, onDragCancel]);

  const tabClass = (active: boolean, isDragging: boolean, hint: 'before' | 'after' | null) => [
    'group relative flex min-w-0 cursor-pointer items-center rounded-md border transition-colors',
    mobileDrawer ? 'min-h-11 gap-1 px-1 py-1 text-xs touch-none select-none' : 'gap-1.5 px-2 py-1.5 text-xs',
    active
      ? 'border-accent/30 bg-accent/10 text-accent'
      : 'border-transparent text-text-secondary hover:bg-bg-hover hover:text-text-primary',
    isDragging ? 'opacity-60 outline-2 outline-dashed outline-accent/70 -outline-offset-2' : '',
    hint === 'before' ? 'shadow-[inset_0_3px_0_var(--color-accent)]' : '',
    hint === 'after' ? 'shadow-[inset_0_-3px_0_var(--color-accent)]' : '',
  ].join(' ');

  return (
    <div
      data-testid={mobileDrawer ? (isExpanded ? 'mobile-workspace-rail-expanded' : 'mobile-workspace-rail-collapsed') : undefined}
      className={mobileDrawer
        ? isExpanded
          ? 'relative z-30 flex h-full min-w-0 flex-1 self-stretch'
          : 'relative z-30 h-full w-11 flex-none self-stretch'
        : 'relative z-30 flex-none self-stretch transition-[width] duration-200 ease-out'}
      style={mobileDrawer ? undefined : { width: isExpanded ? RAIL_WIDTH : 0 }}
    >
      {/* Expanded panel (clipped by the wrapper width while animating) */}
      {(!mobileDrawer || isExpanded) && <div
        className={mobileDrawer
          ? 'flex h-full w-full min-w-0 flex-col overflow-hidden border-l border-border-default bg-bg-secondary'
          : 'absolute inset-y-0 right-0 flex w-[172px] flex-col overflow-hidden border-r border-border-default bg-bg-secondary transition-opacity duration-150'}
        style={mobileDrawer ? undefined : { opacity: isExpanded ? 1 : 0, pointerEvents: isExpanded ? 'auto' : 'none' }}
        aria-hidden={mobileDrawer ? undefined : !isExpanded}
      >
        <div className="flex items-center gap-1 border-b border-border-muted px-2 py-2">
          {mobileDrawer ? (
            <>
              <button
                type="button"
                className="flex min-h-11 min-w-11 items-center justify-center rounded px-2 text-sm text-text-primary transition-colors hover:bg-bg-hover"
                aria-label="收起工作区面板"
                title="收起工作区面板"
                onClick={() => onMobileExpandedChange?.(false)}
              >
                <ChevronsLeft size={16} />
              </button>
              <span className="flex-1 text-sm font-medium text-text-primary">工作区</span>
            </>
          ) : (
            <>
              <span className="flex-1 text-xs tracking-wide text-text-secondary">工作区</span>
              <button
                type="button"
                className="rounded p-0.5 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-primary"
                title="收起"
                onClick={() => setRailExpanded(false)}
              >
                <ChevronsLeft size={14} />
              </button>
            </>
          )}
        </div>

        <div ref={tabScrollRef} data-testid="workspace-tab-scroll" className="flex-1 overflow-y-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <div
            data-workspace-tab-id={ALL_WORKSPACES}
            role="button"
            tabIndex={0}
            className={tabClass(activeWorkspaceId === ALL_WORKSPACES, false, null)}
            title="全部会话（虚拟工作区，不产生归属）"
            onClick={() => switchTo(ALL_WORKSPACES)}
            onKeyDown={(e) => { if (e.key === 'Enter') switchTo(ALL_WORKSPACES); }}
          >
            <Layers size={mobileDrawer ? 11 : 12} className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">全部</span>
            <span className={`shrink-0 rounded-full border border-border-muted bg-bg-tertiary ${mobileDrawer ? 'px-1 text-[9px]' : 'px-1.5 text-[10px]'} leading-4 text-text-tertiary`}>
              {sessions.length}
            </span>
          </div>

          {workspaces.map((workspace) => {
            const isEditing = editing?.id === workspace.id;
            const memberStat = memberStats.get(workspace.id);
            if (isEditing) {
              return (
                <div key={workspace.id} className="flex items-center gap-1.5 px-2 py-1">
                  <Folder size={12} className="text-text-tertiary" />
                  <input
                    autoFocus
                    value={editing.value}
                    className="min-w-0 flex-1 rounded border border-accent bg-bg-primary px-1.5 py-0.5 text-xs text-text-primary outline-none"
                    placeholder="工作区名…"
                    onChange={(e) => setEditing({ id: workspace.id, value: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.nativeEvent.isComposing) return;
                      if (e.key === 'Enter') void commitEditing({ keepEditingOnError: true });
                      if (e.key === 'Escape') setEditing(null);
                    }}
                    onBlur={() => void commitEditing({ keepEditingOnError: false })}
                  />
                </div>
              );
            }
            return (
              <div
                key={workspace.id}
                data-workspace-tab-id={workspace.id}
                data-testid={`workspace-tab-${workspace.id}`}
                role="button"
                tabIndex={0}
                className={tabClass(activeWorkspaceId === workspace.id, draggingId === workspace.id, dropHint?.id === workspace.id ? dropHint.place : null)}
                aria-label={mobileDrawer ? `工作区 ${workspace.name}，短按切换，长按拖动排序` : undefined}
                title={workspace.name}
                onClick={() => {
                  if (suppressTabClickRef.current) {
                    suppressTabClickRef.current = false;
                    return;
                  }
                  switchTo(workspace.id);
                }}
                onDoubleClick={() => { nameErrorShown.current = false; setEditing({ id: workspace.id, value: workspace.name }); }}
                onKeyDown={(e) => { if (e.key === 'Enter') switchTo(workspace.id); }}
                onPointerDown={(e) => startTabDrag(e, workspace.id)}
              >
                <Folder size={mobileDrawer ? 11 : 12} className="shrink-0" />
                <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                <span
                  data-testid={`workspace-count-${workspace.id}`}
                  data-worker-status={memberStat?.workerStatus ?? 'offline'}
                  className={`shrink-0 rounded-full border ${mobileDrawer ? 'px-1 text-[9px]' : 'px-1.5 text-[10px]'} leading-4 ${WORKSPACE_COUNT_STATUS_CLASSES[memberStat?.workerStatus ?? ''] ?? 'border-border-muted bg-bg-tertiary text-text-tertiary'}`}
                >
                  {memberStat?.count ?? 0}
                </span>
                <button
                  type="button"
                  className="hidden rounded p-0.5 text-text-tertiary group-hover:block hover:bg-bg-tertiary hover:text-text-primary"
                  title="更多"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                    setMenuFor({ workspace, x: rect.left, y: rect.bottom + 4 });
                  }}
                >
                  <MoreHorizontal size={12} />
                </button>
              </div>
            );
          })}

          {editing?.id === '__new__' ? (
            <div className="flex items-center gap-1.5 px-2 py-1">
              <Folder size={12} className="text-text-tertiary" />
              <input
                autoFocus
                value={editing.value}
                className="min-w-0 flex-1 rounded border border-accent bg-bg-primary px-1.5 py-0.5 text-xs text-text-primary outline-none"
                placeholder="新工作区名…"
                onChange={(e) => setEditing({ id: '__new__', value: e.target.value })}
                onKeyDown={(e) => {
                  if (e.nativeEvent.isComposing) return;
                  if (e.key === 'Enter') void commitEditing({ keepEditingOnError: true });
                  if (e.key === 'Escape') setEditing(null);
                }}
                onBlur={() => void commitEditing({ keepEditingOnError: false })}
              />
            </div>
          ) : (
            <button
              type="button"
              data-workspace-tab-id={CREATE_WORKSPACE_DROP_TARGET_ID}
              className={`mt-1 flex w-full items-center gap-1 rounded-md border border-dashed border-border-default px-1.5 py-1 text-left text-xs text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-primary ${mobileDrawer ? 'min-h-11' : ''}`}
              onClick={() => {
                nameErrorShown.current = false;
                setEditing({ id: '__new__', value: '' });
                if (mobileDrawer) onMobileExpandedChange?.(true);
                else setRailExpanded(true);
              }}
            >
              <Plus size={12} />
              新建工作区
            </button>
          )}
        </div>
      </div>}

      {/* Floating handle: pinned to the panel's right edge, vertically centered */}
      {(!mobileDrawer || !isExpanded) && <button
        type="button"
        className={mobileDrawer
          ? 'absolute left-0 top-1/2 flex min-h-11 min-w-11 -translate-y-1/2 flex-col items-center justify-center gap-1 rounded-r-lg border border-l-0 border-border-default bg-bg-tertiary text-text-secondary shadow-lg transition-colors hover:bg-bg-hover hover:text-text-primary'
          : 'absolute right-0 top-1/2 z-40 flex -translate-y-1/2 translate-x-full flex-col items-center gap-1.5 rounded-r-lg border border-l-0 border-border-default bg-bg-tertiary px-0.5 py-2.5 text-text-secondary shadow-lg transition-colors hover:bg-bg-hover hover:text-text-primary'}
        style={mobileDrawer ? undefined : { width: HANDLE_WIDTH }}
        title={isExpanded ? `收起工作区面板（当前：${activeName}）` : `当前：${activeName} — 点击展开工作区`}
        aria-label={mobileDrawer ? '展开工作区' : undefined}
        onClick={() => {
          if (mobileDrawer) onMobileExpandedChange?.(!isExpanded);
          else setRailExpanded(!isExpanded);
        }}
      >
        {isExpanded ? <ChevronsLeft size={13} /> : <ChevronsRight size={13} />}
        <span className="max-h-[52vh] overflow-hidden text-ellipsis whitespace-nowrap text-xs tracking-wide [text-orientation:mixed] [writing-mode:vertical-rl]">
          {activeName}
        </span>
      </button>}

      {/* Tab actions menu */}
      {menuFor && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenuFor(null)} />
          <div
            className="fixed z-50 min-w-[120px] rounded-md border border-border-default bg-bg-tertiary py-1 shadow-xl"
            style={{ left: menuFor.x, top: menuFor.y }}
          >
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20"
              onClick={() => { nameErrorShown.current = false; setEditing({ id: menuFor.workspace.id, value: menuFor.workspace.name }); setMenuFor(null); }}
            >
              <Pencil size={12} className="text-text-tertiary" />
              重命名
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-danger transition-colors hover:bg-danger/10"
              onClick={() => toggleReassign(menuFor.workspace)}
            >
              <Trash2 size={12} />
              删除
            </button>
          </div>
        </>
      )}

      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="删除工作区" size="sm">
        {deleteTarget && (
          <div className="flex flex-col gap-4">
            <p className="text-xs leading-relaxed text-text-secondary">
              确定删除工作区「<span className="text-text-primary">{deleteTarget.name}</span>」？
              会话不会被删除，只会解除归属（变为未分组）。
              {(memberStats.get(deleteTarget.id)?.count ?? 0) > 0 && (
                <> 当前有 <span className="text-text-primary">{memberStats.get(deleteTarget.id)?.count}</span> 个会话在该工作区。</>
              )}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded-md border border-border-default bg-bg-tertiary px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-hover"
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="rounded-md border border-danger bg-danger px-3 py-1.5 text-xs font-medium text-white transition-colors hover:brightness-110"
                onClick={() => void confirmDelete()}
              >
                删除
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
