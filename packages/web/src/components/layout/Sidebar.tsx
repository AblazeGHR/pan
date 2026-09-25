import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useShallow } from 'zustand/react/shallow';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEditorStore } from '@/stores/editorStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { SessionList } from '@/components/session/SessionList';
import { NewSessionModal } from '@/components/session/NewSessionModal';
import { ImportModal } from '@/components/session/ImportModal';
import { ManageModal } from '@/components/session/ManageModal';
import { PostboxModal } from '@/components/session/PostboxModal';
import { SessionMenu } from '@/components/session/SessionMenu';
import { SessionDetailsModal } from '@/components/session/SessionDetailsModal';
import { RenameSessionModal } from '@/components/session/RenameSessionModal';
import { SessionDeleteModal } from '@/components/session/SessionDeleteModal';
import { collectDescendantIds, hasManagedChildren } from '@/components/session/sessionDeletePlan';
import { SPECIAL_FILTERS, getSessionListCandidates } from '@/utils/sessionFilters';
import { FileTree } from '@/components/editor/FileTree';
import { SidebarResizer } from './SidebarResizer';
import { AppSettingsModal } from './AppSettingsModal';
import { Button } from '@/components/ui/Button';
import { WorkspaceManagerChangeConfirmationModal } from './WorkspaceManagerChangeConfirmationModal';
import type { WorkspaceMoveConfirmationRequest } from '@/utils/workspaceMoveConfirmation';
import {
  MessageSquare,
  Code,
  PanelLeftClose,
  PanelLeft,
  Plus,
  Settings,
  Import,
  FolderOpen,
  RefreshCw,
  Search,
  ArrowUpDown,
  Layers,
  ListFilter,
  ChevronUp,
  ChevronDown,
  Sun,
  Moon,
  CalendarClock,
} from 'lucide-react';

