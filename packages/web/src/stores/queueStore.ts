import { create } from 'zustand';
import type { AgentQueueItem, MessagePart, QueueDispatchState, QueuedEdit } from '@/types';
import {
  acquireSessionQueueItemEdit,
  deleteSessionQueueItem,
  enqueueSessionMessage,
  fetchSessionQueue,
  releaseSessionQueueItemEdit,
  reorderSessionQueue,
  setSessionAgentReportsPaused,
  setSessionQueueItemLocked,
  setSessionQueueItemsLocked,
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
  locked?: boolean;
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
  agentReportsPaused: Record<string, boolean>;
  agentReportsPauseLoaded: Record<string, boolean>;
  agentReportsPauseUpdating: Record<string, boolean>;
  queueLockUpdating: Record<string, boolean>;
  lockedComposerModes: Record<string, boolean>;
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
    items?: AgentQueueItem[];
    messages?: import('@/types').Message[];
    agentReportsPaused?: boolean;
  }) => void;
  setAgentReportsPaused: (sessionId: string, paused: boolean) => Promise<void>;
  setQueueItemLocked: (sessionId: string, itemId: string, locked: boolean) => Promise<void>;
  setQueueItemsLocked: (sessionId: string, locked: boolean) => Promise<void>;
  setLockedComposerMode: (sessionId: string, locked: boolean) => void;
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
const EDIT_LEASE_RENEW_INTERVAL_MS = 30_000;
const editLeaseTimers = new Map<
  string,
  { editToken: number; timer: ReturnType<typeof setInterval> }
>();

function stopEditLeaseRenewal(sessionId: string, expectedEditToken?: number): void {
  const current = editLeaseTimers.get(sessionId);
  if (!current || (expectedEditToken !== undefined && current.editToken !== expectedEditToken)) return;
  clearInterval(current.timer);
  editLeaseTimers.delete(sessionId);
}

function scheduleEditLeaseRenewal(
  sessionId: string,
  editToken: number,
  readEdit: () => QueuedEdit | null,
  readRevision: (edit: QueuedEdit) => number | undefined,
  onRenewed: (expiresAt: number) => void,
  onFailure: (error: unknown) => void,
): void {
  stopEditLeaseRenewal(sessionId);
  const timer = setInterval(() => {
    const edit = readEdit();
    if (!edit || edit.editToken !== editToken || !edit.serverToken) {
      stopEditLeaseRenewal(sessionId, editToken);
      return;
    }
    void acquireSessionQueueItemEdit(
      sessionId,
      edit.id,
      edit.serverToken,
      readRevision(edit),
    ).then(({ expiresAt }) => onRenewed(expiresAt)).catch(onFailure);
  }, EDIT_LEASE_RENEW_INTERVAL_MS);
  editLeaseTimers.set(sessionId, { editToken, timer });
}

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
  reportsPaused?: boolean,
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
    const edit = state.edits[sessionId];
    const editMissing = edit && !filtered.some((item) => queueIdMatches(item.id, edit.id));
    accepted = true;
    return {
      queues: { ...state.queues, [sessionId]: filtered },
      agentQueues: { ...state.agentQueues, [sessionId]: filtered },
      ...(revision === undefined
        ? {}
        : { queueRevisions: { ...state.queueRevisions, [sessionId]: revision } }),
      ...(editMissing ? { edits: { ...state.edits, [sessionId]: null } } : {}),
      ...(reportsPaused === undefined ? {} : {
        agentReportsPaused: { ...state.agentReportsPaused, [sessionId]: reportsPaused },
        agentReportsPauseLoaded: { ...state.agentReportsPauseLoaded, [sessionId]: true },
      }),
    };
  });
  return accepted;
}

function canApplyAgentReportsPause(
  snapshotAccepted: boolean,
  snapshotRevision: number | undefined,
  currentRevision: number | undefined,
): boolean {
  if (snapshotRevision !== undefined
      && currentRevision !== undefined
      && snapshotRevision < currentRevision) {
    return false;
  }
  // An accepted snapshot may update its pause bit. A rejected equal-revision
  // snapshot is also authoritative for that bit because it names the exact
  // queue version currently held by the store.
  return snapshotAccepted || (
    snapshotRevision !== undefined
    && currentRevision !== undefined
    && snapshotRevision === currentRevision
  );
}

