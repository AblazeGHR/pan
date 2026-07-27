import { useState, useRef, useCallback } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { SessionList } from '@/components/session/SessionList';
import { NewSessionModal } from '@/components/session/NewSessionModal';
import { ImportModal } from '@/components/session/ImportModal';
import { SessionMenu } from '@/components/session/SessionMenu';
import { NavLink } from 'react-router-dom';
import { Button } from '@/components/ui/Button';

export function Sidebar() {
  const { multiSelectMode, exitMultiSelect, selectedIds, batchRemoveSessions, sessions } =
    useSessionStore();
  const { showToast } = useUIStore();

  const [showNewModal, setShowNewModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showImportDropdown, setShowImportDropdown] = useState(false);
  const [menuSession, setMenuSession] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });

  const importRef = useRef<HTMLDivElement>(null);

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`Delete ${selectedIds.size} selected session(s)?`)) return;
    batchRemoveSessions().catch((e) =>
      showToast(e.message || 'Batch delete failed', 'error'),
    );
  };

  const quickNew = useCallback(() => {
    const name = `session-${sessions.length + 1}`;
    const store = useSessionStore.getState();
    store.createNewSession(name).catch((e) =>
      showToast(e.message || 'Creation failed', 'error'),
    );
  }, [sessions.length, showToast]);

  const handleSessionMenu = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenuPosition({ x: rect.right + 4, y: rect.top });
    setMenuSession(id);
  };

  return (
    <aside className="w-60 max-w-[85vw] flex flex-col h-full border-r border-border-default bg-bg-secondary">
      {/* Header with New/Import buttons */}
      <div className="px-3 py-2 border-b border-border-muted">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-lg font-bold text-text-primary">Pan</h1>
        </div>
        {/* View navigation */}
        <div className="flex gap-1 mb-2">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `flex-1 text-center py-1 text-xs rounded transition-colors ${
                isActive
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
              }`
            }
          >
            Chat
          </NavLink>
          <NavLink
            to="/editor"
            className={({ isActive }) =>
              `flex-1 text-center py-1 text-xs rounded transition-colors ${
                isActive
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'
              }`
            }
          >
            Editor
          </NavLink>
        </div>
        <div className="flex gap-1">
          <Button
            variant="primary"
            size="sm"
            onClick={quickNew}
            title="Quick new session"
            className="flex-1"
          >
            + New
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowNewModal(true)}
            title="New with settings"
          >
            ⚙
          </Button>
          <div ref={importRef} className="relative">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowImportDropdown(!showImportDropdown)}
              title="Import session"
            >
              Import ▾
            </Button>
            {showImportDropdown && (
              <div
                className="absolute left-0 top-full mt-1 z-20 bg-bg-tertiary border border-border-default rounded-md shadow-lg py-1 min-w-[140px]"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowImportDropdown(false);
                  setShowImportModal(true);
                }}
              >
                <button className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20">
                  Import from cbc
                </button>
                <button className="w-full text-left px-3 py-1.5 text-xs text-text-primary hover:bg-accent/20">
                  Import from kimi
                </button>
              </div>
            )}
          </div>
        </div>
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
        />
      )}

      {/* Close dropdown on outside click */}
      {showImportDropdown && (
        <div
          className="fixed inset-0 z-10"
          onClick={() => setShowImportDropdown(false)}
        />
      )}

      {/* Modals */}
      <NewSessionModal
        open={showNewModal}
        onClose={() => setShowNewModal(false)}
      />
      <ImportModal
        open={showImportModal}
        onClose={() => setShowImportModal(false)}
      />
    </aside>
  );
}