export function Sidebar({ mobileWorkspaceExpanded = false }: { mobileWorkspaceExpanded?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isEditorRoute = location.pathname === '/editor';
  const { isMobile } = useMediaQuery();

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
  const currentSession = useCurrentSession();

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
    hiddenSessionIds,
    toggleSpecialFilter,
    clearSpecialFilters,
    collapsedGroups,
    collapseAllGroups,
    expandAllGroups,
    filesCollapsed,
    toggleFilesCollapsed,
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
    hiddenSessionIds: s.hiddenSessionIds,
    toggleSpecialFilter: s.toggleSpecialFilter,
    clearSpecialFilters: s.clearSpecialFilters,
    collapsedGroups: s.collapsedGroups,
    collapseAllGroups: s.collapseAllGroups,
    expandAllGroups: s.expandAllGroups,
    filesCollapsed: s.filesCollapsed,
    toggleFilesCollapsed: s.toggleFilesCollapsed,
    theme: s.theme,
    toggleTheme: s.toggleTheme,
    dragEnabled: s.dragEnabled,
    setDragEnabled: s.setDragEnabled,
    activeWorkspaceId: s.activeWorkspaceId,
  })));
  // Workspace rail state (batch move menu + scope label).
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const workspacesLoaded = useWorkspaceStore((s) => s.loaded);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);

  // Editor store
  const treeLoading = useEditorStore((s) => s.treeLoading);
  const refreshTree = useEditorStore((s) => s.refreshTree);

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
  const [showFilterMenu, setShowFilterMenu] = useState(false);
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

  // Group keys for collapse-all (mirrors SessionList workdir/manager grouping)
  const groupKeys = useMemo(() => {
    if (groupBy === 'workdir') {
      const keys = new Set<string>();
      for (const s of sessions) {
        if (s.workdir) {
          keys.add(s.workdir.replace(/\\/g, '/').replace(/\/$/, ''));
        } else {
          keys.add('__no_workdir');
        }
      }
      return [...keys];
    }
    if (groupBy === 'manager') {
      return sessions.map((s) => s.id);
    }
    return [] as string[];
  }, [sessions, groupBy]);

  const selectableSessions = useMemo(
    () => getSessionListCandidates(sessions, {
      multiSelectMode: true,
      hiddenSessionIds,
      searchQuery,
      specialFilters,
      activeWorkspaceId,
    }),
    [sessions, hiddenSessionIds, searchQuery, specialFilters, activeWorkspaceId],
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

  /** Batch workspace move (single membership + manager cascade, scoped selection). */
  const [moveMenuOpen, setMoveMenuOpen] = useState(false);
  const activeScopeName = activeWorkspaceId === 'all'
    ? '全部'
    : workspaces.find((w) => w.id === activeWorkspaceId)?.name ?? '全部';

  // The rail loads workspaces on desktop; the batch menu is reachable on
  // mobile too, so make sure the list exists before it renders items.
  useEffect(() => {
    if (multiSelectMode && !workspacesLoaded) void loadWorkspaces();
  }, [multiSelectMode, workspacesLoaded, loadWorkspaces]);

  const handleBatchMove = async (workspaceId: string | null) => {
    setMoveMenuOpen(false);
    if (selectedIds.size === 0) return;
    try {
      const changed = await useWorkspaceStore.getState().moveSessions([...selectedIds], workspaceId);
      if (changed.length === 0) {
        showToast(workspaceId ? '所选会话均已在目标工作区' : '所选会话本就没有归属', 'error');
      } else if (workspaceId) {
        const name = useWorkspaceStore.getState().workspaces.find((w) => w.id === workspaceId)?.name ?? '工作区';
        showToast(`已将 ${changed.length} 个会话移入「${name}」`);
      } else {
        showToast(`已将 ${changed.length} 个会话移出工作区（未分组）`);
      }
      exitMultiSelect();
    } catch (e) {
      showToast(e instanceof Error ? e.message : '移动失败', 'error');
    }
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
    const name = nextSessionDefaultName(sessions);
    const store = useSessionStore.getState();
    store
      .createNewSession(name)
      .then(() => showToast('Session created'))
      .catch((e) => showToast(e.message || 'Creation failed', 'error'));
  }, [sessions, showToast]);

  // useCallback：SessionList 的 SessionItem 是 React.memo，onSessionMenu 必须
  // 引用稳定（依赖的 setMenuPosition/setMenuSession 均为稳定 setter），否则每次
  // Sidebar 重渲染（如任意 session 卡片流式更新）都会让所有 item 的回调变化 → memo 失效。
  const handleSessionMenu = useCallback((e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenuPosition({ x: rect.right + 4, y: rect.top });
    setMenuSession(id);
  }, []);

  // ── Nav rail (collapsed mode, desktop only) ──

  if (sidebarCollapsed && !isMobile) {
    return (
      <nav className="flex flex-col items-center h-full bg-bg-secondary border-r border-border-default py-3 gap-2" style={{ width: 48 }}>
        <button
          onClick={toggleSidebar}
          className="text-text-tertiary hover:text-text-primary p-1.5 rounded transition-colors"
          title="Expand sidebar"
        >
          <PanelLeft size={18} />
        </button>

        <NavLink
          to="/"
          end
          title="Chat"
          className={({ isActive }) =>
            `p-1.5 rounded transition-colors ${
              isActive
                ? 'text-accent bg-accent/10'
                : 'text-text-tertiary hover:text-text-primary hover:bg-bg-hover'
            }`
          }
        >
          <MessageSquare size={18} />
        </NavLink>

        <NavLink
          to="/editor"
          title="Editor"
          className={({ isActive }) =>
            `p-1.5 rounded transition-colors ${
              isActive
                ? 'text-accent bg-accent/10'
                : 'text-text-tertiary hover:text-text-primary hover:bg-bg-hover'
            }`
          }
        >
          <Code size={18} />
        </NavLink>

        <NavLink
          to="/schedules"
          title="Scheduled tasks"
          className={({ isActive }) =>
            `p-1.5 rounded transition-colors ${
              isActive
                ? 'text-accent bg-accent/10'
                : 'text-text-tertiary hover:text-text-primary hover:bg-bg-hover'
            }`
          }
        >
          <CalendarClock size={18} />
        </NavLink>

        <div className="flex-1" />

        <button
          onClick={() => setShowAppSettings((v) => !v)}
          className="text-text-tertiary hover:text-text-primary p-1.5 rounded transition-colors"
          title="App settings"
        >
          <Settings size={18} />
        </button>
        <AppSettingsModal
          open={showAppSettings}
          onClose={() => setShowAppSettings(false)}
        />

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
      style={{ width: isMobile && mobileWorkspaceExpanded ? '50vw' : sidebarWidth }}
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

            {/* Route nav */}
            <div className="flex gap-1 mb-2">
              <NavLink
                to="/"
                end
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <MessageSquare size={12} />
                Chat
              </NavLink>
              <NavLink
                to="/editor"
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <Code size={12} />
                Editor
              </NavLink>
              <NavLink
                to="/schedules"
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <CalendarClock size={12} />
                Tasks
              </NavLink>
            </div>

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
            <div className="relative flex-1">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
              <input
                type="text"
                placeholder="Filter..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1 pl-6 pr-6 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-text-tertiary hover:text-text-primary"
                  aria-label="Clear session search"
                  title="Clear session search"
                >
                  ×
                </button>
              )}
            </div>
            <div className="relative">
              <button
                onClick={() => setShowFilterMenu((v) => !v)}
                className={`flex items-center gap-1 p-1 rounded transition-colors ${
                  specialFilters.size > 0
                    ? 'text-accent bg-accent/10'
                    : 'text-text-tertiary hover:text-text-primary'
                }`}
                title="Special filters"
                aria-haspopup="menu"
                aria-expanded={showFilterMenu}
              >
                <ListFilter size={14} />
                {specialFilters.size > 0 && (
                  <span className="text-[10px] leading-none font-medium">{specialFilters.size}</span>
                )}
              </button>
              {showFilterMenu && (
                <>
                  {/* Click-away backdrop */}
                  <div className="fixed inset-0 z-20" onClick={() => setShowFilterMenu(false)} />
                  <div
                    role="menu"
                    className="absolute left-0 top-full mt-1 z-30 w-64 rounded border border-border-default bg-bg-primary shadow-lg py-1"
                  >
                    {SPECIAL_FILTERS.map((f) => (
                      <label
                        key={f.id}
                        role="menuitemcheckbox"
                        aria-checked={specialFilters.has(f.id)}
                        className="flex items-start gap-2 px-3 py-1.5 cursor-pointer hover:bg-bg-hover/40 select-none"
                      >
                        <input
                          type="checkbox"
                          checked={specialFilters.has(f.id)}
                          onChange={() => toggleSpecialFilter(f.id)}
                          className="mt-0.5 accent-accent shrink-0"
                        />
                        <span className="min-w-0">
                          <span className="block text-xs text-text-primary">{f.label}</span>
                          <span className="block text-[10px] text-text-tertiary leading-tight">{f.description}</span>
                        </span>
                      </label>
                    ))}
                    {specialFilters.size > 0 && (
                      <button
                        onClick={clearSpecialFilters}
                        className="w-full text-left px-3 py-1.5 mt-1 text-xs text-text-tertiary hover:text-text-primary border-t border-border-muted"
                      >
                        Clear filters
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
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
            {(groupBy === 'workdir' || groupBy === 'manager') && (
              <button
                onClick={() =>
                  collapsedGroups.size > 0 ? expandAllGroups() : collapseAllGroups(groupKeys)
                }
                className="p-1 rounded transition-colors text-text-tertiary hover:text-text-primary"
                title={collapsedGroups.size > 0 ? 'Expand all groups' : 'Collapse all groups'}
              >
                {collapsedGroups.size > 0 ? (
                  <ChevronUp size={14} />
                ) : (
                  <ChevronDown size={14} />
                )}
              </button>
            )}
          </div>

          {/* Multi-select bar */}
          {multiSelectMode && (
            <div className="relative flex items-center gap-2 px-3 py-2 border-b border-border-muted bg-bg-tertiary">
              <span className="text-xs text-text-secondary">
                {selectedIds.size} selected
              </span>
              <span className="text-[10px] text-text-tertiary truncate" title="批量操作的范围（永不跨工作区）">
                范围：{activeScopeName}
              </span>
              <div className="flex-1" />
              <Button
                variant="ghost"
                size="sm"
                onClick={handleToggleSelectAll}
                disabled={selectableIds.length === 0}
                aria-label={allSelectableSelected ? 'Deselect all visible sessions' : 'Select all visible sessions'}
              >
                {allSelectableSelected ? 'Deselect all' : 'Select all'}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setMoveMenuOpen((v) => !v)}
                disabled={selectedIds.size === 0}
              >
                移入工作区
              </Button>
              {moveMenuOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setMoveMenuOpen(false)} />
                  <div className="absolute right-0 top-full z-50 mt-1 max-h-64 w-44 overflow-y-auto rounded-md border border-border-default bg-bg-tertiary py-1 shadow-xl">
                    <button
                      type="button"
                      className="w-full px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20"
                      onClick={() => void handleBatchMove(null)}
                    >
                      未分组（移出工作区）
                    </button>
                    {workspaces.map((workspace) => (
                      <button
                        key={workspace.id}
                        type="button"
                        className="w-full truncate px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20"
                        onClick={() => void handleBatchMove(workspace.id)}
                      >
                        {workspace.name}
                      </button>
                    ))}
                  </div>
                </>
              )}
              <Button
                variant="danger"
                size="sm"
                onClick={handleBatchDelete}
                disabled={selectedIds.size === 0}
              >
                Delete
              </Button>
              <Button variant="ghost" size="sm" onClick={exitMultiSelect}>
                Cancel
              </Button>
            </div>
          )}

          {/* Session list */}
          <div className="flex-1 overflow-y-auto">
            <SessionList onSessionMenu={handleSessionMenu} />
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

            {/* Route nav */}
            <div className="flex gap-1">
              <NavLink
                to="/"
                end
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <MessageSquare size={12} />
                Chat
              </NavLink>
              <NavLink
                to="/editor"
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <Code size={12} />
                Editor
              </NavLink>
              <NavLink
                to="/schedules"
                className={({ isActive }) =>
                  `flex-1 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
                    isActive
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
                  }`
                }
              >
                <CalendarClock size={12} />
                Tasks
              </NavLink>
            </div>
          </div>

          {/* Files section — collapsible */}
          <div className="flex items-center justify-between px-3 py-2 border-b border-border-default min-h-[40px] cursor-pointer select-none hover:bg-bg-hover/30 transition-colors" onClick={toggleFilesCollapsed}>
            <div className="flex items-center gap-1.5 text-xs font-semibold text-text-tertiary uppercase tracking-wider">
              <FolderOpen size={13} />
              Files
            </div>
            <div className="flex items-center gap-0.5">
              {!filesCollapsed && (
                <button
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  onClick={(e) => {
                    e.stopPropagation();
                    refreshTree('');
                  }}
                  title="Refresh file tree"
                >
                  <RefreshCw size={13} className={treeLoading ? 'animate-spin' : ''} />
                </button>
              )}
              {filesCollapsed ? (
                <ChevronDown size={14} className="text-text-tertiary" />
              ) : (
                <ChevronUp size={14} className="text-text-tertiary" />
              )}
            </div>
          </div>

          {/* File tree (collapsible) */}
          {!filesCollapsed && (
            currentSession?.workdir ? (
              <FileTree workdir={currentSession.workdir} />
            ) : (
              <div className="flex-1 flex items-center justify-center px-4 text-xs text-text-tertiary text-center">
                Select a session to browse files
              </div>
            )
          )}

          {/* Workdir footer */}
          {currentSession?.workdir && (
            <div
              className="px-3 py-1.5 text-[10px] text-text-tertiary border-t border-border-default truncate flex-shrink-0"
              title={currentSession.workdir}
            >
              {currentSession.workdir}
            </div>
          )}
        </>
      )}

      {/* Resizer (desktop only) */}
      {!isMobile && <SidebarResizer />}

      {/* Modals */}
      <AppSettingsModal
        open={showAppSettings}
        onClose={() => setShowAppSettings(false)}
      />
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
      <NewSessionModal
        open={showNewModal}
        onClose={() => setShowNewModal(false)}
      />
      <ImportModal
        open={showImportModal}
        onClose={() => setShowImportModal(false)}
      />
      <ManageModal
        open={!!manageSessionId}
        onClose={() => setManageSessionId(null)}
        sessionId={manageSessionId}
      />
      <PostboxModal
        open={!!postboxSessionId}
        onClose={() => setPostboxSessionId(null)}
        sessionId={postboxSessionId}
      />
      <SessionDetailsModal
        session={detailsSessionId ? sessions.find((s) => s.id === detailsSessionId) ?? null : null}
        onClose={() => setDetailsSessionId(null)}
      />
      <RenameSessionModal
        session={renameSessionId ? sessions.find((s) => s.id === renameSessionId) ?? null : null}
        onClose={() => setRenameSessionId(null)}
      />
    </aside>
  );
}
