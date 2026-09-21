import { create } from 'zustand';
import type { AgentQueueItem, MessagePart, QueueDispatchState, QueuedEdit } from '@/types';
import {
  deleteSessionQueueItem,
  enqueueSessionMessage,
  fetchSessionQueue,
  reorderSessionQueue,
  updateSessionQueueItem,
} from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { isRuntimeWorkerRunning } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';

/** The business queue is the server snapshot; localStorage is not a queue. */
export interface EnqueueOptions {
  /**
   * Whether this send transaction may append the queued row to chat history.
   * InputRow captures this at send start so a running→idle transition cannot
   * turn a send that began in the live path into a duplicate optimistic row.
   */
  appendOptimisticHistory?: boolean;
}

interface QueueStore {
  queues: Record<string, AgentQueueItem[]>;
  edits: Record<string, QueuedEdit | null>;
  batchSend: Record<string, boolean>;
  panelOpen: boolean;
  sendingId: string | null;
  agentQueues: Record<string, AgentQueueItem[]>;
  agentQueueLoadSeq: Record<string, number>;
  queueRevisions: Record<string, number>;
  /** Durable projection guards for late ACKs and delivery/remove events. */
  queueTombstones: Record<string, Set<string>>;
  queueDeliveredIds: Record<string, Set<string>>;
  loadForSession: (sessionId: string | null) => void;
  loadAgentQueue: (sessionId: string) => Promise<void>;
  applyQueueEvent: (event: {
    type?: string;
    sessionId?: string;
    queueItemId?: string;
    queueItemIds?: string[];
    queueRevision?: number;
    item?: Record<string, unknown>;
    messages?: import('@/types').Message[];
  }) => void;
  enqueue: (
    text: string,
    parts?: MessagePart[],
    sessionId?: string,
    clientId?: string,
    options?: EnqueueOptions,
  ) => Promise<boolean>;
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
  removeAgentItem: (id: string, sessionId?: string) => Promise<void>;
  /** Move any queued source; the legacy name remains an API alias. */
  moveQueueItem: (id: string, delta: number) => Promise<void>;
  moveAgentItem: (id: string, delta: number) => Promise<void>;
}

function clientMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

let editTokenSeq = 0;

function canonicalQueueId(id: string): string {
  return id.startsWith('queue:') ? id.slice('queue:'.length) : id;
}

function queueIdMatches(a: string, b: string): boolean {
  return a === b || canonicalQueueId(a) === canonicalQueueId(b);
}

function setSnapshot(
  set: (fn: (state: QueueStore) => Partial<QueueStore>) => void,
  sessionId: string,
  items: AgentQueueItem[],
  revision?: number,
  allowEqualRevisionRepair = false,
): boolean {
  let accepted = false;
  set((state) => {
    const currentRevision = state.queueRevisions[sessionId];
    if (revision !== undefined && currentRevision !== undefined && revision < currentRevision) {
      return state;
    }
    if (revision !== undefined && currentRevision !== undefined && revision === currentRevision) {
      // Equal revisions are idempotent receipts, not permission to replace a
      // newer projection with a differently-shaped stale HTTP payload — unless
      // the caller proved this response is a fresh authoritative observation
      // of exactly the revision the projection already holds (the full-GET
      // repair path), in which case it fixes an incomplete/stale projection.
      if (!allowEqualRevisionRepair) return state;
    }
    const tombstones = state.queueTombstones[sessionId] ?? new Set<string>();
    const delivered = state.queueDeliveredIds[sessionId] ?? new Set<string>();
    const filtered = items.filter((item) => {
      const id = canonicalQueueId(item.id);
      return !tombstones.has(id) && !delivered.has(id);
    });
    accepted = true;
    return {
      queues: { ...state.queues, [sessionId]: filtered },
      agentQueues: { ...state.agentQueues, [sessionId]: filtered },
      ...(revision === undefined
        ? {}
        : { queueRevisions: { ...state.queueRevisions, [sessionId]: revision } }),
    };
  });
  return accepted;
}

