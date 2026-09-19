// WebSocket singleton — handles connection, reconnection, and message routing

import type { StreamEvent } from '@/types';

const MAX_RETRY_DELAY = 30_000;
const BASE_RETRY_DELAY = 1_000;
const HEARTBEAT_INTERVAL = 30_000;
const SILENT_CONNECTION_TIMEOUT = HEARTBEAT_INTERVAL * 3;

type MessageHandler = (event: StreamEvent) => void;

function getRetryDelay(attempt: number): number {
  const delay = Math.min(BASE_RETRY_DELAY * 2 ** attempt, MAX_RETRY_DELAY);
  return delay + Math.random() * 1000;
}

class WsClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<MessageHandler>>();
  private retryAttempt = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private silentWatchdogTimer: ReturnType<typeof setInterval> | null = null;
  private url: string;
  private lastActivityAt = 0;
  private eventEpoch: string | null = null;
  private eventSeq = 0;
  private deliveryEpoch: string | null = null;
  private deliverySeq = 0;
  private resyncPending = false;

  constructor(url?: string) {
    const protocol = location.protocol === 'https:' ? 'wss://' : 'ws://';
    this.url = url || `${protocol}${location.host}/ws`;
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    if (this.ws?.readyState === WebSocket.CONNECTING) return;

    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.retryAttempt = 0;
      this.lastActivityAt = Date.now();
      this.resyncPending = false;
      this.startHeartbeat();
      this.startSilentWatchdog();
      this.emit('open', { type: 'open' });
    };

    this.ws.onmessage = (e: MessageEvent) => {
      this.lastActivityAt = Date.now();
      try {
        const data: StreamEvent = JSON.parse(e.data as string);
        const isSnapshot = data.type === 'resync.snapshot';
        const sourceEpoch = data.serverEpoch || data.eventEpoch;
        const deliveryEpoch = data.deliveryEpoch || sourceEpoch;
        const deliverySeq = data.deliverySeq;
        const sourceStart = data.sourceCursorStart ?? data.eventSeq;
        const sourceEnd = data.sourceCursorEnd ?? data.eventSeq;

        // A delivery cursor is contiguous per socket. A source cursor is
        // global and may advance by a range when the server coalesces adjacent
        // deltas. Never use the latter as a raw `last + 1` transport check.
        if (typeof deliveryEpoch === 'string' && typeof deliverySeq === 'number') {
          if (!isSnapshot && this.deliveryEpoch === deliveryEpoch
              && deliverySeq <= this.deliverySeq
              && data.type !== 'resync_required') {
            return;
          }
          const deliveryGap = !isSnapshot
            && this.deliveryEpoch === deliveryEpoch
            && this.deliverySeq > 0
            && deliverySeq > this.deliverySeq + 1;
          if (deliveryGap && !this.resyncPending) {
            this.resyncPending = true;
            this.emit('resync_required', {
              type: 'resync_required',
              reason: 'delivery_cursor_gap',
              eventEpoch: sourceEpoch,
              eventSeq: sourceEnd,
            });
          }
          if (this.deliveryEpoch !== deliveryEpoch || isSnapshot) this.deliverySeq = deliverySeq;
          else this.deliverySeq = Math.max(this.deliverySeq, deliverySeq);
          this.deliveryEpoch = deliveryEpoch;
        }

        if (typeof sourceEpoch === 'string' && typeof sourceEnd === 'number') {
          const sourceGap = !isSnapshot
            && this.eventEpoch === sourceEpoch
            && this.eventSeq > 0
            && (sourceStart ?? sourceEnd) > this.eventSeq + 1;
          if (sourceGap && !this.resyncPending) {
            this.resyncPending = true;
            this.emit('resync_required', {
              type: 'resync_required',
              reason: 'source_cursor_gap',
              eventEpoch: sourceEpoch,
              eventSeq: sourceEnd,
            });
          }
          if (!isSnapshot && this.eventEpoch === sourceEpoch
              && sourceEnd <= this.eventSeq && data.type !== 'resync_required') {
            return;
          }
          if (this.eventEpoch !== sourceEpoch && this.eventEpoch !== null) {
            this.emit('server_epoch_changed', {
              type: 'server_epoch_changed',
              eventEpoch: sourceEpoch,
              serverEpoch: sourceEpoch,
            });
          }
          if (isSnapshot) this.resyncPending = false;
          if (this.eventEpoch !== sourceEpoch || isSnapshot) this.eventSeq = sourceEnd;
          else this.eventSeq = Math.max(this.eventSeq, sourceEnd);
          this.eventEpoch = sourceEpoch;
        }
        if (data.type === 'resync_required') this.resyncPending = true;
        this.dispatch(data);
      } catch {
        // Ignore malformed messages
      }
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose will fire after this
    };
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.stopHeartbeat();
    this.stopSilentWatchdog();
    this.lastActivityAt = 0;
  }

  /** Replaces only this client's stale socket; subscribers remain attached. */
  reconnect(): void {
    if (this.ws?.readyState === WebSocket.CONNECTING) return;
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.stopHeartbeat();
    this.stopSilentWatchdog();
    this.lastActivityAt = 0;
    this.connect();
  }

  isConnectionFresh(maxAgeMs = HEARTBEAT_INTERVAL * 2): boolean {
    return this.isOpen && this.lastActivityAt > 0 && Date.now() - this.lastActivityAt <= maxAgeMs;
  }

  send(data: Record<string, unknown>): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    // CONNECTING/CLOSED 一律返回 false（不假成功）。旧实现在 CONNECTING 时把消息
    // 挂到旧 socket 的 open 事件并返回 true——连接失败则 handler 随 socket 销毁，
    // 消息静默丢失而调用方已按成功处理（H6）。调用方收到 false 应保留待重发状态；
    // queueStore 在 'open' 事件时自动 flush 重试（见 queueStore.ts 底部联动）。
    return false;
  }

  getEventCursor(): { eventEpoch: string | null; eventSeq: number } {
    return { eventEpoch: this.eventEpoch, eventSeq: this.eventSeq };
  }

  on(type: string, handler: MessageHandler): () => void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
    }
    set.add(handler);
    return () => set?.delete(handler);
  }

  onAll(handler: MessageHandler): () => void {
    return this.on('*', handler);
  }

  private dispatch(event: StreamEvent): void {
    // Type-specific handlers
    const set = this.handlers.get(event.type);
    if (set) {
      for (const h of set) h(event);
    }
    // Wildcard handlers
    const all = this.handlers.get('*');
    if (all) {
      for (const h of all) h(event);
    }
  }

  private emit(type: string, event: StreamEvent): void {
    const set = this.handlers.get(type);
    if (set) {
      for (const h of set) h(event);
    }
  }

  private scheduleReconnect(): void {
    const delay = getRetryDelay(this.retryAttempt);
    this.retryAttempt++;
    console.warn(`[WS] disconnected, reconnecting in ${Math.round(delay)}ms`);
    setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, HEARTBEAT_INTERVAL);
  }

  private startSilentWatchdog(): void {
    this.stopSilentWatchdog();
    this.silentWatchdogTimer = setInterval(() => {
      if (this.ws?.readyState !== WebSocket.OPEN || this.lastActivityAt <= 0) return;
      if (Date.now() - this.lastActivityAt > SILENT_CONNECTION_TIMEOUT) {
        // A socket can remain OPEN after the server or an intermediary stopped
        // forwarding frames. Reconnect here as a single flight; focus and
        // visibility recovery continue to share the same wsClient backoff.
        this.reconnect();
      }
    }, HEARTBEAT_INTERVAL);
  }

  private stopSilentWatchdog(): void {
    if (this.silentWatchdogTimer) {
      clearInterval(this.silentWatchdogTimer);
      this.silentWatchdogTimer = null;
    }
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

// Singleton instance
export const wsClient = new WsClient();
