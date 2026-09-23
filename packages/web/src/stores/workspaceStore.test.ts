import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import * as api from '@/services/api';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    deleteWorkspace: vi.fn().mockResolvedValue(undefined),
    setSessionWorkspaces: vi.fn().mockResolvedValue(undefined),
  };
});

describe('workspace membership preservation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspaceStore.getState().reset();
    useSessionStore.setState({
      sessions: [
        { id: 'session-1', workspaceIds: ['workspace-a', 'workspace-b'], managed: [] } as unknown as Session,
      ],
    });
  });

  it('removes only the deleted workspace from local session membership', async () => {
    await useWorkspaceStore.getState().deleteWorkspace('workspace-a');

    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['workspace-b']);
  });

  it('keeps additional memberships when moving into an already assigned workspace', async () => {
    const changed = await useWorkspaceStore.getState().moveSessions(['session-1'], 'workspace-a');

    expect(changed).toEqual([]);
    expect(api.setSessionWorkspaces).not.toHaveBeenCalled();
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['workspace-a', 'workspace-b']);
  });
});
