import { useEffect } from 'react';
import { wsClient } from '@/services/ws';
import { isMockMode } from '@/demo/mockBackend';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useQueueStore } from '@/stores/queueStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import {
  useAdapterStore,
} from '@/stores/adapterStore';
import type { StreamEvent, WorkerEvent, Message, UserInputQuestion } from '@/types';
import { inheritMessageIdentity, rememberMessageIdentity } from '@/utils/messageIdentity';

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

function showCodexWarningToast(): boolean {
  return useAppSettingsStore.getState().notifications.codexWarningToast;
}

const queueRefreshTimers = new Map<string, ReturnType<typeof setTimeout>>();
function refreshAgentQueue(sessionId?: string): void {
  if (!sessionId || queueRefreshTimers.has(sessionId)) return;
  // Coalesce queue.item_* + worker.status bursts into one authoritative GET;
  // the queue store also suppresses overlapping requests across callers.
  queueRefreshTimers.set(sessionId, setTimeout(() => {
    queueRefreshTimers.delete(sessionId);
    void useQueueStore.getState().loadAgentQueue(sessionId);
  }, 0));
}

/**
 * Connects to WebSocket and routes events to Zustand stores.
 * Uses store.getState() for callbacks so React components re-render
 * when subscribed state changes.
 */
