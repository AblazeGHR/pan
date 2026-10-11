import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ChatView from '@/views/ChatView';
import { ToastContainer } from '@/components/ui/Toast';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { useAdapterStore } from '@/stores/adapterStore';
import '@/index.css';

useSessionStore.setState({
  currentSessionId: 's1',
  sessions: ['s1', 's2'].map((id) => ({
    id,
    name: id,
    adapter: 'codex',
    workerId: `w-${id}`,
    workerStatus: 'running',
    nativeControl: true,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    workdir: '/fixture',
  })),
  currentMessages: [],
});
useWorkerStore.setState({
  workers: {
    s1: { id: 'w-s1', sessionId: 's1', status: 'running' },
    s2: { id: 'w-s2', sessionId: 's2', status: 'running' },
  },
  currentWorkerId: 'w-s1',
  currentWorker: { id: 'w-s1', sessionId: 's1', status: 'running' },
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
useAdapterStore.setState({ adapters: [] });
createRoot(document.getElementById('root')!).render(
  <MemoryRouter>
    <div style={{ height: '100dvh' }}>
      <ChatView />
    </div>
    <ToastContainer />
  </MemoryRouter>,
);
