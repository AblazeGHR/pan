import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import * as api from '@/services/api';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    createWorkspace: vi.fn(async (name: string) => ({
      id: 'workspace-new', name, createdAt: '2026-09-24T00:00:00Z', updatedAt: '2026-09-24T00:00:00Z', order: null,
    })),
    deleteWorkspace: vi.fn().mockResolvedValue(undefined),
    setSessionWorkspaces: vi.fn().mockResolvedValue(undefined),
  };
});

describe('workspace membership', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspaceStore.getState().reset();
    useSessionStore.setState({
      sessions: [
        { id: 'session-1', workspaceIds: ['workspace-a'], managed: [] } as unknown as Session,
      ],
    });
  });

  it('clears local membership when its workspace is deleted', async () => {
    await useWorkspaceStore.getState().deleteWorkspace('workspace-a');

    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual([]);
  });

  it('normalizes a moved session to its selected single workspace', async () => {
    useSessionStore.setState({
      sessions: [
        { id: 'session-1', workspaceIds: ['workspace-b'], managed: [] } as unknown as Session,
      ],
    });
    const changed = await useWorkspaceStore.getState().moveSessions(['session-1'], 'workspace-a');

    expect(changed).toEqual(['session-1']);
    expect(api.setSessionWorkspaces).toHaveBeenCalledWith('session-1', ['workspace-a']);
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['workspace-a']);
  });

  it('creates a session-named workspace and increments the suffix for duplicate names', async () => {
    useWorkspaceStore.setState({
      workspaces: [
        { id: 'workspace-a', name: 'Alpha', createdAt: '', updatedAt: '', order: null },
        { id: 'workspace-b', name: 'Alpha-1', createdAt: '', updatedAt: '', order: null },
      ],
    });
    useSessionStore.setState({
      sessions: [{ id: 'session-1', name: 'Alpha', workspaceIds: [], managed: [] } as unknown as Session],
    });

    const workspace = await useWorkspaceStore.getState().createWorkspaceForSession('session-1');

    expect(api.createWorkspace).toHaveBeenCalledWith('Alpha-2');
    expect(workspace.name).toBe('Alpha-2');
    expect(api.setSessionWorkspaces).toHaveBeenCalledWith('session-1', ['workspace-new']);
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['workspace-new']);
  });
});
