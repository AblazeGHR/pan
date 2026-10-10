import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ChatView from './views/ChatView';
import { useSessionStore } from './stores/sessionStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from './stores/appSettingsStore';
import { useQueueStore } from './stores/queueStore';
import { useAdapterStore } from './stores/adapterStore';
import './index.css';

// Browser-only fixture. All API traffic is intercepted by the acceptance script;
// this does not connect to a running Pan instance or a provider.
useSessionStore.setState({ currentSessionId: 'native-browser', sessions: [{
  id: 'native-browser', name: 'Native control browser fixture', adapter: 'codex',
  workerId: 'same-worker', workerStatus: 'idle',
  nativeControl: new URLSearchParams(window.location.search).get('standard') !== '1', alwaysThinkingEnabled: false, effort: '', history: [],
}], currentMessages: [] });
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
useQueueStore.setState({ panelOpen: false });
useAdapterStore.setState({ adapters: [] });
createRoot(document.getElementById('root')!).render(<MemoryRouter><div style={{ height: '100dvh' }}><ChatView /></div></MemoryRouter>);
