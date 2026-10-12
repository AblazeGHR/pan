/* eslint-disable react-refresh/only-export-components */
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { RenameSessionModal } from '@/components/session/RenameSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';
import './index.css';

const sessions: Session[] = [
  { id: 'a', name: 'Session A', alwaysThinkingEnabled: false, effort: '', history: [] },
  { id: 'b', name: 'Session B', alwaysThinkingEnabled: false, effort: '', history: [] },
];
useSessionStore.setState({ sessions });

function Harness() {
  // Production wiring replicated verbatim from Sidebar.tsx:808-811: the modal
  // stays mounted for the app's lifetime and receives the live store object
  // for the currently open session id.
  const storeSessions = useSessionStore((state) => state.sessions);
  const [renameSessionId, setRenameSessionId] = useState<string | null>(null);
  const toasts = useUIStore((state) => state.toastQueue);
  return (
    <>
      <ul style={{ padding: 16 }}>
        {storeSessions.map((session) => (
          <li key={session.id} style={{ marginBottom: 8 }}>
            <span data-session-card-id={session.id} style={{ marginRight: 8 }}>{session.name}</span>
            <button type="button" onClick={() => setRenameSessionId(session.id)}>
              {`Rename ${session.id.toUpperCase()}`}
            </button>
          </li>
        ))}
      </ul>
      <RenameSessionModal
        session={renameSessionId ? storeSessions.find((s) => s.id === renameSessionId) ?? null : null}
        onClose={() => setRenameSessionId(null)}
      />
      <div role="status" className="fixed bottom-0 right-0 max-w-full bg-bg-primary text-xs">
        {toasts.map((toast) => (
          <p key={toast.id}>{toast.message}</p>
        ))}
      </div>
    </>
  );
}
createRoot(document.getElementById('root')!).render(<Harness />);

Object.assign(window, {
  // Simulates a store refresh that replaces the session object identity while
  // the dialog is open — WS stream previews, session.updated and loadSessions
  // merges all funnel through updateSession with a freshly built object.
  simulateRefresh: (id: string, patch: Partial<Session>) =>
    useSessionStore.getState().updateSession(id, patch),
  // Simulates the WS session.renamed handler (useWebSocket.ts:836), including
  // its one-shot event patch that protects against stale snapshot rollback.
  simulateWsRenamed: (id: string, name: string) =>
    useSessionStore.getState().updateSession(id, { name }, true),
  // Real HTTP read through the store (GET /api/sessions?summary=1).
  refreshSessions: () => useSessionStore.getState().loadSessions(),
  storeState: () => useSessionStore.getState().sessions.map((s) => ({
    id: s.id,
    name: s.name,
    lastMessage: s.lastMessage ?? null,
  })),
  dialogState: () => {
    const input = document.querySelector('input#rename-session-input') as HTMLInputElement | null;
    return {
      dialogOpen: Boolean(document.querySelector('[role="dialog"]')),
      value: input?.value ?? null,
      selectionStart: input?.selectionStart ?? null,
      selectionEnd: input?.selectionEnd ?? null,
      inputDisabled: input?.disabled ?? null,
      activeIsInput: document.activeElement === input,
    };
  },
});
