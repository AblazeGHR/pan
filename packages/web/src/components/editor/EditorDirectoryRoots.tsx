import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, FolderOpen, FolderPlus, RefreshCw, X } from 'lucide-react';
import type { EditorRoot } from '@/stores/editorStore';
import { useEditorStore } from '@/stores/editorStore';
import { useCurrentSession } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import { useSessionWorkspace } from '@/hooks/useSessionWorkspace';
import { FileTree } from '@/components/editor/FileTree';
import { AddDirectoryModal } from '@/components/editor/AddDirectoryModal';

/** Human label for a root kind ("标注 workspace dir / temp dir"). */
function rootKindLabel(root: EditorRoot): string {
  if (root.kind === 'cwd') return 'CWD';
  if (root.kind === 'workspace') return 'workspace dir';
  return 'temp dir';
}

/**
 * Editor directory roots for the current Session: the CWD (the Session workdir),
 * every shared directory of the Session's Workspace, and any in-memory Temp
 * directories. Each root collapses independently and workspace/temp roots can be
 * removed from the list (metadata only — never the disk).
 */
export function EditorDirectoryRoots() {
  const currentSession = useCurrentSession();
  const roots = useEditorStore((s) => s.roots);
  const rootTrees = useEditorStore((s) => s.rootTrees);
  const refreshRoot = useEditorStore((s) => s.refreshRoot);
  const removeTempDir = useEditorStore((s) => s.removeTempDir);
  const addTempDir = useEditorStore((s) => s.addTempDir);
  const showToast = useUIStore((s) => s.showToast);
  const { workspaceId, workspaceName } = useSessionWorkspace(currentSession);

  const workspacesLoaded = useWorkspaceStore((s) => s.loaded);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [menuOpen, setMenuOpen] = useState(false);
  const [addMode, setAddMode] = useState<'workspace' | 'temp' | null>(null);

  // Workspace names/dirs are durable server metadata; make sure they exist
  // before rendering workspace roots or enabling the workspace action.
  useEffect(() => {
    if (!workspacesLoaded) void loadWorkspaces();
  }, [workspacesLoaded, loadWorkspaces]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnOutsidePointer = (e: PointerEvent) => {
      if (!(e.target as Element | null)?.closest('[data-testid="add-directory-menu"]')) {
        setMenuOpen(false);
      }
    };
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [menuOpen]);

  const toggleCollapse = (rootId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
      return next;
    });
  };

  const handleRemove = (root: EditorRoot, event: React.MouseEvent) => {
    event.stopPropagation();
    if (root.kind === 'temp') {
      removeTempDir(root.path);
      return;
    }
    if (root.kind === 'workspace' && root.workspaceId) {
      void useWorkspaceStore
        .getState()
        .removeWorkspaceDir(root.workspaceId, root.path)
        .catch((error) =>
          showToast(error instanceof Error ? error.message : '移除目录失败', 'error'),
        );
    }
  };

  const openAdd = (mode: 'workspace' | 'temp') => {
    setMenuOpen(false);
    setAddMode(mode);
  };

  const handleAdd = async (path: string) => {
    if (addMode === 'workspace') {
      if (!workspaceId) throw new Error('当前 Session 未归属工作区');
      await useWorkspaceStore.getState().addWorkspaceDir(workspaceId, path);
      return;
    }
    addTempDir(path);
  };

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="editor-directory-roots">
      {/* Add-directory toolbar */}
      <div className="relative flex items-center justify-between px-3 py-1.5 border-b border-border-default min-h-[36px]">
        <span className="text-[11px] font-semibold text-text-tertiary uppercase tracking-wider">
          Directories
        </span>
        <button
          type="button"
          className="flex items-center gap-1 text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors text-[11px]"
          onClick={() => setMenuOpen((v) => !v)}
          title="添加目录"
          aria-label="添加目录"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <FolderPlus size={14} />
          <span>添加目录</span>
        </button>
        {menuOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
            <div
              role="menu"
              data-testid="add-directory-menu"
              className="absolute right-2 top-full z-50 mt-1 w-56 rounded-md border border-border-default bg-bg-tertiary py-1 shadow-xl"
            >
              <button
                type="button"
                role="menuitem"
                disabled={!workspaceId}
                className="w-full px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:text-text-tertiary disabled:hover:bg-transparent"
                onClick={() => workspaceId && openAdd('workspace')}
                title={workspaceId ? '为工作区添加目录' : '当前 Session 未归属工作区，无法添加工作区目录'}
              >
                为工作区添加目录
              </button>
              {!workspaceId && (
                <p className="px-3 pb-1 text-[10px] text-text-tertiary">
                  当前 Session 未归属工作区，可先将其移入工作区；临时目录仍可用。
                </p>
              )}
              <button
                type="button"
                role="menuitem"
                className="w-full px-3 py-1.5 text-left text-xs text-text-primary transition-colors hover:bg-accent/20"
                onClick={() => openAdd('temp')}
              >
                添加临时目录
              </button>
            </div>
          </>
        )}
      </div>

      {/* Roots */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {roots.length === 0 && (
          <div className="px-3 py-4 text-xs text-text-tertiary" data-testid="editor-roots-empty">
            当前 Session 没有可浏览的目录，可添加临时目录。
          </div>
        )}
        {roots.map((root) => (
          <RootSection
            key={root.id}
            root={root}
            loading={rootTrees[root.id]?.loading ?? false}
            collapsed={collapsed.has(root.id)}
            onToggle={toggleCollapse}
            onRefresh={refreshRoot}
            onRemove={handleRemove}
          />
        ))}
      </div>

      <AddDirectoryModal
        open={addMode === 'workspace'}
        mode="workspace"
        workspaceName={workspaceName}
        onClose={() => setAddMode(null)}
        onSubmit={handleAdd}
      />
      <AddDirectoryModal
        open={addMode === 'temp'}
        mode="temp"
        onClose={() => setAddMode(null)}
        onSubmit={handleAdd}
      />
    </div>
  );
}

