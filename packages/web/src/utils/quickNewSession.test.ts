import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createQuickNewSession } from './quickNewSession';

const mocks = vi.hoisted(() => ({
  fetchAdapterConfig: vi.fn(),
  fetchCliStatus: vi.fn(),
  fetchNewSessionDefaults: vi.fn(),
  fetchSessionTemplates: vi.fn(),
  createNewSession: vi.fn(),
  getCreationWorkspaceIds: vi.fn(),
  activeWorkspaceId: 'ws-current' as string | null,
}));

vi.mock('@/services/api', () => ({
  fetchAdapterConfig: mocks.fetchAdapterConfig,
  fetchCliStatus: mocks.fetchCliStatus,
  fetchNewSessionDefaults: mocks.fetchNewSessionDefaults,
  fetchSessionTemplates: mocks.fetchSessionTemplates,
}));
vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: { getState: () => ({
    sessions: [{ name: 'session-1' }],
    createNewSession: mocks.createNewSession,
  }) },
}));
vi.mock('@/stores/uiStore', () => ({
  useUIStore: { getState: () => ({ activeWorkspaceId: mocks.activeWorkspaceId }) },
}));
vi.mock('@/utils/creationWorkspace', () => ({
  getCreationWorkspaceIds: mocks.getCreationWorkspaceIds,
}));

describe('createQuickNewSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.activeWorkspaceId = 'ws-current';
    mocks.getCreationWorkspaceIds.mockImplementation(async (id) => id ? [id] : []);
    mocks.fetchNewSessionDefaults.mockResolvedValue(null);
    mocks.fetchCliStatus.mockResolvedValue({ adapters: [{ name: 'cbc', available: true }] });
    mocks.fetchSessionTemplates.mockResolvedValue([]);
    mocks.fetchAdapterConfig.mockResolvedValue({ executionModes: ['stream', 'oneshot'] });
    mocks.createNewSession.mockResolvedValue({ id: 'created' });
  });

  it('applies all saved fields, the current Workspace, and the next automatic name', async () => {
    mocks.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'codex',
      outputMode: 'oneshot',
      sessionTemplate: 'review',
      workdir: 'D:\\work\\repo',
    });
    mocks.fetchCliStatus.mockResolvedValue({ adapters: [{ name: 'codex', available: true }] });
    mocks.fetchSessionTemplates.mockResolvedValue([{ name: 'review', adapter: 'codex' }]);

    await createQuickNewSession();

    expect(mocks.getCreationWorkspaceIds).toHaveBeenCalledWith('ws-current');
    expect(mocks.createNewSession).toHaveBeenCalledTimes(1);
    expect(mocks.createNewSession).toHaveBeenCalledWith('session-2', 'D:\\work\\repo', 'codex', 'review', {
      outputMode: 'oneshot',
      workspaceIds: ['ws-current'],
    });
  });

  it('preserves direct-create defaults when no saved config exists', async () => {
    await createQuickNewSession();

    expect(mocks.createNewSession).toHaveBeenCalledTimes(1);
    expect(mocks.createNewSession).toHaveBeenCalledWith('session-2', undefined, undefined, undefined, {
      workspaceIds: ['ws-current'],
    });
    expect(mocks.fetchCliStatus).not.toHaveBeenCalled();
  });

  it('surfaces a defaults read failure without creating a Session', async () => {
    mocks.fetchNewSessionDefaults.mockRejectedValue(new Error('offline'));

    await expect(createQuickNewSession()).rejects.toThrow('offline');
    expect(mocks.createNewSession).not.toHaveBeenCalled();
  });

  it('surfaces a saved-config validation read failure without creating a Session', async () => {
    mocks.fetchNewSessionDefaults.mockResolvedValue({
      adapter: 'cbc', outputMode: '', sessionTemplate: '', workdir: '',
    });
    mocks.fetchCliStatus.mockRejectedValue(new Error('CLI status unavailable'));

    await expect(createQuickNewSession()).rejects.toThrow('CLI status unavailable');
    expect(mocks.createNewSession).not.toHaveBeenCalled();
  });

  it.each([
    ['unavailable adapter', { adapter: 'gone', outputMode: '', sessionTemplate: '', workdir: '' }, { adapters: [] }, [], undefined, 'adapter'],
    ['missing template', { adapter: 'cbc', outputMode: '', sessionTemplate: 'gone', workdir: '' }, { adapters: [{ name: 'cbc', available: true }] }, [], undefined, 'Template'],
    ['unsupported output mode', { adapter: 'cbc', outputMode: 'oneshot', sessionTemplate: '', workdir: '' }, { adapters: [{ name: 'cbc', available: true }] }, [], { executionModes: ['stream'] }, 'Output Mode'],
  ])('rejects a saved config with %s instead of creating with different settings', async (_label, defaults, cliStatus, templates, config, message) => {
    mocks.fetchNewSessionDefaults.mockResolvedValue(defaults);
    mocks.fetchCliStatus.mockResolvedValue(cliStatus);
    mocks.fetchSessionTemplates.mockResolvedValue(templates);
    if (config) mocks.fetchAdapterConfig.mockResolvedValue(config);

    await expect(createQuickNewSession()).rejects.toThrow(message);
    expect(mocks.createNewSession).not.toHaveBeenCalled();
  });

  it('does not issue duplicate creates when invoked again while defaults are loading', async () => {
    let resolveDefaults!: (value: null) => void;
    mocks.fetchNewSessionDefaults.mockImplementation(() => new Promise((resolve) => {
      resolveDefaults = resolve;
    }));
    const first = createQuickNewSession();
    const second = createQuickNewSession();
    expect(mocks.fetchNewSessionDefaults).toHaveBeenCalledTimes(1);
    expect(mocks.createNewSession).not.toHaveBeenCalled();

    resolveDefaults(null);
    await Promise.all([first, second]);
    expect(mocks.createNewSession).toHaveBeenCalledTimes(1);
  });
});
