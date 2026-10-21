import { create } from 'zustand';
import { actionRequest } from '@/services/api';
import type { ActionRegistry } from '@/types/actions';
import type { McpServerInfo, PluginBinding, Session } from '@/types';

/**
 * Shared Action registry cache.
 *
 * The registry is the authority for the Actions list and Group releases. It is
 * loaded once, rendered immediately on re-entry, and revalidated only when the
 * snapshot is older than the freshness window or after an explicit mutation.
 * There is no polling. MCP server catalog, Session list and Session detail are
 * separate caches so a slow secondary read can never hold the registry back.
 */

export const ACTION_REGISTRY_TTL_MS = 30_000;
export const ACTION_CATALOG_TTL_MS = 30_000;
export const ACTION_SESSIONS_TTL_MS = 15_000;
export const ACTION_SESSION_DETAIL_TTL_MS = 15_000;

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

export const emptyActionRegistry = (): ActionRegistry => ({ revision: 0, actions: [], groups: [] });

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface ActionRegistryStore {
  registry: ActionRegistry;
  hasLoaded: boolean;
  status: LoadStatus;
  error: string | null;
  /** A background refresh failed; the last usable registry remains visible. */
  refreshError: string | null;
  lastLoadedAt: number | null;
  refreshing: boolean;
  mutationPending: boolean;
  loadSeq: number;
  mutationSeq: number;

  ensureRegistry: (options?: { force?: boolean }) => Promise<void>;
  refreshRegistry: () => Promise<boolean>;
  maybeRevalidate: () => void;
  runMutation: <T extends ActionRegistry>(
    operation: (expectedRevision: number) => Promise<T>,
  ) => Promise<T>;
  applyBinding: (pluginId: string, session: Session, binding: PluginBinding) => void;
  reset: () => void;
}

let registryInflight: Promise<void> | null = null;
let mutationChain: Promise<unknown> = Promise.resolve();

