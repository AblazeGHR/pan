import { create } from 'zustand';
import { nativeRequest, type NativeControlState } from '@/services/nativeControl';

interface NativeStore {
  sessions: Record<string, NativeControlState>;
  switching: Record<string, boolean>;
  refresh: (id: string) => Promise<void>;
  switchMode: (id: string, mode: 'pan' | 'native') => Promise<void>;
}
const revisions: Record<string, number> = {};
export const useNativeControlStore = create<NativeStore>((set, get) => ({
  sessions: {}, switching: {},
  refresh: async (id) => {
    const revision = revisions[id] ?? 0;
    const state = await nativeRequest<NativeControlState>(id);
    set((current) => current.switching[id] || revision !== (revisions[id] ?? 0)
      ? current : { sessions: { ...current.sessions, [id]: state } });
  },
  switchMode: async (id, mode) => {
    if (get().switching[id]) throw new Error('Control switch busy');
    revisions[id] = (revisions[id] ?? 0) + 1;
    set((current) => ({ switching: { ...current.switching, [id]: true },
      sessions: { ...current.sessions, [id]: { workerId: current.sessions[id]?.workerId ?? null,
        generation: current.sessions[id]?.generation ?? 0, mode: 'switching', error: null } },
    }));
    try {
      const state = await nativeRequest<NativeControlState>(id, '/mode', { mode });
      set((current) => ({ sessions: { ...current.sessions, [id]: state } }));
    } catch (error) {
      // Ownership is unknown after a lost receipt, not automatically Pan.
      set((current) => ({ sessions: { ...current.sessions, [id]: {
        workerId: current.sessions[id]?.workerId ?? null, mode: 'switching',
        generation: current.sessions[id]?.generation ?? 0,
        error: 'Control ownership unconfirmed. Retry the switch.',
      } } }));
      throw error;
    } finally {
      set((current) => ({ switching: { ...current.switching, [id]: false } }));
    }
  },
}));
