/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { TopBar } from './components/layout/TopBar';
import { AppSettingsModal } from './components/layout/AppSettingsModal';
import { ToastContainer } from './components/ui/Toast';
import { useSessionStore } from './stores/sessionStore';
import { useWorkerStore } from './stores/workerStore';
import { useNativeControlStore } from './stores/nativeControlStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from './stores/appSettingsStore';
import './index.css';

// Browser-only fixture for the Chat TopBar fit policy, Session-name copy and
// the Appearance "Show chat view switcher" switch. All API traffic is
// intercepted by the acceptance script; nothing reaches a Pan instance or a
// provider.
const LONG_NAME =
  'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789';
const sessionId = 'topbar-fit-copy';

function setSessionName(name: string) {
  useSessionStore.setState((current) => ({
    sessions: current.sessions.map((item) =>
      item.id === sessionId ? { ...item, name } : item,
    ),
  }));
}

declare global {
  interface Window {
    __setSessionName: (name: string) => void;
    __openSettings: () => void;
  }
}

useSessionStore.setState({
  currentSessionId: sessionId,
  sessions: [{
    id: sessionId,
    name: LONG_NAME,
    adapter: 'codex',
    cliSessionId: 'cli-topbar-fit-copy',
    model: 'gpt-6',
    workerStatus: 'running',
    workerId: 'worker-fit',
    nativeControl: false,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  }],
  currentMessages: [],
});
useWorkerStore.setState({
  currentWorker: {
    id: 'worker-fit', sessionId, status: 'running',
    nativeUsage: { last: { totalTokens: 12345 }, modelContextWindow: 200000 },
  },
  currentWorkerId: 'worker-fit',
});
useNativeControlStore.setState({ sessions: {}, switching: {}, terminalViews: {} });
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
window.__setSessionName = setSessionName;

function Harness() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  window.__openSettings = () => setSettingsOpen(true);
  return (
    <div style={{ height: '100dvh', position: 'relative' }}>
      <TopBar />
      <div style={{ padding: 16, fontSize: 12 }} data-testid="harness-body">fixture</div>
      <AppSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <ToastContainer />
    </div>
  );
}

// Load the persisted ui object (the mock server owns it) so a reload keeps a
// disabled switcher disabled.
void useAppSettingsStore.getState().loadSettings();

createRoot(document.getElementById('root')!).render(<Harness />);