export const useActionRegistryStore = create<ActionRegistryStore>((set, get) => ({
  registry: emptyActionRegistry(),
  hasLoaded: false,
  status: 'idle',
  error: null,
  refreshError: null,
  lastLoadedAt: null,
  refreshing: false,
  mutationPending: false,
  loadSeq: 0,
  mutationSeq: 0,

  ensureRegistry: async (options = {}) => {
    const force = Boolean(options.force);
    const state = get();
    const now = Date.now();
    if (
      !force &&
      state.hasLoaded &&
      state.lastLoadedAt !== null &&
      now - state.lastLoadedAt < ACTION_REGISTRY_TTL_MS
    ) {
      return;
    }
    if (registryInflight && !force) {
      await registryInflight;
      return;
    }

    const loadSeq = state.loadSeq + 1;
    const mutationSeq = state.mutationSeq;
    set({
      loadSeq,
      // Cached data stays on screen; only the first load owns the blocking state.
      status: state.hasLoaded ? state.status : 'loading',
      error: state.hasLoaded ? state.error : null,
      refreshing: state.hasLoaded,
    });

    const task = (async () => {
      try {
        const next = await actionRequest<ActionRegistry>('actions');
        const current = get();
        // A newer load owns the busy state; an older request must never clear
        // it. If this request is still the load owner, every terminal path
        // below converges refreshing even when the snapshot is dropped.
        if (current.loadSeq !== loadSeq) return;
        if (current.mutationSeq !== mutationSeq) {
          set({ refreshing: false });
          return;
        }
        if (next.revision < current.registry.revision) {
          set({ refreshing: false });
          return;
        }
        set({
          registry: next,
          hasLoaded: true,
          status: 'ready',
          error: null,
          refreshError: null,
          lastLoadedAt: Date.now(),
          refreshing: false,
        });
      } catch (error) {
        const current = get();
        if (current.loadSeq !== loadSeq) return;
        if (current.mutationSeq !== mutationSeq) {
          set({ refreshing: false });
          return;
        }
        if (current.hasLoaded) {
          set({ refreshing: false, refreshError: messageOf(error) });
        } else {
          set({ refreshing: false, status: 'error', error: messageOf(error) });
        }
      }
    })();

    registryInflight = task;
    try {
      await task;
    } finally {
      if (registryInflight === task) registryInflight = null;
    }
  },

  refreshRegistry: async () => {
    await get().ensureRegistry({ force: true });
    const state = get();
    return state.status !== 'error' && !state.refreshError;
  },

  maybeRevalidate: () => {
    const state = get();
    if (!state.hasLoaded || state.lastLoadedAt === null) return;
    if (state.refreshing || state.mutationPending) return;
    if (Date.now() - state.lastLoadedAt < ACTION_REGISTRY_TTL_MS) return;
    void state.ensureRegistry();
  },

  runMutation: (operation) => {
    const run = mutationChain.then(async () => {
      const startMutationSeq = get().mutationSeq + 1;
      set({ mutationPending: true, mutationSeq: startMutationSeq });
      try {
        const next = await operation(get().registry.revision);
        if (!next || !Array.isArray(next.actions) || !Array.isArray(next.groups)) {
          throw new Error('The server returned an invalid Action registry.');
        }
        set({
          registry: next,
          hasLoaded: true,
          status: 'ready',
          error: null,
          refreshError: null,
          lastLoadedAt: Date.now(),
          mutationPending: false,
          // Bump again so any GET that started during the mutation cannot
          // apply its pre-mutation Session/Group projection afterwards.
          mutationSeq: get().mutationSeq + 1,
        });
        return next;
      } catch (error) {
        set({ mutationPending: false, mutationSeq: get().mutationSeq + 1 });
        throw error;
      }
    });
    mutationChain = run.catch(() => undefined);
    return run;
  },

  applyBinding: (pluginId, session, binding) => {
    set((state) => ({
      mutationSeq: state.mutationSeq + 1,
      registry: {
        ...state.registry,
        groups: state.registry.groups.map((group) => {
          if (group.build?.plugin_id !== pluginId) return group;
          const existing = group.sessions.find((entry) => entry.id === session.id);
          return {
            ...group,
            sessions: [
              ...group.sessions.filter((entry) => entry.id !== session.id),
              {
                id: session.id,
                name: session.name,
                loadedVersion: existing?.loadedVersion ?? null,
                ...binding,
              },
            ],
          };
        }),
      },
    }));
  },

  reset: () => {
    registryInflight = null;
    mutationChain = Promise.resolve();
    set({
      registry: emptyActionRegistry(),
      hasLoaded: false,
      status: 'idle',
      error: null,
      refreshError: null,
      lastLoadedAt: null,
      refreshing: false,
      mutationPending: false,
      // Invalidate any request already in flight.
      loadSeq: get().loadSeq + 1,
      mutationSeq: get().mutationSeq + 1,
    });
  },
}));

/* ── MCP plugin catalog ─────────────────────────────────────────────────── */

interface ActionCatalogStore {
  servers: McpServerInfo[];
  status: LoadStatus;
  error: string | null;
  lastLoadedAt: number | null;
  refreshing: boolean;
  loadSeq: number;
  invalidationSeq: number;
  ensureCatalog: (options?: { force?: boolean }) => Promise<void>;
  invalidateCatalog: () => void;
  reset: () => void;
}

let catalogInflight: Promise<void> | null = null;

