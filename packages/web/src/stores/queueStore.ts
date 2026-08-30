import { create } from 'zustand';
import type { QueuedMessage, QueuedEdit, Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { wsClient } from '@/services/ws';

/**
 * 客户端发送队列（对齐 vanilla ts/app.ts 的 sendQueue 实现）：
 * - 每 session 一个队列，localStorage 持久化（`pan.sendQueue.<sessionId>`），上限 50 条。
 * - worker busy（running/held）时入队，worker idle 时自动逐条 flush（或批量拼接成一条）。
 * - 编辑先出队：编辑中的消息从队列（内存 + localStorage）移除，避免被 flush 发出；
 *   Enter 保存插回原位置，Esc 恢复原值；编辑态持久化，刷新可恢复。
 * - `sendingId` 单飞锁：防同一条在异步窗口内被 idle 事件重复触发两次。
 */

const QUEUE_KEY_PREFIX = 'pan.sendQueue.';
const BATCH_KEY_PREFIX = 'pan.sendQueue.batch.';
const EDIT_KEY_PREFIX = 'pan.sendQueue.editing.';
const QUEUE_MAX = 50;
const BATCH_SEPARATOR = '\n\n';

function genQueueId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function loadQueue(sessionId: string): QueuedMessage[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY_PREFIX + sessionId);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (x): x is Record<string, unknown> =>
          !!x && typeof x === 'object' && typeof (x as Record<string, unknown>).text === 'string',
      )
      .map((x): QueuedMessage => {
        const anyX = x as Record<string, unknown>;
        return {
          id: typeof anyX.id === 'string' ? anyX.id : genQueueId(),
          text: String(anyX.text),
          createdAt: typeof anyX.createdAt === 'number' ? anyX.createdAt : Date.now(),
          status: 'pending',
        };
      })
      .slice(0, QUEUE_MAX);
  } catch {
    return [];
  }
}

function persistQueue(sessionId: string, queue: QueuedMessage[]): void {
  try {
    localStorage.setItem(QUEUE_KEY_PREFIX + sessionId, JSON.stringify(queue));
  } catch (e) {
    console.warn('[sendQueue] persist queue failed', e);
  }
}

function loadEdit(sessionId: string): QueuedEdit | null {
  try {
    const raw = localStorage.getItem(EDIT_KEY_PREFIX + sessionId);
    if (!raw) return null;
    const x = JSON.parse(raw) as Partial<QueuedEdit>;
    if (!x || typeof x.id !== 'string') return null;
    return {
      id: x.id,
      text: typeof x.text === 'string' ? x.text : '',
      originalText: typeof x.originalText === 'string' ? x.originalText : '',
      index: typeof x.index === 'number' ? x.index : 0,
      createdAt: typeof x.createdAt === 'number' ? x.createdAt : Date.now(),
    };
  } catch {
    return null;
  }
}

function persistEdit(sessionId: string, edit: QueuedEdit | null): void {
  try {
    if (edit) {
      localStorage.setItem(EDIT_KEY_PREFIX + sessionId, JSON.stringify(edit));
    } else {
      localStorage.removeItem(EDIT_KEY_PREFIX + sessionId);
    }
  } catch (e) {
    console.warn('[sendQueue] persist edit failed', e);
  }
}

function loadBatch(sessionId: string): boolean {
  try {
    return localStorage.getItem(BATCH_KEY_PREFIX + sessionId) === '1';
  } catch {
    return false;
  }
}

function persistBatch(sessionId: string, enabled: boolean): void {
  try {
    localStorage.setItem(BATCH_KEY_PREFIX + sessionId, enabled ? '1' : '0');
  } catch {
    // no-op
  }
}

interface QueueStore {
  queues: Record<string, QueuedMessage[]>;
  edits: Record<string, QueuedEdit | null>;
  batchSend: Record<string, boolean>;
  panelOpen: boolean;
  /** 当前正在发送的队列项 id（批量拼接时为 '__batch__'）；null 表示空闲。 */
  sendingId: string | null;
  /** 入队时立即塞进 currentMessages 的「乐观」聊天消息（按 sessionId → queueId →
   *  Message 引用）。Message 对象以引用相等存入 currentMessages，因此可用引用
   *  精确移除，避免在 remove / edit / clear 时留下幽灵消息。flush 发送后仅清理
   *  追踪（保留聊天消息 = 已发送的那条）；remove / clear / startEdit 走移除路径。 */
  optimisticRefs: Record<string, Record<string, Message>>;

