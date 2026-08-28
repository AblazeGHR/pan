import { useEffect, useState, useCallback, useMemo } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useEditorStore } from '@/stores/editorStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { nextSessionDefaultName } from '@/utils/sessionName';
import { SessionList } from '@/components/session/SessionList';
import { NewSessionModal } from '@/components/session/NewSessionModal';
import { ImportModal } from '@/components/session/ImportModal';
import { ManageModal } from '@/components/session/ManageModal';
import { PostboxModal } from '@/components/session/PostboxModal';
import { SessionMenu } from '@/components/session/SessionMenu';
import { FileTree } from '@/components/editor/FileTree';
import { SidebarResizer } from './SidebarResizer';
import { AppSettingsModal } from './AppSettingsModal';
import { Button } from '@/components/ui/Button';
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
  ChevronUp,
  ChevronDown,
  Sun,
  Moon,
} from 'lucide-react';

export function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const isEditorRoute = location.pathname === '/editor';
  const { isMobile } = useMediaQuery();

  // Session store
  const { multiSelectMode, exitMultiSelect, selectedIds, batchRemoveSessions, sessions } =
    useSessionStore();
  const currentSession = useCurrentSession();

  // UI store
  const {
    sidebarWidth,
    sidebarCollapsed,
    toggleSidebar,
    showToast,
    groupBy,
    cycleGroupBy,
    searchQuery,
    setSearchQuery,
    sortBy,
    setSortBy,
    collapsedGroups,
    collapseAllGroups,
    expandAllGroups,
    filesCollapsed,
    toggleFilesCollapsed,
    theme,
    toggleTheme,
  } = useUIStore();

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
  const [showAppSettings, setShowAppSettings] = useState(false);

  // Init editor tree when on editor route and session changes
  useEffect(() => {
    if (isEditorRoute && currentSession?.id && currentSession?.workdir) {
      useEditorStore.getState().setRoot(currentSession.id, currentSession.workdir);
    }
  }, [isEditorRoute, currentSession?.id, currentSession?.workdir]);

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

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`Delete ${selectedIds.size} selected session(s)?`)) return;
    const count = selectedIds.size;
    batchRemoveSessions()
      .then(() => showToast(`Deleted ${count} session(s)`))
      .catch((e) => showToast(e.message || 'Batch delete failed', 'error'));
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
      className="relative flex flex-col h-full border-r border-border-default bg-bg-secondary"
      style={{ width: sidebarWidth }}
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
                  onClick={toggleSidebar}
                  className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
                  title="Collapse sidebar"
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
            </div>

            {/* Buttons */}
            <div className="flex gap-1">
              <Button
                variant="primary"
                size="sm"
                onClick={quickNew}
                title="Quick new session"
                className="flex-1"
              >
                <Plus size={14} />
                New
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowNewModal(true)}
                title="New with settings"
              >
                <Settings size={14} />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowImportModal(true)}
                title="Import session"
              >
                <Import size={14} />
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
                className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1 pl-6 pr-2 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
              />
            </div>
            <button
              onClick={() => setSortBy(sortBy === 'recent' ? 'name' : 'recent')}
              className={`p-1 rounded transition-colors ${
                sortBy === 'name'
                  ? 'text-accent bg-accent/10'
                  : 'text-text-tertiary hover:text-text-primary'
              }`}
              title={`Sort by ${sortBy === 'recent' ? 'name' : 'recent'}`}
            >
              <ArrowUpDown size={14} />
            </button>
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
            <div className="flex items-center gap-2 px-3 py-2 border-b border-border-muted bg-bg-tertiary">
              <span className="text-xs text-text-secondary">
                {selectedIds.size} selected
              </span>
              <div className="flex-1" />
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
            />
          )}
        </>
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
    </aside>
  );
}
