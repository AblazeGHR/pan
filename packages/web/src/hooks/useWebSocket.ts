import { useEffect, useRef } from 'react';
import { wsClient } from '@/services/ws';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import {
  useAdapterStore,
} from '@/stores/adapterStore';
import type { StreamEvent } from '@/types';

/**
 * Connects to WebSocket and routes events to Zustand stores.
 * Uses store.getState() for callbacks so React components re-render
 * when subscribed state changes.
 */
export function useWebSocket() {
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    wsClient.connect();

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

    // Worker destroyed / crashed
    unsubscribers.push(wsClient.on('worker.destroyed', (e: StreamEvent) =>
      handleWorkerUpdate(e, null),
    ));
    unsubscribers.push(wsClient.on('worker.crashed', (e: StreamEvent) =>
      handleWorkerUpdate(e, null),
    ));

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
    }));

    // Session events
    unsubscribers.push(wsClient.on('session.renamed', () => {
      useSessionStore.getState().loadSessions();
    }));
    unsubscribers.push(wsClient.on('session.updated', () => {
      useSessionStore.getState().loadSessions();
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
