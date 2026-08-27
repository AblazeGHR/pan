import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import { ManageSessionsPanel } from '@/components/session/ManageModal';

/**
 * Full-page "Manage Sessions" (mobile): replaces the session-card Manage
 * popup with a dedicated page. Reuses the exact same ManageSessionsPanel as
 * the desktop modal so sections and interactions stay identical.
 */
export default function ManageView() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const sessions = useSessionStore((s) => s.sessions);
  const loadSessions = useSessionStore((s) => s.loadSessions);

  // Direct URL load skips ChatView's WS-init refresh — make sure the session
  // list is present so names / manager labels render.
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const session = sessionId ? (sessions.find((s) => s.id === sessionId) ?? null) : null;

  return (
    <div className="flex flex-col h-full min-h-0 bg-bg-primary">
      {/* Header — pl-10 clears the fixed mobile hamburger button */}
      <div className="flex items-center gap-2 pl-10 md:pl-3 pr-3 py-2.5 border-b border-border-default bg-bg-secondary/50 shrink-0">
        <button
          type="button"
          onClick={() => navigate('/')}
          aria-label="Back"
          title="Back"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border-default bg-bg-tertiary text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary"
        >
          <ArrowLeft size={14} />
        </button>
        <h1 className="text-sm font-semibold text-text-primary">Manage Sessions</h1>
        {session?.name && (
          <span className="min-w-0 truncate text-[11px] text-text-tertiary">{session.name}</span>
        )}
      </div>

      {/* Body — scrollable, centered column on desktop */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl p-4">
          <ManageSessionsPanel open sessionId={sessionId ?? null} />
        </div>
      </div>
    </div>
  );
}