export const useActionCatalogStore = create<ActionCatalogStore>((set, get) => ({
  servers: [],
  status: 'idle',
  error: null,
  lastLoadedAt: null,
  refreshing: false,
  loadSeq: 0,
  invalidationSeq: 0,

  ensureCatalog: async (options = {}) => {
    const force = Boolean(options.force);
    const state = get();
    if (
      !force &&
      state.status === 'ready' &&
      state.lastLoadedAt !== null &&
      Date.now() - state.lastLoadedAt < ACTION_CATALOG_TTL_MS
    ) {
      return;
    }
    if (catalogInflight && !force) {
      await catalogInflight;
      return;
    }

    const loadSeq = state.loadSeq + 1;
    const invalidationSeq = state.invalidationSeq;
    set({
      loadSeq,
      refreshing: true,
      status: state.servers.length ? 'ready' : 'loading',
      error: null,
    });
    const task = (async () => {
      try {
        const data = await actionRequest<{ servers: McpServerInfo[] }>('mcp/servers');
        const current = get();
        // A newer force request or an invalidation makes this response stale;
        // it may neither write old servers nor mark the cache fresh.
        if (current.loadSeq !== loadSeq) return;
        if (current.invalidationSeq !== invalidationSeq) {
          set({ refreshing: false });
          return;
        }
        set({
          servers: data.servers ?? [],
          status: 'ready',
          error: null,
          lastLoadedAt: Date.now(),
          refreshing: false,
        });
      } catch (error) {
        const current = get();
        if (current.loadSeq !== loadSeq) return;
        if (current.invalidationSeq !== invalidationSeq) {
          set({ refreshing: false });
          return;
        }
        // Keep a previously loaded catalog; the picker can show a warning and
        // retry without discarding mode information.
        set({
          status: get().servers.length ? 'ready' : 'error',
          error: messageOf(error),
          refreshing: false,
        });
      }
    })();
    catalogInflight = task;
    try {
      await task;
    } finally {
      if (catalogInflight === task) catalogInflight = null;
    }
  },

  invalidateCatalog: () =>
    set((state) => ({
      lastLoadedAt: null,
      invalidationSeq: state.invalidationSeq + 1,
    })),
  reset: () => {
    catalogInflight = null;
    set((state) => ({
      servers: [],
      status: 'idle',
      error: null,
      lastLoadedAt: null,
      refreshing: false,
      // Invalidate any request already in flight.
      loadSeq: state.loadSeq + 1,
      invalidationSeq: state.invalidationSeq + 1,
    }));
  },
}));

/* ── Session list (lazy; only the binding dialog needs it) ───────────────── */

interface ActionSessionsStore {
  sessions: Session[];
  status: LoadStatus;
  error: string | null;
  lastLoadedAt: number | null;
  refreshing: boolean;
  loadSeq: number;
  invalidationSeq: number;
  ensureSessions: (options?: { force?: boolean }) => Promise<void>;
  refreshSessions: () => Promise<boolean>;
  reset: () => void;
}

let sessionsInflight: Promise<void> | null = null;

export const useActionSessionsStore = create<ActionSessionsStore>((set, get) => ({
  sessions: [],
  status: 'idle',
  error: null,
  lastLoadedAt: null,
  refreshing: false,
  loadSeq: 0,
  invalidationSeq: 0,

  ensureSessions: async (options = {}) => {
    const force = Boolean(options.force);
    const state = get();
    if (
      !force &&
      state.status === 'ready' &&
      state.lastLoadedAt !== null &&
      Date.now() - state.lastLoadedAt < ACTION_SESSIONS_TTL_MS
    ) {
      return;
    }
    if (sessionsInflight && !force) {
      await sessionsInflight;
      return;
    }

    const loadSeq = state.loadSeq + 1;
    const invalidationSeq = state.invalidationSeq;
    set({
      loadSeq,
      refreshing: true,
      status: state.sessions.length ? 'ready' : 'loading',
      error: null,
    });
    const task = (async () => {
      try {
        const data = await actionRequest<{ sessions: Session[] }>('sessions');
        const current = get();
        if (current.loadSeq !== loadSeq) return;
        if (current.invalidationSeq !== invalidationSeq) {
          set({ refreshing: false });
          return;
        }
        set({
          sessions: data.sessions ?? [],
          status: 'ready',
          error: null,
          lastLoadedAt: Date.now(),
          refreshing: false,
        });
      } catch (error) {
        const current = get();
        if (current.loadSeq !== loadSeq) return;
        if (current.invalidationSeq !== invalidationSeq) {
          set({ refreshing: false });
          return;
        }
        set({
          status: get().sessions.length ? 'ready' : 'error',
          error: messageOf(error),
          refreshing: false,
        });
      }
    })();
    sessionsInflight = task;
    try {
      await task;
    } finally {
      if (sessionsInflight === task) sessionsInflight = null;
    }
  },

  refreshSessions: async () => {
    await get().ensureSessions({ force: true });
    return get().error === null;
  },

  reset: () => {
    sessionsInflight = null;
    set((state) => ({
      sessions: [],
      status: 'idle',
      error: null,
      lastLoadedAt: null,
      refreshing: false,
      // Invalidate any request already in flight.
      loadSeq: state.loadSeq + 1,
      invalidationSeq: state.invalidationSeq + 1,
    }));
  },
}));

