import { useEffect } from 'react';
import { getHistoryPrefetchContext, useSessionStore } from '@/stores/sessionStore';
import {
  foregroundActivity,
  noteForegroundActivity,
  subscribeForegroundActivity,
} from '@/services/foregroundActivity';
import {
  cancelHistoryPrefetch,
  nextHistoryPrefetchBefore,
  prefetchHistoryPage,
  syncHistoryPrefetch,
} from '@/services/historyPagePrefetch';
import { wsClient } from '@/services/ws';

const QUIET_MS = 2000;

/** Prefetch raw pages, never prepend/render them without a user gesture. */
export function useIdleHistoryPrefetch(): void {
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let idle: number | null = null;
    let inFlight = false;
    let failedContext = '';

    const eligible = () => {
      const state = useSessionStore.getState();
      const connection = (
        navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }
      ).connection;
      return (
        active &&
        document.visibilityState === 'visible' &&
        !connection?.saveData &&
        !['slow-2g', '2g'].includes(connection?.effectiveType ?? '') &&
        !state.initialLoading &&
        !state.historyLoading &&
        !state.sessionsLoading &&
        foregroundActivity().requests === 0 &&
        !Array.from(document.images).some((image) => !image.complete) &&
        !state.sessions.some(
          (session) =>
            session.workerStatus &&
            !['idle', 'done', 'offline', 'error'].includes(session.workerStatus),
        )
      );
    };
    const clearScheduled = () => {
      if (timer !== null) clearTimeout(timer);
      if (idle !== null) window.cancelIdleCallback(idle);
      timer = null;
      idle = null;
    };
    const wake = () => {
      const context = getHistoryPrefetchContext();
      syncHistoryPrefetch(context);
      if (!eligible() || !context) {
        clearScheduled();
        cancelHistoryPrefetch();
        return;
      }
      if (inFlight || timer !== null || idle !== null || failedContext === JSON.stringify(context))
        return;
      const before = nextHistoryPrefetchBefore(context, useSessionStore.getState().historyLoadEnd);
      if (before === null) return;
      timer = setTimeout(
        run,
        Math.max(250, QUIET_MS - (Date.now() - foregroundActivity().lastActivity)),
      );
    };
    const run = () => {
      timer = null;
      if (!eligible()) {
        wake();
        return;
      }
      const wait = QUIET_MS - (Date.now() - foregroundActivity().lastActivity);
      if (wait > 0) {
        wake();
        return;
      }
      if (window.requestIdleCallback) {
        idle = window.requestIdleCallback((deadline) => {
          idle = null;
          if (deadline.timeRemaining() < 8) {
            wake();
            return;
          }
          void read();
        });
      } else void read();
    };
    const read = async () => {
      const context = getHistoryPrefetchContext();
      if (!eligible() || !context || Date.now() - foregroundActivity().lastActivity < QUIET_MS) {
        wake();
        return;
      }
      const before = nextHistoryPrefetchBefore(context, useSessionStore.getState().historyLoadEnd);
      if (before === null) return;
      inFlight = true;
      const version = foregroundActivity().version;
      const stored = await prefetchHistoryPage(context, before);
      inFlight = false;
      // Do not retry malformed/oversized/failed pages on every idle tick. A new
      // history authority/selection can retry; a foreground abort is retryable.
      if (!stored && version === foregroundActivity().version)
        failedContext = JSON.stringify(context);
      wake();
    };
    const activity = () => {
      cancelHistoryPrefetch();
      clearScheduled();
      // Streaming deltas and input storms only reset a cheap timer. Avoid
      // scanning Session metadata on each foreground event.
      if (active)
        timer = setTimeout(() => {
          timer = null;
          wake();
        }, QUIET_MS);
    };
    const unsubscribeActivity = subscribeForegroundActivity(activity);
    const unsubscribeStore = useSessionStore.subscribe((state, previous) => {
      // A failed/oversized speculative page must not disable every older page
      // after the user has successfully loaded past it through the normal path.
      if (state.historyLoadEnd !== previous.historyLoadEnd) failedContext = '';
      const sid = state.currentSessionId;
      const window = sid ? state.sessionTranscripts[sid]?.window : undefined;
      const oldWindow = sid ? previous.sessionTranscripts[sid]?.window : undefined;
      if (
        sid !== previous.currentSessionId ||
        state.sessions !== previous.sessions ||
        state.serverEpoch !== previous.serverEpoch ||
        state._selectionSeq !== previous._selectionSeq ||
        state.historyLoadEnd !== previous.historyLoadEnd ||
        state.hasMoreMessages !== previous.hasMoreMessages ||
        state.initialLoading !== previous.initialLoading ||
        state.historyLoading !== previous.historyLoading ||
        state.sessionsLoading !== previous.sessionsLoading ||
        window?.epoch !== oldWindow?.epoch ||
        window?.revision !== oldWindow?.revision
      )
        wake();
    });
    const unsubscribeWs = wsClient.onAll((event) => {
      if (!['pong', 'heartbeat'].includes(event.type)) noteForegroundActivity();
    });
    const input = () => noteForegroundActivity();
    for (const name of ['pointerdown', 'keydown', 'wheel', 'touchmove', 'visibilitychange']) {
      document.addEventListener(name, input, { capture: true, passive: true });
    }
    // API/XHR bodies are tracked above; late script/style/image completion also
    // postpones speculation. Chat route loading has completed before this hook.
    const observer =
      typeof PerformanceObserver === 'undefined'
        ? null
        : new PerformanceObserver((list) => {
            if (
              list
                .getEntries()
                .some(
                  (entry) =>
                    !['fetch', 'xmlhttprequest'].includes(
                      (entry as PerformanceResourceTiming).initiatorType,
                    ),
                )
            )
              input();
          });
    observer?.observe({ type: 'resource', buffered: true });
    wake();
    return () => {
      active = false;
      clearScheduled();
      unsubscribeActivity();
      unsubscribeStore();
      unsubscribeWs();
      observer?.disconnect();
      for (const name of ['pointerdown', 'keydown', 'wheel', 'touchmove', 'visibilitychange'])
        document.removeEventListener(name, input, true);
      syncHistoryPrefetch(null);
    };
  }, []);
}
