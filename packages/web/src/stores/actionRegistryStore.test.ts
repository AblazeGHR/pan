import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { actionRequest } from '@/services/api';
import {
  ACTION_REGISTRY_TTL_MS,
  resetActionCachesForTests,
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
});
