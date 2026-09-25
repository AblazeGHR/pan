// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, waitFor } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { createSession } from '@/services/api';
import type { Session } from '@/types';

function mk(id: string, name: string, managedBy?: string | null): Session {
  return {
    id,
    name,
    managedBy,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  };
}

// Deferred createSession so a test can interleave a loadSessions() while the
// create call is still in flight — the exact race that previously dropped the
// new session until a page refresh.
let serverSessions: Session[] = [];
let resolveCreate: (() => void) | null = null;
const apiMock = vi.hoisted(() => ({
  fetchUiSettings: vi.fn(),
  reimportSession: vi.fn(),
  setSessionWorkspaces: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchUiSettings: apiMock.fetchUiSettings,
    fetchSessions: vi.fn(async () => serverSessions),
    createSession: vi.fn(
      (name: string, _workdir?: string | null, _adapter?: string, _template?: string,
       settings?: { workspaceIds?: string[] }) =>
        new Promise<Session>((resolve) => {
          resolveCreate = () =>
            resolve({
              id: `real_${name}`,
              name,
              adapter: 'cbc',
              model: null,
              permissionMode: null,
              alwaysThinkingEnabled: false,
              effort: '',
              workspaceIds: settings?.workspaceIds ?? [],
              history: [],
            } as Session);
        }),
    ),
    reimportSession: apiMock.reimportSession,
    setSessionWorkspaces: apiMock.setSessionWorkspaces,
  };
});