function setAgentReportsPauseState(
  set: (fn: (state: QueueStore) => Partial<QueueStore>) => void,
  sessionId: string,
  paused: boolean,
): void {
  set((state) => ({
    agentReportsPaused: {
      ...state.agentReportsPaused,
      [sessionId]: paused,
    },
    agentReportsPauseLoaded: {
      ...state.agentReportsPauseLoaded,
      [sessionId]: true,
    },
  }));
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
      : rawKind === 'wechat'
        ? 'wechat'
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
        : kind === 'wechat'
          ? 'wechat'
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
      locked: Boolean(raw.queueLockManual || raw.queueLockAutoReport || meta.locked),
      lockManual: Boolean(raw.queueLockManual || meta.lockManual),
      lockAutoReport: Boolean(raw.queueLockAutoReport || meta.lockAutoReport),
    },
  } as AgentQueueItem;
}

export const useQueueStore = create<QueueStore>((set, get) => {
  const startLeaseRenewal = (sessionId: string, editToken: number) => {
    scheduleEditLeaseRenewal(
      sessionId,
      editToken,
      () => get().edits[sessionId] ?? null,
      (edit) => (get().queues[sessionId] ?? []).find((candidate) =>
        queueIdMatches(candidate.id, edit.id))?.meta?.revision,
      (expiresAt) => {
        const active = get().edits[sessionId];
        if (!active || active.editToken !== editToken) return;
        set((state) => ({
          edits: { ...state.edits, [sessionId]: { ...active, leaseExpiresAt: expiresAt } },
        }));
      },
      (error) => {
        const active = get().edits[sessionId];
        if (!active || active.editToken !== editToken) return;
        stopEditLeaseRenewal(sessionId, editToken);
        set((state) => ({ edits: { ...state.edits, [sessionId]: null } }));
        useUIStore.getState().showToast(
          `编辑锁已失效：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      },
    );
  };

  return {
  queues: {},
  edits: {},
  batchSend: {},
  panelOpen: false,
  sendingId: null,
  agentQueues: {},
  agentQueueLoadSeq: {},
  queueRevisions: {},
  agentReportsPaused: {},
  agentReportsPauseLoaded: {},
  agentReportsPauseUpdating: {},
  queueLockUpdating: {},
  lockedComposerModes: {},
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
        const accepted = setSnapshot(set, sessionId, items, items.queueRevision,
          freshObservation, items.agentReportsPaused);
        if (!accepted && items.agentReportsPaused !== undefined
            && canApplyAgentReportsPause(
              accepted,
              items.queueRevision,
              get().queueRevisions[sessionId],
            )) {
          setAgentReportsPauseState(set, sessionId, items.agentReportsPaused);
        }
      } catch {
        // Preserve the last authoritative snapshot; reconnect/session switch retries.
      }
    })();
    await request;
  },

  setAgentReportsPaused: async (sessionId, paused) => {
    set((state) => ({
      agentReportsPauseUpdating: {
        ...state.agentReportsPauseUpdating,
        [sessionId]: true,
      },
    }));
    try {
      const items = await setSessionAgentReportsPaused(sessionId, paused);
      const accepted = setSnapshot(set, sessionId, items, items.queueRevision,
        true, items.agentReportsPaused);
      if (!accepted && items.agentReportsPaused !== undefined
          && canApplyAgentReportsPause(
            accepted,
            items.queueRevision,
            get().queueRevisions[sessionId],
          )) {
        setAgentReportsPauseState(set, sessionId, items.agentReportsPaused);
      }
    } catch (error) {
      useUIStore.getState().showToast(
        `报告暂停状态更新失败：${error instanceof Error ? error.message : String(error)}`,
        'error',
      );
      // The server may have committed before a connection failure. Reload the
      // authoritative queue and pause state so this Session returns to truth.
      await get().loadAgentQueue(sessionId);
    } finally {
      set((state) => ({
        agentReportsPauseUpdating: {
          ...state.agentReportsPauseUpdating,
          [sessionId]: false,
        },
      }));
    }
  },

  setQueueItemLocked: async (sessionId, itemId, locked) => {
    set((state) => ({ queueLockUpdating: { ...state.queueLockUpdating, [sessionId]: true } }));
    try {
      const items = await setSessionQueueItemLocked(sessionId, itemId, locked);
      setSnapshot(set, sessionId, items, items.queueRevision, true, items.agentReportsPaused);
    } catch (error) {
      useUIStore.getState().showToast(
        `队列锁定失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      await get().loadAgentQueue(sessionId);
    } finally {
      set((state) => ({ queueLockUpdating: { ...state.queueLockUpdating, [sessionId]: false } }));
    }
  },

  setQueueItemsLocked: async (sessionId, locked) => {
    set((state) => ({ queueLockUpdating: { ...state.queueLockUpdating, [sessionId]: true } }));
    try {
      const items = await setSessionQueueItemsLocked(sessionId, locked);
      setSnapshot(set, sessionId, items, items.queueRevision, true, items.agentReportsPaused);
    } catch (error) {
      useUIStore.getState().showToast(
        `队列批量锁定失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      await get().loadAgentQueue(sessionId);
    } finally {
      set((state) => ({ queueLockUpdating: { ...state.queueLockUpdating, [sessionId]: false } }));
    }
  },

  setLockedComposerMode: (sessionId, locked) => {
    set((state) => ({ lockedComposerModes: { ...state.lockedComposerModes, [sessionId]: locked } }));
  },

  applyQueueEvent: (event) => {
    const sid = event.sessionId;
    if (!sid) return;
    const currentRevision = get().queueRevisions[sid];
    if (
      event.queueRevision !== undefined &&
      currentRevision !== undefined &&
      event.queueRevision < currentRevision
    ) return;
    const missedRevision = event.queueRevision !== undefined
      && (currentRevision === undefined || event.queueRevision > currentRevision + 1);
    if (event.type === 'queue.snapshot' && Array.isArray(event.items)) {
      const accepted = setSnapshot(set, sid, event.items, event.queueRevision,
        true, event.agentReportsPaused);
      if (!accepted && event.agentReportsPaused !== undefined
          && canApplyAgentReportsPause(false, event.queueRevision, get().queueRevisions[sid])) {
        setAgentReportsPauseState(set, sid, event.agentReportsPaused);
      }
      return;
    }
    if (event.queueRevision !== undefined
        && currentRevision !== undefined
        && event.queueRevision === currentRevision) {
      if (event.agentReportsPaused !== undefined
          && canApplyAgentReportsPause(false, event.queueRevision, currentRevision)) {
        setAgentReportsPauseState(set, sid, event.agentReportsPaused);
      }
      return;
    }
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
        const activeEdit = state.edits[sid];
        const edits = activeEdit && queueIdMatches(activeEdit.id, id)
          ? { ...state.edits, [sid]: null }
          : state.edits;
        return {
          queueTombstones: { ...state.queueTombstones, [sid]: nextTombstones },
          edits,
        };
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
      if (event.agentReportsPaused !== undefined
          && canApplyAgentReportsPause(
            accepted,
            event.queueRevision,
            get().queueRevisions[sid],
          )) {
        setAgentReportsPauseState(set, sid, event.agentReportsPaused);
      }
      if (!accepted) return;
      // A later incremental event cannot reconstruct skipped lock/order
      // mutations. Fetch the complete durable projection at this revision.
      if (missedRevision) void get().loadAgentQueue(sid);
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
        ? await enqueueSessionMessage(sid, text, messageId, parts, options?.locked)
        : await enqueueSessionMessage(sid, text, messageId, undefined, options?.locked);
      if (options?.locked && result.item.meta?.locked !== true) {
        throw new Error('服务端未确认消息已锁定');
      }
      const current = get().queues[sid] ?? [];
      const next = result.items ?? (current.some((item) => queueIdMatches(item.id, result.item.id))
        ? current
        : [...current, result.item]);
      const accepted = setSnapshot(set, sid, next, result.queueRevision,
        Array.isArray(result.items), result.agentReportsPaused);
      if (!accepted && result.agentReportsPaused !== undefined
          && canApplyAgentReportsPause(
            accepted,
            result.queueRevision,
            get().queueRevisions[sid],
          )) {
        setAgentReportsPauseState(set, sid, result.agentReportsPaused);
      }
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
    // Preserve the first edit transaction, including an in-flight PATCH.
    // Repeated clicks must not replace its draft or token.
    if (get().edits[sid]) return;
    const items = get().queues[sid] ?? [];
    const item = items.find((candidate) => queueIdMatches(candidate.id, id));
    if (
      !item ||
      item.kind !== 'task' ||
      item.source !== 'user' ||
      item.meta?.dispatchState !== 'queued'
    )
      return;
    const editToken = ++editTokenSeq;
    const serverToken = clientMessageId();
    const edit: QueuedEdit = {
      id: item.id,
      text: item.text,
      originalText: item.text,
      index: items.findIndex((candidate) => queueIdMatches(candidate.id, id)),
      createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
      editToken,
      serverToken,
      acquiring: true,
    };
    set((state) => ({
      edits: {
        ...state.edits,
        [sid]: edit,
      },
    }));
    void acquireSessionQueueItemEdit(sid, item.id, serverToken, item.meta?.revision)
      .then(({ expiresAt }) => {
        const current = get().edits[sid];
        if (!current || current.editToken !== editToken) {
          // A canceled or removed edit may acquire its server lease after the
          // UI transaction has already moved on. Release only this token.
          void releaseSessionQueueItemEdit(sid, item.id, serverToken).catch(() => {});
          return;
        }
        if (current.cancelRequested) {
          set((state) => ({
            edits: { ...state.edits, [sid]: { ...current, acquiring: false, releasing: true } },
          }));
          void releaseSessionQueueItemEdit(sid, item.id, serverToken)
            .then(() => {
              stopEditLeaseRenewal(sid, editToken);
              set((state) => {
                const latest = state.edits[sid];
                return latest?.editToken === editToken
                  ? { edits: { ...state.edits, [sid]: null } }
                  : state;
              });
            })
            .catch((error) => {
              const latest = get().edits[sid];
              if (latest?.editToken !== editToken) return;
              set((state) => ({
                edits: {
                  ...state.edits,
                  [sid]: { ...latest, acquiring: false, releasing: false, cancelRequested: false },
                },
              }));
              startLeaseRenewal(sid, editToken);
              useUIStore.getState().showToast(
                `取消编辑失败：${error instanceof Error ? error.message : String(error)}`,
                'error',
              );
            });
          return;
        }
        const acquired: QueuedEdit = {
          ...current,
          acquiring: false,
          leaseExpiresAt: expiresAt,
        };
        set((state) => ({ edits: { ...state.edits, [sid]: acquired } }));
        startLeaseRenewal(sid, editToken);
      })
      .catch((error) => {
        const current = get().edits[sid];
        if (!current || current.editToken !== editToken) return;
        stopEditLeaseRenewal(sid, editToken);
        set((state) => ({ edits: { ...state.edits, [sid]: null } }));
        useUIStore.getState().showToast(
          `无法编辑队列消息：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      });
  },

  updateEditDraft: (text) => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    if (!sid || !edit || edit.saving || edit.acquiring || edit.releasing) return;
    set((state) => ({ edits: { ...state.edits, [sid]: { ...edit, text } } }));
  },

  saveEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    const item = sid
      ? (get().queues[sid] ?? []).find((candidate) => edit && queueIdMatches(candidate.id, edit.id))
      : null;
    if (!sid || !edit || edit.saving || edit.acquiring || edit.releasing || !item
        || !edit.serverToken) return;
    const editToken = edit.editToken ?? ++editTokenSeq;
    const text = edit.text.trim() ? edit.text : edit.originalText;
    stopEditLeaseRenewal(sid, editToken);
    set((state) => {
      const current = state.edits[sid];
      if (!current || current.editToken !== editToken) return state;
      return { edits: { ...state.edits, [sid]: { ...current, editToken, saving: true } } };
    });
    void (async () => {
      try {
        const result = await updateSessionQueueItem(
          sid,
          edit.id,
          text,
          item.meta?.revision,
          edit.serverToken,
        );
        const active = get().edits[sid];
        if (!active || active.editToken !== editToken) return;
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
          stopEditLeaseRenewal(sid, editToken);
          return { edits: { ...state.edits, [sid]: null } };
        });
      } catch (error) {
        const current = get().edits[sid];
        if (!current || current.editToken !== editToken) return;
        set((state) => ({
          edits: { ...state.edits, [sid]: { ...current, saving: false } },
        }));
        startLeaseRenewal(sid, editToken);
        useUIStore.getState().showToast(
          `编辑失败：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      }
    })();
  },

  cancelEdit: () => {
    const sid = useSessionStore.getState().currentSessionId;
    const edit = sid ? get().edits[sid] : null;
    if (!sid || !edit || edit.saving || edit.releasing) return;
    if (edit.acquiring) {
      set((state) => ({
        edits: { ...state.edits, [sid]: { ...edit, cancelRequested: true } },
      }));
      return;
    }
    if (!edit.serverToken) return;
    stopEditLeaseRenewal(sid, edit.editToken);
    set((state) => ({
      edits: { ...state.edits, [sid]: { ...edit, releasing: true } },
    }));
    void releaseSessionQueueItemEdit(sid, edit.id, edit.serverToken)
      .then(() => {
        set((state) => {
          const current = state.edits[sid];
          return current?.editToken === edit.editToken
            ? { edits: { ...state.edits, [sid]: null } }
            : state;
        });
      })
      .catch((error) => {
        const current = get().edits[sid];
        if (!current || current.editToken !== edit.editToken) return;
        const activeEdit = current;
        set((state) => ({
          edits: { ...state.edits, [sid]: { ...activeEdit, releasing: false } },
        }));
        startLeaseRenewal(sid, activeEdit.editToken ?? editTokenSeq);
        useUIStore.getState().showToast(
          `取消编辑失败：${error instanceof Error ? error.message : String(error)}`,
          'error',
        );
      });
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
      const agentReportsPaused = { ...state.agentReportsPaused };
      const agentReportsPauseLoaded = { ...state.agentReportsPauseLoaded };
      const agentReportsPauseUpdating = { ...state.agentReportsPauseUpdating };
      const queueLockUpdating = { ...state.queueLockUpdating };
      const lockedComposerModes = { ...state.lockedComposerModes };
      delete queueTombstones[sessionId];
      delete queueDeliveredIds[sessionId];
      delete queueRevisions[sessionId];
      delete agentReportsPaused[sessionId];
      delete agentReportsPauseLoaded[sessionId];
      delete agentReportsPauseUpdating[sessionId];
      delete queueLockUpdating[sessionId];
      delete lockedComposerModes[sessionId];
      return {
        queues, edits, agentQueues, queueTombstones, queueDeliveredIds, queueRevisions,
        agentReportsPaused, agentReportsPauseLoaded, agentReportsPauseUpdating,
        queueLockUpdating, lockedComposerModes,
      };
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
  };
});

useQueueStore.subscribe((state, previous) => {
  for (const [sessionId, edit] of Object.entries(previous.edits)) {
    if (edit && !state.edits[sessionId]) stopEditLeaseRenewal(sessionId, edit.editToken);
  }
});

useSessionStore.subscribe((state, previous) => {
  if (state.sessions === previous.sessions) return;
  const live = new Set(state.sessions.map((session) => session.id));
  for (const session of previous.sessions)
    if (!live.has(session.id)) useQueueStore.getState().removeSession(session.id);
});
