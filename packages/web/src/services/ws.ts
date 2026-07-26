// WebSocket singleton — handles connection, reconnection, and message routing

import type { StreamEvent } from '@/types';

const MAX_RETRY_DELAY = 30_000;
const BASE_RETRY_DELAY = 1_000;
const HEARTBEAT_INTERVAL = 30_000;

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
  private url: string;

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
      this.startHeartbeat();
      this.emit('open', { type: 'open' });
    };

    this.ws.onmessage = (e: MessageEvent) => {
      try {
        const data: StreamEvent = JSON.parse(e.data as string);
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
  }

  send(data: Record<string, unknown>): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    if (this.ws?.readyState === WebSocket.CONNECTING) {
      const handler = () => {
        this.ws?.send(JSON.stringify(data));
        this.ws?.removeEventListener('open', handler);
      };
      this.ws?.addEventListener('open', handler, { once: true });
      return true;
    }
    return false;
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
