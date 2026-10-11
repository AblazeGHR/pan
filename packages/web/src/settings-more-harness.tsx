/* eslint-disable react-refresh/only-export-components */
import { createRoot } from 'react-dom/client';
import { useRef } from 'react';
import { useSessionStore } from './stores/sessionStore';
import { useWorkerStore } from './stores/workerStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from './stores/appSettingsStore';
import { useAdapterStore } from './stores/adapterStore';
import { useQueueStore } from './stores/queueStore';
import { useNativeControlStore } from './stores/nativeControlStore';
import { TopBar } from './components/layout/TopBar';
import { SettingsPopover } from './components/chat/SettingsPopover';
import type { NativeControlState } from './services/nativeControl';
import './index.css';

// Browser-only fixture for the mobile Session Settings "More" Terminal TUI
// entry. All API traffic is intercepted by the acceptance script; this does not
// connect to a running Pan instance or a provider.
const session = {
  id: 'settings-more-browser',
  name: 'Settings More terminal entry fixture',
  adapter: 'codex' as const,
  workerId: 'worker-1',
  workerStatus: 'running',
  nativeControl: false,
  model: 'gpt-6',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

useSessionStore.setState({
  currentSessionId: session.id,
  sessions: [session],
  currentMessages: [],
});
useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
useQueueStore.setState({ panelOpen: false });
useAdapterStore.setState({
  currentAdapter: 'codex',
  adapterConfigs: {
    codex: {
      models: ['gpt-6'],
      defaultModel: 'gpt-6',
      permissionModes: [{ value: 'default', label: 'Default' }],
      defaultPermissionMode: 'default',
      supportedSettings: [
        'model',
        'permissionMode',
        'thinking',
        'effort',
        'modelContextWindow',
        'modelAutoCompactTokenLimit',
      ],
      effortValues: ['low', 'high'],
    },
  },
});
useWorkerStore.setState({ currentWorker: null });

function Harness() {
  const anchorRef = useRef<HTMLButtonElement | null>(null);
  return (
    <div style={{ height: '100dvh', position: 'relative' }}>
      {/* TopBar is the shared poller that completes pending-idle upgrades. */}
      <TopBar />
      {/* The popover anchors above this button, like the composer gear. */}
      <button
        ref={anchorRef}
        type="button"
        style={{ position: 'absolute', left: 8, bottom: 8 }}
        aria-label="Anchor"
      >
        anchor
      </button>
      <div data-settings-popover>
        <SettingsPopover open onClose={() => {}} anchorRef={anchorRef} />
      </div>
    </div>
  );
}

window.__setTerminalState = (state: Partial<NativeControlState>) => {
  // Mirror the state into the acceptance script's mock server so its GET
  // responses (polled every second) keep confirming what the UI shows.
  void fetch('/api/__test/set-state', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state),
  }).catch(() => {
    /* mock-only channel */
  });
  useNativeControlStore.setState((current) => ({
    sessions: {
      ...current.sessions,
      [session.id]: {
        workerId: 'worker-1',
        mode: 'pan',
        generation: 1,
        error: null,
        enabled: false,
        pendingEnabled: null,
        ...state,
      },
    },
  }));
  if (typeof state.enabled === 'boolean') {
    useSessionStore.setState((current) => ({
      sessions: current.sessions.map((item) =>
        item.id === session.id ? { ...item, nativeControl: state.enabled } : item,
      ),
    }));
  }
};

createRoot(document.getElementById('root')!).render(<Harness />);

declare global {
  interface Window {
    __setTerminalState: (state: Partial<NativeControlState>) => void;
  }
}