describe('sessionStore createNewSession race', () => {
  beforeEach(() => {
    serverSessions = [mk('M', 'M'), mk('A', 'A', 'M')];
    resolveCreate = null;
    apiMock.fetchUiSettings.mockResolvedValue({ defaultNewSessionToCurrentWorkspace: true });
    apiMock.reimportSession.mockReset();
    apiMock.setSessionWorkspaces.mockReset();
    useSessionStore.setState({
      sessions: [mk('M', 'M'), mk('A', 'A', 'M')],
      currentSessionId: null,
      currentMessages: [],
    });
    useUIStore.setState({ activeWorkspaceId: 'all' });
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
  });

  it('waits for startup settings before sending createSession workspaceIds', async () => {
    let resolveSettings!: (value: Record<string, unknown>) => void;
    apiMock.fetchUiSettings.mockReturnValue(new Promise((resolve) => {
      resolveSettings = resolve;
    }));
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: false });
    useUIStore.setState({ activeWorkspaceId: 'ws-at-action-start' });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('Hydrated');
    });
    await waitFor(() => expect(apiMock.fetchUiSettings).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createSession)).not.toHaveBeenCalled();

    useUIStore.setState({ activeWorkspaceId: 'ws-after-action-start' });
    resolveSettings({ defaultNewSessionToCurrentWorkspace: false });

    await waitFor(() => expect(vi.mocked(createSession)).toHaveBeenCalledWith(
      'Hydrated', undefined, undefined, undefined, { workspaceIds: [] },
    ));

    await act(async () => {
      resolveCreate?.();
      await promise!;
    });
    expect(useSessionStore.getState().sessions.find((item) => item.id === 'real_Hydrated')?.workspaceIds)
      .toEqual([]);
  });

  it('keeps the new session when loadSessions interleaves during creation', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('X');
    });
    // Placeholder present while create is pending.
    expect(
      useSessionStore.getState().sessions.some((s) => s.id === '__pending_X'),
    ).toBe(true);

    // Concurrent loadSessions (server hasn't committed X yet) overwrites the
    // list and wipes the client-only placeholder.
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    expect(
      useSessionStore.getState().sessions.some((s) => s.id === '__pending_X'),
    ).toBe(false);

    // Create finally resolves — the real session must still be inserted.
    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    const ids = useSessionStore.getState().sessions.map((s) => s.id);
    expect(ids).toContain('real_X');
    expect(useSessionStore.getState().currentSessionId).toBe('real_X');
  });

  it('does not duplicate a session a concurrent loadSessions already returned', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('X');
    });
    // Server commits X while the create call is still in flight; a reload
    // brings it in.
    serverSessions = [
      mk('M', 'M'),
      mk('A', 'A', 'M'),
      mk('real_X', 'X', 'M'),
    ];
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });
    expect(
      useSessionStore.getState().sessions.filter((s) => s.id === 'real_X').length,
    ).toBe(1);

    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    const ids = useSessionStore.getState().sessions.map((s) => s.id);
    expect(ids.filter((id) => id === 'real_X').length).toBe(1);
    expect(useSessionStore.getState().currentSessionId).toBe('real_X');
  });

  it('uses the active Workspace by default for new-session entry points without explicit settings', async () => {
    useUIStore.setState({ activeWorkspaceId: 'ws-current' });
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession('X');
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(useSessionStore.getState().sessions.find((s) => s.id === '__pending_X')?.workspaceIds)
      .toEqual(['ws-current']);
    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    expect(useSessionStore.getState().sessions.find((s) => s.id === 'real_X')?.workspaceIds)
      .toEqual(['ws-current']);
  });

  it('keeps the create response Workspace when a concurrent list inserted the same Session', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession(
        'X', null, 'cbc', undefined, { workspaceIds: ['ws-current'] },
      );
    });
    serverSessions = [
      mk('M', 'M'),
      mk('A', 'A', 'M'),
      { ...mk('real_X', 'X'), workspaceIds: [] },
    ];
    await act(async () => {
      await useSessionStore.getState().loadSessions();
    });

    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    const created = useSessionStore.getState().sessions.filter((s) => s.id === 'real_X');
    expect(created).toHaveLength(1);
    expect(created[0]?.workspaceIds).toEqual(['ws-current']);
  });

  it('keeps the requested Workspace on the optimistic placeholder and created Session', async () => {
    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().createNewSession(
        'Scoped', null, 'cbc', undefined, { workspaceIds: ['ws-current'] },
      );
    });

    expect(useSessionStore.getState().sessions.find((item) => item.id === '__pending_Scoped')?.workspaceIds)
      .toEqual(['ws-current']);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      resolveCreate?.();
      await promise!;
    });

    expect(useSessionStore.getState().sessions.find((item) => item.id === 'real_Scoped')?.workspaceIds)
      .toEqual(['ws-current']);
  });

  it.each(['all', 'ungrouped'])('preserves membership when reimport starts in %s scope', async (activeWorkspaceId) => {
    const existing = {
      ...mk('ses_reimport', 'Reimport'),
      adapter: 'cbc',
      cliSessionId: 'native-reimport',
      workspaceIds: ['ws-original'],
    } as Session;
    useSessionStore.setState({ sessions: [existing], currentSessionId: 'ses_reimport' });
    apiMock.reimportSession.mockResolvedValue({
      id: 'ses_reimport',
      name: 'Reimport',
      alwaysThinkingEnabled: false,
      effort: '',
      history: [],
    } as Session);
    useUIStore.setState({ activeWorkspaceId });

    await act(async () => {
      await useSessionStore.getState().reimport('ses_reimport');
    });

    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-original']);
    expect(apiMock.reimportSession).toHaveBeenCalledWith(
      'ses_reimport', 'cbc', 'native-reimport', undefined,
    );
    expect(apiMock.setSessionWorkspaces).not.toHaveBeenCalled();
  });

  it('reimports a managed child in a concrete Workspace without changing inherited membership or manager', async () => {
    const manager = {
      ...mk('ses_manager', 'Manager'),
      workspaceIds: ['ws-inherited'],
    } as Session;
    const child = {
      ...mk('ses_managed_child', 'Managed child', manager.id),
      adapter: 'cbc',
      cliSessionId: 'native-managed-child',
      workspaceIds: ['ws-inherited'],
    } as Session;
    const reimportedChild = {
      ...child,
      history: [{ role: 'user', content: 'updated child history' }],
    } as Session;
    useSessionStore.setState({
      sessions: [manager, child],
      currentSessionId: child.id,
    });
    useUIStore.setState({ activeWorkspaceId: 'ws-target' });
    apiMock.reimportSession.mockResolvedValue(reimportedChild);
    apiMock.setSessionWorkspaces.mockRejectedValue(new Error('managed_session'));

    await act(async () => {
      await useSessionStore.getState().reimport(child.id);
    });

    expect(apiMock.setSessionWorkspaces).not.toHaveBeenCalled();
    const sessions = useSessionStore.getState().sessions;
    expect(sessions.find((item) => item.id === manager.id)?.workspaceIds).toEqual(['ws-inherited']);
    expect(sessions.find((item) => item.id === child.id)).toMatchObject({
      managedBy: manager.id,
      workspaceIds: ['ws-inherited'],
      history: reimportedChild.history,
    });
  });

  it('moves an existing Session to the concrete Workspace regardless of the new-session preference', async () => {
    const existing = {
      ...mk('ses_reimport', 'Reimport'),
      adapter: 'cbc',
      cliSessionId: 'native-reimport',
      workspaceIds: ['ws-original'],
    } as Session;
    const reimported = {
      ...existing,
      history: [{ role: 'user', content: 'updated history' }],
    } as Session;
    useSessionStore.setState({ sessions: [existing], currentSessionId: 'ses_reimport' });
    useUIStore.setState({ activeWorkspaceId: 'ws-target' });
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      loaded: true,
      defaultNewSessionToCurrentWorkspace: false,
    });
    apiMock.reimportSession.mockResolvedValue(reimported);
    apiMock.setSessionWorkspaces.mockResolvedValue({
      ...existing,
      workspaceIds: ['ws-target'],
    });

    await act(async () => {
      await useSessionStore.getState().reimport('ses_reimport');
    });

    expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith('ses_reimport', ['ws-target']);
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-target']);
    expect(useSessionStore.getState().sessions[0]?.history).toEqual(reimported.history);
  });

  it('keeps the Reimport-start Workspace if the active Workspace changes during history import', async () => {
    const existing = {
      ...mk('ses_reimport', 'Reimport'),
      adapter: 'cbc',
      cliSessionId: 'native-reimport',
      workspaceIds: ['ws-original'],
    } as Session;
    useSessionStore.setState({ sessions: [existing], currentSessionId: 'ses_reimport' });
    useUIStore.setState({ activeWorkspaceId: 'ws-at-reimport-start' });
    let resolveReimport!: (session: Session) => void;
    apiMock.reimportSession.mockReturnValue(new Promise((resolve) => {
      resolveReimport = resolve;
    }));
    apiMock.setSessionWorkspaces.mockResolvedValue({
      ...existing,
      workspaceIds: ['ws-at-reimport-start'],
    });

    let promise: Promise<void>;
    act(() => {
      promise = useSessionStore.getState().reimport('ses_reimport');
    });
    useUIStore.setState({ activeWorkspaceId: 'ws-switched-during-reimport' });
    resolveReimport({ ...existing, history: [{ role: 'user', content: 'updated' }] });

    await act(async () => {
      await promise!;
    });

    expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith(
      'ses_reimport', ['ws-at-reimport-start'],
    );
    expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-at-reimport-start']);
  });

  it('reports membership persistence failure after preserving reimported history and prior membership', async () => {
    const existing = {
      ...mk('ses_reimport', 'Reimport'),
      adapter: 'cbc',
      cliSessionId: 'native-reimport',
      workspaceIds: ['ws-original'],
    } as Session;
    const reimported = {
      ...existing,
      history: [{ role: 'user', content: 'updated history' }],
    } as Session;
    serverSessions = [reimported];
    useSessionStore.setState({ sessions: [existing], currentSessionId: 'ses_reimport' });
    apiMock.reimportSession.mockResolvedValue(reimported);
    apiMock.setSessionWorkspaces.mockRejectedValue(new Error('Workspace not found'));

    await expect(useSessionStore.getState().reimport('ses_reimport', 'ws-deleted'))
      .rejects.toThrow('Session history was reimported, but the Workspace move was not confirmed: Workspace not found');
    await waitFor(() => {
      expect(useSessionStore.getState().sessions[0]?.workspaceIds).toEqual(['ws-original']);
      expect(useSessionStore.getState().sessions[0]?.history).toEqual(reimported.history);
    });
    expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith('ses_reimport', ['ws-deleted']);
  });
});
