import { useRef, useCallback, useEffect, useMemo, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { groupMessages, MessageDisplayItem, getItemRole } from './MessageBubble';
import { filterVisibleMessages } from './messageFilter';
import { getDisplayItemKey } from '@/utils/messageIdentity';
import { ArrowDown, Loader2 } from 'lucide-react';

// Keep the follow zone small enough that scrolling up to read older content
// opts out, while absorbing normal wheel/touch settling and sub-pixel layout
// rounding near the end of the list.
export const SCROLL_BOTTOM_THRESHOLD = 48;

// Scroll events do not carry portable source attribution. Keep an explicit
// user-input activity window instead: input starts it, subsequent scrolls
// refresh it, and scrollend/timer expiry ends it. The window is long enough
// for wheel/touch inertia to deliver scrolls over several animation frames,
// but bounded so a stale gesture cannot swallow a later real gesture.
const USER_SCROLL_QUIET_MS = 320;
const USER_SCROLLEND_GRACE_MS = 240;
const PROGRAMMATIC_SETTLE_FRAMES = 2;
const PROGRAMMATIC_SETTLE_TIMEOUT_MS = 120;

export function ChatMessages() {
  const parentRef = useRef<HTMLDivElement>(null);
  const currentMessages = useSessionStore((s) => s.currentMessages);
  const hasMoreMessages = useSessionStore((s) => s.hasMoreMessages);
  const historyLoading = useSessionStore((s) => s.historyLoading);
  const initialLoading = useSessionStore((s) => s.initialLoading);
  const loadOlderMessages = useSessionStore((s) => s.loadOlderMessages);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  // The old Bubble/TUI names were reversed. Keep the deprecated Bubble branch
  // wired for a possible future re-enable; TUI is the default branch here.
  const tuiViewEnabled = useUIStore((s) => s.tuiViewEnabled);
  const showMetaAgent = useAppSettingsStore((s) => s.showMetaAgent);
  const showTaskAgent = useAppSettingsStore((s) => s.showTaskAgent);
  const showQQ = useAppSettingsStore((s) => s.showQQ);

  // Frontend-only display filter — currentMessages in the store is never
  // mutated; hidden messages reappear when their toggle is switched back on.
  const visibleMessages = useMemo(
    () =>
      filterVisibleMessages(currentMessages, {
        showMetaAgent,
        showTaskAgent,
        showQQ,
      }),
    [currentMessages, showMetaAgent, showTaskAgent, showQQ],
  );

  // Group messages: consecutive tool messages become ToolGroup
  const grouped = useMemo(() => groupMessages(visibleMessages), [visibleMessages]);

  const virtualizer = useVirtualizer({
    count: grouped.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100,
    overscan: 5,
    // The default key is the array index. Streaming replaces message objects,
    // prepending history shifts indexes, and tool grouping changes row shapes;
    // an index key lets Virtualizer reuse another row's height/DOM node.
    // Local expansion state and measured row heights are Session-scoped even
    // when two Sessions happen to expose the same native/message identity.
    getItemKey: (index) =>
      `${currentSessionId ?? 'no-session'}:${getDisplayItemKey(grouped[index], index)}`,
  });
  // Virtualized content height. Changes when messages are added/removed or
  // when items get measured after layout. Re-scrolling on this (while the user
  // is pinned to the bottom) is what lands the view at the *true* bottom once
  // the virtualizer's measurements settle, instead of the initial estimate.
  const totalSize = virtualizer.getTotalSize();
  const virtualItems = virtualizer.getVirtualItems();

  // Whether the user wants the view to follow the bottom. This is deliberately
  // state held outside React rendering: a streaming delta can arrive between
  // renders, and a render must not infer "follow" from a transient scrollTop.
  // Starts true so the first message load of a session scrolls down; a user
  // scroll away from the follow zone opts out until they return to it (or
  // explicitly press the scroll-to-bottom button).
  const shouldFollowBottomRef = useRef(true);
  // Scroll events happen outside React rendering. Keep a renderable copy so
  // the button appears/disappears immediately when the user crosses the
  // follow boundary.
  const [isNearBottom, setIsNearBottom] = useState(true);
  // A newly selected session starts with an empty/unlaid-out container. Allow
  // its first history render to establish the initial bottom position even
  // though scrollTop is 0 before the content is mounted.
  const initialScrollPendingRef = useRef(true);
  const lastScrollMetricsRef = useRef<{
    height: number;
    top: number;
    clientHeight: number;
  } | null>(null);
  const paginationAnchorRef = useRef<{ top: number; height: number } | null>(null);
  // A browser may emit a scroll event when a measured row changes the scroll
  // range, even though the user did not scroll. Keep that event separate from
  // the user-input state machine below. The generation and bounded settle
  // window suppress the next layout/virtualizer correction without using a
  // browser trust flag as a source-of-intent heuristic.
  const layoutChangePendingRef = useRef(false);
  const layoutChangeRafRef = useRef<number | null>(null);
  const programmaticGenerationRef = useRef(0);
  const programmaticSuppressionRef = useRef<{
    generation: number;
    raf: number | null;
    timer: ReturnType<typeof setTimeout> | null;
  } | null>(null);
  const userScrollStateRef = useRef<{
    active: boolean;
    generation: number;
    timer: ReturnType<typeof setTimeout> | null;
  }>({ active: false, generation: 0, timer: null });

  // Clamp only negative browser rounding artefacts to zero, then use the
  // small follow zone below instead of requiring exact geometry equality.
  const getDistanceFromBottom = useCallback((): number => {
    const el = parentRef.current;
    if (!el) return 0;
    return Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight);
  }, []);

  const isNearBottomPosition = useCallback((): boolean => {
    return getDistanceFromBottom() <= SCROLL_BOTTOM_THRESHOLD;
  }, [getDistanceFromBottom]);

  const captureScrollMetrics = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    lastScrollMetricsRef.current = {
      height: el.scrollHeight,
      top: el.scrollTop,
      clientHeight: el.clientHeight,
    };
  }, []);

  const clearProgrammaticSuppression = useCallback(() => {
    const suppression = programmaticSuppressionRef.current;
    if (!suppression) return;
    if (suppression.raf !== null) cancelAnimationFrame(suppression.raf);
    if (suppression.timer !== null) clearTimeout(suppression.timer);
    programmaticSuppressionRef.current = null;
  }, []);

  const markProgrammaticChange = useCallback(() => {
    clearProgrammaticSuppression();
    const generation = ++programmaticGenerationRef.current;
    const suppression = {
      generation,
      raf: null as number | null,
      timer: null as ReturnType<typeof setTimeout> | null,
    };
    programmaticSuppressionRef.current = suppression;

    let frames = 0;
    const settle = () => {
      if (programmaticSuppressionRef.current?.generation !== generation) return;
      if (++frames >= PROGRAMMATIC_SETTLE_FRAMES) {
        clearProgrammaticSuppression();
        return;
      }
      suppression.raf = requestAnimationFrame(settle);
    };
    suppression.raf = requestAnimationFrame(settle);
    suppression.timer = setTimeout(() => {
      if (programmaticSuppressionRef.current?.generation === generation) {
        clearProgrammaticSuppression();
      }
    }, PROGRAMMATIC_SETTLE_TIMEOUT_MS);
  }, [clearProgrammaticSuppression]);

  const clearUserScrollActivity = useCallback(() => {
    const state = userScrollStateRef.current;
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = null;
    state.active = false;
  }, []);

  const scheduleUserScrollExpiry = useCallback((delay = USER_SCROLL_QUIET_MS) => {
    const state = userScrollStateRef.current;
    if (state.timer !== null) clearTimeout(state.timer);
    const generation = state.generation;
    state.timer = setTimeout(() => {
      const current = userScrollStateRef.current;
      if (current.generation === generation) {
        current.timer = null;
        current.active = false;
      }
    }, delay);
  }, []);

  const markUserScrollInput = useCallback(() => {
    // A new explicit input always wins over an older layout suppression. This
    // prevents a bounded correction window from swallowing the next gesture.
    clearProgrammaticSuppression();
    const state = userScrollStateRef.current;
    state.generation += 1;
    state.active = true;
    scheduleUserScrollExpiry();
  }, [clearProgrammaticSuppression, scheduleUserScrollExpiry]);

  const refreshUserScrollActivity = useCallback(() => {
    if (!userScrollStateRef.current.active) return false;
    scheduleUserScrollExpiry();
    return true;
  }, [scheduleUserScrollExpiry]);

  const scrollToBottom = useCallback(() => {
    const el = parentRef.current;
    if (el) {
      shouldFollowBottomRef.current = true;
      markProgrammaticChange();
      el.scrollTop = el.scrollHeight;
      setIsNearBottom(true);
      captureScrollMetrics();
    }
  }, [captureScrollMetrics, markProgrammaticChange]);

  const recoverToBottom = useCallback(() => {
    // Explicit recovery ends the previous input sequence. Automatic pinning
    // keeps the sequence alive so a delayed inertia scroll cannot be lost to
    // a same-frame session/layout rAF.
    clearUserScrollActivity();
    scrollToBottom();
  }, [clearUserScrollActivity, scrollToBottom]);

  const markLayoutChange = useCallback(() => {
    layoutChangePendingRef.current = true;
    markProgrammaticChange();
    if (layoutChangeRafRef.current !== null) {
      cancelAnimationFrame(layoutChangeRafRef.current);
    }
    layoutChangeRafRef.current = requestAnimationFrame(() => {
      layoutChangeRafRef.current = null;
      layoutChangePendingRef.current = false;
    });
  }, [markProgrammaticChange]);

  // Auto-scroll on new messages / measurement-driven size changes — but only
  // when the user hasn't scrolled away from the bottom. This is ALSO what
  // lands the view at the bottom after entering a session: the session-change
  // effect below sets the initial-scroll flag, so when the asynchronously-
  // loaded history arrives (currentMessages changes) — and again once the
  // virtualizer measures the real heights (totalSize changes) — we scroll only
  // when the container is truly at the bottom, except for that initial history
  // render.
  useEffect(() => {
    const el = parentRef.current;
    markLayoutChange();
    const previous = lastScrollMetricsRef.current;
    const layoutDrivenUpdate = layoutChangePendingRef.current;
    const grewWhilePinned = Boolean(
      el &&
        previous &&
        el.scrollHeight !== previous.height &&
        el.scrollTop === previous.top &&
        Math.max(0, previous.height - previous.top - previous.clientHeight) <=
          SCROLL_BOTTOM_THRESHOLD,
    );
    const nearBottom = isNearBottomPosition();
    let followedBottom = false;
    if (
      shouldFollowBottomRef.current &&
      (initialScrollPendingRef.current || nearBottom || grewWhilePinned || layoutDrivenUpdate)
    ) {
      initialScrollPendingRef.current = false;
      scrollToBottom();
      followedBottom = true;
    }
    setIsNearBottom(followedBottom || nearBottom || grewWhilePinned);
    captureScrollMetrics();
  }, [currentMessages, totalSize, captureScrollMetrics, isNearBottomPosition, markLayoutChange, scrollToBottom]);

  // Lazy load older messages on scroll to top
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const markUserScrollKey = (event: KeyboardEvent) => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
        markUserScrollInput();
      }
    };
    const markPointerScrollInput = (event: PointerEvent) => {
      // Do not treat ordinary mouse hover or a body click as scroll intent.
      // A pressed mouse/pen pointer can drag a scrollbar or drive a custom
      // pointer scroller; touch remains covered by the touch listeners above.
      if ((event.pointerType === 'mouse' || event.pointerType === 'pen') && event.buttons !== 0) {
        markUserScrollInput();
      }
    };
    const handler = () => {
      initialScrollPendingRef.current = false;
      const nearBottom = isNearBottomPosition();
      const programmaticSuppressed = programmaticSuppressionRef.current !== null;
      if (programmaticSuppressed) {
        // Consume the correction window on the first resulting scroll. A new
        // wheel/touch/pointer/key input cancels this suppression first, so a
        // later genuine gesture can never be permanently ignored.
        clearProgrammaticSuppression();
      } else if (refreshUserScrollActivity()) {
        shouldFollowBottomRef.current = nearBottom;
      } else if (nearBottom) {
        // Returning to the bottom is an explicit recovery path even when the
        // browser emits the final scroll without another input event.
        shouldFollowBottomRef.current = nearBottom;
      }
      setIsNearBottom(nearBottom);
      captureScrollMetrics();

      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        if (el.scrollTop <= 200 && hasMoreMessages && !historyLoading) {
          // Keep the message currently under the user's eyes in place. The
          // old code used only the height delta, which was correct at exactly
          // scrollTop=0 but jumped down when pagination began at any other
          // position. Disable native scroll anchoring so this is the only
          // restoration applied after the prepend.
          paginationAnchorRef.current = {
            top: el.scrollTop,
            height: el.scrollHeight,
          };
          loadOlderMessages().then(() => {
            // Preserve scroll position after DOM has updated
            requestAnimationFrame(() => {
              const anchor = paginationAnchorRef.current;
              if (!anchor) return;
              markProgrammaticChange();
              el.scrollTop = anchor.top + el.scrollHeight - anchor.height;
              paginationAnchorRef.current = null;
            });
          });
        }
      }, 150);
    };

    const handleScrollEnd = () => {
      // Chromium/WebKit expose scrollend, but Firefox and older browsers do
      // not. Keep a bounded grace period after scrollend because touch/wheel
      // inertia can still deliver a late scroll task; the normal scroll
      // handler expands it back to USER_SCROLL_QUIET_MS when that happens.
      if (userScrollStateRef.current.active) {
        scheduleUserScrollExpiry(USER_SCROLLEND_GRACE_MS);
      }
    };

    el.addEventListener('wheel', markUserScrollInput, { passive: true });
    el.addEventListener('touchstart', markUserScrollInput, { passive: true });
    el.addEventListener('touchmove', markUserScrollInput, { passive: true });
    el.addEventListener('pointermove', markPointerScrollInput, { passive: true });
    el.addEventListener('keydown', markUserScrollKey);
    el.addEventListener('scroll', handler);
    el.addEventListener('scrollend', handleScrollEnd);
    return () => {
      el.removeEventListener('wheel', markUserScrollInput);
      el.removeEventListener('touchstart', markUserScrollInput);
      el.removeEventListener('touchmove', markUserScrollInput);
      el.removeEventListener('pointermove', markPointerScrollInput);
      el.removeEventListener('keydown', markUserScrollKey);
      el.removeEventListener('scroll', handler);
      el.removeEventListener('scrollend', handleScrollEnd);
      if (timer) clearTimeout(timer);
    };
  }, [
    captureScrollMetrics,
    clearProgrammaticSuppression,
    clearUserScrollActivity,
    grouped.length,
    hasMoreMessages,
    historyLoading,
    isNearBottomPosition,
    loadOlderMessages,
    markProgrammaticChange,
    markUserScrollInput,
    refreshUserScrollActivity,
    scheduleUserScrollExpiry,
  ]);

  // A viewport resize changes the meaning of "bottom" without changing the
  // message array or virtualizer total size. Re-pin only while follow mode is
  // active; a reader who opted out keeps their viewport.
  useEffect(() => {
    const el = parentRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    let raf: number | null = null;
    const observer = new ResizeObserver(() => {
      markLayoutChange();
      if (!shouldFollowBottomRef.current) {
        captureScrollMetrics();
        return;
      }
      if (raf !== null) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        raf = null;
        if (shouldFollowBottomRef.current) scrollToBottom();
      });
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (raf !== null) cancelAnimationFrame(raf);
    };
  }, [captureScrollMetrics, grouped.length, markLayoutChange, scrollToBottom]);

  // Scroll to bottom when the session changes. Reset the pinned anchor first
  // so the auto-scroll effect above forces us down once this session's history
  // loads (async) and again after the virtualizer measures the real heights.
  // The rAF re-scroll covers the same-frame layout of the freshly swapped DOM.
  useEffect(() => {
    shouldFollowBottomRef.current = true;
    setIsNearBottom(true);
    initialScrollPendingRef.current = true;
    lastScrollMetricsRef.current = null;
    paginationAnchorRef.current = null;
    layoutChangePendingRef.current = false;
    clearUserScrollActivity();
    clearProgrammaticSuppression();
    scrollToBottom();
    // If this session already has a mounted message container, the session
    // switch itself performed the initial positioning. Keep later updates
    // subject to the strict bottom check; leave the flag pending only when
    // history is still empty and its container has not mounted yet.
    if (parentRef.current) {
      initialScrollPendingRef.current = false;
    }
    const raf = requestAnimationFrame(scrollToBottom);
    return () => {
      cancelAnimationFrame(raf);
      if (layoutChangeRafRef.current !== null) cancelAnimationFrame(layoutChangeRafRef.current);
      clearUserScrollActivity();
      clearProgrammaticSuppression();
    };
  }, [
    clearProgrammaticSuppression,
    clearUserScrollActivity,
    currentSessionId,
    scrollToBottom,
  ]);

  // Empty state — but ONLY after the initial history fetch has settled. While
  // it is in flight (currentMessages empty + initialLoading) show a spinner so
  // a session that actually has content never flashes "No messages yet".
  if (currentMessages.length === 0) {
    if (initialLoading) {
      return (
        <div className="flex-1 flex items-center justify-center gap-2 text-text-tertiary text-sm">
          <Loader2 size={16} className="animate-spin" />
          Loading...
        </div>
      );
    }
    return (
      <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
        {currentSessionId
          ? 'No messages yet. Start a conversation.'
          : 'Select a session to start'}
      </div>
    );
  }

  // A tail can be entirely hidden by the Meta/Task/QQ filters while older
  // canonical pages still contain visible messages. Scroll-to-top cannot
  // fire when there are no rendered rows, so expose an explicit bounded page
  // load entry instead of presenting a permanent blank state.
  if (grouped.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-text-tertiary text-sm">
        <span>当前尾页消息已被过滤</span>
        {hasMoreMessages && (
          <button
            type="button"
            onClick={() => void loadOlderMessages()}
            disabled={historyLoading}
            className="rounded border border-border px-3 py-1.5 text-text-secondary hover:bg-bg-hover disabled:opacity-50"
          >
            {historyLoading ? 'Loading older messages...' : 'Load older messages'}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      <div
        ref={parentRef}
        className={`flex-1 min-h-0 overflow-auto ${!tuiViewEnabled ? 'bubble-mode' : ''}`}
        style={{ overflowAnchor: 'none' }}
      >
        <div
          style={{
            minHeight: `${totalSize}px`,
            width: '100%',
            // Keep the virtual spacer and its rows in one formatting context
            // so top spacing is measured as part of the scroll content.
            display: 'flow-root',
          }}
        >
          {virtualItems.map((vItem, virtualIndex) => {
            const item = grouped[vItem.index];
            if (!item) return null;
            const prevItem = grouped[vItem.index - 1];
            const prevRole = prevItem ? getItemRole(prevItem) : null;
            const previousVirtualItem = virtualItems[virtualIndex - 1];
            // The first rendered row reserves the omitted prefix. Subsequent
            // rows use only a non-negative gap: if a streamed/collapsible row
            // is taller than its last measurement, normal flow pushes the next
            // row down instead of allowing stale absolute coordinates to
            // overlap it. TanStack will measure the new height and settle the
            // spacer on the next update.
            const flowOffset = previousVirtualItem
              ? Math.max(0, vItem.start - previousVirtualItem.start - previousVirtualItem.size)
              : Math.max(0, vItem.start);
            return (
              <div
                key={vItem.key}
                data-index={vItem.index}
                ref={virtualizer.measureElement}
                style={{
                  width: '100%',
                  marginTop: `${flowOffset}px`,
                  // Keep child margins and collapsible content inside the
                  // measured row's formatting context. The viewport may move
                  // because of auto-scroll, but it must not alter row order.
                  display: 'flow-root',
                }}
              >
                <MessageDisplayItem item={item} prevRole={prevRole} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Scroll-to-bottom button */}
      {!isNearBottom && (
        <button
          onClick={recoverToBottom}
          className="absolute bottom-2 right-4 rounded-full bg-accent text-white p-2 shadow-lg hover:bg-accent-hover transition-colors z-10"
          title="Scroll to bottom"
        >
          <ArrowDown size={16} />
        </button>
      )}

      {/* Loading indicator */}
      {historyLoading && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 bg-bg-tertiary px-3 py-1 rounded text-xs text-text-secondary">
          Loading older messages...
        </div>
      )}
    </div>
  );
}
