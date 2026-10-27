import React from 'react';
import { createRoot } from 'react-dom/client';
import { NewSessionModal } from '@/components/session/NewSessionModal';
import { useAdapterStore } from '@/stores/adapterStore';
import { useSessionStore } from '@/stores/sessionStore';
import { usePlatformStore } from '@/stores/platformStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useUIStore } from '@/stores/uiStore';

// CLI inventory is inert fixture data: no installed provider is probed/launched.
useAdapterStore.setState({
  adapters: [{ name: 'codex', defaultModel: '', supportsResume: true, supportsFork: false }],
  cliStatus: { adapters: [{ name: 'codex', label: 'Codex', available: true, command: [], missing: [], hint: '' }], available: ['codex'], hasAvailable: true },
  cliStatusLoading: false, cliStatusError: null,
  adapterConfigs: { codex: { models: [], defaultModel: '', permissionModes: [], defaultPermissionMode: '', effortValues: [], supportedSettings: [], executionModes: ['stream'] } },
  loadAdapterList: async () => {}, loadCliStatus: async () => {}, loadConfig: async () => {},
});
useWorkspaceStore.setState({ workspaces: [], loaded: true });
useAppSettingsStore.setState({ loaded: true });
useUIStore.setState({ activeWorkspaceId: 'all' });
useSessionStore.setState({ sessions: [] });
await usePlatformStore.getState().load();
createRoot(document.getElementById('root')!).render(<NewSessionModal open onClose={() => {}} />);
