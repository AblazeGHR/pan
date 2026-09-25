import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import { ScheduledTaskPanel } from '@/components/schedule/ScheduledTaskPanel';

/**
 * Full-page "Scheduled Tasks" (alarm-style automation). Like ManageView it
 * loads its own data on mount: arriving through a direct URL skips ChatView's
 * WS-driven refresh, and this route has no session param to key off.
 */
export default function ScheduledTasksView() {
  const navigate = useNavigate();
  const loadSessions = useSessionStore((s) => s.loadSessions);

  // Direct URL load skips ChatView's WS-init refresh — the panel needs the
  // session list to resolve target-session names.
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

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
        <h1 className="text-sm font-semibold text-text-primary">Scheduled Tasks</h1>
      </div>

      {/* Body — scrollable, centered column on desktop */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl p-4">
          <ScheduledTaskPanel />
        </div>
      </div>
    </div>
  );
}