function normalizeQueueEventItem(raw: Record<string, unknown>): AgentQueueItem | null {
  const id = raw.queueItemId ?? raw.id;
  if (typeof id !== 'string' || !id) return null;
  const rawMeta = raw.meta;
  const meta =
    rawMeta && typeof rawMeta === 'object' ? { ...(rawMeta as Record<string, unknown>) } : {};
  const rawKind = raw.kind ?? raw.type;
  const kind =
    rawKind === 'qq'
      ? 'qq'
      : rawKind === 'report' || rawKind === 'notice' || rawKind === 'zombie'
        ? 'report'
        : 'task';
  const text =
    typeof raw.text === 'string' ? raw.text : typeof raw.result === 'string' ? raw.result : '';
  const source =
    typeof raw.source === 'string'
      ? raw.source
      : kind === 'qq'
        ? 'qq'
        : kind === 'task'
          ? 'user'
          : 'report';
  const state = raw.dispatchState ?? raw.deliveryState ?? meta.dispatchState ?? 'queued';
  const revision = raw.revision ?? meta.revision ?? 1;
  const parts = Array.isArray(raw.parts) ? (raw.parts as AgentQueueItem['parts']) : undefined;
  return {
    id,
    queueItemId: id,
    kind,
    text,
    ...(parts ? { parts } : {}),
    createdAt:
      typeof raw.createdAt === 'string' || typeof raw.createdAt === 'number' ? raw.createdAt : 0,
    source,
    meta: {
      ...meta,
      dispatchState: state as QueueDispatchState,
      revision: typeof revision === 'number' ? revision : 1,
    },
  } as AgentQueueItem;
}

