import { useEffect } from 'react';
import { wsClient } from '@/services/ws';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useQueueStore } from '@/stores/queueStore';
import {
  useAdapterStore,
} from '@/stores/adapterStore';
import type { StreamEvent } from '@/types';

// ── Debounced full-list refresh (mirrors legacy app.ts scheduleRefreshSessions) ──
// WS events can burst (rapid task completions, session updates); firing a full
// /api/sessions fetch for every one of them re-renders the whole sidebar list
// per event and — worse — the snapshot can land in the backend's transient
// "done"/"error" status window before `w.status` is reset to "idle", which
// would override the locally-set idle WorkerDot. Coalescing to one fetch 300ms
// after the last event keeps the UI snappy and lets the backend status settle.
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleRefreshSessions(): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    useSessionStore.getState().loadSessions();
  }, 300);
}

/**
 * Connects to WebSocket and routes events to Zustand stores.
 * Uses store.getState() for callbacks so React components re-render
 * when subscribed state changes.
 */
export function useWebSocket() {
  useEffect(() => {
    wsClient.connect();

    // 初始加载兜底：StrictMode dev 下 effect 会 setup→cleanup→setup 重跑，
    // open 处理器被注销后再注册，而 wsClient.connect() 幂等（首个 setup 已
    // 发起连接，重跑时不再触发新 open）——这里显式刷新一次，保证会话列表
    // 始终加载，且 WS 在重挂载前已连接时（HMR/切路由回来）也能拿到最新数据。
    useSessionStore.getState().loadSessions();
    useWorkerStore.getState().refresh();

    // Capture every unsubscribe so remounts don't accumulate duplicate
    // handlers on the singleton wsClient.
    const unsubscribers: Array<() => void> = [];

    // Open handler — refresh sessions on connect
    unsubscribers.push(wsClient.on('open', () => {
      useSessionStore.getState().loadSessions();
      useWorkerStore.getState().refresh();
      useAdapterStore.getState().loadAdapterList();
      useAdapterStore.getState().loadConfig('cbc');
    }));

    // Worker spawned / restarted / reconfigured
    unsubscribers.push(wsClient.on('worker.spawned', (e: StreamEvent) =>
      handleWorkerUpdate(e, 'idle'),
    ));
    unsubscribers.push(wsClient.on('worker.restarted', (e: StreamEvent) =>
      handleWorkerUpdate(e, 'idle'),
    ));
    unsubscribers.push(wsClient.on('worker.reconfigured', (e: StreamEvent) =>
      handleWorkerUpdate(e, 'idle'),
    ));

    // Worker destroyed / crashed — 除就地更新状态点外触发防抖全量兜底：
    // 崩溃/销毁是低频事件，且流式片段已逐块落盘，刷新让列表吸收已持久化的
    // 部分回复（镜像 vanilla _applyWorkerUpdate → scheduleRefreshSessions）。
    unsubscribers.push(wsClient.on('worker.destroyed', (e: StreamEvent) => {
      handleWorkerUpdate(e, null);
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('worker.crashed', (e: StreamEvent) => {
      handleWorkerUpdate(e, null);
      scheduleRefreshSessions();
    }));

    // Worker status update
    unsubscribers.push(wsClient.on('worker.status', (e: StreamEvent) => {
      handleWorkerUpdate(e, e.status ?? 'idle');
    }));

    // Stream events (real-time message chunks)
    unsubscribers.push(wsClient.on('worker.stream', (e: StreamEvent) => {
      const store = useSessionStore.getState();
      if (e.sessionId !== store.currentSessionId || !e.event) return;
      appendEvent(e.event);
    }));

    // Result event
    unsubscribers.push(wsClient.on('worker.result', (e: StreamEvent) => {
      const sessionStore = useSessionStore.getState();
      if (e.sessionId === sessionStore.currentSessionId) {
        const status = e.status === 'error' ? 'error' : 'done';
        sessionStore.addMessage({
          role: 'system',
          content: `[${status.toUpperCase()}] Task completed`,
        });
      }
      handleWorkerUpdate(e, 'idle');
      // 就地更新该 session 卡片（lastResult + 结果文本追加 + historyTotal），
      // 不等 300ms 防抖全量兜底即可让「最后消息 summary」立即最新。
      if (e.sessionId) sessionStore.applyResultToSession(e.sessionId, e);
      // 实时刷新侧边栏列表（lastResult / historyTotal / workerStatus 等卡片
      // 数据）。防抖合并为单次全量抓取：既避免每个任务完成都触发整列表重渲染
      // 造成的滞涩，也避开了后端「done→idle」的瞬态窗口（否则快照可能把已置为
      // idle 的指示灯回退成灰色）。指示灯本身已由 handleWorkerUpdate 同步更新。
      scheduleRefreshSessions();
    }));

    // Session events — created/deleted 也需刷新列表（否则新 session 不出现、
    // 删除的残留，需手动刷新才更新）。同样防抖合并。
    unsubscribers.push(wsClient.on('session.renamed', () => {
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('session.updated', () => {
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('session.created', () => {
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('session.deleted', () => {
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('sessions.deleted', () => {
      scheduleRefreshSessions();
    }));

    // Error
    unsubscribers.push(wsClient.on('error', (e: StreamEvent) => {
      useUIStore.getState().showToast(e.message ?? 'Unknown error', 'error');
    }));

    return () => {
      // Don't disconnect on unmount — connection is managed by singleton.
      // But DO remove handlers so a remount re-registers cleanly.
      unsubscribers.forEach((unsub) => unsub());
    };
  }, []);
}

function handleWorkerUpdate(
  e: StreamEvent,
  status: string | null,
): void {
  if (!e.sessionId) return;
  const sessStore = useSessionStore.getState();
  sessStore.updateSession(e.sessionId, {
    workerId: e.workerId ?? undefined,
    workerStatus: status ?? undefined,
  });
  const workerStore = useWorkerStore.getState();
  workerStore.updateWorker(e.sessionId, e.workerId ?? null, status);

  // 队列自动发送：worker 变 idle 且属于当前 session → 发送队首 1 条
  // （发送后 worker 变 queued/running，不再是 idle，天然防重复；result→idle 再取下一条）
  if (
    status === 'idle' &&
    e.sessionId === useSessionStore.getState().currentSessionId
  ) {
    useQueueStore.getState().flush();
  }
}

function appendEvent(event: StreamEvent['event']): void {
  if (!event) return;
  const t = event.type;
  if (t === 'system' && event.subtype === 'init') return;
  if (t === 'result') return;

  const store = useSessionStore.getState();

  if (t === 'assistant') {
    const content = event.message?.content || [];
    for (const b of content) {
      if (b.type === 'text') {
        store.addMessage({ role: 'assistant', content: b.text || '' });
      } else if (b.type === 'thinking') {
        store.markUnread(b.thinking || '');
        store.addMessage({
          role: 'thinking',
          content: b.thinking || '',
        });
      } else if (b.type === 'tool_use') {
        const c =
          (b.name || '') + '(' + JSON.stringify(b.input || {}) + ')';
        store.markUnread(c);
        store.addMessage({ role: 'tool', content: c });
      }
    }
  }
}
