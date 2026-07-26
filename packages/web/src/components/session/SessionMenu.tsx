import { useEffect, useRef } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

interface SessionMenuProps {
  session: Session;
  position: { x: number; y: number };
  onClose: () => void;
}

export function SessionMenu({ session, position, onClose }: SessionMenuProps) {
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
    setTimeout(() => document.addEventListener('click', handler), 0);
    return () => document.removeEventListener('click', handler);
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

  return (
    <div
      ref={menuRef}
      className="absolute z-30 bg-bg-tertiary border border-border-default rounded-md shadow-xl py-1 min-w-[140px]"
      style={{ left: position.x, top: position.y }}
    >
      <button
        onClick={handleRename}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors"
      >
        Rename
      </button>
      {session.cliSessionId && (
        <>
          <button
            onClick={handleReimport}
            className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors"
          >
            Reimport
          </button>
          <button
            onClick={handleBranch}
            className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors"
          >
            Branch
          </button>
        </>
      )}
      <button
        onClick={handleMultiSelect}
        className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20 transition-colors"
      >
        Select
      </button>
      <div className="border-t border-border-muted my-1" />
      <button
        onClick={handleDelete}
        className="w-full text-left px-3 py-1.5 text-xs text-danger hover:bg-danger/10 transition-colors"
      >
        Delete
      </button>
    </div>
  );
}
