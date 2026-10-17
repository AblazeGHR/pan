import { lazy, Suspense, useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { SearchInput } from '@/components/ui/SearchInput';
import { useLocation, useNavigate } from 'react-router-dom';
import { useShallow } from 'zustand/react/shallow';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { createQuickNewSession } from '@/utils/quickNewSession';
import { SessionList } from '@/components/session/SessionList';
import { useHistorySearchViewStore } from '@/stores/historySearchViewStore';
import { SessionMenu } from '@/components/session/SessionMenu';
import { RenameSessionModal } from '@/components/session/RenameSessionModal';
import { SessionDeleteModal } from '@/components/session/SessionDeleteModal';
import { collectDescendantIds, hasManagedChildren } from '@/components/session/sessionDeletePlan';
import { UNGROUPED_WORKSPACES, getSessionListCandidates } from '@/utils/sessionFilters';
import { SidebarResizer } from './SidebarResizer';
import { SessionFilterMenu } from './SessionFilterMenu';
import { ViewEntryGroups } from './ViewEntryGroups';
import { ViewEntryRail } from './ViewEntryRail';
import { Button } from '@/components/ui/Button';
import { SelectedSessionActions } from '@/components/session/SelectedSessionActions';
import { WorkspaceManagerChangeConfirmationModal } from './WorkspaceManagerChangeConfirmationModal';
import type { WorkspaceMoveConfirmationRequest } from '@/utils/workspaceMoveConfirmation';

const AppSettingsModal = lazy(() => import('./AppSettingsModal').then((module) => ({ default: module.AppSettingsModal })));
const NewSessionModal = lazy(() => import('@/components/session/NewSessionModal').then((module) => ({ default: module.NewSessionModal })));
const ImportModal = lazy(() => import('@/components/session/ImportModal').then((module) => ({ default: module.ImportModal })));
const ManageModal = lazy(() => import('@/components/session/ManageModal').then((module) => ({ default: module.ManageModal })));
const PostboxModal = lazy(() => import('@/components/session/PostboxModal').then((module) => ({ default: module.PostboxModal })));
const SessionDetailsModal = lazy(() => import('@/components/session/SessionDetailsModal').then((module) => ({ default: module.SessionDetailsModal })));
const EditorDirectoryRoots = lazy(() => import('@/components/editor/EditorDirectoryRoots').then((module) => ({ default: module.EditorDirectoryRoots })));
import {
  PanelLeftClose,
  PanelLeft,
  Plus,
  Settings,
  Import,
  ArrowUpDown,
  Layers,
  ChevronUp,
  ChevronDown,
  Sun,
  Moon,
  Trash2,
  X,
} from 'lucide-react';

export function Sidebar({ mobileWorkspaceExpanded = false }: { mobileWorkspaceExpanded?: boolean }) {
  const matchingSessionIds = useHistorySearchViewStore((state) => state.matchingSessionIds);
  const historySearchPreparing = useHistorySearchViewStore((state) => state.preparing);
  const location = useLocation();
  const navigate = useNavigate();
  const isEditorRoute = location.pathname === '/editor';
  const { isMobile } = useMediaQuery();
  const showGroupBy = useAppSettingsStore((s) => s.showGroupBy);

  // Session store — 细粒度订阅（useShallow）：只在此切片变化时重渲染。
  // 不能用 useSessionStore() 整体订阅：inputDrafts（每次敲键）、
  // liveStreamBuffers/currentMessages（每个流式 chunk）、rendering 等无关字段
  // 都会让整条侧栏链路重渲染。
  const { multiSelectMode, exitMultiSelect, selectedIds, batchRemoveSessions, removeSessions, removeSession, sessions } =
    useSessionStore(useShallow((s) => ({
      multiSelectMode: s.multiSelectMode,
      exitMultiSelect: s.exitMultiSelect,
      selectedIds: s.selectedIds,
      batchRemoveSessions: s.batchRemoveSessions,
      removeSessions: s.removeSessions,
      removeSession: s.removeSession,
      sessions: s.sessions,
    })));

  // UI store — 同上：toast 队列、审批/输入/终端交互请求等高频字段不应触发侧栏重渲染。
  const {
    sidebarWidth,
    sidebarCollapsed,
    toggleSidebar,
    setMobileSidebarOpen,
    showToast,
    groupBy,
    cycleGroupBy,
    searchQuery,
    setSearchQuery,
    sortBy,
    cycleSortBy,
    specialFilters,
    adapterFilters,
    hiddenSessionIds,
    collapsedGroups,
    collapseAllGroups,
    expandAllGroups,
    theme,
    toggleTheme,
    dragEnabled,
    setDragEnabled,
    activeWorkspaceId,
  } = useUIStore(useShallow((s) => ({
    sidebarWidth: s.sidebarWidth,
    sidebarCollapsed: s.sidebarCollapsed,
    toggleSidebar: s.toggleSidebar,
    setMobileSidebarOpen: s.setMobileSidebarOpen,
    showToast: s.showToast,
    groupBy: s.groupBy,
    cycleGroupBy: s.cycleGroupBy,
    searchQuery: s.searchQuery,
    setSearchQuery: s.setSearchQuery,
    sortBy: s.sortBy,
    cycleSortBy: s.cycleSortBy,
    specialFilters: s.specialFilters,
    adapterFilters: s.adapterFilters,
    hiddenSessionIds: s.hiddenSessionIds,
    collapsedGroups: s.collapsedGroups,
    collapseAllGroups: s.collapseAllGroups,
    expandAllGroups: s.expandAllGroups,
    theme: s.theme,
    toggleTheme: s.toggleTheme,
    dragEnabled: s.dragEnabled,
    setDragEnabled: s.setDragEnabled,
    activeWorkspaceId: s.activeWorkspaceId,
  })));
  const effectiveGroupBy = showGroupBy ? groupBy : 'manager';
  const activeScopeName = useWorkspaceStore((state) => state.workspaces.find((workspace) => workspace.id === activeWorkspaceId)?.name);

  const [selectedActionBusy, setSelectedActionBusy] = useState(false);

  // Local state
  const [showNewModal, setShowNewModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [menuSession, setMenuSession] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });
  const [manageSessionId, setManageSessionId] = useState<string | null>(null);
  const [postboxSessionId, setPostboxSessionId] = useState<string | null>(null);
  const [detailsSessionId, setDetailsSessionId] = useState<string | null>(null);
  const [renameSessionId, setRenameSessionId] = useState<string | null>(null);
  const [showAppSettings, setShowAppSettings] = useState(false);
  const [showDragMenu, setShowDragMenu] = useState(false);
  const [workspaceMoveConfirmation, setWorkspaceMoveConfirmation] = useState<(
    WorkspaceMoveConfirmationRequest & { resolve: (confirmed: boolean) => void }
  ) | null>(null);
  const sortMenuRef = useRef<HTMLDivElement | null>(null);
  const sortPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sortLongPressedRef = useRef(false);
  const sortPressStartRef = useRef<{ x: number; y: number } | null>(null);
  const [deleteRequest, setDeleteRequest] = useState<{
    ids: string[];
    specialIds: string[];
    normalIds: string[];
    descendantCount: number;
  } | null>(null);

  const clearSortPress = useCallback(() => {
    if (sortPressTimerRef.current) clearTimeout(sortPressTimerRef.current);
    sortPressTimerRef.current = null;
    sortPressStartRef.current = null;
  }, []);

  useEffect(() => clearSortPress, [clearSortPress]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<WorkspaceMoveConfirmationRequest & { resolve: (confirmed: boolean) => void }>).detail;
      setWorkspaceMoveConfirmation(detail);
    };
    window.addEventListener('pan:confirm-workspace-manager-change', handler);
    return () => window.removeEventListener('pan:confirm-workspace-manager-change', handler);
  }, []);

  useEffect(() => {
    if (!showDragMenu) return;
    const closeOnOutsidePointer = (e: PointerEvent) => {
      if (!sortMenuRef.current?.contains(e.target as Node)) setShowDragMenu(false);
    };
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowDragMenu(false);
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [showDragMenu]);

  const handleSortPointerDown = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    sortLongPressedRef.current = false;
    clearSortPress();
    sortPressStartRef.current = { x: e.clientX, y: e.clientY };
    sortPressTimerRef.current = setTimeout(() => {
      sortLongPressedRef.current = true;
      setShowDragMenu(true);
      sortPressTimerRef.current = null;
    }, 550);
  }, [clearSortPress]);

  const handleSortPointerMove = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const start = sortPressStartRef.current;
    if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) clearSortPress();
  }, [clearSortPress]);

  const handleSortClick = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    if (sortLongPressedRef.current) {
      e.preventDefault();
      sortLongPressedRef.current = false;
      return;
    }
    cycleSortBy();
  }, [cycleSortBy]);

  const handleSortPointerUp = useCallback(() => {
    if (!sortLongPressedRef.current) clearSortPress();
  }, [clearSortPress]);

  // Scope for collapse-all / expand-all: every collapsible key in the
  // currently visible (search/filter/workspace-scoped) list, at ALL tree
  // levels. Derived from the same data-level candidate list SessionList groups
  // (NOT from rendered DOM nodes), so children hidden under collapsed parents
  // are included, while sessions filtered out by search/filters/workspace or
  // hidden are out of scope and keep their own collapse state. Key
  // normalization mirrors SessionList's workdir/manager grouping.
  const groupKeys = useMemo(() => {
    if (effectiveGroupBy !== 'workdir' && effectiveGroupBy !== 'manager') return [] as string[];
    const scoped = getSessionListCandidates(sessions, {
      multiSelectMode,
      hiddenSessionIds,
      searchQuery,
      specialFilters,
      adapterFilters,
      activeWorkspaceId,
      matchingSessionIds,
    });
    if (effectiveGroupBy === 'workdir') {
      const keys = new Set<string>();
      for (const s of scoped) {
        keys.add(s.workdir ? s.workdir.replace(/\\/g, '/').replace(/\/$/, '') : '__no_workdir');
      }
      return [...keys];
    }
    return scoped.map((s) => s.id);
  }, [sessions, effectiveGroupBy, multiSelectMode, hiddenSessionIds, searchQuery, specialFilters, adapterFilters, activeWorkspaceId, matchingSessionIds]);

  // Binary toggle judged against the operation scope only: collapsed state of
  // out-of-scope keys must not flip the button (e.g. a group collapsed in
  // another workspace or before a filter was applied).
  const allScopeCollapsed = groupKeys.length > 0 && groupKeys.every((k) => collapsedGroups.has(k));

  const selectableSessions = useMemo(
    () => getSessionListCandidates(sessions, {
      multiSelectMode: true,
      hiddenSessionIds,
      searchQuery,
      specialFilters,
      adapterFilters,
      activeWorkspaceId,
      matchingSessionIds,
    }),
    [sessions, hiddenSessionIds, searchQuery, specialFilters, adapterFilters, activeWorkspaceId, matchingSessionIds],
  );
  const selectableIds = useMemo(
    () => selectableSessions.map((session) => session.id),
    [selectableSessions],
  );
  const allSelectableSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id));

  const handleToggleSelectAll = () => {
    const selectable = new Set(selectableIds);
    const next = new Set(selectedIds);
    if (allSelectableSelected) {
      for (const id of selectable) next.delete(id);
    } else {
      for (const id of selectable) next.add(id);
    }
    useSessionStore.setState({ selectedIds: next });
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    const ids = [...selectedIds];
    const specialIds = ids.filter((id) => {
      const session = sessions.find((s) => s.id === id);
      return session ? hasManagedChildren(session) : false;
    });
    const normalIds = ids.filter((id) => !specialIds.includes(id));
    if (specialIds.length === 0) {
      if (!confirm(`Delete ${ids.length} selected session(s)?`)) return;
      batchRemoveSessions().then(() => showToast(`Deleted ${ids.length} session(s)`));
      return;
    }
    setDeleteRequest({
      ids,
      specialIds,
      normalIds,
      descendantCount: collectDescendantIds(sessions, specialIds).length,
    });
  };

  const handleDeleteRequest = (ids: string[]) => {
    const firstId = ids[0];
    if (!firstId) return;
    const specialIds = ids.filter((id) => {
      const session = sessions.find((s) => s.id === id);
      return session ? hasManagedChildren(session) : false;
    });
    if (specialIds.length === 0) {
      if (!confirm(`Delete session ${firstId.slice(0, 12)}...?`)) return;
      removeSession(firstId).catch((e) => showToast(e.message || 'Delete failed', 'error'));
      return;
    }
    setDeleteRequest({
      ids,
      specialIds,
      normalIds: ids.filter((id) => !specialIds.includes(id)),
      descendantCount: collectDescendantIds(sessions, specialIds).length,
    });
  };

  const deleteNormalAfterSpecialCancel = () => {
    const request = deleteRequest;
    setDeleteRequest(null);
    if (!request || request.normalIds.length === 0) return;
    void removeSessions(request.normalIds);
  };

  const quickNew = useCallback(() => {
    createQuickNewSession()
      .then(() => showToast('Session created'))
      .catch((e) => showToast(e.message || 'Creation failed', 'error'));
  }, [showToast]);

  // useCallback：SessionList 的 SessionItem 是 React.memo，onSessionMenu 必须
  // 引用稳定（依赖的 setMenuPosition/setMenuSession 均为稳定 setter），否则每次
  // Sidebar 重渲染（如任意 session 卡片流式更新）都会让所有 item 的回调变化 → memo 失效。
  const handleSessionMenu = useCallback((e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenuPosition({ x: rect.right + 4, y: rect.top });
    setMenuSession(id);
  }, []);

  const handleSessionClick = useCallback(() => {
    if (location.pathname !== '/jobs') return;
    navigate('/');
    if (isMobile) setMobileSidebarOpen(false);
  }, [isMobile, location.pathname, navigate, setMobileSidebarOpen]);

  // ── Nav rail (collapsed mode, desktop only) ──

  if (sidebarCollapsed && !isMobile) {
    return (
      <nav aria-label="页面入口" className="flex flex-col items-center h-full bg-bg-secondary border-r border-border-default py-3 gap-2" style={{ width: 48 }}>
        <button
          onClick={toggleSidebar}
          className="text-text-tertiary hover:text-text-primary p-1.5 rounded transition-colors"
          title="Expand sidebar"
        >
          <PanelLeft size={18} />
        </button>

        {/* Page entries — same shared list as the expanded strip
            (ViewEntryGroups), so the collapsed rail always covers every
            view route with active state, tooltip and click navigation. */}
        <ViewEntryRail />

        <div className="flex-1" />

        <button
          onClick={() => setShowAppSettings((v) => !v)}
          className="text-text-tertiary hover:text-text-primary p-1.5 rounded transition-colors"
          title="App settings"
        >
          <Settings size={18} />
        </button>
        {showAppSettings && <Suspense fallback={null}>
          <AppSettingsModal open onClose={() => setShowAppSettings(false)} />
        </Suspense>}

        <button
          onClick={toggleTheme}
          className="text-text-tertiary hover:text-text-primary p-1.5 rounded transition-colors"
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </nav>
    );
  }

  // ── Expanded sidebar ──

  return (
    <aside
      className="relative flex min-w-0 flex-col h-full border-r border-border-default bg-bg-secondary"
      style={{
        width: isMobile && mobileWorkspaceExpanded ? '50vw' : `min(${sidebarWidth}px, 100vw)`,
        minWidth: isMobile && mobileWorkspaceExpanded ? 'min(280px, 50vw)' : 'min(280px, 100vw)',
      }}
    >
      {/* ── Chat route content ── */}
      {!isEditorRoute && (
        <>
          {/* Header */}
          <div className="px-3 py-2 border-b border-border-muted">
            <div className="flex items-center justify-between mb-2">
              <h1 className="text-lg font-bold text-text-primary">Pan</h1>
              <div className="flex items-center gap-0.5">
                <button
                  onClick={() => setShowAppSettings((v) => !v)}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title="App settings"
                >
                  <Settings size={16} />
                </button>
                <button
                  onClick={toggleTheme}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
                >
                  {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
                </button>
                <button
                  onClick={() => {
                    if (isMobile) setMobileSidebarOpen(false);
                    else toggleSidebar();
                  }}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title={isMobile ? 'Close sidebar' : 'Collapse sidebar'}
                  aria-label={isMobile ? '收起侧边栏' : 'Collapse sidebar'}
                  data-testid={isMobile ? 'mobile-sidebar-close' : undefined}
                >
                  <PanelLeftClose size={16} />
                </button>
              </div>
            </div>

            <div className="mb-2"><ViewEntryGroups isMobile={isMobile} /></div>

            {/* Buttons */}
            <div className="flex gap-1">
              <div className="flex flex-[3] min-w-0 overflow-hidden rounded border border-transparent bg-accent">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={quickNew}
                  title="Quick new session"
                  className="min-w-0 flex-1 rounded-none border-0"
                >
                  <Plus size={14} />
                  New
                </Button>
                <span aria-hidden="true" className="w-px shrink-0 bg-white/30" />
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setShowNewModal(true)}
                  title="New with settings"
                  className="rounded-none border-0"
                >
                  <Settings size={14} />
                </Button>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowImportModal(true)}
                title="Import session"
                className="flex-[2] min-w-0 justify-center"
              >
                <Import size={14} />
                Import
              </Button>
            </div>
          </div>

          {/* Search + tools bar */}
          <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-muted bg-bg-secondary">
            <SearchInput value={searchQuery} onChange={setSearchQuery} placeholder="Filter..." label="session" />
            <SessionFilterMenu />
            <div ref={sortMenuRef} className="relative">
              <button
                onClick={handleSortClick}
                onPointerDown={handleSortPointerDown}
                onPointerMove={handleSortPointerMove}
                onPointerUp={handleSortPointerUp}
                onPointerCancel={clearSortPress}
                aria-expanded={showDragMenu}
                aria-label={`Sort sessions: ${sortBy}`}
                className={`flex items-center gap-1 p-1 rounded transition-colors ${
                  sortBy !== 'recent'
                    ? 'text-accent bg-accent/10'
                    : 'text-text-tertiary hover:text-text-primary'
                }`}
                title={`Sort: ${sortBy} (click to cycle recent → name → custom)`}
              >
                <ArrowUpDown size={14} />
                <span className="text-[10px] leading-none">
                  {sortBy === 'custom' ? 'custom' : sortBy === 'name' ? 'name' : 'recent'}
                </span>
              </button>
              {showDragMenu && (
                <div
                  role="menu"
                  aria-label="Session list options"
                  className="absolute right-0 top-full mt-1 z-30 w-44 rounded border border-border-default bg-bg-primary shadow-lg py-1"
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setShowDragMenu(false);
                  }}
                >
                  <label
                    role="menuitemcheckbox"
                    aria-checked={dragEnabled}
                    className="flex items-center gap-2 px-3 py-2 text-xs text-text-primary cursor-pointer hover:bg-bg-hover/40 select-none"
                  >
                    <input
                      type="checkbox"
                      checked={dragEnabled}
                      onChange={(e) => setDragEnabled(e.target.checked)}
                      className="accent-accent"
                    />
                    <span>拖动排序</span>
                  </label>
                </div>
              )}
            </div>
            {showGroupBy && (
              <button
                onClick={cycleGroupBy}
                className={`flex items-center gap-1 p-1 rounded transition-colors ${
                  groupBy !== 'none'
                    ? 'text-accent bg-accent/10'
                    : 'text-text-tertiary hover:text-text-primary'
                }`}
                title={`Group by ${groupBy === 'workdir' ? 'manager' : groupBy === 'manager' ? 'none' : 'dir'} (click to cycle)`}
              >
                <Layers size={14} />
                <span className="text-[10px] leading-none">
                  {groupBy === 'workdir' ? 'dir' : groupBy === 'manager' ? 'manager' : 'off'}
                </span>
              </button>
            )}
            {(effectiveGroupBy === 'workdir' || effectiveGroupBy === 'manager') && (
              <button
                onClick={() =>
                  allScopeCollapsed ? expandAllGroups(groupKeys) : collapseAllGroups(groupKeys)
                }
                className="p-1 rounded transition-colors text-text-tertiary hover:text-text-primary"
                title={allScopeCollapsed ? 'Expand all groups' : 'Collapse all groups'}
              >
                {allScopeCollapsed ? (
                  <ChevronUp size={14} />
                ) : (
                  <ChevronDown size={14} />
                )}
              </button>
            )}
          </div>

          {/* Multi-select bar */}
          {multiSelectMode && (
            <div className="sidebar-selection-bar relative flex min-w-0 flex-wrap items-center gap-1.5 px-2 py-2 border-b border-border-muted bg-bg-tertiary">
              <span className="shrink-0 text-xs text-text-secondary">
                {selectedIds.size} selected
              </span>
              <span className="min-w-0 max-w-full flex-1 text-[10px] text-text-tertiary truncate" title="批量操作的范围（永不跨工作区）">
                范围：{activeWorkspaceId === 'all' ? '全部' : activeWorkspaceId === UNGROUPED_WORKSPACES ? '无工作区' : activeScopeName ?? activeWorkspaceId}
              </span>
              <div className="sidebar-selection-actions ml-auto flex max-w-full flex-wrap items-center justify-end gap-1.5">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleToggleSelectAll}
                  disabled={selectedActionBusy || selectableIds.length === 0}
                  aria-label={allSelectableSelected ? 'Deselect all visible sessions' : 'Select all visible sessions'}
                >
                  {allSelectableSelected ? 'Deselect all' : 'Select all'}
                </Button>
                <SelectedSessionActions onBusyChange={setSelectedActionBusy} />
                <Button
                  variant="danger"
                  size="sm"
                  onClick={handleBatchDelete}
                  disabled={selectedActionBusy || selectedIds.size === 0}
                  aria-label="Delete selected sessions"
                  title="Delete selected sessions"
                  className="sidebar-selection-action-button"
                >
                  <span className="sidebar-selection-action-label">Delete</span>
                  <Trash2 className="sidebar-selection-action-icon" size={16} aria-hidden="true" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={exitMultiSelect}
                  disabled={selectedActionBusy}
                  aria-label="Cancel selection"
                  title="Cancel selection"
                  className="sidebar-selection-action-button"
                >
                  <span className="sidebar-selection-action-label">Cancel</span>
                  <X className="sidebar-selection-action-icon" size={16} aria-hidden="true" />
                </Button>
              </div>
            </div>
          )}

          {/* Session list */}
          <div className="flex-1 overflow-y-auto">
            {matchingSessionIds !== null && <div className="px-3 py-2 text-xs text-[var(--color-text-secondary)]" role="status">
              {matchingSessionIds.size} matching Sessions{historySearchPreparing ? ' · still searching…' : ''}
            </div>}
            <SessionList onSessionClick={handleSessionClick} onSessionMenu={handleSessionMenu} />
          </div>

          {/* Session context menu */}
          {menuSession && (
            <SessionMenu
              session={sessions.find((s) => s.id === menuSession)!}
              position={menuPosition}
              onClose={() => setMenuSession(null)}
              onManage={(id) => {
                if (isMobile) {
                  // 移动端：关闭抽屉后进入整页 Manage（不再弹 Modal）
                  useUIStore.getState().setMobileSidebarOpen(false);
                  navigate(`/manage/${id}`);
                } else {
                  setManageSessionId(id);
                }
              }}
              onPostbox={setPostboxSessionId}
              onDetails={setDetailsSessionId}
              onRename={setRenameSessionId}
              onDelete={(id) => handleDeleteRequest([id])}
            />
          )}
        </>
      )}

      {deleteRequest && (
        <SessionDeleteModal
          sessions={sessions}
          specialIds={deleteRequest.specialIds}
          normalIds={deleteRequest.normalIds}
          descendantCount={deleteRequest.descendantCount}
          onClose={deleteNormalAfterSpecialCancel}
          onCancelSpecial={deleteNormalAfterSpecialCancel}
          onConfirm={(cascade) => {
            const request = deleteRequest;
            setDeleteRequest(null);
            void removeSessions(request.ids, cascade ? request.specialIds : []);
          }}
        />
      )}

      {/* ── Editor route content ── */}
      {isEditorRoute && (
        <>
          {/* Top header — Pan title + collapse */}
          <div className="px-3 py-2 border-b border-border-muted">
            <div className="flex items-center justify-between mb-2">
              <h1 className="text-lg font-bold text-text-primary">Pan</h1>
              <div className="flex items-center gap-0.5">
                <button
                  onClick={() => setShowAppSettings((v) => !v)}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title="App settings"
                >
                  <Settings size={16} />
                </button>
                <button
                  onClick={toggleTheme}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
                >
                  {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
                </button>
                <button
                  onClick={toggleSidebar}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title="Collapse sidebar"
                >
                  <PanelLeftClose size={16} />
                </button>
              </div>
            </div>

            <ViewEntryGroups isMobile={isMobile} />
          </div>

          {/* Directory roots: CWD + workspace dirs + temp dirs, each collapsible */}
          <Suspense fallback={null}><EditorDirectoryRoots /></Suspense>
        </>
      )}

      {/* Resizer (desktop only) */}
      {!isMobile && <SidebarResizer />}

      {/* Modals */}
      {showAppSettings && <Suspense fallback={null}>
        <AppSettingsModal open onClose={() => setShowAppSettings(false)} />
      </Suspense>}
      <WorkspaceManagerChangeConfirmationModal
        request={workspaceMoveConfirmation}
        onClose={() => {
          workspaceMoveConfirmation?.resolve(false);
          setWorkspaceMoveConfirmation(null);
        }}
        onConfirm={() => {
          workspaceMoveConfirmation?.resolve(true);
          setWorkspaceMoveConfirmation(null);
        }}
      />
      <Suspense fallback={null}>
        {showNewModal && <NewSessionModal open onClose={() => setShowNewModal(false)} />}
        {showImportModal && <ImportModal open onClose={() => setShowImportModal(false)} />}
        {manageSessionId && <ManageModal
          open
          onClose={() => setManageSessionId(null)}
          sessionId={manageSessionId}
          onViewRelationship={setManageSessionId}
        />}
        {postboxSessionId && <PostboxModal
          open
          onClose={() => setPostboxSessionId(null)}
          sessionId={postboxSessionId}
        />}
        {detailsSessionId && <SessionDetailsModal
          session={sessions.find((s) => s.id === detailsSessionId) ?? null}
          onClose={() => setDetailsSessionId(null)}
        />}
      </Suspense>
      <RenameSessionModal
        session={renameSessionId ? sessions.find((s) => s.id === renameSessionId) ?? null : null}
        onClose={() => setRenameSessionId(null)}
      />
    </aside>
  );
}
