/* eslint-disable react-refresh/only-export-components */
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { Session } from '@/types';
import './index.css';

const sessions: Session[] = [
  { id: 'a', name: 'Session A', alwaysThinkingEnabled: false, effort: '', history: [] },
  { id: 'b', name: 'Session B', alwaysThinkingEnabled: false, effort: '', history: [] },
];
useSessionStore.setState({
  sessions,
  currentSessionId: null,
  multiSelectMode: false,
  selectedIds: new Set(),
});
useUIStore.setState({
  sidebarCollapsed: false,
  mobileSidebarOpen: true,
  searchQuery: '',
  specialFilters: new Set(),
  groupBy: 'none',
  sortBy: 'recent',
  dragEnabled: false,
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });

function Harness() {
  // The real production entry path: the Sidebar owns the session card menu and
  // the RenameSessionModal, so the menu -> dialog focus handoff is exercised.
  const toasts = useUIStore((state) => state.toastQueue);
  return (
    <>
      <div style={{ height: '100dvh', display: 'flex' }}>
        <MemoryRouter>
          <Sidebar />
        </MemoryRouter>
      </div>
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
  toastMessages: () => useUIStore.getState().toastQueue.map((toast) => toast.message),
  dialogState: () => {
    const input = document.querySelector('input#rename-session-input') as HTMLInputElement | null;
    return {
      dialogOpen: Boolean(document.querySelector('[role="dialog"]')),
      menuOpen: Boolean(document.querySelector('body > div.fixed.z-50')),
      value: input?.value ?? null,
      selectionStart: input?.selectionStart ?? null,
      selectionEnd: input?.selectionEnd ?? null,
      inputDisabled: input?.disabled ?? null,
      activeIsInput: document.activeElement === input,
    };
  },
});
