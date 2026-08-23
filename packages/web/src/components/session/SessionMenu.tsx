import { useEffect, useRef } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import {
  Pencil,
  RotateCcw,
  GitBranch,
  Settings,
  Mail,
  ListChecks,
  Trash2,
} from 'lucide-react';
import type { Session } from '@/types';

interface SessionMenuProps {
  session: Session;
  position: { x: number; y: number };
  onClose: () => void;
  /** Open the "manage sessions" modal for this session (as manager). */
  onManage?: (id: string) => void;
  /** Open the "QQ postbox" subscription modal for this session. */
  onPostbox?: (id: string) => void;
}

export function SessionMenu({ session, position, onClose, onManage, onPostbox }: SessionMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const { rename, removeSession, reimport, branch, toggleMultiSelect } =
    useSessionStore();
  const { showToast } = useUIStore();

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
    const newName = (prompt('New session name:') || '').trim();
    if (!newName) return;
    rename(session.id, newName).catch((e) =>
      showToast(e.message || 'Rename failed', 'error'),
    );
  };

  const handleReimport = () => {
    onClose();
    reimport(session.id).catch((e) =>
      showToast(e.message || 'Reimport failed', 'error'),
    );
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
    if (!confirm(`Delete session ${session.id.slice(0, 12)}...?`)) return;
    removeSession(session.id).catch((e) =>
      showToast(e.message || 'Delete failed', 'error'),
    );
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

  return (
    <div
      ref={menuRef}
      className="absolute z-30 bg-bg-tertiary border border-border-default rounded-md shadow-xl py-1 min-w-[140px]"
      style={{ left: position.x, top: position.y }}
    >
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
        Postbox
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
    </div>
  );
}
