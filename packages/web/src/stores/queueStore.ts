import { create } from 'zustand';
import type { AgentQueueItem, QueuedEdit } from '@/types';
import {
  deleteSessionQueueItem,
  enqueueSessionMessage,
  fetchSessionQueue,
  reorderSessionQueue,
  updateSessionQueueItem,
} from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

/** The business queue is the server snapshot; localStorage is not a queue. */
interface QueueStore {
  queues: Record<string, AgentQueueItem[]>;
  edits: Record<string, QueuedEdit | null>;
  batchSend: Record<string, boolean>;
  panelOpen: boolean;
  sendingId: string | null;
  agentQueues: Record<string, AgentQueueItem[]>;
  agentQueueLoadSeq: Record<string, number>;
  queueRevisions: Record<string, number>;
  loadForSession: (sessionId: string | null) => void;
  loadAgentQueue: (sessionId: string) => Promise<void>;
  enqueue: (text: string) => Promise<boolean>;
  remove: (id: string) => void;
  startEdit: (id: string) => void;
  updateEditDraft: (text: string) => void;
  saveEdit: () => void;
  cancelEdit: () => void;
  move: (id: string, delta: number) => void;
  clear: () => void;
  toggleBatchSend: () => void;
  togglePanel: () => void;
  setPanelOpen: (open: boolean) => void;
  flush: (_forceOffline?: boolean) => void;
  removeSession: (sessionId: string) => void;
  removeAgentItem: (id: string) => Promise<void>;
  /** Move any queued source; the legacy name remains an API alias. */
  moveQueueItem: (id: string, delta: number) => Promise<void>;
  moveAgentItem: (id: string, delta: number) => Promise<void>;
}

function clientMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function setSnapshot(
  set: (fn: (state: QueueStore) => Partial<QueueStore>) => void,
  sessionId: string,
  items: AgentQueueItem[],
  revision?: number,
): void {
  set((state) => ({
    queues: { ...state.queues, [sessionId]: items },
    agentQueues: { ...state.agentQueues, [sessionId]: items },
    ...(revision === undefined ? {} : { queueRevisions: { ...state.queueRevisions, [sessionId]: revision } }),
  }));
}

