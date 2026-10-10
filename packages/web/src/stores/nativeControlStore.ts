import { create } from 'zustand';
import { nativeRequest, type NativeControlState, type NativeConfiguration } from '@/services/nativeControl';
import { useSessionStore } from './sessionStore';

interface NativeStore {
  sessions: Record<string, NativeControlState>;
  switching: Record<string, boolean>;
  terminalViews: Record<string, boolean>;
  showTerminal: (id: string, visible: boolean) => void;
  refresh: (id: string) => Promise<void>;
  switchMode: (id: string, mode: 'pan' | 'native') => Promise<void>;
  configureSession: (id: string, enabled: boolean) => Promise<NativeConfiguration>;
}
const revisions: Record<string, number> = {};
const viewRevisions: Record<string, number> = {};
export const useNativeControlStore = create<NativeStore>((set, get) => ({
  sessions: {}, switching: {}, terminalViews: {},
  showTerminal: (id, visible) => {
    viewRevisions[id] = (viewRevisions[id] ?? 0) + 1;
    set((current) => ({ terminalViews: { ...current.terminalViews, [id]: visible } }));
  },
  refresh: async (id) => {
    const revision = revisions[id] ?? 0;
    const state = await nativeRequest<NativeControlState>(id);
    set((current) => current.switching[id] || revision !== (revisions[id] ?? 0)
      ? current : { sessions: { ...current.sessions, [id]: state } });
    if (!get().switching[id] && typeof state.enabled === 'boolean' && revision === (revisions[id] ?? 0))
      useSessionStore.setState((current) => ({ sessions: current.sessions.map((s) => s.id === id ? { ...s, nativeControl: state.enabled } : s) }));
  },
  configureSession: async (id, enabled) => {
    if (get().switching[id]) throw new Error('Control switch busy');
    revisions[id] = (revisions[id] ?? 0) + 1;
    set((current) => ({ switching: { ...current.switching, [id]: true } }));
    try {
      const config = await nativeRequest<NativeConfiguration>(id, '/configuration', { enabled });
      set((current) => ({ sessions: { ...current.sessions, [id]: {
        workerId: current.sessions[id]?.workerId ?? null, generation: current.sessions[id]?.generation ?? 0,
        mode: current.sessions[id]?.mode ?? 'pan', error: current.sessions[id]?.error ?? null, ...config,
      } } }));
      useSessionStore.setState((current) => ({ sessions: current.sessions.map((s) => s.id === id ? { ...s, nativeControl: config.enabled } : s) }));
      return config;
    } finally {
      set((current) => ({ switching: { ...current.switching, [id]: false } }));
    }
  },
  switchMode: async (id, mode) => {
    if (get().switching[id]) throw new Error('Control switch busy');
    revisions[id] = (revisions[id] ?? 0) + 1;
    const viewRevision = viewRevisions[id] ?? 0;
    set((current) => ({ switching: { ...current.switching, [id]: true },
      sessions: { ...current.sessions, [id]: { workerId: current.sessions[id]?.workerId ?? null,
        generation: current.sessions[id]?.generation ?? 0, mode: 'switching', error: null } },
    }));
    try {
      const state = await nativeRequest<NativeControlState>(id, '/mode', { mode });
      set((current) => ({ sessions: { ...current.sessions, [id]: state },
        terminalViews: viewRevision === (viewRevisions[id] ?? 0)
          ? { ...current.terminalViews, [id]: mode === 'native' } : current.terminalViews }));
    } catch (error) {
      // Ownership is unknown after a lost receipt, not automatically Pan.
      set((current) => ({ sessions: { ...current.sessions, [id]: {
        workerId: current.sessions[id]?.workerId ?? null, mode: 'switching',
        generation: current.sessions[id]?.generation ?? 0,
        error: 'Control ownership unconfirmed. Retry the switch.',
      } } }));
      // A rejected request may have an authoritative Pan receipt, while a lost
      // transport must remain unknown. Reconcile rather than guessing either.
      try {
        const state = await nativeRequest<NativeControlState>(id);
        if (!state || !['pan', 'native', 'switching'].includes(state.mode)) throw new Error('Invalid control status');
        set((current) => ({ sessions: { ...current.sessions, [id]: state } }));
      } catch { /* keep unknown ownership and suspend both input surfaces */ }
      throw error;
    } finally {
      set((current) => ({ switching: { ...current.switching, [id]: false } }));
    }
  },
}));
