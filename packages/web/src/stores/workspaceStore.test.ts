import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import * as api from '@/services/api';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    createWorkspace: vi.fn(async (name: string) => ({
      id: 'workspace-new', name, createdAt: '2026-09-24T00:00:00Z', updatedAt: '2026-09-24T00:00:00Z', order: null,
    })),
    deleteWorkspace: vi.fn().mockResolvedValue(undefined),
    setSessionWorkspaces: vi.fn().mockResolvedValue(undefined),
    unclaimSession: vi.fn().mockResolvedValue(undefined),
  };
});

describe('workspace membership', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspaceStore.getState().reset();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
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

  it('moves a manager tree by writing only its root membership', async () => {
    useSessionStore.setState({ sessions: [
      { id: 'root', name: 'Root', workspaceIds: ['workspace-b'], managed: ['child'] } as unknown as Session,
      { id: 'child', name: 'Child', managedBy: 'root', workspaceIds: ['workspace-b'], managed: ['leaf'] } as unknown as Session,
      { id: 'leaf', name: 'Leaf', managedBy: 'child', workspaceIds: ['workspace-b'], managed: [] } as unknown as Session,
    ] });
    const changed = await useWorkspaceStore.getState().moveSessions(['root'], 'workspace-a');

    expect(api.setSessionWorkspaces).toHaveBeenCalledTimes(1);
    expect(api.setSessionWorkspaces).toHaveBeenCalledWith('root', ['workspace-a']);
    expect(changed).toEqual(['root', 'child', 'leaf']);
  });

  it('asks before moving a managed subtree across workspaces and sends no request on cancel', async () => {
    useSessionStore.setState({ sessions: [
      { id: 'manager', name: 'Manager', workspaceIds: ['workspace-a'], managed: ['child'] } as unknown as Session,
      { id: 'child', name: 'Child', managedBy: 'manager', workspaceIds: ['workspace-a'], managed: ['leaf'] } as unknown as Session,
      { id: 'leaf', name: 'Leaf', managedBy: 'child', workspaceIds: ['workspace-a'], managed: [] } as unknown as Session,
    ] });
    const prompt = new Promise<void>((resolve) => {
      window.addEventListener('pan:confirm-workspace-manager-change', ((event: Event) => {
        const detail = (event as CustomEvent<{ changeType: string; resolve: (accepted: boolean) => void }>).detail;
        expect(detail.changeType).toBe('detach');
        expect(detail.resolve).toBeTypeOf('function');
        detail.resolve(false);
        resolve();
      }) as EventListener, { once: true });
    });
    const moving = useWorkspaceStore.getState().moveSessions(['child'], 'workspace-b');
    await prompt;
    expect(await moving).toEqual([]);
    expect(api.unclaimSession).not.toHaveBeenCalled();
    expect(api.setSessionWorkspaces).not.toHaveBeenCalled();
  });

  it('detaches and moves a managed subtree without prompting when confirmation is disabled', async () => {
    useAppSettingsStore.setState({
      notifications: { ...DEFAULT_SETTINGS.notifications, confirmCrossWorkspaceManagement: false },
    });
    useSessionStore.setState({ sessions: [
      { id: 'manager', name: 'Manager', workspaceIds: ['workspace-a'], managed: ['child'] } as unknown as Session,
      { id: 'child', name: 'Child', managedBy: 'manager', workspaceIds: ['workspace-a'], managed: [] } as unknown as Session,
    ] });
    const prompt = vi.fn();
    window.addEventListener('pan:confirm-workspace-manager-change', prompt);

    const changed = await useWorkspaceStore.getState().moveSessions(['child'], 'workspace-b');
    window.removeEventListener('pan:confirm-workspace-manager-change', prompt);

    expect(changed).toEqual(['child']);
    expect(api.unclaimSession).toHaveBeenCalledWith('manager', 'child');
    expect(api.setSessionWorkspaces).toHaveBeenCalledWith('child', ['workspace-b']);
    expect(prompt).not.toHaveBeenCalled();
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