export const useQueueStore = create<QueueStore>((set, get) => ({
  queues: {}, edits: {}, batchSend: {}, panelOpen: false, sendingId: null,
  agentQueues: {}, agentQueueLoadSeq: {}, queueRevisions: {},

  loadForSession: (sessionId) => { if (sessionId) void get().loadAgentQueue(sessionId); },

  loadAgentQueue: async (sessionId) => {
    const requestSeq = (get().agentQueueLoadSeq[sessionId] ?? 0) + 1;
    set((state) => ({ agentQueueLoadSeq: { ...state.agentQueueLoadSeq, [sessionId]: requestSeq } }));
    try {
      const items = await fetchSessionQueue(sessionId);
      if (get().agentQueueLoadSeq[sessionId] !== requestSeq) return;
      const currentRevision = get().queueRevisions[sessionId];
      if (items.queueRevision !== undefined && currentRevision !== undefined
          && items.queueRevision < currentRevision) return;
      setSnapshot(set, sessionId, items, items.queueRevision);
    } catch {
      // Preserve the last authoritative snapshot; reconnect/session switch retries.
    }
  },

  enqueue: async (text) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid || !text.trim()) return false;
    try {
      const result = await enqueueSessionMessage(sid, text, clientMessageId());
      const current = get().queues[sid] ?? [];
      const next = current.some((item) => item.id === result.item.id) ? current : [...current, result.item];
      setSnapshot(set, sid, next, result.queueRevision);
      useUIStore.getState().showToast('消息已进入服务端队列');
      return true;
    } catch (error) {
      useUIStore.getState().showToast(`消息尚未入队：${error instanceof Error ? error.message : String(error)}`, 'error');
      return false;
    }
  },

  remove: (id) => { void get().removeAgentItem(id); },

  startEdit: (id) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    const items = get().queues[sid] ?? [];
    const item = items.find((candidate) => candidate.id === id);
    if (!item || item.kind !== 'task' || item.source !== 'user'
        || item.meta?.dispatchState !== 'queued') return;
    set((state) => ({ edits: { ...state.edits, [sid]: {
      id: item.id, text: item.text, originalText: item.text,
      index: items.findIndex((candidate) => candidate.id === id),
      createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
    } } }));
  },

  updateEditDraft: (text) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid || !get().edits[sid]) return;
    set((state) => ({ edits: { ...state.edits, [sid]: { ...state.edits[sid]!, text } } }));
  },

  saveEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    const item = sid ? (get().queues[sid] ?? []).find((candidate) => candidate.id === edit?.id) : null;
    if (!sid || !edit || !item) return;
    void (async () => {
      try {
        const result = await updateSessionQueueItem(
          sid,
          edit.id,
          edit.text.trim() ? edit.text : edit.originalText,
          item.meta?.revision,
        );
        // Apply the server's returned item immediately. This avoids briefly
        // restoring the old text if the follow-up GET races an older snapshot.
        if (result.item) {
          const current = get().queues[sid] ?? [];
          const next = current.map((candidate) =>
            candidate.id === edit.id ? result.item! : candidate,
          );
          setSnapshot(set, sid, next, result.queueRevision);
        }
        await get().loadAgentQueue(sid);
        set((state) => ({ edits: { ...state.edits, [sid]: null } }));
      } catch (error) {
        useUIStore.getState().showToast(`编辑失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      }
    })();
  },

  cancelEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    if (sid) set((state) => ({ edits: { ...state.edits, [sid]: null } }));
  },
  move: (id, delta) => { void get().moveQueueItem(id, delta); },

  clear: () => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    void (async () => {
      for (const item of get().queues[sid] ?? []) {
        if (item.meta?.dispatchState === 'queued') await get().removeAgentItem(item.id);
      }
    })();
  },

  toggleBatchSend: () => {
    const sid = useSessionStore.getState().currentSessionId;
    if (sid) set((state) => ({ batchSend: { ...state.batchSend, [sid]: !state.batchSend[sid] } }));
  },
  togglePanel: () => set((state) => ({ panelOpen: !state.panelOpen })),
  setPanelOpen: (open) => set({ panelOpen: open }),
  flush: () => {},

  removeSession: (sessionId) => {
    // Legacy keys are cleanup-only. They are never read or retransmitted.
    try {
      localStorage.removeItem(`pan.sendQueue.${sessionId}`);
      localStorage.removeItem(`pan.sendQueue.editing.${sessionId}`);
      localStorage.removeItem(`pan.sendQueue.batch.${sessionId}`);
    } catch { /* storage may be unavailable */ }
    set((state) => {
      const queues = { ...state.queues }; delete queues[sessionId];
      const edits = { ...state.edits }; delete edits[sessionId];
      const agentQueues = { ...state.agentQueues }; delete agentQueues[sessionId];
      return { queues, edits, agentQueues };
    });
  },

  removeAgentItem: async (id) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    try {
      await deleteSessionQueueItem(sid, id);
      await get().loadAgentQueue(sid);
    } catch (error) {
      useUIStore.getState().showToast(`删除失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      await get().loadAgentQueue(sid);
    }
  },

  moveQueueItem: async (id, delta) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    // The panel and the order API both operate on the real pending view.
    // Ignore stale delivery-ledger rows that may still be present in an old
    // in-memory snapshot; they are not movable queue entries.
    const current = (get().queues[sid] ?? [])
      .filter((item) => item.meta?.dispatchState === 'queued');
    const index = current.findIndex((item) => item.id === id);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= current.length) return;
    const next = current.slice(); [next[index], next[target]] = [next[target]!, next[index]!];
    setSnapshot(set, sid, next);
    try {
      const items = await reorderSessionQueue(sid, next.map((item) => item.id), get().queueRevisions[sid]);
      setSnapshot(set, sid, items, items.queueRevision);
    } catch { await get().loadAgentQueue(sid); }
  },

  // Compatibility for callers that still use the old agent-specific name.
  moveAgentItem: async (id, delta) => get().moveQueueItem(id, delta),
}));

useSessionStore.subscribe((state, previous) => {
  if (state.sessions === previous.sessions) return;
  const live = new Set(state.sessions.map((session) => session.id));
  for (const session of previous.sessions) if (!live.has(session.id)) useQueueStore.getState().removeSession(session.id);
});