export const useQueueStore = create<QueueStore>((set, get) => ({
  queues: {},
  edits: {},
  batchSend: {},
  panelOpen: false,
  sendingId: null,
  agentQueues: {},
  agentQueueLoadSeq: {},
  queueRevisions: {},
  queueTombstones: {},
  queueDeliveredIds: {},

  loadForSession: (sessionId) => {
    if (sessionId) void get().loadAgentQueue(sessionId);
  },

  loadAgentQueue: async (sessionId) => {
    const requestSeq = (get().agentQueueLoadSeq[sessionId] ?? 0) + 1;
    set((state) => ({
      agentQueueLoadSeq: { ...state.agentQueueLoadSeq, [sessionId]: requestSeq },
    }));
    const request = (async () => {
      try {
        const revisionAtRequest = get().queueRevisions[sessionId];
        const items = await fetchSessionQueue(sessionId);
        if (get().agentQueueLoadSeq[sessionId] !== requestSeq) return;
        const currentRevision = get().queueRevisions[sessionId];
        if (
          items.queueRevision !== undefined &&
          currentRevision !== undefined &&
          items.queueRevision < currentRevision
        )
          return;
        // A full GET that observed exactly the revision the local projection
        // already held at request time is a fresh authoritative observation:
        // the equal revision proves no newer server state exists, so it may
        // repair a projection left stale by a lost delivery event or a failed
        // mutation.  A response whose local revision moved on while in flight
        // keeps the strict stale-response rules instead.
        const freshObservation = items.queueRevision !== undefined
          && currentRevision !== undefined
          && revisionAtRequest !== undefined
          && items.queueRevision === currentRevision
          && currentRevision === revisionAtRequest;
        setSnapshot(set, sessionId, items, items.queueRevision, freshObservation);
      } catch {
        // Preserve the last authoritative snapshot; reconnect/session switch retries.
      }
    })();
    await request;
  },

  applyQueueEvent: (event) => {
    const sid = event.sessionId;
    if (!sid) return;
    const currentRevision = get().queueRevisions[sid];
    if (
      event.queueRevision !== undefined &&
      currentRevision !== undefined &&
      event.queueRevision <= currentRevision
    )
      return;
    const current = get().queues[sid] ?? [];
    const id = event.queueItemId ?? event.item?.queueItemId ?? event.item?.id;
    let next = current;
    if (event.type === 'queue.item_added' && event.item) {
      const item = normalizeQueueEventItem(event.item);
      const blocked = item && (
        get().queueTombstones[sid]?.has(canonicalQueueId(item.id))
        || get().queueDeliveredIds[sid]?.has(canonicalQueueId(item.id))
      );
      if (item && !blocked && item.meta?.dispatchState === 'queued') {
        const index = current.findIndex((candidate) => queueIdMatches(candidate.id, item.id));
        next =
          index < 0
            ? [...current, item]
            : current.map((candidate, candidateIndex) =>
                candidateIndex === index ? item : candidate,
              );
      }
    } else if (event.type === 'queue.item_updated' && event.item && typeof id === 'string') {
      const item = normalizeQueueEventItem(event.item);
      const index = current.findIndex((candidate) => queueIdMatches(candidate.id, id));
      if (item?.meta?.dispatchState === 'queued') {
        next =
          index < 0
            ? [...current, item]
            : current.map((candidate, candidateIndex) =>
                candidateIndex === index ? item : candidate,
              );
      } else if (index >= 0) {
        next = current.filter((_, candidateIndex) => candidateIndex !== index);
      }
    } else if (event.type === 'queue.item_removed' && typeof id === 'string') {
      const canonical = canonicalQueueId(id);
      set((state) => {
        const nextTombstones = new Set(state.queueTombstones[sid] ?? []);
        nextTombstones.add(canonical);
        return { queueTombstones: { ...state.queueTombstones, [sid]: nextTombstones } };
      });
      next = current.filter((candidate) => !queueIdMatches(candidate.id, id));
    } else if (event.type === 'queue.item_delivered' && Array.isArray(event.queueItemIds)) {
      const delivered = new Set(
        event.queueItemIds.filter(
          (queueItemId): queueItemId is string =>
            typeof queueItemId === 'string' && queueItemId.length > 0,
        ),
      );
      if (delivered.size) {
        const canonicalIds = new Set([...delivered].map(canonicalQueueId));
        set((state) => ({
          queueDeliveredIds: {
            ...state.queueDeliveredIds,
            [sid]: new Set([...(state.queueDeliveredIds[sid] ?? []), ...canonicalIds]),
          },
          edits: state.edits[sid] && [...delivered].some((id) =>
            queueIdMatches(state.edits[sid]!.id, id),
          )
            ? { ...state.edits, [sid]: null }
            : state.edits,
        }));
        next = current.filter((candidate) => ![...delivered].some((id) => queueIdMatches(candidate.id, id)));
      }
    }
    if (next !== current || event.queueRevision !== undefined) {
      const accepted = setSnapshot(set, sid, next, event.queueRevision);
      if (!accepted) return;
      if (event.type === 'queue.item_updated' && event.item) {
        const item = normalizeQueueEventItem(event.item);
        if (item?.meta?.dispatchState === 'queued') {
          useSessionStore.getState().updateQueuedMessage(sid, item);
        }
      } else if (event.type === 'queue.item_removed' && typeof id === 'string') {
        useSessionStore.getState().removeQueuedMessage(sid, id);
      }
      if (event.type === 'queue.item_delivered' && Array.isArray(event.messages)) {
        useSessionStore.getState().appendDeliveredMessages(sid, event.messages);
      }
    }
  },

  enqueue: async (text, parts, sessionId, clientId, options) => {
    // A send transaction captures its Session before any await.  Falling back
    // to the current Session is retained for queue-panel/legacy callers, but
    // InputRow passes the explicit id so a Session switch cannot reroute or
    // clear a later request.
    const sid = sessionId || useSessionStore.getState().currentSessionId;
    if (!sid || !text.trim()) return false;
    if (get().edits[sid]) {
      useUIStore.getState().showToast('请先保存或取消队列消息编辑', 'error');
      return false;
    }
    try {
      const messageId = clientId || clientMessageId();
      const result = parts
        ? await enqueueSessionMessage(sid, text, messageId, parts)
        : await enqueueSessionMessage(sid, text, messageId);
      const current = get().queues[sid] ?? [];
      const next = current.some((item) => queueIdMatches(item.id, result.item.id))
        ? current
        : [...current, result.item];
      const accepted = setSnapshot(set, sid, next, result.queueRevision);
      // Re-check the same cached, Session-keyed runtime registry at the
      // append boundary.  This covers a non-running→running transition
      // during HTTP enqueue without another request or an async wait.
      // Unknown/null/mismatched entries intentionally retain the historical
      // optimistic behavior; only a confirmed current-session `running`
      // state suppresses the row.
      if (accepted
          && !get().queueTombstones[sid]?.has(canonicalQueueId(result.item.id))
          && !get().queueDeliveredIds[sid]?.has(canonicalQueueId(result.item.id))
          && options?.appendOptimisticHistory !== false
          && !isRuntimeWorkerRunning(sid)) {
        useSessionStore.getState().appendQueuedMessage(sid, result.item);
      }
      useUIStore.getState().showToast('消息已进入服务端队列');
      return true;
    } catch (error) {
      useUIStore
        .getState()
        .showToast(
          `消息尚未入队：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      return false;
    }
  },

  remove: (id) => {
    void get().removeAgentItem(id);
  },

  startEdit: (id) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    const items = get().queues[sid] ?? [];
    const item = items.find((candidate) => queueIdMatches(candidate.id, id));
    if (
      !item ||
      item.kind !== 'task' ||
      item.source !== 'user' ||
      item.meta?.dispatchState !== 'queued'
    )
      return;
    set((state) => ({
      edits: {
        ...state.edits,
        [sid]: {
          id: item.id,
          text: item.text,
          originalText: item.text,
          index: items.findIndex((candidate) => queueIdMatches(candidate.id, id)),
          createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
          editToken: ++editTokenSeq,
        },
      },
    }));
  },

  updateEditDraft: (text) => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    if (!sid || !edit || edit.saving) return;
    set((state) => ({ edits: { ...state.edits, [sid]: { ...edit, text } } }));
  },

  saveEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    const item = sid
      ? (get().queues[sid] ?? []).find((candidate) => edit && queueIdMatches(candidate.id, edit.id))
      : null;
    if (!sid || !edit || edit.saving || !item) return;
    const editToken = edit.editToken ?? ++editTokenSeq;
    const text = edit.text.trim() ? edit.text : edit.originalText;
    set((state) => {
      const current = state.edits[sid];
      if (!current || current.id !== edit.id) return state;
      return { edits: { ...state.edits, [sid]: { ...current, editToken, saving: true } } };
    });
    void (async () => {
      try {
        const result = await updateSessionQueueItem(
          sid,
          edit.id,
          text,
          item.meta?.revision,
        );
        // Apply the server's returned item immediately. This avoids briefly
        // restoring the old text if the follow-up GET races an older snapshot.
        if (result.item) {
          const current = get().queues[sid] ?? [];
          const next = current.map((candidate) =>
            queueIdMatches(candidate.id, edit.id) ? result.item! : candidate,
          );
          const accepted = setSnapshot(set, sid, next, result.queueRevision);
          if (accepted && result.item?.meta?.dispatchState === 'queued') {
            useSessionStore.getState().updateQueuedMessage(sid, result.item);
          }
        }
        await get().loadAgentQueue(sid);
        set((state) => {
          const current = state.edits[sid];
          if (!current || current.editToken !== editToken) return state;
          return { edits: { ...state.edits, [sid]: null } };
        });
      } catch (error) {
        set((state) => {
          const current = state.edits[sid];
          if (!current || current.editToken !== editToken) return state;
          return { edits: { ...state.edits, [sid]: { ...current, saving: false } } };
        });
        useUIStore.getState().showToast(
          `编辑失败：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      }
    })();
  },

  cancelEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    if (sid) set((state) => ({ edits: { ...state.edits, [sid]: null } }));
  },
  move: (id, delta) => {
    void get().moveQueueItem(id, delta);
  },

  clear: () => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    void (async () => {
      for (const item of get().queues[sid] ?? []) {
        if (item.meta?.dispatchState === 'queued') await get().removeAgentItem(item.id, sid);
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
    } catch {
      /* storage may be unavailable */
    }
    set((state) => {
      const queues = { ...state.queues };
      delete queues[sessionId];
      const edits = { ...state.edits };
      delete edits[sessionId];
      const agentQueues = { ...state.agentQueues };
      delete agentQueues[sessionId];
      const queueTombstones = { ...state.queueTombstones };
      const queueDeliveredIds = { ...state.queueDeliveredIds };
      const queueRevisions = { ...state.queueRevisions };
      delete queueTombstones[sessionId];
      delete queueDeliveredIds[sessionId];
      delete queueRevisions[sessionId];
      return { queues, edits, agentQueues, queueTombstones, queueDeliveredIds, queueRevisions };
    });
  },

  removeAgentItem: async (id, explicitSessionId) => {
    const sid = explicitSessionId || useSessionStore.getState().currentSessionId;
    if (!sid) return;
    try {
      const result = await deleteSessionQueueItem(sid, id);
      const revision = (result as { queueRevision?: number }).queueRevision;
      const current = get().queues[sid] ?? [];
      const next = current.filter((item) => !queueIdMatches(item.id, id));
      set((state) => ({
        queueTombstones: {
          ...state.queueTombstones,
          [sid]: new Set([...(state.queueTombstones[sid] ?? []), canonicalQueueId(id)]),
        },
      }));
      setSnapshot(set, sid, next, revision);
      useSessionStore.getState().removeQueuedMessage(sid, id);
      await get().loadAgentQueue(sid);
    } catch (error) {
      useUIStore
        .getState()
        .showToast(`删除失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      await get().loadAgentQueue(sid);
    }
  },

  moveQueueItem: async (id, delta) => {
    const sid = useSessionStore.getState().currentSessionId;
    if (!sid) return;
    // The panel and the order API both operate on the real pending view.
    // Ignore stale delivery-ledger rows that may still be present in an old
    // in-memory snapshot; they are not movable queue entries.
    const current = (get().queues[sid] ?? []).filter(
      (item) => item.meta?.dispatchState === 'queued',
    );
    const index = current.findIndex((item) => item.id === id);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= current.length) return;
    const next = current.slice();
    [next[index], next[target]] = [next[target]!, next[index]!];
    setSnapshot(set, sid, next);
    try {
      const items = await reorderSessionQueue(
        sid,
        next.map((item) => item.id),
        get().queueRevisions[sid],
      );
      setSnapshot(set, sid, items, items.queueRevision);
    } catch {
      await get().loadAgentQueue(sid);
    }
  },

  // Compatibility for callers that still use the old agent-specific name.
  moveAgentItem: async (id, delta) => get().moveQueueItem(id, delta),
}));

useSessionStore.subscribe((state, previous) => {
  if (state.sessions === previous.sessions) return;
  const live = new Set(state.sessions.map((session) => session.id));
  for (const session of previous.sessions)
    if (!live.has(session.id)) useQueueStore.getState().removeSession(session.id);
});
