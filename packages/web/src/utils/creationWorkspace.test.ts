// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { getCreationWorkspaceIds } from './creationWorkspace';

describe('getCreationWorkspaceIds', () => {
  beforeEach(() => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useUIStore.setState({ activeWorkspaceId: 'ws-current' });
  });

  it('returns the concrete active Workspace id', async () => {
    await expect(getCreationWorkspaceIds()).resolves.toEqual(['ws-current']);
  });

  it.each(['all', 'ungrouped'])('returns no membership for the %s scope', async (activeWorkspaceId) => {
    useUIStore.setState({ activeWorkspaceId });
    await expect(getCreationWorkspaceIds()).resolves.toEqual([]);
  });

  it('reads the active scope on every request instead of caching modal-open state', async () => {
    await expect(getCreationWorkspaceIds()).resolves.toEqual(['ws-current']);
    useUIStore.setState({ activeWorkspaceId: 'ws-switched-during-modal' });
    await expect(getCreationWorkspaceIds()).resolves.toEqual(['ws-switched-during-modal']);
  });

  it('returns no membership when the default-to-current-Workspace preference is disabled', async () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      loaded: true,
      defaultNewSessionToCurrentWorkspace: false,
    });
    await expect(getCreationWorkspaceIds()).resolves.toEqual([]);
  });
});
