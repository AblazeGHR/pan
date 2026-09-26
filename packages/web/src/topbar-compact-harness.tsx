/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { TopBar } from './components/layout/TopBar';
import { useSessionStore } from './stores/sessionStore';
import { useWorkerStore } from './stores/workerStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from './stores/appSettingsStore';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import './index.css';

const session = {
  id: 'session-123456789',
  name: 'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789',
  workerStatus: 'running',
  workerId: 'worker-123',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

function Harness() {
  const [navigationEnabled, setNavigationEnabled] = useState(true);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const rightAction = navigationEnabled ? (
    <button
      type="button"
      className="message-navigation-mobile-toggle"
      aria-label={navigationOpen ? 'Close message navigation rail' : 'Open message navigation rail'}
      aria-expanded={navigationOpen}
      title={navigationOpen ? 'Close message navigation rail' : 'Open message navigation rail'}
      onClick={() => setNavigationOpen((value) => !value)}
    >
      {navigationOpen ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
    </button>
  ) : undefined;

  window.__setTopBarScenario = (scenario) => {
    useSessionStore.setState({
      currentSessionId: scenario === 'none' ? null : session.id,
      sessions: scenario === 'none' ? [] : [{
        ...session,
        name: scenario === 'short' ? 'Session' : session.name,
        workerStatus: scenario === 'offline' ? 'offline' : 'running',
        workerId: scenario === 'offline' ? null : session.workerId,
      }],
    });
    useWorkerStore.setState({
      currentWorker: scenario === 'offline' || scenario === 'none' ? null : {
        id: session.workerId, sessionId: session.id, status: 'running',
      },
      currentWorkerId: scenario === 'offline' || scenario === 'none' ? null : session.workerId,
    });
    setNavigationEnabled(scenario !== 'nav-off');
    setNavigationOpen(false);
  };

  return <TopBar rightAction={rightAction} />;
}

declare global {
  interface Window {
    __setTopBarScenario: (scenario: string) => void;
  }
}

useSessionStore.setState({
  currentSessionId: session.id,
  sessions: [session],
});
useWorkerStore.setState({
  currentWorker: { id: session.workerId, sessionId: session.id, status: 'running' },
  currentWorkerId: session.workerId,
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, showMessageNavigationRail: true });

createRoot(document.getElementById('root')!).render(<Harness />);
