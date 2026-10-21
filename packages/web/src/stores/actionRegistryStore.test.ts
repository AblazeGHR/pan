import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { actionRequest } from '@/services/api';
import {
  ACTION_REGISTRY_TTL_MS,
  resetActionCachesForTests,
  useActionCatalogStore,
  useActionRegistryStore,
  useActionSessionDetailStore,
  useActionSessionsStore,
} from './actionRegistryStore';
import type { ActionRegistry } from '@/types/actions';
import type { Session } from '@/types';

vi.mock('@/services/api', () => ({ actionRequest: vi.fn() }));
const invoke = vi.mocked(actionRequest);

const action = {
  id: 'list_sessions',
  name: 'List Sessions',
  description: 'List Sessions',
  enabled: true,
  effect: 'read' as const,
  output_format: 'json' as const,
  input_schema: { type: 'object' as const, properties: {}, required: [], additionalProperties: false as const },
  execution: { kind: 'api' as const, method: 'GET' as const, path: '/api/sessions' },
};

function registry(revision = 1): ActionRegistry {
  return { revision, actions: [structuredClone(action)], groups: [] };
}

function session(id: string, name = id): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    pluginBindings: [],
    pluginSelectionRevision: 1,
    history: [],
  } as unknown as Session;
}

beforeEach(() => {
  invoke.mockReset();
  resetActionCachesForTests();
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-21T08:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('action registry cache', () => {
  it('dedupes concurrent first loads and keeps secondary resources lazy', async () => {
    let resolve: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementation(() => new Promise<ActionRegistry>((done) => { resolve = done; }));
    const first = useActionRegistryStore.getState().ensureRegistry();
    const second = useActionRegistryStore.getState().ensureRegistry();
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith('actions');
    resolve(registry());
    await Promise.all([first, second]);
    expect(useActionRegistryStore.getState().hasLoaded).toBe(true);
    expect(invoke.mock.calls.some(([path]) => path === 'mcp/servers' || path === 'sessions')).toBe(false);
  });

  it('serves a fresh cache on re-entry and revalidates only after the TTL', async () => {
    invoke.mockResolvedValue(registry());
    await useActionRegistryStore.getState().ensureRegistry();
    expect(invoke).toHaveBeenCalledTimes(1);

    await useActionRegistryStore.getState().ensureRegistry();
    expect(invoke).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date(Date.now() + ACTION_REGISTRY_TTL_MS + 1));
    invoke.mockResolvedValue(registry(2));
    await useActionRegistryStore.getState().ensureRegistry();
    expect(invoke).toHaveBeenCalledTimes(2);
    expect(useActionRegistryStore.getState().registry.revision).toBe(2);
  });

  it('keeps the last usable registry when a background refresh fails', async () => {
    invoke.mockResolvedValueOnce(registry(3));
    await useActionRegistryStore.getState().ensureRegistry();
    vi.setSystemTime(new Date(Date.now() + ACTION_REGISTRY_TTL_MS + 1));
    invoke.mockRejectedValueOnce(new Error('offline'));
    await useActionRegistryStore.getState().ensureRegistry();
    const state = useActionRegistryStore.getState();
    expect(state.registry.revision).toBe(3);
    expect(state.hasLoaded).toBe(true);
    expect(state.status).toBe('ready');
    expect(state.refreshError).toBe('offline');
  });

  it('drops a late GET that started before a successful mutation', async () => {
    let resolveLoad: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementationOnce(() => new Promise<ActionRegistry>((done) => { resolveLoad = done; }));
    const load = useActionRegistryStore.getState().ensureRegistry();
    await Promise.resolve();
    const mutated = await useActionRegistryStore.getState().runMutation(async () => {
      invoke.mockResolvedValueOnce(registry(9));
      return registry(9);
    });
    expect(mutated.revision).toBe(9);
    resolveLoad(registry(1));
    await load;
    expect(useActionRegistryStore.getState().registry.revision).toBe(9);
  });

  it('serializes mutations so an older response cannot overwrite a newer one', async () => {
    invoke.mockResolvedValueOnce(registry(1));
    await useActionRegistryStore.getState().ensureRegistry();
    let releaseFirst: (value: ActionRegistry) => void = () => {};
    const first = useActionRegistryStore.getState().runMutation(
      () => new Promise<ActionRegistry>((done) => { releaseFirst = done; }),
    );
    const second = useActionRegistryStore.getState().runMutation(async () => registry(3));
    await Promise.resolve();
    releaseFirst(registry(2));
    await first;
    await second;
    expect(useActionRegistryStore.getState().registry.revision).toBe(3);
  });
});

describe('registry busy convergence and stale responses', () => {
  it('ends refreshing when a cached refresh is invalidated by a later mutation', async () => {
    invoke.mockResolvedValueOnce(registry(1));
    await useActionRegistryStore.getState().ensureRegistry();

    let releaseOld: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<ActionRegistry>((done) => { releaseOld = done; }),
    );
    const refresh = useActionRegistryStore.getState().ensureRegistry({ force: true });
    await Promise.resolve();
    expect(useActionRegistryStore.getState().refreshing).toBe(true);

    await useActionRegistryStore.getState().runMutation(async () => registry(2));
    releaseOld(registry(1));
    await refresh;

    const state = useActionRegistryStore.getState();
    expect(state.registry.revision).toBe(2);
    expect(state.refreshing).toBe(false);
    expect(state.status).toBe('ready');
    expect(state.refreshError).toBeNull();

    invoke.mockResolvedValueOnce(registry(3));
    await useActionRegistryStore.getState().refreshRegistry();
    expect(useActionRegistryStore.getState().registry.revision).toBe(3);
  });

  it('keeps a binding applied during an in-flight refresh', async () => {
    invoke.mockResolvedValueOnce({
      revision: 1,
      actions: [structuredClone(action)],
      groups: [
        {
          id: 'tools',
          name: 'Tools',
          action_ids: ['list_sessions'],
          build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: ['list_sessions'] },
          blockers: [],
          sessions: [],
        },
      ],
    });
    await useActionRegistryStore.getState().ensureRegistry();

    let releaseOld: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<ActionRegistry>((done) => { releaseOld = done; }),
    );
    const refresh = useActionRegistryStore.getState().ensureRegistry({ force: true });
    await Promise.resolve();

    useActionRegistryStore.getState().applyBinding(
      'action-group-tools',
      session('s1', 'First'),
      { plugin_id: 'action-group-tools', enabled: true, mode: 'gateway' },
    );
    releaseOld(registry(1));
    await refresh;

    const group = useActionRegistryStore.getState().registry.groups[0]!;
    expect(group.sessions).toEqual([
      {
        id: 's1',
        name: 'First',
        loadedVersion: null,
        plugin_id: 'action-group-tools',
        enabled: true,
        mode: 'gateway',
      },
    ]);
    expect(useActionRegistryStore.getState().refreshing).toBe(false);
  });

  it('does not regress on a lower-revision response and allows another Refresh', async () => {
    invoke.mockResolvedValueOnce(registry(5));
    await useActionRegistryStore.getState().ensureRegistry();

    invoke.mockResolvedValueOnce(registry(4));
    await useActionRegistryStore.getState().refreshRegistry();
    expect(useActionRegistryStore.getState().registry.revision).toBe(5);
    expect(useActionRegistryStore.getState().refreshing).toBe(false);

    invoke.mockResolvedValueOnce(registry(6));
    await useActionRegistryStore.getState().refreshRegistry();
    expect(useActionRegistryStore.getState().registry.revision).toBe(6);
  });

  it('does not let an older refresh clear a newer refresh busy state', async () => {
    invoke.mockResolvedValueOnce(registry(1));
    await useActionRegistryStore.getState().ensureRegistry();

    let releaseFirst: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<ActionRegistry>((done) => { releaseFirst = done; }),
    );
    const first = useActionRegistryStore.getState().ensureRegistry({ force: true });
    await Promise.resolve();
    let releaseSecond: (value: ActionRegistry) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<ActionRegistry>((done) => { releaseSecond = done; }),
    );
    const second = useActionRegistryStore.getState().ensureRegistry({ force: true });
    await Promise.resolve();

    releaseFirst(registry(1));
    await first;
    expect(useActionRegistryStore.getState().refreshing).toBe(true);

    releaseSecond(registry(2));
    await second;
    expect(useActionRegistryStore.getState().registry.revision).toBe(2);
    expect(useActionRegistryStore.getState().refreshing).toBe(false);
  });
});

