import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ChatView from '@/views/ChatView';
import { ToastContainer } from '@/components/ui/Toast';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { useAdapterStore } from '@/stores/adapterStore';
import '@/index.css';

const historyMessage = {
  role: 'user' as const,
  content: '[Docs](https://example.test/history?q=1#h)',
  messageId: 'm-history',
  ts: new Date().toISOString(),
};

useSessionStore.setState({
  currentSessionId: 's1',
  sessions: [
    {
      id: 's1',
      name: 'Composer URL node',
      adapter: 'codex',
      workerId: 'w-s1',
      workerStatus: 'running',
      nativeControl: true,
      alwaysThinkingEnabled: false,
      effort: '',
      history: [historyMessage],
      workdir: '/fixture',
    },
  ],
  currentMessages: [historyMessage],
});
useWorkerStore.setState({
  workers: { s1: { id: 'w-s1', sessionId: 's1', status: 'running' } },
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
