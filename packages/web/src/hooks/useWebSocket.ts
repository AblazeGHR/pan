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
import type { StreamEvent, WorkerEvent, Message, UserInputQuestion } from '@/types';

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

function clearInteractiveRequests(sessionId?: string): void {
  if (!sessionId) return;
  const ui = useUIStore.getState();
  ui.clearApprovalRequests(sessionId);
  ui.clearUserInputRequests(sessionId);
  ui.clearElicitationRequests(sessionId);
  ui.clearTerminalInteractions(sessionId);
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

    // ── 流式 lastMessage 卡片预览（throttle）──
    // worker.stream 对每个回复块广播一次；除当前 session 消息区外，侧边栏所有
    // session 的卡片预览也要实时跟随。为不因每个 chunk 都更新 store 而拖累列表，
    // 按 session 做 500ms 节流：窗口内合并到最新文本，到点 flush 一次。result 落地
    // 时取消 pending，保证最终 lastMessage 以 result 为准（节流 timer 不会迟到
    // 覆盖 result）。状态放 effect 闭包里，卸载即清，StrictMode 重挂载不残留。
    const STREAM_PREVIEW_THROTTLE_MS = 500;
    const streamPreviewPending = new Map<string, string>(); // sessionId → 最新待 flush 文本
    const streamPreviewLastFlush = new Map<string, number>(); // sessionId → 上次 flush 时间戳
    const streamPreviewTimers = new Map<string, ReturnType<typeof setTimeout>>();

    const flushStreamPreview = (sessionId: string, now: number): void => {
      const text = streamPreviewPending.get(sessionId);
      if (text === undefined) return;
      useSessionStore.getState().updateSession(sessionId, {
        lastMessage: text.slice(0, 200),
      });
      streamPreviewPending.delete(sessionId);
      streamPreviewLastFlush.set(sessionId, now);
    };

    const throttledLastMessageUpdate = (
      sessionId: string,
      text: string,
    ): void => {
      // 保留最新文本，窗口内合并
      streamPreviewPending.set(sessionId, text);
      const now = Date.now();
      const last = streamPreviewLastFlush.get(sessionId) ?? 0;
      if (now - last >= STREAM_PREVIEW_THROTTLE_MS) {
        flushStreamPreview(sessionId, now);
      } else if (!streamPreviewTimers.has(sessionId)) {
        // 距上次 flush 未满 500ms → 排一个尾随 timer，到点 flush 最新文本
        const delay = STREAM_PREVIEW_THROTTLE_MS - (now - last);
        streamPreviewTimers.set(
          sessionId,
          setTimeout(() => {
            streamPreviewTimers.delete(sessionId);
            flushStreamPreview(sessionId, Date.now());
          }, delay),
        );
      }
    };

    const cancelStreamPreview = (sessionId: string): void => {
      streamPreviewPending.delete(sessionId);
      const timer = streamPreviewTimers.get(sessionId);
      if (timer !== undefined) {
        clearTimeout(timer);
        streamPreviewTimers.delete(sessionId);
      }
      streamPreviewLastFlush.delete(sessionId);
    };

    const syncInteractiveRequests = (): void => {
      // The backend keeps a worker-local snapshot of native prompts while the
      // JSON-RPC request is still open. Ask for it after every connection so a
      // browser refresh/reconnect does not strand the user at a hidden prompt.
      wsClient.send({ type: 'sync_interactive' });
    };

    // Open handler — refresh sessions and restore live native prompts on connect
    unsubscribers.push(wsClient.on('open', () => {
      useSessionStore.getState().loadSessions();
      useWorkerStore.getState().refresh();
      useAdapterStore.getState().loadAdapterList();
      useAdapterStore.getState().loadConfig('cbc');
      syncInteractiveRequests();
    }));
    // If the singleton was already open before this hook mounted (HMR/route
    // remount), no new `open` event will arrive; sync explicitly as well.
    if (wsClient.isOpen) syncInteractiveRequests();

    // Worker spawned / restarted / reconfigured
    unsubscribers.push(wsClient.on('worker.spawned', (e: StreamEvent) => {
      clearInteractiveRequests(e.sessionId);
      handleWorkerUpdate(e, 'idle');
    }));
    unsubscribers.push(wsClient.on('worker.restarted', (e: StreamEvent) => {
      clearInteractiveRequests(e.sessionId);
      handleWorkerUpdate(e, 'idle');
    }));
    unsubscribers.push(wsClient.on('worker.reconfigured', (e: StreamEvent) => {
      clearInteractiveRequests(e.sessionId);
      handleWorkerUpdate(e, 'idle');
    }));

    // Worker destroyed / crashed — 除就地更新状态点外触发防抖全量兜底：
    // 崩溃/销毁是低频事件，且流式片段已逐块落盘，刷新让列表吸收已持久化的
    // 部分回复（镜像 vanilla _applyWorkerUpdate → scheduleRefreshSessions）。
    unsubscribers.push(wsClient.on('worker.destroyed', (e: StreamEvent) => {
      if (e.sessionId) cancelStreamPreview(e.sessionId);
      clearInteractiveRequests(e.sessionId);
      handleWorkerUpdate(e, null);
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('worker.crashed', (e: StreamEvent) => {
      if (e.sessionId) cancelStreamPreview(e.sessionId);
      clearInteractiveRequests(e.sessionId);
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
      if (!e.sessionId || !e.event) return;
      if (e.event.type === 'codex.thread_status' && e.event.native_status) {
        useWorkerStore.getState().updateNativeStatus(
          e.sessionId,
          e.workerId,
          e.event.native_status,
        );
      }
      if (e.event.type === 'codex.token_usage' && e.event.token_usage) {
        useWorkerStore.getState().updateNativeUsage(
          e.sessionId,
          e.workerId,
          e.event.token_usage,
        );
      }
      if (
        e.event.type === 'approval.request' &&
        e.workerId &&
        e.event.method &&
        e.event.request_id !== undefined
      ) {
        useUIStore.getState().addApprovalRequest({
          sessionId: e.sessionId,
          workerId: e.workerId,
          requestId: e.event.request_id,
          method: e.event.method,
          params: e.event.params ?? {},
        });
      }
      if (
        e.event.type === 'codex.user_input' &&
        e.workerId &&
        e.event.method === 'item/tool/requestUserInput' &&
        e.event.request_id !== undefined
      ) {
        const questions = e.event.params?.questions;
        useUIStore.getState().addUserInputRequest({
          sessionId: e.sessionId,
          workerId: e.workerId,
          requestId: e.event.request_id,
          method: e.event.method,
          questions: Array.isArray(questions) ? questions as UserInputQuestion[] : [],
        });
      }
      if (
        e.event.type === 'codex.elicitation' &&
        e.workerId &&
        e.event.method === 'mcpServer/elicitation/request' &&
        e.event.request_id !== undefined
      ) {
        useUIStore.getState().addElicitationRequest({
          sessionId: e.sessionId,
          workerId: e.workerId,
          requestId: e.event.request_id,
          method: e.event.method,
          params: e.event.params ?? {},
        });
      }
      if (
        e.event.type === 'codex.terminal_interaction' &&
        e.workerId &&
        e.event.item_id !== undefined &&
        e.event.process_id !== undefined
      ) {
        useUIStore.getState().addTerminalInteraction({
          sessionId: e.sessionId,
          workerId: e.workerId,
          itemId: String(e.event.item_id),
          processId: String(e.event.process_id),
          stdin: typeof e.event.stdin === 'string' ? e.event.stdin : '',
          params: e.event.params ?? {},
        });
      }
      if (e.event.type === 'codex.request_resolved' && e.sessionId && e.event.request_id !== undefined) {
        const requestId = e.event.request_id;
        const ui = useUIStore.getState();
        ui.removeApprovalRequest(e.sessionId, requestId);
        ui.removeUserInputRequest(e.sessionId, requestId);
        ui.removeElicitationRequest(e.sessionId, requestId);
      }
      const store = useSessionStore.getState();
      // 消息区：仅当前 session 追加（原有逻辑，保留）
      if (e.sessionId === store.currentSessionId) appendEvent(e.event);
      // 卡片预览：所有 session 就地 throttle 更新 lastMessage（无文本事件跳过）
      const text = extractStreamText(e.event);
      if (text) throttledLastMessageUpdate(e.sessionId, text);
    }));

    // Result event
    unsubscribers.push(wsClient.on('worker.result', (e: StreamEvent) => {
      const sessionStore = useSessionStore.getState();
      clearInteractiveRequests(e.sessionId);
      if (e.sessionId === sessionStore.currentSessionId) {
        const status = e.status === 'error'
          ? 'error'
          : e.status === 'cancelled' || e.cancelled
            ? 'cancelled'
            : 'done';
        sessionStore.addMessage({
          role: 'system',
          content: `[${status.toUpperCase()}] Task completed`,
        });
      }
      // 流式预览节流：result 为最终 lastMessage，先清掉该 session 未 flush 的
      // pending 文本与尾随 timer，防止其迟到覆盖 result（applyResultToSession
      // 紧接着以 result 写入 lastMessage）。
      if (e.sessionId) cancelStreamPreview(e.sessionId);
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
      // 卸载时清掉流式预览节流 timer，避免迟到 flush 更新卸载后的 store
      for (const timer of streamPreviewTimers.values()) clearTimeout(timer);
      streamPreviewTimers.clear();
      streamPreviewPending.clear();
      streamPreviewLastFlush.clear();
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

/** 把 worker.stream 的 assistant 事件规整为 `{role, content}` 块列表。
 *  兼容两种事件形状（与后端各 adapter 的 extract_assistant_blocks 语义对齐）：
 *  - cbc：{type:'assistant', message:{content:[{type:'text',text}...]}}
 *  - kimi：{role:'assistant', content: 字符串|块数组}、
 *          {type:'content.part', role:'assistant', part:{type,text}}、
 *          以及 tool_calls —— kimi 事件以 role 标识、可无 type 字段，
 *          此前前端只认 type==='assistant' 导致 kimi 流式/完成后均不渲染。 */
function extractBlocks(
  event: WorkerEvent,
): Array<{ role: string; content: string }> {
  const blocks: Array<{ role: string; content: string }> = [];
  if (event.type === 'codex.item.completed' && event.item) {
    const item = { ...event.item };
    const kind = String(item.type ?? 'CodexItem');
    delete item.id;
    delete item.type;
    let rendered = pyJsonDumps(item);
    if (rendered.length > 4000) rendered = rendered.slice(0, 4000) + '…';
    return [{ role: 'tool', content: `${kind}(${rendered})` }];
  }
  const role = event.role ?? event.type;
  if (role !== 'assistant' && role !== 'thinking') return blocks;

  // cbc: message.content；kimi: content（纯字符串或块数组）
  const content = event.message?.content ?? event.content;
  if (typeof content === 'string') {
    blocks.push({
      role: role === 'thinking' ? 'thinking' : 'assistant',
      content,
    });
  } else if (Array.isArray(content)) {
    for (const b of content) {
      if (!b || typeof b !== 'object') continue;
      if (b.type === 'text') {
        blocks.push({ role: 'assistant', content: b.text || '' });
      } else if (b.type === 'thinking' || b.type === 'think') {
        blocks.push({
          role: 'thinking',
          content: b.thinking ?? b.think ?? '',
        });
      } else if (b.type === 'tool_use') {
        const c =
          (b.name || '') + '(' + pyJsonDumps(b.input || {}) + ')';
        blocks.push({ role: 'tool', content: c });
      }
    }
  }

  // kimi content.part 增量块：{type:'content.part', part:{type:'text', text}}
  if (blocks.length === 0 && event.type === 'content.part') {
    const part = (event.part ?? {}) as Record<string, unknown>;
    const ptype = String(part.type ?? '');
    const text = String(part[ptype] ?? '').trim();
    if (text) {
      blocks.push({
        role: ptype === 'think' ? 'thinking' : 'assistant',
        content: text,
      });
    }
  }

  // kimi tool_calls：{tool_calls:[{function:{name, arguments}}]}
  for (const tc of event.tool_calls ?? []) {
    const fn = tc?.function ?? {};
    blocks.push({
      role: 'tool',
      content: `${fn.name ?? '?'}(${fn.arguments ?? '{}'})`,
    });
  }

  return blocks;
}

/** 从 worker.stream 的 assistant 事件提取最新文本块（卡片 lastMessage 预览）。
 *  每个 text 块在消息区各成一条 assistant 消息，预览取最后一个 text 块即
 *  「最新消息」。无 text 块（thinking/tool/meta 等）→ 返回 null。 */
function extractStreamText(event: WorkerEvent): string | null {
  if (event.delta && event.stream_text) return event.stream_text;
  let text = '';
  for (const b of extractBlocks(event)) {
    if (b.role === 'assistant' && b.content) text = b.content;
  }
  return text || null;
}

function appendEvent(event: StreamEvent['event']): void {
  if (!event) return;
  const t = event.type;
  if (t === 'system' && event.subtype === 'init') return;
  if (t === 'result') return;

  for (const b of extractBlocks(event)) {
    const store = useSessionStore.getState();
    const messages = store.currentMessages;
    const nativeItemId = event.item_id !== undefined ? String(event.item_id) : undefined;
    const nativeIndex = nativeItemId
      ? messages.findIndex((message) => message.nativeItemId === nativeItemId)
      : -1;
    const lastIndex = messages.length - 1;
    const targetIndex = nativeIndex >= 0 ? nativeIndex : lastIndex;
    const target = targetIndex >= 0 ? messages[targetIndex] : undefined;
    if (event.replace && target?.role === b.role) {
      const updated = { ...target, content: b.content };
      useSessionStore.setState({
        currentMessages: messages.map((message, index) => index === targetIndex ? updated : message),
      });
      continue;
    }
    if (event.delta) {
      if (target?.role === b.role && (nativeIndex >= 0 || !nativeItemId)) {
        const content = event.replace ? b.content : target.content + b.content;
        const updated = {
          ...target,
          content,
          ...(nativeItemId ? { nativeItemId } : {}),
        };
        useSessionStore.setState({
          currentMessages: messages.map((message, index) => index === targetIndex ? updated : message),
        });
      } else {
        useSessionStore.getState().addMessage({
          role: b.role,
          content: b.content,
          ...(nativeItemId ? { nativeItemId } : {}),
        });
      }
      continue;
    }
    if (event.final && target?.role === b.role && target.content !== b.content) {
      // Replace the prefix accumulated from app-server deltas with the
      // authoritative completed item.  If it is unrelated, retain both.
      if (b.content.startsWith(target.content)) {
        const updated = {
          ...target,
          content: b.content,
          ...(nativeItemId ? { nativeItemId } : {}),
        };
        useSessionStore.setState({
          currentMessages: messages.map((message, index) => index === targetIndex ? updated : message),
        });
        continue;
      }
    }
    if (event.final && target?.role === b.role && target.content === b.content) {
      continue;
    }
    if (b.role === 'assistant') {
      useSessionStore.getState().addMessage({
        role: 'assistant', content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
      });
    } else if (b.role === 'thinking') {
      store.markUnread(b.content);
      useSessionStore.getState().addMessage({
        role: 'thinking', content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
      });
    } else if (b.role === 'tool') {
      store.markUnread(b.content);
      useSessionStore.getState().addMessage({
        role: 'tool', content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
      });
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