/* ── Session detail (keyed cache; late responses cannot replace selection) ─ */

export interface SessionDetailEntry {
  data: Session | null;
  status: LoadStatus;
  error: string | null;
  lastLoadedAt: number | null;
}

interface ActionSessionDetailStore {
  entries: Record<string, SessionDetailEntry>;
  loadSession: (id: string, options?: { force?: boolean }) => Promise<Session | null>;
  mergeSession: (session: Session) => void;
  invalidateSession: (id: string) => void;
  reset: () => void;
}

const detailInflight = new Map<string, Promise<Session | null>>();
const detailSeq = new Map<string, number>();
// Reset may clear per-id sequences back to zero; the epoch keeps an in-flight
// response from a previous cache generation from reusing a matching sequence.
let detailEpoch = 0;

const idleEntry = (): SessionDetailEntry => ({
  data: null,
  status: 'idle',
  error: null,
  lastLoadedAt: null,
});

export const useActionSessionDetailStore = create<ActionSessionDetailStore>((set, get) => ({
  entries: {},

  loadSession: async (id, options = {}) => {
    const force = Boolean(options.force);
    const existing = get().entries[id];
    if (
      !force &&
      existing?.data &&
      existing.lastLoadedAt !== null &&
      Date.now() - existing.lastLoadedAt < ACTION_SESSION_DETAIL_TTL_MS
    ) {
      return existing.data;
    }
    const pending = detailInflight.get(id);
    if (pending && !force) return pending;

    const epoch = detailEpoch;
    const seq = (detailSeq.get(id) ?? 0) + 1;
    detailSeq.set(id, seq);
    set((state) => ({
      entries: {
        ...state.entries,
        [id]: {
          ...(state.entries[id] ?? idleEntry()),
          status: 'loading',
          error: null,
        },
      },
    }));

    const task = (async () => {
      try {
        const data = await actionRequest<Session>(`sessions/${encodeURIComponent(id)}`);
        if (data && 'error' in data && (data as Session & { error?: string }).error) {
          throw new Error((data as Session & { error?: string }).error);
        }
        if (detailEpoch === epoch && detailSeq.get(id) === seq) {
          set((state) => ({
            entries: {
              ...state.entries,
              [id]: { data, status: 'ready', error: null, lastLoadedAt: Date.now() },
            },
          }));
        }
        return data;
      } catch (error) {
        if (detailEpoch === epoch && detailSeq.get(id) === seq) {
          set((state) => ({
            entries: {
              ...state.entries,
              [id]: {
                ...(state.entries[id] ?? idleEntry()),
                status: 'error',
                error: messageOf(error),
              },
            },
          }));
        }
        return null;
      }
    })();
    detailInflight.set(id, task);
    void task.finally(() => {
      if (detailInflight.get(id) === task) detailInflight.delete(id);
    });
    return task;
  },

  mergeSession: (session) => {
    detailSeq.set(session.id, (detailSeq.get(session.id) ?? 0) + 1);
    set((state) => ({
      entries: {
        ...state.entries,
        [session.id]: { data: session, status: 'ready', error: null, lastLoadedAt: Date.now() },
      },
    }));
  },

  invalidateSession: (id) => {
    detailSeq.set(id, (detailSeq.get(id) ?? 0) + 1);
    set((state) => {
      const existing = state.entries[id];
      if (!existing) return state;
      return {
        entries: {
          ...state.entries,
          [id]: { ...existing, lastLoadedAt: null },
        },
      };
    });
  },

  reset: () => {
    detailInflight.clear();
    detailSeq.clear();
    detailEpoch += 1;
    set({ entries: {} });
  },
}));

export function resetActionCachesForTests(): void {
  useActionRegistryStore.getState().reset();
  useActionCatalogStore.getState().reset();
  useActionSessionsStore.getState().reset();
  useActionSessionDetailStore.getState().reset();
}
