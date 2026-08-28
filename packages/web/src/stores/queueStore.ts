import { create } from 'zustand';
import type { QueuedMessage, QueuedEdit } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { wsClient } from '@/services/ws';

/**
 * 客户端发送队列（对齐 vanilla ts/app.ts 的 sendQueue 实现）：
 * - 每 session 一个队列，localStorage 持久化（`pan.sendQueue.<sessionId>`），上限 50 条。
 * - worker busy（running/held）时入队，worker idle 时自动逐条 flush（或批量拼接成一条）。
 * - 编辑先出队：编辑中的消息从队列（内存 + localStorage）移除，避免被 flush 发出；
 *   Enter 保存插回原位置，Esc 恢复原值；编辑态持久化，刷新可恢复。
 * - `sendingId` 单飞锁：防同一条在异步窗口内被 idle 事件重复触发两次。
 * - 语义：排队消息**不进聊天历史**（它不在服务端 history 中，伪装上屏会在刷新后
 *   凭空消失）。可见性由 SendQueuePanel + InputRow 角标 + toast 提供（数据源
 *   localStorage 持久，刷新后仍在）；flush 发送成功后才 addMessage 上屏。
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
  flush: () => void;
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

  /** 发送单条文本（封装 spawn + WS 投递）。WS 已投递 → onSent(true)；
   *  未连接（CONNECTING/CLOSED，wsClient.send 返回 false）→ onSent(false)，
   *  调用方（flush）保留队列项，等 WS 'open' 联动或下次 idle 事件重试。 */
  function sendText(sessionId: string, text: string, onSent: (ok: boolean) => void): void {
    const doSend = (): void => {
      const msg = { type: 'user_inject', sessionId, text };
      if (wsClient.send(msg)) {
        onSent(true);
        return;
      }
      useUIStore
        .getState()
        .showToast('未连接到服务器 — 消息已保留在发送队列，连接恢复后自动发送', 'error');
      onSent(false);
    };

    const session = useSessionStore.getState().sessions.find((x) => x.id === sessionId);
    if (!session?.workerId) {
      useWorkerStore
        .getState()
        .startWorker(sessionId)
        .then(() => doSend())
        .catch((e: unknown) => {
          useUIStore
            .getState()
            .showToast('Spawn failed: ' + (e instanceof Error ? e.message : String(e)), 'error');
          onSent(false);
        });
      return;
    }
    doSend();
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
  }

  return {
    queues: {},
    edits: {},
    batchSend: {},
    panelOpen: false,
    sendingId: null,

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
      // 排队消息不上屏：它尚未进入服务端会话历史，伪装成已发送会在刷新后
      // 凭空消失。可见性由 toast + SendQueuePanel + 输入框 ^ 角标提供。
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
      if (idx < 0) return; // 已不在队列（已发送或不存在）
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

    flush: () => {
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
      if (status !== 'idle' && status !== 'offline') return; // queued/running/…：等 idle 事件

      const finish = (idsToRemove: string[], message: string): void => {
        // 消息已被服务端接受进任务流 → 现在才上屏（桥接到服务端历史包含它为止；
        // sessionStore 的 isServerHistoryPrefix 防护会保留这条本地消息不被过期
        // 快照冲掉）。单条与批量路径统一：聊天区只显示已确认发送的内容。
        if (useSessionStore.getState().currentSessionId === sid) {
          useSessionStore.getState().addMessage({ role: 'user', content: message });
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
        sendText(sid, combined, (ok) => {
          set({ sendingId: null });
          if (!ok) return; // 失败：全部保留待重试
          finish(
            queue.map((x) => x.id),
            combined,
          );
        });
        return;
      }

      const head = queue[0];
      if (!head) return;
      set({ sendingId: head.id });
      sendText(sid, head.text, (ok) => {
        set({ sendingId: null });
        if (!ok) return; // 发送失败：保留队首待下次重试
        finish([head.id], head.text);
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
        return { queues, edits, batchSend };
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
