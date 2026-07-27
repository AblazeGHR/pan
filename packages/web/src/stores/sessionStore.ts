import { create } from 'zustand';
import type {
  Session,
  Message,
  ApiSessionHistoryResponse,
} from '@/types';
import {
  fetchSessions,
  fetchSessionHistory,
  createSession,
  deleteSession,
  batchDeleteSessions,
  renameSession,
  branchSession,
  reimportSession,
} from '@/services/api';

interface SessionStore {
  // State
  sessions: Session[];
  currentSessionId: string | null;
  currentMessages: Message[];
  hasMoreMessages: boolean;
  historyLoading: boolean;
  historyLoadEnd: number;
  multiSelectMode: boolean;
  selectedIds: Set<string>;
  inputDrafts: Record<string, string>;
  sessionUnread: Record<string, Set<string>>;
  rendering: boolean;

  // Actions
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  loadOlderMessages: () => Promise<void>;
  createNewSession: (
    name: string,
    workdir?: string | null,
    adapter?: string,
  ) => Promise<void>;
  removeSession: (id: string) => Promise<void>;
  batchRemoveSessions: () => Promise<void>;
  rename: (id: string, name: string) => Promise<void>;
  branch: (id: string, name: string) => Promise<void>;
  reimport: (id: string) => Promise<void>;
  setInputDraft: (id: string, draft: string) => void;
  addMessage: (msg: Message) => void;
  appendMessages: (msgs: Message[]) => void;
  updateSession: (id: string, data: Partial<Session>) => void;
  updateFromServer: (id: string, session: Session) => void;
  toggleMultiSelect: (id?: string) => void;
  toggleSelection: (id: string) => void;
  exitMultiSelect: () => void;
  setRendering: (v: boolean) => void;
  getUnread: () => Set<string>;
  markUnread: (content: string) => void;
  clearUnread: () => void;
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  currentMessages: [],
  hasMoreMessages: false,
  historyLoading: false,
  historyLoadEnd: 0,
  multiSelectMode: false,
  selectedIds: new Set(),
  inputDrafts: {},
  sessionUnread: {},
  rendering: false,

  loadSessions: async () => {
    try {
      const sessions = await fetchSessions();
      const { currentSessionId } = get();
      set({ sessions });
      // Restore current session messages after refresh
      if (currentSessionId) {
        const found = sessions.find((s) => s.id === currentSessionId);
        if (found) {
          set({
            currentMessages: found.history || [],
            hasMoreMessages: !!found.historyTruncated,
            historyLoadEnd: Math.max(
              0,
              (found.historyTotal ?? (found.history || []).length) -
                (found.history || []).length,
            ),
          });
        } else {
          set({
            currentSessionId: null,
            currentMessages: [],
            hasMoreMessages: false,
          });
        }
      }
    } catch {
      console.warn('[sessionStore] loadSessions failed');
    }
  },

  selectSession: async (id: string) => {
    const { currentSessionId } = get();

    set((s) => {
      const currentInput = (
        document.getElementById('chatInput') as HTMLInputElement | null
      )?.value;
      const newDrafts = { ...s.inputDrafts };
      if (currentSessionId && currentInput?.trim()) {
        newDrafts[currentSessionId] = currentInput;
      } else if (currentSessionId) {
        delete newDrafts[currentSessionId];
      }
      return { inputDrafts: newDrafts, currentSessionId: id };
    });

    const session = get().sessions.find((s) => s.id === id);
    if (!session) return;

    const loaded = (session.history || []).length;
    set({
      currentMessages: session.history || [],
      hasMoreMessages: !!session.historyTruncated,
      historyLoading: false,
      historyLoadEnd: Math.max(
        0,
        (session.historyTotal ?? loaded) - loaded,
      ),
    });

    if (session.historyTruncated) {
      get().loadOlderMessages();
    }
  },

  loadOlderMessages: async () => {
    const { currentSessionId, historyLoading, historyLoadEnd } = get();
    if (
      historyLoading ||
      historyLoadEnd <= 0 ||
      !currentSessionId
    )
      return;

    set({ historyLoading: true });
    const sid = currentSessionId;

    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        sid,
        historyLoadEnd,
        50,
      );
      if (get().currentSessionId !== sid) return;

      const msgs = data.history || data.history || [];
      if (msgs.length === 0) {
        set({ historyLoading: false });
        return;
      }

