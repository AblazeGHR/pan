// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { getCreationWorkspaceIds } from './creationWorkspace';

describe('getCreationWorkspaceIds', () => {
  beforeEach(() => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    useUIStore.setState({ activeWorkspaceId: 'ws-current' });
  });

  it('returns the concrete active Workspace id', () => {
    expect(getCreationWorkspaceIds()).toEqual(['ws-current']);
  });

  it.each(['all', 'ungrouped'])('returns no membership for the %s scope', (activeWorkspaceId) => {
    useUIStore.setState({ activeWorkspaceId });
    expect(getCreationWorkspaceIds()).toEqual([]);
  });

  it('reads the active scope on every request instead of caching modal-open state', () => {
    expect(getCreationWorkspaceIds()).toEqual(['ws-current']);
    useUIStore.setState({ activeWorkspaceId: 'ws-switched-during-modal' });
    expect(getCreationWorkspaceIds()).toEqual(['ws-switched-during-modal']);
  });

  it('returns no membership when the default-to-current-Workspace preference is disabled', () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      defaultNewSessionToCurrentWorkspace: false,
    });
    expect(getCreationWorkspaceIds()).toEqual([]);
  });
});
