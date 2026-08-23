import { useEffect } from 'react';
import { useCurrentSession, useSessionStore } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { EditorPane } from '@/components/editor/EditorPane';

export default function EditorView() {
  const currentSession = useCurrentSession();
  const sessions = useSessionStore((s) => s.sessions);
  const selectSession = useSessionStore((s) => s.selectSession);

  useEffect(() => {
    if (currentSession?.id && currentSession?.workdir) {
      useEditorStore.getState().setRoot(currentSession.id, currentSession.workdir);
    }
  }, [currentSession?.id, currentSession?.workdir]);

  if (!currentSession) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-text-tertiary text-sm gap-2">
        <p>Select a session from the sidebar to browse its working directory</p>
        {sessions.length > 0 && (
          <div className="flex flex-col items-center gap-1 mt-2">
            <p className="text-xs text-text-tertiary">Recent sessions:</p>
            <div className="flex flex-col gap-0.5">
              {sessions.filter(s => s.workdir).slice(0, 5).map((s) => (
                <button
                  key={s.id}
                  onClick={() => selectSession(s.id)}
                  className="text-xs text-accent hover:text-accent-hover transition-colors"
                >
                  {s.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (!currentSession.workdir) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-text-tertiary text-sm gap-2">
        <p>This session has no working directory</p>
        <p className="text-xs">Only sessions with a working directory can be browsed.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full min-w-0 bg-bg-primary">
      <div className="flex items-center gap-2 pl-10 md:pl-3 pr-3 py-1.5 border-b border-border-default bg-bg-secondary/50">
        <span className="text-xs text-text-secondary truncate">
          {currentSession.name}
        </span>
        <span className="text-[10px] text-text-tertiary truncate" title={currentSession.workdir}>
          {currentSession.workdir}
        </span>
      </div>
      <div className="flex-1 flex min-h-0">
        <EditorPane />
      </div>
    </div>
  );
}