export function useWebSocket() {
  useEffect(() => {
    // Mock/no-backend demo (?mock=1): no WS connection. The initial list is
    // served by the intercepted fetch in mockBackend.
    if (isMockMode()) {
      useSessionStore.getState().loadSessions();
      return;
    }

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

    let lastInteractiveSyncGeneration: number | null = null;
    const syncInteractiveRequests = (): void => {
      // The backend keeps a worker-local snapshot of native prompts while the
      // JSON-RPC request is still open. Ask for it after every connection so a
      // browser refresh/reconnect does not strand the user at a hidden prompt.
      const generation = wsClient.getConnectionGeneration();
      if (generation > 0 && generation === lastInteractiveSyncGeneration) return;
      if (wsClient.sendInteractiveSync() && generation > 0) {
        lastInteractiveSyncGeneration = generation;
      }
    };

    const syncAuthoritativeSnapshot = (recovery = false): void => {
      const state = useSessionStore.getState();
      const sessionIds = [...new Set([
        ...(state.currentSessionId ? [state.currentSessionId] : []),
        ...Object.keys(state.liveStreamBuffers),
      ])];
      const cursor = typeof wsClient.getEventCursor === 'function'
        ? wsClient.getEventCursor()
        : { eventEpoch: null, eventSeq: 0 };
      wsClient.sendAuthoritativeResync({
        type: 'resync',
        ...(sessionIds.length ? { sessionIds } : {}),
        includeAllSessions: true,
        includeIdentity: true,
        ...(cursor.eventEpoch ? { eventEpoch: cursor.eventEpoch } : {}),
        eventSeq: cursor.eventSeq,
      }, recovery ? 'recovery' : 'initial');
    };

    const refreshAuthoritativeState = (): void => {
      // HTTP convergence is deliberately separate from the native-interaction
      // handshake.  resync_required may need a new snapshot on this socket,
      // but must not turn every refresh/snapshot callback into another replay.
      const sessionId = useSessionStore.getState().currentSessionId;
      void useSessionStore.getState().loadSessions();
      void useWorkerStore.getState().refresh();
      if (sessionId) {
        void useSessionStore.getState().refreshCurrentSessionHistory();
        void useQueueStore.getState().loadAgentQueue(sessionId);
      }
    };

    // Open handler — refresh sessions and restore live native prompts on connect
    unsubscribers.push(wsClient.on('open', () => {
      useSessionStore.getState().loadSessions();
      useWorkerStore.getState().refresh();
      useAdapterStore.getState().loadAdapterList();
      useAdapterStore.getState().loadConfig('cbc');
      // An open event is the completion point for a focus-triggered stale
      // reconnect. The snapshot callback finalizes buffered tasks before
      // refreshing history, so a missed terminal cannot leave a partial echo.
      const sessionId = useSessionStore.getState().currentSessionId;
      if (sessionId) {
        void useQueueStore.getState().loadAgentQueue(sessionId);
      }
      syncAuthoritativeSnapshot();
      syncInteractiveRequests();
    }));
    // If the singleton was already open before this hook mounted (HMR/route
    // remount), no new `open` event will arrive; sync explicitly as well.
    if (wsClient.isOpen) {
      syncAuthoritativeSnapshot();
      syncInteractiveRequests();
    }

    // A bounded sender can explicitly evict this dashboard when it cannot
    // preserve the live stream.  The event is not replay: converge from the
    // authoritative HTTP snapshots, then let the socket reconnect normally.
    unsubscribers.push(wsClient.on('resync_required', () => {
      // If the server did not close the socket (for example, the client
      // detected a live event cursor gap), request a fresh boundary on the
      // same connection.  A close-triggered resync simply returns false and
      // the normal open/reconnect path repeats this handshake.
      syncAuthoritativeSnapshot(true);
    }));
    unsubscribers.push(wsClient.on('server_epoch_changed', (e: StreamEvent) => {
      useSessionStore.getState().acceptServerEpoch(e.serverEpoch || e.eventEpoch);
      useWorkerStore.getState().acceptServerEpoch(e.serverEpoch || e.eventEpoch);
    }));
    unsubscribers.push(wsClient.on('resync.snapshot', (e: StreamEvent) => {
      useSessionStore.getState().acceptServerEpoch(e.serverEpoch || e.eventEpoch);
      useWorkerStore.getState().acceptServerEpoch(e.serverEpoch || e.eventEpoch);
      // A task may have completed while the physical socket was absent. The
      // snapshot's lastResult is the durable terminal boundary, even when no
      // worker.result frame can be replayed. Finalize its buffered partial
      // answer before any history merge, otherwise the id-less canonical final
      // and the old partial are rendered as two separate assistant messages.
      for (const [sessionId, detail] of Object.entries(e.details ?? {})) {
        const last = detail.lastResult as Record<string, unknown> | null | undefined;
        const buffer = useSessionStore.getState().liveStreamBuffers[sessionId];
        if (!buffer || !last || typeof last.taskSeq !== 'number') continue;
        if (last.taskSeq !== buffer.taskSeq) continue;
        useSessionStore.getState().reconcileWorkerResult(sessionId, {
          result: typeof last.result === 'string' ? last.result : '',
          status: typeof last.status === 'string' ? last.status : 'done',
          historyEpoch: typeof last.historyEpoch === 'string' ? last.historyEpoch : undefined,
          historyRevision: typeof last.historyRevision === 'number' ? last.historyRevision : undefined,
        }, {
          serverEpoch: e.serverEpoch || e.eventEpoch,
          workerId: typeof last.workerId === 'string' ? last.workerId : buffer.workerId,
          generation: typeof last.generation === 'number' ? last.generation : buffer.generation,
          taskSeq: last.taskSeq,
          taskId: typeof last.taskId === 'string' ? last.taskId : undefined,
        });
      }
      const knownSessionIds = [
        ...useSessionStore.getState().sessions.map((session) => session.id),
        ...(e.sessions ?? []).map((session) => session.id),
        ...(e.workers ?? [])
          .map((worker) => typeof worker.sessionId === 'string' ? worker.sessionId : null)
          .filter((id): id is string => Boolean(id)),
      ];
      useSessionStore.getState().beginUnscopedReplay(knownSessionIds);
      refreshAuthoritativeState();
      syncInteractiveRequests();
    }));

    // Browser lifecycle events (visibilitychange / pageshow / focus) are only
    // signals. The singleton connection layer remains the single owner of
    // reconnect/open synchronization. All three funnel through one debounced
    // recovery so a window switch cannot fan out into duplicate request bursts:
    //   - signals within RECOVERY_DEBOUNCE_MS collapse into a single run;
    //   - if the socket is still fresh and authoritative state was refreshed
    //     within RECOVERY_COALESCE_MS, the duplicate signal is absorbed — the
    //     live connection already keeps the UI current, so there is nothing new
    //     to fetch.
    // The stale-socket path is always preserved: the freshness check runs for
    // every recovery, a stale socket is always reconnected (whose open handler
    // performs the authoritative refresh), and absorption only happens on a
    // fresh socket — so a duplicate signal never loses disconnect recovery.
    // A hidden page is a special case: Chromium may freeze the JS event loop
    // while the TCP/WebSocket object still reports OPEN. On resume, its last
    // activity timestamp can therefore look fresh even though a resync sent
    // through that half-open transport will never produce a response. A real
    // hidden→visible transition forces one new physical socket; focus-only
    // signals still use the cheaper freshness path.
    const RECOVERY_DEBOUNCE_MS = 100;
    const RECOVERY_COALESCE_MS = 500;
    let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
    let lastRecoveryAt = 0;
    let lastRecoveryReplacedTransport = false;
    // Track transitions observed by this mounted dashboard. If the app is
    // first mounted in a background tab, its initial open/replay path is the
    // appropriate owner; the next observed hidden→visible transition still
    // receives the forced transport recovery.
    let hiddenSince: number | null = null;
    let forceTransportRecovery = false;

    const isConnectionFresh = (): boolean =>
      typeof wsClient.isConnectionFresh === 'function'
        ? wsClient.isConnectionFresh()
        : wsClient.isOpen;

    const drainRecovery = (): void => {
      const forceReconnect = forceTransportRecovery;
      forceTransportRecovery = false;
      const fresh = isConnectionFresh();
      const elapsed = Date.now() - lastRecoveryAt;
      if (
        fresh
        && lastRecoveryAt > 0
        && elapsed < RECOVERY_COALESCE_MS
        && (!forceReconnect || lastRecoveryReplacedTransport)
      ) {
        // Same resume already refreshed authoritative state on a live socket —
        // absorb the duplicate instead of issuing a second request burst.
        return;
      }
      lastRecoveryAt = Date.now();
      if (forceReconnect || !fresh) {
        lastRecoveryReplacedTransport = true;
        // reconnect() preserves all subscribers and its open handler above
        // performs the authoritative refresh once the new socket is live.
        if (typeof wsClient.reconnect === 'function') wsClient.reconnect();
        else wsClient.connect();
        return;
      }
      lastRecoveryReplacedTransport = false;
      // A buffered task needs its durable terminal boundary before history
      // can converge. Without a partial stream, HTTP alone is sufficient.
      if (Object.keys(useSessionStore.getState().liveStreamBuffers).length > 0) {
        syncAuthoritativeSnapshot(true);
      } else {
        refreshAuthoritativeState();
      }
    };

    const recover = (forceReconnect = false): void => {
      if (forceReconnect) forceTransportRecovery = true;
      if (recoveryTimer) clearTimeout(recoveryTimer);
      recoveryTimer = setTimeout(() => {
        recoveryTimer = null;
        drainRecovery();
      }, RECOVERY_DEBOUNCE_MS);
    };
    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden') {
        hiddenSince ??= Date.now();
        return;
      }
      if (document.visibilityState === 'visible') {
        const resumedFromBackground = hiddenSince !== null;
        hiddenSince = null;
        recover(resumedFromBackground);
      }
    };
    const onPageShow = (event: PageTransitionEvent): void => {
      // `persisted` covers a bfcache restore where visibilitychange may not
      // arrive while the document is frozen. An ordinary initial pageshow is
      // intentionally cheap and remains covered by the normal open path.
      recover(event.persisted || hiddenSince !== null);
    };
    const onFocus = (): void => recover();
    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pageshow', onPageShow);
    window.addEventListener('focus', onFocus);

    // Queue events are convergence hints. The server snapshot remains the
    // business source of truth, so a stale or duplicated event cannot create
    // a second local queue item.
    for (const eventType of ['queue.item_added', 'queue.item_updated', 'queue.item_removed', 'queue.item_delivered', 'queue.snapshot']) {
      unsubscribers.push(wsClient.on(eventType, (e: StreamEvent) => {
        useQueueStore.getState().applyQueueEvent(e);
        refreshAgentQueue(e.sessionId);
      }));
    }
    unsubscribers.push(wsClient.on('queue.item_delivered', (e: StreamEvent) => {
      if (!e.sessionId || !Array.isArray(e.messages)) return;
      useSessionStore.getState().appendDeliveredMessages(e.sessionId, e.messages);
    }));

    // Worker spawned / restarted / reconfigured
    unsubscribers.push(wsClient.on('worker.spawned', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e)) return;
      if (e.sessionId) {
        clearNativeTurnAliases(e.sessionId);
        useSessionStore.getState().clearLiveStream(e.sessionId);
      }
      clearInteractiveRequests(e.sessionId);
      if (!handleWorkerUpdate(e, 'idle')) return;
      refreshAgentQueue(e.sessionId);
    }));
    unsubscribers.push(wsClient.on('worker.restarted', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e)) return;
      if (e.sessionId) {
        clearNativeTurnAliases(e.sessionId);
        useSessionStore.getState().clearLiveStream(e.sessionId);
      }
      clearInteractiveRequests(e.sessionId);
      if (!handleWorkerUpdate(e, 'idle')) return;
      refreshAgentQueue(e.sessionId);
    }));
    unsubscribers.push(wsClient.on('worker.reconfigured', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e)) return;
      if (e.sessionId) {
        clearNativeTurnAliases(e.sessionId);
        useSessionStore.getState().clearLiveStream(e.sessionId);
      }
      clearInteractiveRequests(e.sessionId);
      if (!handleWorkerUpdate(e, 'idle')) return;
      refreshAgentQueue(e.sessionId);
    }));

    // Worker destroyed / crashed — 除就地更新状态点外触发防抖全量兜底：
    // 崩溃/销毁是低频事件，且流式片段已逐块落盘，刷新让列表吸收已持久化的
    // 部分回复（通过防抖刷新会话列表）。
    unsubscribers.push(wsClient.on('worker.destroyed', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e, true)) return;
      if (e.sessionId) clearNativeTurnAliases(e.sessionId);
      if (e.sessionId) cancelStreamPreview(e.sessionId);
      clearInteractiveRequests(e.sessionId);
      if (!handleWorkerUpdate(e, null, true)) return;
      refreshAgentQueue(e.sessionId);
      // A destroyed worker may be the only event after a user queued text
      // while it was running.  Do not wait for a stale idle status or a later
      // refresh: the durable session route can accept the message offline.
      useQueueStore.getState().flush(true);
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('worker.crashed', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e, true)) return;
      if (e.sessionId) clearNativeTurnAliases(e.sessionId);
      if (e.sessionId) cancelStreamPreview(e.sessionId);
      clearInteractiveRequests(e.sessionId);
      if (!handleWorkerUpdate(e, null, true)) return;
      refreshAgentQueue(e.sessionId);
      useQueueStore.getState().flush(true);
      scheduleRefreshSessions();
    }));

    // Worker status update
    unsubscribers.push(wsClient.on('worker.status', (e: StreamEvent) => {
      if (!handleWorkerUpdate(e, e.status ?? 'idle')) return;
      // A task/report remains in the durable server queue until the provider
      // hand-off boundary (stream write+drain, or one-shot process creation).
      // Refresh on this transition so the panel converges after hand-off while
      // a queued item appended during the running turn remains visible.
      if (e.status === 'running') refreshAgentQueue(e.sessionId);
      // agent 编排消息实时同步：meta-agent 的 worker_send（////by agent 前缀）
      // 注入的 user 消息只在服务端 s.history 落盘，WS 从不广播（只广播 assistant
      // 回复的 worker.stream / 完成的 worker.result），前端对自己的发送有乐观追加、
      // 对 agent 注入没有 → 切走再切回才显示。任务开始 running 时（source 已带
      // 进广播）拉取历史把缺的 user 消息并入 currentMessages；首次快照若早于
      // 注入落盘则由 syncAgentInjectedMessage 做短暂重试。
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
      if (!isCurrentWorkerEvent(e)) return;
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
      if (e.event.type === 'codex.rate_limits' && e.event.rate_limits) {
        useWorkerStore.getState().updateNativeRateLimits(
          e.sessionId,
          e.workerId,
          e.event.rate_limits,
        );
      }
      if (e.event.type === 'codex.mcp_status' && e.sessionId === useSessionStore.getState().currentSessionId) {
        const status = e.event.mcp_status;
        if (
          status &&
          String(status.status || '').toLowerCase() === 'failed' &&
          showCodexWarningToast()
        ) {
          const name = String(status.name || 'server');
          const detail = status.error || status.failureReason || 'startup failed';
          useUIStore.getState().showToast(`Codex MCP ${name}: ${detail}`, 'error');
        }
      }
      if (e.event.type === 'codex.model_rerouted' && e.sessionId === useSessionStore.getState().currentSessionId) {
        const rerouted = e.event.model_rerouted;
        if (rerouted && showCodexWarningToast()) {
          const from = String(rerouted.fromModel || 'configured model');
          const to = String(rerouted.toModel || 'fallback model');
          const reason = rerouted.reason ? ` (${String(rerouted.reason)})` : '';
          useUIStore.getState().showToast(`Codex switched model: ${from} → ${to}${reason}`);
        }
      }
      if (e.event.type === 'codex.turn_error' && e.sessionId === useSessionStore.getState().currentSessionId) {
        if (showCodexWarningToast()) {
          const detail = e.event.error_text || 'Codex turn failed';
          useUIStore.getState().showToast(`Codex: ${detail}`, 'error');
        }
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
        e.event.type === 'claude.permission_resolved' &&
        e.sessionId &&
        e.event.request_id !== undefined
      ) {
        useUIStore.getState().removeApprovalRequest(e.sessionId, e.event.request_id);
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
      // Persist a transient live suffix for every session. The store projects
      // it into currentMessages only when that session is selected, so A→B→A
      // does not lose deltas while the selected-session viewport changes.
      appendEvent(e.sessionId, e.event, e);
      // 卡片预览：所有 session 就地 throttle 更新 lastMessage（无文本事件跳过）
      const text = extractStreamText(e.event);
      if (text) throttledLastMessageUpdate(e.sessionId, text);
    }));

    // Result event
    unsubscribers.push(wsClient.on('worker.result', (e: StreamEvent) => {
      if (!isCurrentWorkerEvent(e)) {
        // A terminal event attributed to an older worker generation is dropped
        // (a late result must not clear its replacement), but the drop must not
        // silently strand the card's status dot either: fall back to the
        // authoritative list snapshot so the indicator converges.
        scheduleRefreshSessions();
        return;
      }
      if (!e.sessionId) return;
      const sessionId = e.sessionId;
      const notification = e.notification as { title?: string; body?: string; browser?: boolean } | undefined;
      // Permission is explicitly requested from msgBridge; completion events
      // never prompt in the background.
      if (notification?.browser && typeof window !== 'undefined' && 'Notification' in window
        && Notification.permission === 'granted') {
        new Notification(notification.title || 'Pan:', { body: notification.body || '' });
      }
      const sessionStore = useSessionStore.getState();
      clearInteractiveRequests(sessionId);
      const reconciled = sessionStore.reconcileWorkerResult(sessionId, e, {
        serverEpoch: e.serverEpoch || e.eventEpoch,
        workerId: e.workerId,
        generation: e.generation,
        taskSeq: e.taskSeq,
        taskId: typeof e.taskId === 'string' ? e.taskId : undefined,
      });
      if (!reconciled) {
        scheduleRefreshSessions();
        return;
      }
      const afterReconcile = useSessionStore.getState();
      if (sessionId === afterReconcile.currentSessionId) {
        const status = e.status === 'error'
          ? 'error'
          : e.status === 'cancelled' || e.cancelled
            ? 'cancelled'
            : 'done';
        const resultKey = e.taskSeq === undefined
          ? undefined
          : `worker.result:${e.sessionId}:${e.taskSeq}`;
        const alreadyShown = resultKey
          ? afterReconcile.currentMessages.some((message) => message.nativeItemId === resultKey)
          : false;
        if (!alreadyShown) {
          afterReconcile.addMessage({
            role: 'system',
            content: `[${status.toUpperCase()}] Task completed`,
            ...(resultKey ? { nativeItemId: resultKey } : {}),
          });
        }
      }
      // 流式预览节流：result 为最终 lastMessage，先清掉该 session 未 flush 的
      // pending 文本与尾随 timer，防止其迟到覆盖 result（applyResultToSession
      // 紧接着以 result 写入 lastMessage）。
      cancelStreamPreview(sessionId);
      clearNativeTurnAliases(sessionId);
      handleWorkerUpdate(e, 'idle');
      refreshAgentQueue(e.sessionId);
      // 就地更新该 session 卡片（lastResult + 结果文本追加 + historyTotal），
      // 不等 300ms 防抖全量兜底即可让「最后消息 summary」立即最新。
      // 实时刷新侧边栏列表（lastResult / historyTotal / workerStatus 等卡片
      // 数据）。防抖合并为单次全量抓取：既避免每个任务完成都触发整列表重渲染
      // 造成的滞涩，也避开了后端「done→idle」的瞬态窗口（否则快照可能把已置为
      // idle 的指示灯回退成灰色）。指示灯本身已由 handleWorkerUpdate 同步更新。
      scheduleRefreshSessions();
    }));

    // Session events — created/deleted 也需刷新列表（否则新 session 不出现、
    // 删除的残留，需手动刷新才更新）。同样防抖合并。
    const applySessionEvent = (e: StreamEvent): void => {
      if (!e.sessionId) return;
      const patch: Partial<import('@/types').Session> = e.session
        ? { ...e.session }
        : {};
      if (e.type === 'session.renamed') {
        const nextName = e.name ?? e.newName;
        if (nextName) patch.name = nextName;
      }
      if (Object.keys(patch).length > 0) {
        useSessionStore.getState().updateSession(e.sessionId, patch, true);
      }
    };
    unsubscribers.push(wsClient.on('session.renamed', (e: StreamEvent) => {
      applySessionEvent(e);
      scheduleRefreshSessions();
    }));
    unsubscribers.push(wsClient.on('session.updated', (e: StreamEvent) => {
      applySessionEvent(e);
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
    // Cold-start projection repair is an eventual metadata-only operation.
    // Its completion event is the explicit refresh trigger for cards that
    // initially rendered an unknown count/preview.
    unsubscribers.push(wsClient.on('session.summaryBackfillCompleted', () => {
      scheduleRefreshSessions();
    }));
    // Workspaces: metadata is durable server state — refetch on any change
    // (the payload is tiny). Membership snapshots are applied verbatim so the
    // rail counts, the card badges and the active scope converge without a
    // full session refetch.
    unsubscribers.push(wsClient.on('workspace.created', () => {
      void useWorkspaceStore.getState().loadWorkspaces();
    }));
    unsubscribers.push(wsClient.on('workspace.updated', () => {
      void useWorkspaceStore.getState().loadWorkspaces();
    }));
    unsubscribers.push(wsClient.on('workspace.orderUpdated', () => {
      void useWorkspaceStore.getState().loadWorkspaces();
    }));
    unsubscribers.push(wsClient.on('workspace.deleted', () => {
      void useWorkspaceStore.getState().loadWorkspaces();
    }));
    unsubscribers.push(wsClient.on('workspace.membershipUpdated', (e: StreamEvent) => {
      if (e.workspaceId && Array.isArray(e.sessionIds)) {
        useWorkspaceStore.getState().applyMembership(e.workspaceId, e.sessionIds);
      }
    }));
    unsubscribers.push(wsClient.on('session.workspaceUpdated', (e: StreamEvent) => {
      if (e.sessionId && Array.isArray(e.workspaceIds)) {
        useWorkspaceStore.getState().applySessionMembership(e.sessionId, e.workspaceIds);
      }
    }));
    // Custom session order persisted (POST /api/sessions/order broadcast). A
    // debounced full-list refresh re-reads the server snapshot; in custom sort
    // mode loadSessions aligns customOrder with the authoritative order, so a
    // reorder from another client is reflected here too.
    unsubscribers.push(wsClient.on('session.orderUpdated', () => {
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
      for (const timer of queueRefreshTimers.values()) clearTimeout(timer);
      queueRefreshTimers.clear();
      if (recoveryTimer) clearTimeout(recoveryTimer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('focus', onFocus);
    };
  }, []);
}

function isCurrentWorkerEvent(e: StreamEvent, terminal = false): boolean {
  if (!e.sessionId) return false;
  const known = useWorkerStore.getState().workers[e.sessionId];
  if (
    known &&
    e.generation !== undefined &&
    known.generation !== undefined &&
    e.generation < known.generation
  ) return false;
  // A late terminal event from an older worker must not clear the replacement.
  if (terminal && known?.id && e.workerId && known.id !== e.workerId) return false;
  return true;
}

function handleWorkerUpdate(
  e: StreamEvent,
  status: string | null,
  terminal = false,
): boolean {
  if (!e.sessionId) return false;
  if (!isCurrentWorkerEvent(e, terminal)) return false;
  const sessStore = useSessionStore.getState();
  const accepted = sessStore.applyWorkerStatus(
    e.sessionId,
    status,
    {
      serverEpoch: e.serverEpoch || e.eventEpoch,
      workerId: e.workerId,
      generation: e.generation,
      taskSeq: e.taskSeq,
      taskId: typeof e.taskId === 'string' ? e.taskId : undefined,
    },
    terminal,
  );
  if (!accepted) return false;
  const workerStore = useWorkerStore.getState();
  workerStore.updateWorker(e.sessionId, e.workerId ?? null, status, e.generation, terminal);

  // 队列自动发送：worker 变 idle 且属于当前 session → 发送队首 1 条
  // （发送后 worker 变 queued/running，不再是 idle，天然防重复；result→idle 再取下一条）
  if (
    status === 'idle' &&
    e.sessionId === useSessionStore.getState().currentSessionId
  ) {
    useQueueStore.getState().flush();
  }
  return true;
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
interface ExtractedBlock {
  role: string;
  content: string;
  /** Provider-supplied block identity when one exists. */
  blockId?: string;
}

function extractBlocks(event: WorkerEvent): ExtractedBlock[] {
  const blocks: ExtractedBlock[] = [];
  if (event.type === 'codex.plan' && Array.isArray(event.plan)) {
    const statusMark: Record<string, string> = {
      completed: '[x]',
      inProgress: '[>]',
      pending: '[ ]',
    };
    const lines = event.plan.map((step) => {
      const text = String(step.step ?? '').trim();
      const mark = statusMark[String(step.status ?? '')] ?? '[ ]';
      return `${mark} ${text}`.trimEnd();
    }).filter(Boolean);
    const explanation = typeof event.explanation === 'string'
      ? event.explanation.trim()
      : '';
    const content = [explanation, lines.join('\n')].filter(Boolean).join('\n\n');
    return content ? [{ role: 'thinking', content }] : blocks;
  }
  if (event.type === 'codex.diff' && typeof event.diff === 'string' && event.diff) {
    return [{
      role: 'tool',
      content: `CodexDiff(${pyJsonDumps({ diff: event.diff })})`,
    }];
  }
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
    // Delta whitespace is part of the assistant content. Trimming here
    // corrupts Markdown across chunk boundaries (for example, the blank line
    // between a heading and the next paragraph), even though the same delta
    // is already being accumulated into the single canonical message.
    const text = String(part[ptype] ?? '');
    if (text) {
      blocks.push({
        role: ptype === 'think' ? 'thinking' : 'assistant',
        content: text,
      });
    }
  }

  // Codex can expose a tool delta only as the cumulative stream_text while
  // the final envelope carries the structured tool_use block.  Keep the
  // delta visible and let the final item identity replace this placeholder.
  if (blocks.length === 0 && event.delta && typeof event.stream_text === 'string'
      && (event.type === 'assistant' || event.role === 'assistant')) {
    blocks.push({ role: 'assistant', content: event.stream_text });
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

// App-server can use a different item id for the delta and item/completed
// notifications. Keep that transient alias outside Message so it does not
// become persisted history, while retaining the first item's position.
const nativeTurnItemAliases = new Map<string, string>();
/** Whether the first assistant item in a turn arrived as a delta or completed. */
const nativeTurnAliasOrigins = new Map<string, 'delta' | 'completed'>();
// An alias is safe for a late delta only after this turn has completed an
// assistant item. Before that point, a new item id must remain a new message:
// multiple native items can legitimately interleave within one turn.
const nativeTurnCompleted = new Set<string>();

function appendEventToMessages(
  sessionId: string,
  event: StreamEvent['event'],
  initialMessages: Message[],
  scope?: Pick<StreamEvent, 'workerId' | 'generation' | 'taskSeq' | 'taskId'>,
): Message[] {
  if (!event) return initialMessages;
  const t = event.type;
  if (t === 'system' && event.subtype === 'init') return initialMessages;
  if (t === 'result') return initialMessages;
  let messages = initialMessages;
  const blocks = extractBlocks(event);
  // A single native event may contain thinking, text, and tool blocks for the
  // same item. Each block must consume a different existing row; otherwise a
  // repeated findIndex() updates the first row and later blocks drift into it.
  // The ordinary one-block path avoids allocating this Set.
  const usedIndexes = blocks.length > 1 ? new Set<number>() : undefined;
  const cumulativeStreamText = event.delta
    && blocks.length === 1
    && typeof event.stream_text === 'string'
    ? event.stream_text
    : undefined;

  // Stream arrival order is the display order: the first event for a native
  // item reserves its position, and later deltas/completion replace that item
  // in place. Thinking/tool blocks therefore stay before or after content
  // according to the adapter's event semantics, never according to the
  // render timing or the current viewport position.
  for (const [blockIndex, b] of blocks.entries()) {
    // A Codex assistant reply is one logical message for the whole turn. The
    // native bridge can expose different item ids for its delta and completed
    // notifications (and an interleaved tool can become the last message), so
    // use the native item id as the canonical identity. The turn id is only a
    // transient alias for bridges that change ids between delta and completed.
    const itemId = event.item_id !== undefined ? String(event.item_id) : undefined;
    const eventTurnId = event.turn_id !== undefined
      ? String(event.turn_id)
      : undefined;
    const turnId = b.role === 'assistant' ? eventTurnId : undefined;
    const scopeSuffix = eventTurnId && scope?.taskSeq !== undefined
      ? `:seq:${scope.taskSeq}`
      : eventTurnId && scope?.taskId
        ? `:task:${scope.taskId}`
        : '';
    const turnKey = eventTurnId ? `${sessionId}${scopeSuffix}:${eventTurnId}` : undefined;
    const aliasKey = turnId ? turnKey : undefined;
    const aliasedItemId = aliasKey ? nativeTurnItemAliases.get(aliasKey) : undefined;
    const aliasOrigin = aliasKey ? nativeTurnAliasOrigins.get(aliasKey) : undefined;
    const completedBeforeEvent = Boolean(aliasKey && nativeTurnCompleted.has(aliasKey));
    const nativeItemId = itemId ?? (aliasedItemId ?? (turnId ? `turn:${turnId}` : undefined));
    const blockId = b.blockId
      ?? (itemId && blocks.length > 1 ? `${itemId}:block:${blockIndex}` : undefined);
    if (aliasKey && itemId && !aliasedItemId) {
      nativeTurnItemAliases.set(aliasKey, itemId);
      nativeTurnAliasOrigins.set(aliasKey, event.delta ? 'delta' : 'completed');
    }

    // App-server may start the final assistant text before the command item
    // completes.  The command completion is the durable ordering boundary:
    // history will contain [tool, assistant], while the live buffer has so far
    // reserved [assistant, tool].  Move only that still-open native assistant
    // item behind the tool.  A completed assistant is protected by
    // nativeTurnCompleted, and a tool-first turn has an alias pointing at its
    // tool row rather than an assistant, so neither case is reordered.
    if (b.role === 'tool' && turnKey && !usedIndexes
        && !nativeTurnCompleted.has(turnKey)) {
      const openItemId = nativeTurnItemAliases.get(turnKey);
      const openIndex = openItemId === undefined ? -1 : messages.findIndex((message) =>
        message.role === 'assistant' && message.nativeItemId === openItemId);
      if (openIndex >= 0) {
        const openMessage = messages[openIndex]!;
        messages = [
          ...messages.slice(0, openIndex),
          ...messages.slice(openIndex + 1),
          openMessage,
        ];
      }
    }

    if (event.delta && cumulativeStreamText !== undefined && completedBeforeEvent
        && itemId && aliasedItemId && itemId !== aliasedItemId
        && messages.some((message) => message.role === b.role
          && message.nativeItemId === aliasedItemId
          && message.content.startsWith(cumulativeStreamText))) {
      // A late cumulative prefix of a completed item is an echo. Ignore the
      // echo without claiming its new item id: if that id later diverges, it
      // can still become a distinct assistant item in its proper position.
      continue;
    }

    // A turn can contain several completed assistant items separated by tools.
    // Cumulative stream_text belongs to its explicit item_id, so it must not
    // update the first completed item just because both share a turn_id. Keep
    // the narrow bridges for delta(A) → completed(B) and completed(B) → a
    // late non-cumulative delta(A) from older adapters.
    const allowTurnAlias = Boolean(aliasedItemId && itemId !== aliasedItemId
      && ((event.final && aliasOrigin === 'delta' && !completedBeforeEvent)
        || (event.delta && aliasOrigin === 'completed' && completedBeforeEvent
          && cumulativeStreamText === undefined)));
    const nativeIds = [
      nativeItemId,
      ...(allowTurnAlias ? [aliasedItemId!] : []),
      ...(turnId && nativeItemId !== `turn:${turnId}` ? [`turn:${turnId}`] : []),
    ].filter((id): id is string => Boolean(id));
    let nativeIndex = -1;
    if (blockId) {
      nativeIndex = usedIndexes
        ? messages.findIndex((message, index) =>
            !usedIndexes.has(index)
            && (message.role === b.role || event.final || event.replace)
            && message.blockId === blockId)
        : messages.findIndex((message) =>
            (message.role === b.role || event.final || event.replace)
            && message.blockId === blockId);
    }
    if (nativeIndex < 0 && nativeIds.length > 0) {
      // Exact item identity takes precedence over the turn's compatibility
      // aliases, regardless of row order. A tool delta can have claimed the
      // first assistant alias before the real answer item began streaming.
      for (const id of nativeIds) {
        nativeIndex = messages.findIndex((message, index) =>
          !usedIndexes?.has(index)
          && (message.role === b.role || event.final || event.replace)
          && message.nativeItemId === id);
        if (nativeIndex >= 0) break;
      }
    }
    if (nativeIndex < 0 && usedIndexes && event.final && !nativeItemId && !blockId) {
      // Claude stream-json deltas do not carry an item id, while its final
      // assistant envelope can add thinking/tool blocks before repeating the
      // complete text. Rebind that final text only to one same-role live row
      // in this task buffer whose body is a prefix of the final body (or the
      // reverse for providers that corrected a suffix). This stays local to
      // the current event/task and never dedupes equal text across turns.
      const untaggedFinalMatches = messages.flatMap((candidate, index) =>
        !usedIndexes.has(index)
          && candidate.role === b.role
          && (candidate.content.startsWith(b.content) || b.content.startsWith(candidate.content))
          ? [index]
          : [],
      );
      if (untaggedFinalMatches.length === 1) nativeIndex = untaggedFinalMatches[0]!;
    }
    const lastIndex = messages.length - 1;
    // A native id is an explicit target. Falling back to the last message here
    // lets an interleaved later item be replaced by an earlier item's update.
    // Untagged adapter events retain the legacy last-message behavior.
    const targetIndex = nativeIndex >= 0 || nativeItemId
      ? nativeIndex
      : usedIndexes?.has(lastIndex) ? -1 : lastIndex;
    const target = targetIndex >= 0 ? messages[targetIndex] : undefined;
    const aliasOnlyMatch = Boolean(
      itemId && aliasedItemId && target?.nativeItemId === aliasedItemId && itemId !== aliasedItemId,
    );
    if (event.replace && target && (target.role === b.role || nativeIndex >= 0)) {
      const updated = { ...target, role: b.role, content: b.content };
      if (blockId && !updated.blockId) updated.blockId = blockId;
      inheritMessageIdentity(updated, target);
      messages = messages.map((message, index) => index === targetIndex ? updated : message);
      if (usedIndexes) usedIndexes.add(targetIndex);
      continue;
    }
    if (event.delta) {
      if (target?.role === b.role && (nativeIndex >= 0 || !nativeItemId)) {
        const cumulativeContent = cumulativeStreamText !== undefined
          && (b.role === 'assistant' || b.role === 'thinking')
          ? cumulativeStreamText
          : undefined;
        const content = event.replace
          ? b.content
          : cumulativeContent !== undefined
            ? cumulativeContent
            : target.content + b.content;
        const updated = {
          ...target,
          content,
          ...(nativeItemId && !target?.nativeItemId ? { nativeItemId } : {}),
          ...(blockId && !target?.blockId ? { blockId } : {}),
        };
        inheritMessageIdentity(updated, target);
        messages = messages.map((message, index) => index === targetIndex ? updated : message);
        if (usedIndexes) usedIndexes.add(targetIndex);
      } else {
        const message = {
          role: b.role,
          // The first frame this browser sees may be a coalesced/reconnected
          // delta from the middle of an item. Its cumulative body includes
          // the prefix that never arrived on this socket.
          content: cumulativeStreamText !== undefined
            && (b.role === 'assistant' || b.role === 'thinking')
            ? cumulativeStreamText : b.content,
          ...(nativeItemId ? { nativeItemId } : {}),
          ...(blockId ? { blockId } : {}),
        };
        rememberMessageIdentity(message);
        messages = [...messages, message];
        if (usedIndexes) usedIndexes.add(messages.length - 1);
      }
      continue;
    }
    if (aliasKey && event.final && b.role === 'assistant') {
      nativeTurnCompleted.add(aliasKey);
    }
    if (event.final && target && (target.role === b.role || nativeIndex >= 0)
        && target.content !== b.content) {
      // Replace the prefix accumulated from app-server deltas with the
      // authoritative completed item.  If it is unrelated, retain both.
      if ((!aliasOnlyMatch && nativeIndex >= 0) || b.content.startsWith(target.content)) {
        const updated = {
          ...target,
          role: b.role,
          content: b.content,
          ...(nativeItemId && !target.nativeItemId ? { nativeItemId } : {}),
        };
        inheritMessageIdentity(updated, target);
        messages = messages.map((message, index) => index === targetIndex ? updated : message);
        if (usedIndexes) usedIndexes.add(targetIndex);
        continue;
      }
    }
    if (event.final && target && (target.role === b.role || nativeIndex >= 0)
        && target.content === b.content) {
      if (target.role !== b.role) {
        const updated = { ...target, role: b.role };
        inheritMessageIdentity(updated, target);
        messages = messages.map((message, index) => index === targetIndex ? updated : message);
      }
      if (usedIndexes) usedIndexes.add(targetIndex);
      continue;
    }
    // Some adapters repeat a complete item envelope without setting either
    // delta or final.  Native identity makes this an idempotent replay, not a
    // second message.  Do not use body text globally: this check is scoped to
    // the explicit item/block target above.
    if (!event.delta && !event.final && !event.replace
        && nativeIds.length > 0
        && targetIndex >= 0 && target?.role === b.role
        && target.content === b.content) {
      if (usedIndexes) usedIndexes.add(targetIndex);
      continue;
    }
    if (b.role === 'assistant') {
      const message = {
        role: 'assistant',
        content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
        ...(blockId ? { blockId } : {}),
      };
      rememberMessageIdentity(message);
      messages = [...messages, message];
      if (usedIndexes) usedIndexes.add(messages.length - 1);
    } else if (b.role === 'thinking') {
      const message = {
        role: 'thinking',
        content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
        ...(blockId ? { blockId } : {}),
      };
      rememberMessageIdentity(message);
      messages = [...messages, message];
      if (usedIndexes) usedIndexes.add(messages.length - 1);
    } else if (b.role === 'tool') {
      const message = {
        role: 'tool',
        content: b.content,
        ...(nativeItemId ? { nativeItemId } : {}),
        ...(blockId ? { blockId } : {}),
      };
      rememberMessageIdentity(message);
      messages = [...messages, message];
      if (usedIndexes) usedIndexes.add(messages.length - 1);
    }
  }
  if (event.final && usedIndexes && usedIndexes.size === blocks.length) {
    // A multi-block final envelope defines the canonical order of those
    // blocks. Reconcile first so an already-streamed id-less assistant row
    // keeps its React identity, then move the matched block run into envelope
    // order (for Claude: thinking → tool → assistant).
    const targetIndexes = [...usedIndexes];
    const orderedTargets = [...new Set(targetIndexes)].sort((a, b) => a - b);
    const canReorder = orderedTargets.length === targetIndexes.length
      && orderedTargets.length > 1
      && orderedTargets.at(-1)! - orderedTargets[0]! + 1 === orderedTargets.length
      && targetIndexes.some((index, slot) => index !== orderedTargets[0]! + slot);
    if (canReorder) {
      const first = orderedTargets[0]!;
      const targetSet = new Set(orderedTargets);
      const blockRows = targetIndexes.map((index) => messages[index]!);
      const withoutBlocks = messages.filter((_message, index) => !targetSet.has(index));
      const beforeCount = messages
        .slice(0, first)
        .filter((_message, index) => !targetSet.has(index))
        .length;
      messages = [
        ...withoutBlocks.slice(0, beforeCount),
        ...blockRows,
        ...withoutBlocks.slice(beforeCount),
      ];
    }
  }
  return messages;
}

function appendEvent(sessionId: string, event: StreamEvent['event'], meta: StreamEvent): boolean {
  if (!event) return false;
  const store = useSessionStore.getState();
  const scope = {
    serverEpoch: meta.serverEpoch || meta.eventEpoch,
    workerId: meta.workerId,
    generation: meta.generation,
    taskSeq: typeof meta.taskSeq === 'number' ? meta.taskSeq : undefined,
    taskId: typeof meta.taskId === 'string' && meta.taskId ? meta.taskId : undefined,
    replayed: meta.replayed === true,
    turnId: event.turn_id,
    itemId: event.item_id !== undefined ? String(event.item_id) : undefined,
    streamText: event.delta && typeof event.stream_text === 'string'
      ? event.stream_text
      : undefined,
  };
  // Do this check before resolving native ids/aliases.  A stale frame must
  // not mutate the transient alias table and then make a later native item
  // look like a current task, even when applyLiveStream would reject it.
  if (!store.canApplyLiveStream(sessionId, scope)) return false;
  const before = store.getLiveStreamMessages(sessionId);
  // The native alias table is only a transient accelerator. If the durable
  // live buffer is gone (result/restart or a fresh client state), an alias
  // from an earlier turn must not attach a new event to that old turn.
  if (before.length === 0) clearNativeTurnAliases(sessionId);
  const messages = appendEventToMessages(sessionId, event, before, meta);
  const accepted = store.applyLiveStream(sessionId, messages, scope);
  if (accepted) {
    for (const block of extractBlocks(event)) {
      if (block.role === 'thinking' || block.role === 'tool') {
        store.markUnread(sessionId, block.content);
      }
    }
  }
  return accepted;
}

function clearNativeTurnAliases(sessionId: string): void {
  const prefix = `${sessionId}:`;
  for (const key of nativeTurnItemAliases.keys()) {
    if (key.startsWith(prefix)) nativeTurnItemAliases.delete(key);
  }
  for (const key of nativeTurnAliasOrigins.keys()) {
    if (key.startsWith(prefix)) nativeTurnAliasOrigins.delete(key);
  }
  for (const key of nativeTurnCompleted) {
    if (key.startsWith(prefix)) nativeTurnCompleted.delete(key);
  }
}

// ── Agent 注入消息实时同步 ──
// meta-agent 的 worker_send / 订阅报告会把 user 消息写进服务端 s.history，但 WS
// 只广播 assistant 回复（worker.stream）与完成（worker.result）——user 消息前端
// 无实时来源。worker.status(running) 可能先于 history 快照可读，故在首次未合并
// 到新消息时做有界重试，覆盖落盘与 GET 的短暂竞态，同时避免无限轮询。
let agentSyncInFlight = false;
const AGENT_SYNC_RETRY_DELAYS_MS = [50, 150, 500] as const;

function syncAgentInjectedMessage(): void {
  const sid = useSessionStore.getState().currentSessionId;
  if (!sid || agentSyncInFlight) return;
  agentSyncInFlight = true;
  const sync = async (): Promise<void> => {
    for (let attempt = 0; attempt <= AGENT_SYNC_RETRY_DELAYS_MS.length; attempt++) {
      if (attempt > 0) {
        await new Promise<void>((resolve) => {
          setTimeout(resolve, AGENT_SYNC_RETRY_DELAYS_MS[attempt - 1]);
        });
      }

      const store = useSessionStore.getState();
      if (store.currentSessionId !== sid) return; // 用户已切走，丢弃过期结果

      try {
        const before = store.currentMessages;
        // Use the store's guarded history reconciliation rather than writing
        // the HTTP response directly.  This keeps agent/report injection on
        // the same identity-aware path as Steer, queue delivery, and replay.
        await useSessionStore.getState().refreshCurrentSessionHistory();
        const latest = useSessionStore.getState();
        if (latest.currentSessionId !== sid) return;
        if (latest.currentMessages !== before) {
          return;
        }
        // 当前快照没有带来新消息：注入可能仍在异步落盘，继续下一轮。
      } catch {
        // 短暂网络失败也进入下一轮；所有尝试失败后保留本地状态。
      }
    }
  };

  void sync().finally(() => {
    agentSyncInFlight = false;
  });
}
