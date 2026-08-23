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

  // ── Internal staleness guards ──
  // Incremented on every loadSessions() start so an older in-flight response
  // can never overwrite a newer refresh.
  _loadSeq: number;
  // Global monotonic touch counter; per-session snapshots of it let
  // loadSessions() skip reverting workerStatus/workerId that WS events
  // freshened while its own HTTP request was in flight.
  _touchSeq: number;
  // sessionId → touchSeq of the last workerStatus/workerId update (WS events).
  _sessionWsTouchedSeq: Record<string, number>;

  // Actions
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  loadOlderMessages: () => Promise<void>;
  createNewSession: (
    name: string,
    workdir?: string | null,
    adapter?: string,
    sessionTemplate?: string,
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
  toggleMultiSelect: (id?: string) => void;
  toggleSelection: (id: string) => void;
  exitMultiSelect: () => void;
  setRendering: (v: boolean) => void;
  getUnread: () => Set<string>;
  markUnread: (content: string) => void;
  clearUnread: () => void;
}

// Stable empty Set returned by getUnread() when there are no unread items.
// MUST be a shared reference — returning `new Set()` each call would make
// `useSessionStore((s) => s.getUnread())` an unstable selector, which
// (via useSyncExternalStore) triggers infinite re-renders → React #185.
const EMPTY_UNREAD_SET: Set<string> = new Set();

/** True when the server-reported history is a prefix of the locally-rendered
 *  history (element-wise by role+content). A stale snapshot during streaming
 *  is exactly this — the backend persists each streamed block slightly after
 *  broadcasting it, so its history lags what we already show locally. Blindly
 *  overwriting `currentMessages` with such a prefix would wipe the in-flight
 *  assistant reply. Mirrors the legacy frontend's `_isServerHistoryPrefix`. */
function isServerHistoryPrefix(
  localHistory: Message[],
  serverHistory: Message[],
): boolean {
  if (serverHistory.length > localHistory.length) return false;
  for (let i = 0; i < serverHistory.length; i++) {
    const s = serverHistory[i];
    const c = localHistory[i];
    if (!s || !c || s.role !== c.role || s.content !== c.content) return false;
  }
  return true;
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
  _loadSeq: 0,
  _touchSeq: 0,
  _sessionWsTouchedSeq: {},

  loadSessions: async () => {
    // Reserve this refresh's sequence + snapshot the touch counter so a stale
    // in-flight response can neither overwrite a newer refresh nor revert
    // sessions that were locally freshened while the request was in flight.
    const loadSeq = get()._loadSeq + 1;
    const touchSeqAtStart = get()._touchSeq;
    set({ _loadSeq: loadSeq });
    try {
      const sessions = await fetchSessions();
      if (get()._loadSeq !== loadSeq) return; // superseded by a newer refresh
      const { currentSessionId, currentMessages } = get();
      const wsTouchedSeq = get()._sessionWsTouchedSeq;

      set((s) => {
        // Merge the snapshot with locally-fresher workerStatus/workerId: WS
        // worker events that landed while this fetch was in flight are newer
        // than the snapshot — don't let a stale snapshot revert the card's
        // status dot (mirrors legacy `_wsWorkerTs` re-apply).
        const merged = sessions.map((sess) => {
          const sid = sess.id;
          const cur = s.sessions.find((x) => x.id === sid);
          if (cur && (wsTouchedSeq[sid] ?? 0) > touchSeqAtStart) {
            return {
              ...sess,
              workerStatus: cur.workerStatus ?? sess.workerStatus,
              workerId: cur.workerId ?? sess.workerId,
            };
          }
          return sess;
        });
        return { sessions: merged };
      });

      // Restore current session messages after refresh — but NEVER clobber the
      // live-rendered messages with a stale snapshot. While streaming, the
      // server history is a prefix of what we already show locally (the
      // backend persists each block slightly after broadcasting it), so a
      // blind overwrite would wipe the in-flight assistant reply (bug: needs
      // a manual refresh to reappear).
      if (currentSessionId) {
        const found = sessions.find((s) => s.id === currentSessionId);
        if (found) {
          const serverHistory = found.history || [];
          const keepLocal = isServerHistoryPrefix(
            currentMessages,
            serverHistory,
          );
          set({
            currentMessages: keepLocal ? currentMessages : serverHistory,
            hasMoreMessages: !!found.historyTruncated,
            historyLoadEnd: Math.max(
              0,
              (found.historyTotal ?? serverHistory.length) -
                serverHistory.length,
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
    // Drafts are persisted by InputRow's onChange → setInputDraft; nothing to
    // save here. (Previously read a non-existent `#chatInput` DOM node.)

    const session = get().sessions.find((s) => s.id === id);
    if (!session) return;

    const loaded = (session.history || []).length;
    const needsOlder = !!session.historyTruncated;

    // Single set — one render for session switch.
    // NOTE: do NOT set historyLoading=true here. Previously this blocked the
    // scroll-handler lazy-load race, but it also made the subsequent
    // loadOlderMessages() call a no-op (its own guard sees historyLoading=true
    // and returns), leaving the "Loading older messages..." indicator stuck
    // forever and breaking scroll-up lazy loading. The scroll handler's 150ms
    // debounce plus scrollToBottom() in ChatMessages is enough to prevent
    // spurious loads on session switch.
    set({
      currentSessionId: id,
      currentMessages: session.history || [],
      hasMoreMessages: needsOlder,
      historyLoading: false,
      historyLoadEnd: Math.max(
        0,
        (session.historyTotal ?? loaded) - loaded,
      ),
    });
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

  createNewSession: async (name, workdir, adapter, sessionTemplate) => {
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
      const session = await createSession(name, workdir, adapter, sessionTemplate);
      set((s) => {
        // Drop the placeholder first — a concurrent loadSessions() (e.g. from
        // a WS event) may have overwritten `sessions` while the create call was
        // in flight, so the placeholder may no longer be present. Blindly using
        // .map() would then fail to insert the real session and it would not
        // appear until a later reload/refresh.
        const withoutPlaceholder = s.sessions.filter(
          (se) => se.id !== placeholder.id,
        );
        // Only append the real session if a concurrent reload didn't already
        // bring it in (avoids a duplicate row).
        const sessions = withoutPlaceholder.some((se) => se.id === session.id)
          ? withoutPlaceholder
          : [...withoutPlaceholder, session];
        // A concurrent loadSessions() also resets currentSessionId to null
        // when it can't find the client-only placeholder in the server list —
        // treat that as "still the newly created session" so the selection
        // lands on the real session.
        const wasCurrent =
          s.currentSessionId === placeholder.id || s.currentSessionId === null;
        return {
          sessions,
          currentSessionId: wasCurrent ? session.id : s.currentSessionId,
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
    const touchSeq = get()._touchSeq + 1;
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? { ...session, ...data } : session,
      ),
      _touchSeq: touchSeq,
      _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [id]: touchSeq },
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
    if (!currentSessionId) return EMPTY_UNREAD_SET;
    return sessionUnread[currentSessionId] ?? EMPTY_UNREAD_SET;
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
