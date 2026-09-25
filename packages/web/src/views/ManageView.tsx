import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { ManageSessionsPanel } from '@/components/session/ManageModal';

/**
 * Full-page "Manage Sessions" (mobile): replaces the session-card Manage
 * popup with a dedicated page. Reuses the exact same ManageSessionsPanel as
 * the desktop modal so sections and interactions stay identical.
 */
export default function ManageView() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const { isMobile } = useMediaQuery();
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const setMobileSidebarOpen = useUIStore((s) => s.setMobileSidebarOpen);

  // Direct URL load skips ChatView's WS-init refresh — make sure the session
  // list is present so names / manager labels render.
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const session = sessionId ? (sessions.find((s) => s.id === sessionId) ?? null) : null;
  const closeManage = () => {
    // Manage is entered from the mobile drawer after the drawer is hidden.
    // Closing it should reveal that same drawer again, without selecting or
    // navigating into the managed session.
    if (isMobile) setMobileSidebarOpen(true);
    navigate('/');
  };
  const viewRelationship = (targetSessionId: string) => {
    navigate(`/manage/${encodeURIComponent(targetSessionId)}`);
  };

  return (
    <div className="flex flex-col h-full min-h-0 bg-bg-primary">
      {/* Match Session Details: close from the top-right and restore the
          mobile drawer rather than selecting the current session. */}
      <div className="flex items-center justify-between gap-2 pl-10 md:pl-3 pr-3 py-2.5 border-b border-border-default bg-bg-secondary/50 shrink-0">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="text-sm font-semibold text-text-primary">Manage Sessions</h1>
          {session?.name && (
            <span className="min-w-0 truncate text-[11px] text-text-tertiary">{session.name}</span>
          )}
        </div>
        <button
          type="button"
          onClick={closeManage}
          aria-label="Close Manage Sessions"
          title="Close"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border-default bg-bg-tertiary text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
        >
          <X size={14} />
        </button>
      </div>

      {/* Body — scrollable, centered column on desktop */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl p-4">
          <ManageSessionsPanel
            open
            sessionId={sessionId ?? null}
            onViewRelationship={viewRelationship}
          />
        </div>
      </div>
    </div>
  );
}
