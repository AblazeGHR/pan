import { useEffect } from 'react';
import { wsClient } from '@/services/ws';
import { fetchSessionHistory } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useQueueStore } from '@/stores/queueStore';
import {
  useAdapterStore,
} from '@/stores/adapterStore';
import type { StreamEvent, Message } from '@/types';

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
      // agent 编排消息实时同步：meta-agent 的 worker_send（////by agent 前缀）
      // 注入的 user 消息只在服务端 s.history 落盘，WS 从不广播（只广播 assistant
      // 回复的 worker.stream / 完成的 worker.result），前端对自己的发送有乐观追加、
      // 对 agent 注入没有 → 切走再切回才显示。任务开始 running 时（source 已带
      // 进广播）拉一次历史把缺的 user 消息并入 currentMessages。
      if (
        e.status === 'running' &&
        (e.source === 'agent' || e.source === 'report') &&
        e.sessionId === useSessionStore.getState().currentSessionId
      ) {
        syncAgentInjectedMessage();
      }
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

/** Python `json.dumps(input, separators=(',',':'), ensure_ascii=True)` 兼容的
 *  序列化。后端 cbc adapter 落盘 tool_use 内容用 Python json.dumps（默认
 *  ensure_ascii=True，中文/emoji 转义为小写 \uXXXX），而 JS `JSON.stringify`
 *  不转义非 ASCII——同一 tool 消息前后端内容不一致会让 isServerHistoryPrefix
 *  误判（→ loadSessions 全量重建时把刚发的乐观用户消息抹掉，长历史会话更易
 *  触发）。此处按 Python 规则补转义；键序两端同源、均按插入序，其余转义规则
 *  JSON.stringify 与 json.dumps 一致（代理对也逐半转义，与 Python 相同）。 */
function pyJsonDumps(value: unknown): string {
  return JSON.stringify(value).replace(
    /[\u007f-\uffff]/g,
    (ch) => '\\u' + ch.charCodeAt(0).toString(16).padStart(4, '0'),
  );
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
          (b.name || '') + '(' + pyJsonDumps(b.input || {}) + ')';
        store.markUnread(c);
        store.addMessage({ role: 'tool', content: c });
      }
    }
  }
}

// ── Agent 注入消息实时同步 ──
// meta-agent 的 worker_send / 订阅报告会把 user 消息写进服务端 s.history，但 WS
// 只广播 assistant 回复（worker.stream）与完成（worker.result）——user 消息前端
// 无实时来源，切走再切回（selectSession fetch 历史）才显示。这里在 agent/report
// 任务开始 running 时拉一次最近历史，把服务端有、本地缺的 user 消息并入。
let agentSyncInFlight = false;
function syncAgentInjectedMessage(): void {
  const sid = useSessionStore.getState().currentSessionId;
  if (!sid || agentSyncInFlight) return;
  agentSyncInFlight = true;
  fetchSessionHistory(sid, 0, 50)
    .then((data) => {
      const store = useSessionStore.getState();
      if (store.currentSessionId !== sid) return; // 用户已切走，丢弃过期结果
      const merged = mergeServerMessages(store.currentMessages, data.history || []);
      if (merged !== store.currentMessages) {
        useSessionStore.setState({ currentMessages: merged });
      }
    })
    .catch(() => {
      // 拉取失败：保留本地，agent 消息仍会随下次切 session 出现
    })
    .finally(() => {
      agentSyncInFlight = false;
    });
}

/** 把服务端历史里本地缺失的消息并入本地（幂等），同时保留本地已在流式的
 *  assistant 块（服务端落盘滞后）。无变化时返回原引用，避免多余重渲染。 */
function mergeServerMessages(
  local: Message[],
  server: Message[],
): Message[] {
  if (server.length === 0) return local;
  // 最长公共前缀：服务端在分叉点之前与本地一致
  let k = 0;
  while (
    k < local.length &&
    k < server.length &&
    local[k]!.role === server[k]!.role &&
    local[k]!.content === server[k]!.content
  ) {
    k++;
  }
  // 服务端历史是本地前缀 → 服务端没有本地没有的消息（流式中）→ 不动
  if (k === server.length) return local;
  const serverTail = server.slice(k);
  const localTail = local.slice(k);
  // 本地尾部中已被服务端新段覆盖的部分（流式块已落盘 + 新 user 消息），按
  // 「本地前缀 == 服务端后缀」判定，插入后不重复。
  let overlap = 0;
  for (let n = 1; n <= Math.min(localTail.length, serverTail.length); n++) {
    let match = true;
    for (let i = 0; i < n; i++) {
      const a = localTail[i]!;
      const b = serverTail[serverTail.length - n + i]!;
      if (a.role !== b.role || a.content !== b.content) {
        match = false;
        break;
      }
    }
    if (match) overlap = n;
  }
  return [
    ...local.slice(0, k),
    ...serverTail,
    ...localTail.slice(overlap),
  ];
}
