/* eslint-disable react-refresh/only-export-components */
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import './index.css';

const sessions = ['a', 'b', 'c'].map((id) => ({
  id,
  name: `Session ${id}`,
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
  workspaceIds: ['w1'],
  unreadDoneGeneration: 4,
  unreadDoneReadGeneration: 1,
  unreadDoneCount: 3,
  msgBridgeEnabled: false,
  // a and c carry a live worker; b has none (Kill/Delayed restart boundaries).
  ...(id === 'b' ? {} : { workerId: `worker-${id}`, workerStatus: 'idle', workerGeneration: 1 }),
}));
useSessionStore.setState({ sessions, multiSelectMode: true, selectedIds: new Set(['a', 'b']) });
useWorkerStore.setState({ workers: {}, delayedRestarts: {} });
useWorkspaceStore.setState({
  loaded: true,
  workspaces: [
    { id: 'w1', name: 'Original', order: 0 },
    { id: 'w2', name: 'Destination', order: 1 },
  ],
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
useUIStore.setState({
  sidebarCollapsed: false,
  mobileSidebarOpen: true,
  activeWorkspaceId: 'all',
  groupBy: 'none',
  specialFilters: new Set(),
});
Object.assign(window, {
  selectTargets: (ids: string[]) => useSessionStore.setState({ selectedIds: new Set(ids) }),
  setManager: (id: string, manager: string | null) => useSessionStore.getState().updateSession(id, { managedBy: manager }),
  setWorker: (id: string, workerId: string | null, status: string | null, generation = 1) => {
    useSessionStore.getState().updateSession(id, { workerId, workerStatus: status, workerGeneration: generation });
    useWorkerStore.getState().updateWorker(id, workerId, status, generation);
  },
  applyDelayedRestart: (intent: Record<string, unknown>) =>
    useWorkerStore.getState().applyDelayedRestart(intent as never),
  delayedRestartState: () => useWorkerStore.getState().delayedRestarts,
  clearToasts: () => useUIStore.setState({ toastQueue: [] }),
  sessionState: () => useSessionStore.getState().sessions,
  resetScenario: () => {
    useSessionStore.setState({ sessions, multiSelectMode: true, selectedIds: new Set(['a', 'b']) });
    useWorkerStore.setState({ workers: {}, delayedRestarts: {} });
    useUIStore.setState({ toastQueue: [] });
  },
});
function Harness() {
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