function RootSection({
  root,
  loading,
  collapsed,
  onToggle,
  onRefresh,
  onRemove,
}: {
  root: EditorRoot;
  loading: boolean;
  collapsed: boolean;
  onToggle: (rootId: string) => void;
  onRefresh: (rootId: string) => void | Promise<void>;
  onRemove: (root: EditorRoot, event: React.MouseEvent) => void;
}) {
  return (
    <div className="border-b border-border-muted/60" data-testid={`editor-root-${root.kind}`}>
      <div
        className="flex items-center gap-1.5 px-3 py-1.5 cursor-pointer select-none hover:bg-bg-hover/30 transition-colors"
        onClick={() => onToggle(root.id)}
        data-testid="editor-root-header"
      >
        {collapsed ? (
          <ChevronRight size={13} className="text-text-tertiary flex-shrink-0" />
        ) : (
          <ChevronDown size={13} className="text-text-tertiary flex-shrink-0" />
        )}
        <FolderOpen size={13} className="text-text-tertiary flex-shrink-0" />
        <span className="text-[11px] font-semibold text-text-secondary flex-shrink-0" data-testid="editor-root-kind">
          {rootKindLabel(root)}
        </span>
        <span
          className="min-w-0 flex-1 truncate text-[10px] text-text-tertiary"
          title={root.path}
        >
          {root.path}
        </span>
        <button
          type="button"
          className="text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            void onRefresh(root.id);
          }}
          title={`刷新 ${root.path}`}
          aria-label={`刷新 ${root.path}`}
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
        {root.kind !== 'cwd' && (
          <button
            type="button"
            className="text-text-tertiary hover:text-danger p-0.5 rounded transition-colors flex-shrink-0"
            onClick={(e) => onRemove(root, e)}
            title={`从列表移除 ${root.path}`}
            aria-label={`从列表移除 ${root.path}`}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {!collapsed && <FileTree rootId={root.id} />}
    </div>
  );
}
