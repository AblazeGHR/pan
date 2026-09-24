import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  Pencil,
  RotateCcw,
  GitBranch,
  Settings,
  Mail,
  ListChecks,
  Info,
  Trash2,
  Folder,
  Layers,
  Check,
  ChevronLeft,
} from 'lucide-react';
import type { Session } from '@/types';
import { useWorkspaceStore } from '@/stores/workspaceStore';

interface SessionMenuProps {
  session: Session;
  position: { x: number; y: number };
  onClose: () => void;
  /** Open the "manage sessions" modal for this session (as manager). */
  onManage?: (id: string) => void;
  /** Open the "QQ postbox" subscription modal for this session. */
  onPostbox?: (id: string) => void;
  /** Open the session details modal for this session. */
  onDetails?: (id: string) => void;
  onRename?: (id: string) => void;
  onDelete?: (id: string) => void;
}

export function SessionMenu({ session, position, onClose, onManage, onPostbox, onDetails, onRename, onDelete }: SessionMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  // 挂载前先用点击锚点，量取菜单尺寸后按视口空间翻转/收敛到最终落点。
  const [placement, setPlacement] = useState<{ x: number; y: number }>(() => position);
  /** Submenu view: main actions vs. the single-select "move to workspace" list. */
  const [view, setView] = useState<'main' | 'move'>('main');
  const { reimport, branch, toggleMultiSelect } =
    useSessionStore();
  const { showToast } = useUIStore();
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const workspacesLoaded = useWorkspaceStore((s) => s.loaded);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);
  const moveSessions = useWorkspaceStore((s) => s.moveSessions);
  const currentWorkspaceId = (session.workspaceIds ?? [])[0] ?? null;

  useEffect(() => {
    if (view === 'move' && !workspacesLoaded) void loadWorkspaces();
  }, [view, workspacesLoaded, loadWorkspaces]);

  const handleMoveToWorkspace = async (workspaceId: string | null) => {
    onClose();
    try {
      const changed = await moveSessions([session.id], workspaceId);
      if (changed.length === 0) {
        showToast(workspaceId ? '该会话已在该工作区' : '该会话当前未归属任何工作区', 'error');
        return;
      }
      const followed = changed.length - 1;
      const target = workspaceId ? workspaces.find((w) => w.id === workspaceId)?.name ?? '工作区' : null;
      if (!target) showToast(`已将「${session.name}」移出工作区（未分组）`);
      else if (followed > 0) showToast(`已将「${session.name}」及其 ${followed} 个子孙会话移入「${target}」`);
      else showToast(`已将「${session.name}」移入「${target}」`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '移动失败', 'error');
    }
  };

  // 保证菜单始终落在视口内：下方放得下就向下展开（锚点即按钮顶边），放不下就
  // 向上翻转（bottom 对齐按钮顶边）；水平方向越界则右对齐/左对齐收敛，两侧都
  // 留 MENU_MARGIN 边距。视口高度取 min(innerHeight, visualViewport.height)，
  // 与 App.tsx 的 viewportH 口径一致（避免分数缩放/键盘遮挡时误判可用空间）。
  // 用 useLayoutEffect 在绘制前同步量取并落位，用户看不到初始未收敛的一帧。
  useLayoutEffect(() => {
    const el = menuRef.current;
    if (!el) return;
    const vw = window.innerWidth;
    const vh = Math.min(window.innerHeight, window.visualViewport?.height ?? window.innerHeight);
    const MENU_MARGIN = 8;
    const menuW = el.offsetWidth;
    const menuH = el.offsetHeight;

    // 水平：优先锚定按钮右侧，越界则右对齐（左边界同样收敛）。
    let x = position.x;
    x = Math.max(MENU_MARGIN, Math.min(x, vw - menuW - MENU_MARGIN));

    // 垂直：下方空间不足 → 向上翻转；最终再统一夹到视口内。
    let y = position.y;
    if (vh - MENU_MARGIN - position.y < menuH) {
      y = position.y - menuH;
    }
    y = Math.max(MENU_MARGIN, Math.min(y, vh - menuH - MENU_MARGIN));

    setPlacement({ x, y });
  }, [position]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    // Delay to avoid immediate close from the click that opened it
    const timer = setTimeout(() => document.addEventListener('click', handler), 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('click', handler);
    };
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleRename = () => {
    onClose();
    onRename?.(session.id);
  };

  const handleReimport = async () => {
    onClose();
    try {
      await reimport(session.id);
      showToast('Session reimported');
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Reimport failed', 'error');
    }
  };

  const handleBranch = () => {
    onClose();
    const defaultName = session.name ? `${session.name}-branch` : '';
    const newName = (prompt('Branch session name:', defaultName) || '').trim();
    if (!newName) return;
    branch(session.id, newName).catch((e) =>
      showToast(e.message || 'Branch failed', 'error'),
    );
  };

  const handleDelete = () => {
    onClose();
    if (onDelete) {
      onDelete(session.id);
      return;
    }
    if (!confirm(`Delete session ${session.id.slice(0, 12)}...?`)) return;
  };

  const handleMultiSelect = () => {
    onClose();
    toggleMultiSelect(session.id);
  };

  const handleManage = () => {
    onClose();
    onManage?.(session.id);
  };

  const handlePostbox = () => {
    onClose();
    onPostbox?.(session.id);
  };

  const handleDetails = () => {
    onClose();
    onDetails?.(session.id);
  };

  // 通过 portal 挂到 <body>：Sidebar 的移动端容器带 transform（translateX），
  // 会成为 `position: fixed` 后代的包含块（与 Modal.tsx 相同的原因）。固定定位
  // 的坐标来自 getBoundingClientRect()（视口坐标），保证菜单相对视口落位准确。
  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-50 bg-bg-tertiary border border-border-default rounded-md shadow-xl py-1 min-w-[140px] max-h-[60vh] overflow-y-auto"
      style={{ left: placement.x, top: placement.y }}
    >
      {view === 'move' ? (
        <>
          <button
            onClick={() => setView('main')}
            className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:bg-accent/20 transition-colors flex items-center gap-2"
          >
            <ChevronLeft size={12} className="text-text-tertiary shrink-0" />
            移入工作区
          </button>
          <div className="border-t border-border-muted my-1" />
          <button
            onClick={() => void handleMoveToWorkspace(null)}
            className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
          >
            <Layers size={12} className="text-text-tertiary shrink-0" />
            未分组（无工作区）
            {currentWorkspaceId === null && <Check size={12} className="ml-auto text-accent shrink-0" />}
          </button>
          {workspaces.map((workspace) => (
            <button
              key={workspace.id}
              onClick={() => void handleMoveToWorkspace(workspace.id)}
              className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
            >
              <Folder size={12} className="text-text-tertiary shrink-0" />
              <span className="truncate">{workspace.name}</span>
              {currentWorkspaceId === workspace.id && <Check size={12} className="ml-auto text-accent shrink-0" />}
            </button>
          ))}
          {workspaces.length === 0 && (
            <div className="px-3 py-1.5 text-xs text-text-tertiary">还没有工作区</div>
          )}
        </>
      ) : (
        <>
      <button
        onClick={handleRename}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <Pencil size={12} className="text-text-tertiary shrink-0" />
        Rename
      </button>
      {session.cliSessionId && (
        <>
          <button
            onClick={handleReimport}
            className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
          >
            <RotateCcw size={12} className="text-text-tertiary shrink-0" />
            Reimport
          </button>
          <button
            onClick={handleBranch}
            className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
          >
            <GitBranch size={12} className="text-text-tertiary shrink-0" />
            Branch
          </button>
        </>
      )}
      <button
        onClick={handleManage}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <Settings size={12} className="text-text-tertiary shrink-0" />
        Manage
      </button>
      <button
        onClick={handlePostbox}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <Mail size={12} className="text-text-tertiary shrink-0" />
        msgBridge
      </button>
      <button
        onClick={handleDetails}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <Info size={12} className="text-text-tertiary shrink-0" />
        Details
      </button>
      <button
        onClick={() => setView('move')}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <Folder size={12} className="text-text-tertiary shrink-0" />
        Move to workspace
      </button>
      <button
        onClick={handleMultiSelect}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors flex items-center gap-2"
      >
        <ListChecks size={12} className="text-text-tertiary shrink-0" />
        Select
      </button>
      <div className="border-t border-border-muted my-1" />
      <button
        onClick={handleDelete}
        className="w-full text-left px-3 py-1.5 text-xs text-danger hover:bg-danger/10 transition-colors flex items-center gap-2"
      >
        <Trash2 size={12} className="shrink-0" />
        Delete
      </button>
        </>
      )}
    </div>,
    document.body,
  );
}