describe('auxiliary caches', () => {
  it('loads Sessions lazily and keeps cached rows on failure', async () => {
    invoke.mockResolvedValueOnce({ sessions: [session('s1')] });
    await useActionSessionsStore.getState().ensureSessions();
    expect(useActionSessionsStore.getState().sessions).toHaveLength(1);
    vi.setSystemTime(new Date(Date.now() + 20_000));
    invoke.mockRejectedValueOnce(new Error('sessions offline'));
    await useActionSessionsStore.getState().ensureSessions({ force: true });
    expect(useActionSessionsStore.getState().sessions).toHaveLength(1);
    expect(useActionSessionsStore.getState().error).toBe('sessions offline');
  });

  it('caches Session detail per id and ignores a late response for a switched id', async () => {
    let releaseFirst: (value: Session) => void = () => {};
    invoke.mockImplementationOnce(() => new Promise<Session>((done) => { releaseFirst = done; }));
    const first = useActionSessionDetailStore.getState().loadSession('s1');
    await Promise.resolve();
    invoke.mockResolvedValueOnce(session('s2'));
    await useActionSessionDetailStore.getState().loadSession('s2');
    releaseFirst(session('s1'));
    await first;
    const entries = useActionSessionDetailStore.getState().entries;
    expect(entries.s2?.data?.id).toBe('s2');
    expect(entries.s1?.data?.id).toBe('s1');
  });

  it('drops an in-flight Session detail response after cache reset', async () => {
    let releaseOld: (value: Session) => void = () => {};
    invoke.mockImplementationOnce(() => new Promise<Session>((done) => { releaseOld = done; }));
    const load = useActionSessionDetailStore.getState().loadSession('s1');
    await Promise.resolve();
    resetActionCachesForTests();
    releaseOld(session('s1'));
    await load;
    expect(useActionSessionDetailStore.getState().entries).toEqual({});
  });

  it('drops a late catalog response after a force refresh', async () => {
    invoke.mockResolvedValueOnce({ servers: [{ name: 'initial' }] });
    await useActionCatalogStore.getState().ensureCatalog();

    let releaseOld: (value: { servers: { name: string }[] }) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<{ servers: { name: string }[] }>((done) => { releaseOld = done; }),
    );
    const oldEnsure = useActionCatalogStore.getState().ensureCatalog({ force: true });
    await Promise.resolve();
    invoke.mockResolvedValueOnce({ servers: [{ name: 'new' }] });
    const forceEnsure = useActionCatalogStore.getState().ensureCatalog({ force: true });
    await Promise.resolve();

    releaseOld({ servers: [{ name: 'old' }] });
    await oldEnsure;
    await forceEnsure;

    const state = useActionCatalogStore.getState();
    expect(state.servers).toEqual([{ name: 'new' }]);
    expect(state.refreshing).toBe(false);
    expect(state.lastLoadedAt).not.toBeNull();
  });

  it('drops a build-time catalog invalidation response and still accepts the next force load', async () => {
    invoke.mockResolvedValueOnce({ servers: [{ name: 'fresh' }] });
    await useActionCatalogStore.getState().ensureCatalog();

    let releaseOld: (value: { servers: { name: string }[] }) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<{ servers: { name: string }[] }>((done) => { releaseOld = done; }),
    );
    useActionCatalogStore.getState().invalidateCatalog();
    const oldEnsure = useActionCatalogStore.getState().ensureCatalog();
    await Promise.resolve();
    useActionCatalogStore.getState().invalidateCatalog();
    releaseOld({ servers: [{ name: 'old' }] });
    await oldEnsure;

    const invalidated = useActionCatalogStore.getState();
    expect(invalidated.servers).toEqual([{ name: 'fresh' }]);
    expect(invalidated.lastLoadedAt).toBeNull();
    expect(invalidated.refreshing).toBe(false);

    invoke.mockResolvedValueOnce({ servers: [{ name: 'rebuilt' }] });
    await useActionCatalogStore.getState().ensureCatalog({ force: true });
    const reloaded = useActionCatalogStore.getState();
    expect(reloaded.servers).toEqual([{ name: 'rebuilt' }]);
    expect(reloaded.lastLoadedAt).not.toBeNull();
  });

  it('drops a late Session list response after a force refresh', async () => {
    invoke.mockResolvedValueOnce({ sessions: [session('initial')] });
    await useActionSessionsStore.getState().ensureSessions();

    let releaseOld: (value: { sessions: Session[] }) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<{ sessions: Session[] }>((done) => { releaseOld = done; }),
    );
    const oldEnsure = useActionSessionsStore.getState().ensureSessions({ force: true });
    await Promise.resolve();
    invoke.mockResolvedValueOnce({ sessions: [session('new')] });
    const forceEnsure = useActionSessionsStore.getState().ensureSessions({ force: true });
    await Promise.resolve();

    releaseOld({ sessions: [session('old')] });
    await oldEnsure;
    await forceEnsure;

    const state = useActionSessionsStore.getState();
    expect(state.sessions.map((item) => item.id)).toEqual(['new']);
    expect(state.refreshing).toBe(false);
    expect(state.lastLoadedAt).not.toBeNull();
  });

  it('drops an in-flight Session list response after cache reset', async () => {
    let releaseOld: (value: { sessions: Session[] }) => void = () => {};
    invoke.mockImplementationOnce(
      () => new Promise<{ sessions: Session[] }>((done) => { releaseOld = done; }),
    );
    const load = useActionSessionsStore.getState().ensureSessions();
    await Promise.resolve();
    resetActionCachesForTests();
    releaseOld({ sessions: [session('s1')] });
    await load;
    const state = useActionSessionsStore.getState();
    expect(state.sessions).toEqual([]);
    expect(state.status).toBe('idle');
    expect(state.refreshing).toBe(false);
  });
});