  /** session 切换 / 首次进入时从 localStorage 恢复。 */
  loadForSession: (sessionId: string | null) => void;
  /** 入队（worker busy 时调用）；成功返回 true（已持久化 + 清输入框由调用方负责）。 */
  enqueue: (text: string) => boolean;
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
  /** 自动发送：worker idle/offline 时取队首（或批量拼接全部）发送，成功后上屏 + 出队。 */
  flush: (forceOffline?: boolean) => void;
  /** session 删除时清理孤儿 localStorage。 */
  removeSession: (sessionId: string) => void;
}

export const useQueueStore = create<QueueStore>((set, get) => {
  function ensureLoaded(sessionId: string): void {
    const { queues, edits, batchSend } = get();
    if (queues[sessionId] !== undefined) return;
    set({
      queues: { ...queues, [sessionId]: loadQueue(sessionId) },
      edits: { ...edits, [sessionId]: loadEdit(sessionId) },
      batchSend: { ...batchSend, [sessionId]: loadBatch(sessionId) },
    });
  }

  /** Send only becomes successful after the server confirms durable receipt.
   * The same client id is retained on retry, making reconnect retransmission
   * idempotent instead of guessing from WebSocket.send(). */
  function sendText(
    sessionId: string,
    clientMessageId: string,
    text: string,
    onSent: (ok: boolean) => void,
  ): void {
    let settled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const finish = (ok: boolean): void => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      offAccepted();
      offRejected();
      onSent(ok);
    };
    const matches = (e: { sessionId?: string; clientMessageId?: string }): boolean =>
      e.sessionId === sessionId && e.clientMessageId === clientMessageId;
    const offAcceptedRaw = wsClient.on('user_inject.accepted', (e) => {
      if (matches(e)) finish(true);
    });
    const offRejectedRaw = wsClient.on('user_inject.rejected', (e) => {
      if (!matches(e)) return;
      useUIStore.getState().showToast(e.message || '服务器拒绝了消息，已保留在发送队列', 'error');
      finish(false);
    });
    // A few embedders provide a minimal wsClient test double without event
    // subscriptions.  Preserve the old fire-and-forget contract for those
    // doubles; the real singleton always returns unsubscribe functions and
    // therefore waits for the durable server acknowledgement below.
    const ackSupported = typeof offAcceptedRaw === 'function' && typeof offRejectedRaw === 'function';
    const offAccepted = typeof offAcceptedRaw === 'function' ? offAcceptedRaw : () => {};
    const offRejected = typeof offRejectedRaw === 'function' ? offRejectedRaw : () => {};
    const payload = ackSupported
      ? { type: 'user_inject', sessionId, text, clientMessageId }
      : { type: 'user_inject', sessionId, text };
    if (!wsClient.send(payload)) {
      useUIStore
        .getState()
        .showToast('未连接到服务器 — 消息已保留在发送队列，连接恢复后自动发送', 'error');
      finish(false);
      return;
    }
    if (!ackSupported) {
      finish(true);
      return;
    }
    // An accepted ack can be lost with the connection. Keep the entry and
    // retry the same id; the backend receipt ledger makes that safe.
    timeout = setTimeout(() => finish(false), 10_000);
  }

  /** 把编辑中的条目按原位置插回队列（原值），并清除编辑态。 */
  function restoreEdit(sessionId: string): void {
    const edit = get().edits[sessionId];
    if (!edit) return;
    const queue = get().queues[sessionId] ?? [];
    const insertAt = Math.min(edit.index, queue.length);
    const next = queue.slice();
    next.splice(insertAt, 0, {
      id: edit.id,
      text: edit.originalText,
      createdAt: edit.createdAt,
      status: 'pending',
    });
    persistQueue(sessionId, next);
    persistEdit(sessionId, null);
    set((s) => ({
      queues: { ...s.queues, [sessionId]: next },
      edits: { ...s.edits, [sessionId]: null },
    }));
    // 该条目回到「待发送」状态 → 补一条乐观聊天消息（之前 startEdit 移走了）
    _addOptimisticForItem(sessionId, edit.id, edit.originalText);
  }

  // ── 乐观聊天消息 helpers ──
  // 入队时立即把用户消息追加到 currentMessages，让用户按 Enter 就看到自己的消息
  // （与 InputRow 直接发送一致）。后续 flush 发送完成时仅清理追踪、不重复 addMessage；
  // remove / edit / clear 走 _clearOptimisticForQids 把对应消息从聊天里也撤掉。

  function _addOptimisticForItem(sid: string, qid: string, text: string): void {
    // 切到别的 session 时入队（理论 race）→ 不污染对方聊天区
    if (useSessionStore.getState().currentSessionId !== sid) return;
    const msg: Message = { role: 'user', content: text };
    useSessionStore.getState().addMessage(msg);
    const cur = { ...(get().optimisticRefs[sid] ?? {}) };
    cur[qid] = msg;
    set((s) => ({ optimisticRefs: { ...s.optimisticRefs, [sid]: cur } }));
  }

  function _clearOptimisticForQids(sid: string, qids: string[]): void {
    const refs = get().optimisticRefs[sid];
    if (!refs) return;
    const removeRefs: Message[] = [];
    const cur = { ...refs };
    for (const qid of qids) {
      const r = cur[qid];
      if (r) {
        removeRefs.push(r);
        delete cur[qid];
      }
    }
    if (removeRefs.length === 0) return;
    // 仅当聊天里还引用着这些对象时才动 currentMessages（切走 session 等导致
    // currentMessages 已被替换时，跳过即可，避免误删别人的消息）。
    useSessionStore.setState((s) => {
      const hasAny = removeRefs.some((r) => s.currentMessages.includes(r));
      if (!hasAny) return {};
      return { currentMessages: s.currentMessages.filter((m) => !removeRefs.includes(m)) };
    });
    set((s) => ({ optimisticRefs: { ...s.optimisticRefs, [sid]: cur } }));
  }

  /** 仅清理追踪，保留聊天消息（消息已发送，聊天里那条就是它）。 */
  function _untrackOptimistic(sid: string, qids: string[]): void {
    const refs = get().optimisticRefs[sid];
    if (!refs) return;
    let changed = false;
    const cur = { ...refs };
    for (const qid of qids) {
      if (qid in cur) {
        delete cur[qid];
        changed = true;
      }
    }
    if (changed) set((s) => ({ optimisticRefs: { ...s.optimisticRefs, [sid]: cur } }));
  }

  return {
    queues: {},
    edits: {},
    batchSend: {},
    panelOpen: false,
    sendingId: null,
    optimisticRefs: {},

    loadForSession: (sessionId) => {
      if (!sessionId) return;
      ensureLoaded(sessionId);
      // 切换 session：尝试立即发送（覆盖"worker 恰好已 idle"的窗口）
      get().flush();
    },

    enqueue: (text) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return false;
      ensureLoaded(sid);
      const queue = get().queues[sid] ?? [];
      if (queue.length >= QUEUE_MAX) {
        useUIStore.getState().showToast(`发送队列已满（上限 ${QUEUE_MAX} 条）`, 'error');
        return false;
      }
      const item: QueuedMessage = {
        id: genQueueId(),
        text,
        createdAt: Date.now(),
        status: 'pending',
      };
      const next = [...queue, item];
      persistQueue(sid, next);
      set((s) => ({ queues: { ...s.queues, [sid]: next } }));
      // 立即在聊天窗口追加用户消息（与 InputRow 直接发送一致）——之前要等 worker
      // idle 触发 flush 才上屏，发送中的延迟会被用户误判为「没发出去」。
      _addOptimisticForItem(sid, item.id, text);
      useUIStore.getState().showToast(`已加入发送队列（${next.length} 条待发）`);
      // 竞态兜底：busy 检查与入队之间 worker 可能已 idle（idle 事件先于入队触发过）
      get().flush();
      return true;
    },

    remove: (id) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      const queue = get().queues[sid] ?? [];
      const idx = queue.findIndex((x) => x.id === id);
      if (idx < 0) return; // 已不在队列（已发送或不存在）→ 无乐观消息需要移除
      // 该项是「待发送」状态：从聊天区撤掉入队时追加的乐观消息，避免幽灵。
      _clearOptimisticForQids(sid, [id]);
      const next = queue.slice();
      next.splice(idx, 1);
      persistQueue(sid, next);
      set((s) => ({ queues: { ...s.queues, [sid]: next } }));
      useUIStore.getState().showToast('已从发送队列删除');
    },

    startEdit: (id) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      // 若已有编辑中的条目，先恢复它，避免被覆盖后丢失（该条已出队）
      if (get().edits[sid]) {
        restoreEdit(sid);
      }
      const queue = get().queues[sid] ?? [];
      const idx = queue.findIndex((x) => x.id === id);
      if (idx < 0) return;
      const item = queue[idx];
      if (!item) return;
      // 先出队：从内存 + localStorage 移除，避免被 flush 发出
      const next = queue.slice();
      next.splice(idx, 1);
      persistQueue(sid, next);
      // 该项从「待发送」转入「编辑」→ 撤掉乐观聊天消息（saveEdit / cancelEdit 重新入队
      // 时会通过 restoreEdit 或 saveEdit 内部再补一条新的乐观消息）。
      _clearOptimisticForQids(sid, [id]);
      const edit: QueuedEdit = {
        id: item.id,
        text: item.text,
        originalText: item.text,
        index: idx,
        createdAt: item.createdAt,
      };
      persistEdit(sid, edit);
      set((s) => ({
        queues: { ...s.queues, [sid]: next },
        edits: { ...s.edits, [sid]: edit },
      }));
    },

    updateEditDraft: (text) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      const edit = get().edits[sid];
      if (!edit) return;
      const updated = { ...edit, text };
      persistEdit(sid, updated);
      set((s) => ({ edits: { ...s.edits, [sid]: updated } }));
    },

    saveEdit: () => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      const edit = get().edits[sid];
      if (!edit) return;
      const trimmed = edit.text.trim();
      // 保存为空 → 视为取消，恢复原值（对齐 vanilla）
      const finalText = trimmed ? edit.text : edit.originalText;
      const queue = get().queues[sid] ?? [];
      const insertAt = Math.min(edit.index, queue.length);
      const next = queue.slice();
      next.splice(insertAt, 0, {
        id: edit.id,
        text: finalText,
        createdAt: edit.createdAt,
        status: 'pending',
      });
      persistQueue(sid, next);
      persistEdit(sid, null);
      set((s) => ({
        queues: { ...s.queues, [sid]: next },
        edits: { ...s.edits, [sid]: null },
      }));
      // 重新入队 → 补一条乐观聊天消息（startEdit 撤掉过；文本可能已修改）
      _addOptimisticForItem(sid, edit.id, finalText);
      get().flush();
    },

    cancelEdit: () => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      restoreEdit(sid);
      get().flush();
    },

    move: (id, delta) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      const queue = get().queues[sid] ?? [];
      const idx = queue.findIndex((x) => x.id === id);
      if (idx < 0) return;
      const target = idx + delta;
      if (target < 0 || target >= queue.length) return;
      const next = queue.slice();
      // idx/target 均已通过 findIndex/边界校验，取值必然存在
      const a = next[idx] as QueuedMessage;
      const b = next[target] as QueuedMessage;
      next[idx] = b;
      next[target] = a;
      persistQueue(sid, next);
      set((s) => ({ queues: { ...s.queues, [sid]: next } }));
      // 提到队首且 worker 空闲 → 立即发送
      if (target === 0) get().flush();
    },

    clear: () => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      // 清空队列 → 撤掉所有「待发送」项对应的乐观聊天消息（保留已编辑中的 edit）
      const queue = get().queues[sid] ?? [];
      _clearOptimisticForQids(sid, queue.map((x) => x.id));
      persistQueue(sid, []);
      set((s) => ({ queues: { ...s.queues, [sid]: [] } }));
      useUIStore.getState().showToast('已清空发送队列');
    },

    toggleBatchSend: () => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      const next = !get().batchSend[sid];
      persistBatch(sid, next);
      set((s) => ({ batchSend: { ...s.batchSend, [sid]: next } }));
      if (next) get().flush();
    },

    togglePanel: () => {
      set((s) => ({ panelOpen: !s.panelOpen }));
    },

    setPanelOpen: (open) => {
      set({ panelOpen: open });
    },

    flush: (forceOffline = false) => {
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) return;
      ensureLoaded(sid);
      const state = get();
      const queue = state.queues[sid] ?? [];
      if (queue.length === 0) return;
      if (state.sendingId) return; // 已有 1 条在发送中，等下一次 idle 事件
      if (state.edits[sid]) return; // 编辑中（该条已出队），不要并发 flush 其它项

      const session = useSessionStore.getState().sessions.find((x) => x.id === sid);
      const status = session?.workerStatus || 'offline';
      if (status === 'held') return; // takeover：服务端硬拒，跳过自动发送
      if (!forceOffline && status !== 'idle' && status !== 'offline') return; // queued/running/…：等 idle 事件

      const finish = (idsToRemove: string[], message: string, isBatch: boolean): void => {
        if (isBatch) {
          // 批量：N 条乐观消息替换为 1 条合并后的消息（与服务端一致）。
          _clearOptimisticForQids(sid, idsToRemove);
          if (useSessionStore.getState().currentSessionId === sid) {
            useSessionStore.getState().addMessage({ role: 'user', content: message });
          }
        } else {
          // 单条：入队时已把乐观消息塞进聊天区，发送完成只需清理追踪——保留那条消息
          // （它就是刚刚发出去的用户消息）。
          _untrackOptimistic(sid, idsToRemove);
        }
        const cur = get().queues[sid] ?? [];
        const removeSet = new Set(idsToRemove);
        const next = cur.filter((x) => !removeSet.has(x.id));
        persistQueue(sid, next);
        set((s) => ({ queues: { ...s.queues, [sid]: next } }));
      };

      if (state.batchSend[sid]) {
        // 批量拼接：把全部消息拼成一条发出
        const combined = queue.map((x) => x.text).join(BATCH_SEPARATOR);
        set({ sendingId: '__batch__' });
        const batchId = `batch:${queue.map((item) => item.id).join(',')}`;
        sendText(sid, batchId, combined, (ok) => {
          set({ sendingId: null });
          if (!ok) return; // 失败：全部保留待重试
          finish(
            queue.map((x) => x.id),
            combined,
            true,
          );
        });
        return;
      }

      const head = queue[0];
      if (!head) return;
      set({ sendingId: head.id });
      sendText(sid, head.id, head.text, (ok) => {
        set({ sendingId: null });
        if (!ok) return; // 发送失败：保留队首待下次重试
        finish([head.id], head.text, false);
      });
    },

    removeSession: (sessionId) => {
      try {
        localStorage.removeItem(QUEUE_KEY_PREFIX + sessionId);
        localStorage.removeItem(EDIT_KEY_PREFIX + sessionId);
        localStorage.removeItem(BATCH_KEY_PREFIX + sessionId);
      } catch {
        // ignore
      }
      set((s) => {
        const queues = { ...s.queues };
        delete queues[sessionId];
        const edits = { ...s.edits };
        delete edits[sessionId];
        const batchSend = { ...s.batchSend };
        delete batchSend[sessionId];
        // session 删了 → 它的乐观聊天消息追踪也清掉（currentMessages 已被
        // sessionStore 重置，不用管）
        const optimisticRefs = { ...s.optimisticRefs };
        delete optimisticRefs[sessionId];
        return { queues, edits, batchSend, optimisticRefs };
      });
    },
  };
});

// ── 联动：session 删除时清理孤儿 localStorage key ──
// （session 切换时的队列加载由 SendQueuePanel 的 mount/currentSessionId effect 负责）

useSessionStore.subscribe((state, prevState) => {
  if (state.sessions !== prevState.sessions) {
    const prevIds = new Set(prevState.sessions.map((s) => s.id));
    for (const s of state.sessions) prevIds.delete(s.id);
    for (const removedId of prevIds) {
      useQueueStore.getState().removeSession(removedId);
    }
  }
});

// ── 联动：WS 连接建立时重试当前 session 的队列 ──
// sendText 在 WS 未连接（CONNECTING/CLOSED）时收到 false 会保留队列项；这里补上
// 「连接恢复 → 自动重发」的闭环，否则 flush 失败后只能等下一次 idle/切 session 才重试。

wsClient.on('open', () => {
  useQueueStore.getState().flush();
});