      set((s) => {
        const merged = [...msgs, ...s.currentMessages];
        // Update session in the list
        const sessions = s.sessions.map((session) => {
          if (session.id === sid) {
            return {
              ...session,
              history: merged,
              historyTruncated: data.start > 0,
            };
          }
          return session;
        });
        return {
          sessions,
          currentMessages: merged,
          hasMoreMessages: data.start > 0,
          historyLoadEnd: data.start,
          historyLoading: false,
        };
      });
    } catch {
      set({ historyLoading: false });
    }
  },

  createNewSession: async (name, workdir, adapter) => {
    const placeholder: Session = {
      id: `__pending_${name}`,
      name: '...',
      adapter: adapter || 'cbc',
      model: null,
      permissionMode: null,
      alwaysThinkingEnabled: false,
      effort: '',
      history: [],
    };
    set((s) => ({
      sessions: [...s.sessions, placeholder],
      currentSessionId: placeholder.id,
    }));

    try {
      const session = await createSession(name, workdir, adapter);
      set((s) => {
        const sessions = s.sessions.map((se) =>
          se.id === placeholder.id ? session : se,
        );
        return {
          sessions,
          currentSessionId:
            s.currentSessionId === placeholder.id
              ? session.id
              : s.currentSessionId,
        };
      });
    } catch (e) {
      set((s) => ({
        sessions: s.sessions.filter((se) => se.id !== placeholder.id),
      }));
      throw e;
    }
  },

  removeSession: async (id: string) => {
    if (id.startsWith('__pending_')) return;
    // Optimistic removal
    set((s) => ({
      sessions: s.sessions.filter((session) => session.id !== id),
      currentSessionId:
        s.currentSessionId === id ? null : s.currentSessionId,
      currentMessages:
        s.currentSessionId === id ? [] : s.currentMessages,
    }));

    try {
      await deleteSession(id);
      if (get().currentSessionId === id) {
        set({ currentSessionId: null, currentMessages: [] });
      }
      await get().loadSessions();
    } catch {
      await get().loadSessions(); // Recover
    }
  },

  batchRemoveSessions: async () => {
    const { selectedIds } = get();
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    set((s) => ({
      sessions: s.sessions.filter(
        (session) => !selectedIds.has(session.id),
      ),
      multiSelectMode: false,
      selectedIds: new Set(),
    }));

    // Clear current if deleted
    const { currentSessionId } = get();
    if (currentSessionId && selectedIds.has(currentSessionId)) {
      set({ currentSessionId: null, currentMessages: [] });
    }

    try {
      await batchDeleteSessions(ids);
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },

  rename: async (id: string, name: string) => {
    try {
      await renameSession(id, name);
      set((s) => ({
        sessions: s.sessions.map((session) =>
          session.id === id ? { ...session, name } : session,
        ),
      }));
    } catch (e) {
      throw e;
    }
  },

  branch: async (id: string, name: string) => {
    try {
      await branchSession(id, name);
      await get().loadSessions();
    } catch (e) {
      throw e;
    }
  },

  reimport: async (id: string) => {
    const session = get().sessions.find((s) => s.id === id);
    if (!session?.cliSessionId) return;

    try {
      const newSession = await reimportSession(
        id,
        session.adapter || 'cbc',
        session.cliSessionId,
        session.workdir,
      );
      set((s) => ({
        sessions: s.sessions.map((session) =>
          session.id === id ? newSession : session,
        ),
        currentSessionId:
          s.currentSessionId === id ? newSession.id : s.currentSessionId,
      }));
    } catch (e) {
      throw e;
    }
  },

  setInputDraft: (id: string, draft: string) => {
    set((s) => ({
      inputDrafts: { ...s.inputDrafts, [id]: draft },
    }));
  },

  addMessage: (msg: Message) => {
    set((s) => ({
      currentMessages: [...s.currentMessages, msg],
    }));
  },

  appendMessages: (msgs: Message[]) => {
    set((s) => ({
      currentMessages: [...s.currentMessages, ...msgs],
    }));
  },

  updateSession: (id: string, data: Partial<Session>) => {
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? { ...session, ...data } : session,
      ),
    }));
  },

  updateFromServer: (id: string, serverSession: Session) => {
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? serverSession : session,
      ),
    }));
  },

  toggleMultiSelect: (initId?: string) => {
    set((s) => {
      const isActive = !s.multiSelectMode;
      return {
        multiSelectMode: isActive,
        selectedIds: isActive && initId ? new Set([initId]) : new Set<string>(),
      };
    });
  },

  toggleSelection: (id: string) => {
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return { selectedIds: next };
    });
  },

  exitMultiSelect: () => {
    set({ multiSelectMode: false, selectedIds: new Set() });
  },

  setRendering: (v: boolean) => {
    set({ rendering: v });
  },

  getUnread: () => {
    const { currentSessionId, sessionUnread } = get();
    if (!currentSessionId) return new Set();
    return sessionUnread[currentSessionId] ?? new Set();
  },

  markUnread: (content: string) => {
    const { currentSessionId } = get();
    if (!currentSessionId) return;
    set((s) => {
      const perSession = s.sessionUnread[currentSessionId] ?? new Set();
      perSession.add(content);
      return {
        sessionUnread: {
          ...s.sessionUnread,
          [currentSessionId]: perSession,
        },
      };
    });
  },

  clearUnread: () => {
    const { currentSessionId } = get();
    if (!currentSessionId) return;
    set((s) => {
      const copy = { ...s.sessionUnread };
      copy[currentSessionId] = new Set();
      return { sessionUnread: copy };
    });
  },
}));

// Custom hook: derive currentSession from sessions + currentSessionId.
// We cannot use a getter (killed by Zustand's Object.assign) nor a
// subscribe+setState pattern (triggers React #185 nested-update guard).
// Instead, each consumer calls this hook which uses a pure Zustand selector.
export function useCurrentSession() {
  return useSessionStore((s) =>
    s.currentSessionId
      ? s.sessions.find((session) => session.id === s.currentSessionId) ?? null
      : null,
  );
}
