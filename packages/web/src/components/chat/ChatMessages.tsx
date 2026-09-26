import { forwardRef, useRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { groupMessages, MessageDisplayItem, getItemRole } from './MessageBubble';
import { filterVisibleMessages } from './messageFilter';
import { getDisplayItemKey, getMessageIdentity } from '@/utils/messageIdentity';
import { isValidMessageTs } from '@/utils/messageTimestamp';
import type { Message } from '@/types';
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

// ── Scroll memory across route round-trips ─────────────────────────────────
// Leaving Chat (Editor / Tasks / Manage / any route) unmounts ChatView, so
// coming back remounts ChatMessages with an unchanged sessionId. Every mount
// effect ran again, and the "new session" path pushed whoever was reading an
// older message back to the bottom. Remember one anchor row per session — the
// row the user was actually looking at, expressed in virtual-content
// coordinates — and restore it before the browser paints on the way back.
interface ScrollSnapshot {
  /** Guards the restore against a history that changed while away. */
  fingerprint: string;
  /** Stable key shared with the rendered/virtualized display row. */
  rowKey?: string;
  /** Stable logical message identity when this row is a single message. */
  identity?: string;
  /** Row top relative to the scroll container's top (px). */
  anchorOffset: number;
  /** Row top inside the virtual content (px). */
  contentOffset: number;
}

interface ViewportAnchor {
  sessionId: string;
  rowKey?: string;
  identity?: string;
  /** Row top relative to the scroll container's top. */
  offset: number;
}

const scrollSnapshots = new Map<string, ScrollSnapshot>();
/** sessionId → measured row height by virtual item key, for the next mount. */
const measuredHeights = new Map<string, Map<string, number>>();

function listFingerprint(items: DisplayItem[]): string {
  if (items.length === 0) return 'empty';
  const last = items.length - 1;
  return `${items.length}:${getDisplayItemKey(items[0], 0)}:${getDisplayItemKey(items[last], last)}`;
}

/** Key shared by the virtualizer's item identity and the measured-height cache. */
function measuredRowKey(
  sessionId: string | null,
  item: DisplayItem | undefined,
  index: number,
): string {
  return `${sessionId ?? 'no-session'}:${getDisplayItemKey(item, index)}`;
}

/** Disclosure rows remount folded, so their previous expanded heights are stale. */
function invalidateDisclosureHeights(sessionId: string, items: DisplayItem[]): void {
  const heights = measuredHeights.get(sessionId);
  if (!heights) return;
  items.forEach((item, index) => {
    if ('type' in item && (
      item.type === 'thinking_group' ||
      item.type === 'tool_group' ||
      item.type === 'non_body_group'
    )) {
      heights.delete(measuredRowKey(sessionId, item, index));
    }
  });
}

type DisplayItem = ReturnType<typeof groupMessages>[number];

export interface ChatMessagesHandle {
  /** Scroll to a currently loaded message and briefly highlight its row. */
  scrollToMessage: (message: import('@/types').Message, historyIndex?: number) => boolean;
}

export const ChatMessages = forwardRef<ChatMessagesHandle>(function ChatMessages(_, ref) {
  const parentRef = useRef<HTMLDivElement>(null);
  const currentMessages = useSessionStore((s) => s.currentMessages);
  const hasMoreMessages = useSessionStore((s) => s.hasMoreMessages);
  const historyLoading = useSessionStore((s) => s.historyLoading);
  const initialLoading = useSessionStore((s) => s.initialLoading);
  const loadOlderMessages = useSessionStore((s) => s.loadOlderMessages);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  // Two chat presentations share this component. TUI (the default) lays out
  // full-width role-bar rows; Bubble adds `.bubble-mode` on the scroll
  // container, which is what the shrink-to-fit bubble rules are scoped to.
  const tuiViewEnabled = useAppSettingsStore((s) => s.chatViewStyle === 'tui');
  const lastTuiViewEnabledRef = useRef(tuiViewEnabled);
  const showMetaAgent = useAppSettingsStore((s) => s.showMetaAgent);
  const showTaskAgent = useAppSettingsStore((s) => s.showTaskAgent);
  const showQQ = useAppSettingsStore((s) => s.showQQ);
  const mergeConsecutiveNonBodyBlocks = useAppSettingsStore((s) => s.mergeConsecutiveNonBodyBlocks);
  // Whether a Session switch may adopt the position this Session was left at.
  // Read straight from the store so toggling the Appearance switch applies to
  // the next switch without a reload. The route round-trip restore below is
  // deliberately independent of this flag.
  const keepScrollOnSessionSwitch = useAppSettingsStore((s) => s.keepScrollOnSessionSwitch);
  const settingsLoaded = useAppSettingsStore((s) => s.loaded);

  // A timestamp highlight is only enqueued after a same-session tail append.
  // The anchor check at the previous tail's index rejects prepended history,
  // while the session check rejects history loaded during a session switch.
  const [timestampFlashMessages, setTimestampFlashMessages] = useState<ReadonlySet<Message>>(
    () => new Set(),
  );
  const observedMessagesRef = useRef<{
    sessionId: string | null;
    length: number;
    tailIdentity: string | null;
    initialLoading: boolean;
  } | null>(null);

  useLayoutEffect(() => {
    const previous = observedMessagesRef.current;
    if (previous && previous.sessionId !== currentSessionId) {
      setTimestampFlashMessages(new Set());
    }
    if (
      currentSessionId &&
      !initialLoading &&
      previous?.sessionId === currentSessionId &&
      !previous.initialLoading &&
      previous.length > 0 &&
      currentMessages.length > previous.length
    ) {
      const oldTailAtSameOffset = currentMessages[previous.length - 1];
      if (
        oldTailAtSameOffset &&
        getMessageIdentity(oldTailAtSameOffset) === previous.tailIdentity
      ) {
        const appended = filterVisibleMessages(currentMessages.slice(previous.length), {
          showMetaAgent,
          showTaskAgent,
          showQQ,
        }).filter((message) => message.ts && isValidMessageTs(message.ts));
        if (appended.length > 0) {
          setTimestampFlashMessages((current) => new Set([...current, ...appended]));
        }
      }
    }

    const tail = currentMessages[currentMessages.length - 1];
    observedMessagesRef.current = {
      sessionId: currentSessionId,
      length: currentMessages.length,
      tailIdentity: tail ? getMessageIdentity(tail) : null,
      initialLoading,
    };
  }, [
    currentMessages,
    currentSessionId,
    initialLoading,
    showMetaAgent,
    showTaskAgent,
    showQQ,
  ]);

  // CSS owns the pulse. This one-shot timeout is only a fallback for virtual
  // rows that never mount, or reduced-motion CSS where animationend is absent.
  useEffect(() => {
    if (timestampFlashMessages.size === 0) return;
    const timeout = window.setTimeout(() => setTimestampFlashMessages(new Set()), 2200);
    return () => window.clearTimeout(timeout);
  }, [timestampFlashMessages]);

  const clearTimestampFlash = useCallback((flashKeys: readonly string[]) => {
    const consumed = new Set(flashKeys);
    setTimestampFlashMessages((current) => {
      const next = new Set([...current].filter((message) => !consumed.has(getMessageIdentity(message))));
      return next.size === current.size ? current : next;
    });
  }, []);

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

  // Preserve the existing separate tool/thinking rows unless the user opts in
  // to one parent disclosure for each adjacent non-body run.
  const grouped = useMemo(
    () => groupMessages(visibleMessages, mergeConsecutiveNonBodyBlocks, timestampFlashMessages),
    [visibleMessages, mergeConsecutiveNonBodyBlocks, timestampFlashMessages],
  );

  const [highlightedTarget, setHighlightedTarget] = useState<{ identity: string; historyIndex?: number } | null>(null);

  // Latest render value the module-level helper below needs. It must hold a
  // stable identity because the scroll effects depend on that helper.
  const groupedRef = useRef(grouped);
  groupedRef.current = grouped;

  // Latest render's session id, for the unmount cleanup. An effect-assigned
  // module variable is one paint too late: a session switch that unmounts the
  // view in the same tick would file the heights under the previous session.
  const currentSessionIdRef = useRef(currentSessionId);
  currentSessionIdRef.current = currentSessionId;

  // Route round-trip: adopt the position this session was left at, but only for
  // a remount of the *same* session whose message list did not change.
  const restoreRef = useRef<ScrollSnapshot | null | undefined>(undefined);
  const restoreSessionRef = useRef<string | null | undefined>(undefined);
  const sessionSwitchWaitingForSettingsRef = useRef(false);
  const sessionAnchorRef = useRef<ViewportAnchor | null>(null);
  const ignoreUnattributedScrollRef = useRef(false);
  if (restoreRef.current === undefined) {
    restoreSessionRef.current = currentSessionId;
    // A session can keep loading/prepending while it is off-screen (for
    // example, the user leaves immediately after a navigation jump). In that
    // case the list fingerprint is expected to change; the stable message
    // identity is the useful part of the snapshot.
    restoreRef.current = currentSessionId
      ? scrollSnapshots.get(currentSessionId) ?? null
      : null;
    // Keep the session snapshot until a later real user scroll replaces it.
  }
  if (restoreRef.current && sessionAnchorRef.current === null && currentSessionId) {
    sessionAnchorRef.current = {
      sessionId: currentSessionId,
      rowKey: restoreRef.current.rowKey,
      identity: restoreRef.current.identity,
      offset: restoreRef.current.anchorOffset,
    };
  }
  const isRestoringRef = useRef(restoreRef.current !== null);

  const virtualizer = useVirtualizer({
    count: grouped.length,
    getScrollElement: () => parentRef.current,
    // Rows measured during the previous visit. Without them the remounted
    // virtualizer starts from the flat estimate, whose total size differs from
    // the one the remembered content offset was taken against. The lookup key
    // must be the same expression the write side stores (see `measuredRowKey`).
    estimateSize: (index) =>
      measuredHeights
        .get(currentSessionId ?? '')
        ?.get(measuredRowKey(currentSessionId, grouped[index], index)) ?? 100,
    overscan: 5,
    // The default key is the array index. Streaming replaces message objects,
    // prepending history shifts indexes, and tool grouping changes row shapes;
    // an index key lets Virtualizer reuse another row's height/DOM node.
    // Local expansion state and measured row heights are Session-scoped even
    // when two Sessions happen to expose the same native/message identity.
    getItemKey: (index) => measuredRowKey(currentSessionId, grouped[index], index),
  });
  // Virtualized content height. Changes when messages are added/removed or
  // when items get measured after layout. Re-scrolling on this (while the user
  // is pinned to the bottom) is what lands the view at the *true* bottom once
  // the virtualizer's measurements settle, instead of the initial estimate.
  const totalSize = virtualizer.getTotalSize();
  const virtualItems = virtualizer.getVirtualItems();
  const virtualizerRef = useRef(virtualizer);
  virtualizerRef.current = virtualizer;

  // Remember where the reader is, for the route round-trip above. Rows are
  // virtualized, so keep the visible anchor row's offset inside the virtual
  // content: it is independent of the virtualization window and survives a
  // re-measurement of everything above it. No layout (jsdom) yields no anchor —
  // and therefore no snapshot — which keeps this inert under unit tests.
  // Bookkeeping for the single retry below. `null` means "no retry pending";
  // holding the rAF id keeps the retry chain to exactly one frame.
  const snapshotRetryRef = useRef<number | null>(null);

  const rememberScrollPosition = useCallback((el: HTMLElement, allowRetry = true) => {
    // The session whose rows are on screen right now — read from the ref rather
    // than from this callback's closure. On a session switch React runs the
    // previous render's effect cleanup *after* the DOM has been swapped, so a
    // closure id would file the new session's rows under the session being
    // left, overwriting its snapshot with a foreign identity/offset pair.
    const sid = currentSessionIdRef.current;
    if (!sid || sessionSwitchWaitingForSettingsRef.current || ignoreUnattributedScrollRef.current) return;
    const viewport = el.getBoundingClientRect();
    const messageAnchor = [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find((node) => {
      const rect = node.getBoundingClientRect();
      return rect.bottom > viewport.top && rect.top < viewport.bottom;
    });
    const row = messageAnchor?.closest<HTMLElement>('[data-scroll-anchor-key]') ??
      [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find((node) => {
        const rect = node.getBoundingClientRect();
        return rect.bottom > viewport.top && rect.top < viewport.bottom;
      });
    const anchor = messageAnchor ?? row;
    const rowKey = row?.dataset.scrollAnchorKey;
    const identity = messageAnchor?.dataset.messageIdentity ??
      row?.dataset.messageIdentity ??
      row?.querySelector<HTMLElement>('[data-message-identity]')?.dataset.messageIdentity;
    if (!anchor || !rowKey) {
      // The render window can lag the scroll position (a long jump lands before
      // React has rendered the new window), leaving no anchor row to remember.
      // Skipping the write that way loses the reader's place, so retry exactly
      // once on the next frame. Only this failing path pays for the retry: a
      // successful write never schedules anything, and `snapshotRetryRef` keeps
      // the chain to a single frame even when several writes fail in a row.
      if (allowRetry && snapshotRetryRef.current === null && el.isConnected) {
        snapshotRetryRef.current = requestAnimationFrame(() => {
          snapshotRetryRef.current = null;
          if (el.isConnected) rememberScrollPosition(el, false);
        });
      }
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const snap = {
      fingerprint: listFingerprint(groupedRef.current),
      rowKey,
      ...(identity ? { identity } : {}),
      anchorOffset: rect.top - viewport.top,
      contentOffset: rect.top - viewport.top + el.scrollTop,
    };
    scrollSnapshots.set(sid, snap);
    sessionAnchorRef.current = {
      sessionId: sid,
      rowKey,
      ...(identity ? { identity } : {}),
      offset: rect.top - viewport.top,
    };
    // Stable identity: the session id is read from a ref, so the unmount
    // cleanup that captures the final position no longer re-runs on every
    // session switch (where it used to write a snapshot for the wrong session).
  }, []);

  const scrollToMessage = useCallback((message: import('@/types').Message, historyIndex?: number): boolean => {
    const identity = getMessageIdentity(message);
    const itemIndex = grouped.findIndex((item) => {
      if ('type' in item && item.type === 'tool_group') return false;
      return getMessageIdentity(item as import('@/types').Message) === identity;
    });
    if (itemIndex < 0) return false;
    virtualizer.scrollToIndex(itemIndex, { align: 'center', behavior: 'auto' });
    setHighlightedTarget({ identity, historyIndex });
    // A jump moves the viewport without touching the scroll listener (and may
    // not even change totalSize), so refresh the round-trip anchor once the
    // targeted row has landed. Otherwise leaving right after a jump would
    // remember the position from before it.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const el = parentRef.current;
        if (el) rememberScrollPosition(el);
      });
    });
    return true;
  }, [grouped, virtualizer, rememberScrollPosition]);

  useImperativeHandle(ref, () => ({ scrollToMessage }), [scrollToMessage]);

  useEffect(() => {
    if (!highlightedTarget) return;
    const timer = window.setTimeout(() => setHighlightedTarget(null), 1400);
    return () => window.clearTimeout(timer);
  }, [highlightedTarget]);

  // Whether the user wants the view to follow the bottom. This is deliberately
  // state held outside React rendering: a streaming delta can arrive between
  // renders, and a render must not infer "follow" from a transient scrollTop.
  // Starts true so the first message load of a session scrolls down; a user
  // scroll away from the follow zone opts out until they return to it (or
  // explicitly press the scroll-to-bottom button). A restore must NOT start in
  // follow mode: that was what dragged a reader back to the bottom.
  const shouldFollowBottomRef = useRef(!isRestoringRef.current);
  // Scroll events happen outside React rendering. Keep a renderable copy so
  // the button appears/disappears immediately when the user crosses the
  // follow boundary.
  const [isNearBottom, setIsNearBottom] = useState(true);
  // A newly selected session starts with an empty/unlaid-out container. Allow
  // its first history render to establish the initial bottom position even
  // though scrollTop is 0 before the content is mounted. On a restore the
  // position is already known, so nothing may override it.
  const initialScrollPendingRef = useRef(!isRestoringRef.current);

  // Unlike a route round-trip, selecting another session reuses this mounted
  // component. Pick up that session's saved anchor during render so the layout
  // restore runs before the auto-scroll effect can pin it to the end — but only
  // while the reader opted into position memory (`Keep reading position per
  // session`). With the default (off) the adopt is skipped, so the session
  // switch effect below runs its unconditional "start at the newest message"
  // path. Keep the stored anchor so a later visit with the switch enabled can
  // still restore the position the reader last saved for that session.
  if (restoreSessionRef.current !== currentSessionId) {
    restoreSessionRef.current = currentSessionId;
    ignoreUnattributedScrollRef.current = true;
    const pendingSnapshot = currentSessionId ? scrollSnapshots.get(currentSessionId) ?? null : null;
    if (!settingsLoaded && pendingSnapshot) {
      sessionSwitchWaitingForSettingsRef.current = true;
      restoreRef.current = null;
      sessionAnchorRef.current = null;
      isRestoringRef.current = true;
      shouldFollowBottomRef.current = false;
      initialScrollPendingRef.current = false;
    } else if (!settingsLoaded) {
      // A session with no saved position has the same first-open behavior
      // whether the preference resolves true or false: follow its latest tail.
      sessionSwitchWaitingForSettingsRef.current = false;
      restoreRef.current = null;
      sessionAnchorRef.current = null;
      isRestoringRef.current = false;
      shouldFollowBottomRef.current = true;
      initialScrollPendingRef.current = true;
    } else {
      sessionSwitchWaitingForSettingsRef.current = false;
      restoreRef.current = keepScrollOnSessionSwitch && currentSessionId
        ? scrollSnapshots.get(currentSessionId) ?? null
        : null;
      sessionAnchorRef.current = restoreRef.current && currentSessionId
        ? {
            sessionId: currentSessionId,
            rowKey: restoreRef.current.rowKey,
            identity: restoreRef.current.identity,
            offset: restoreRef.current.anchorOffset,
          }
        : null;
      isRestoringRef.current = restoreRef.current !== null;
      shouldFollowBottomRef.current = !isRestoringRef.current;
      initialScrollPendingRef.current = !isRestoringRef.current;
    }
  } else if (sessionSwitchWaitingForSettingsRef.current && settingsLoaded) {
    sessionSwitchWaitingForSettingsRef.current = false;
    restoreRef.current = keepScrollOnSessionSwitch && currentSessionId
      ? scrollSnapshots.get(currentSessionId) ?? null
      : null;
    sessionAnchorRef.current = restoreRef.current && currentSessionId
      ? {
          sessionId: currentSessionId,
          rowKey: restoreRef.current.rowKey,
          identity: restoreRef.current.identity,
          offset: restoreRef.current.anchorOffset,
        }
      : null;
    isRestoringRef.current = restoreRef.current !== null;
    shouldFollowBottomRef.current = !isRestoringRef.current;
    initialScrollPendingRef.current = !isRestoringRef.current;
  }

  const lastScrollMetricsRef = useRef<{
    height: number;
    top: number;
    clientHeight: number;
  } | null>(null);
  const paginationAnchorRef = useRef<{
    top: number;
    scrollTop: number;
    height: number;
    element: HTMLElement | null;
    measurable: boolean;
    // Prepend anchors use the same stable display identity as the virtualizer,
    // so a newly inserted page cannot turn a connected row into a different
    // message while React/TanStack recycles the render window.
    rowKey?: string;
    // Route restores must follow the logical row, not a DOM node that the
    // virtualizer may recycle after history is prepended.
    identity?: string;
  } | null>(null);
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
  const upwardPaginationArmedRef = useRef(false);
  const lastScrollTopRef = useRef<number | null>(null);
  const lastTouchYRef = useRef<number | null>(null);
  const paginationRestoreRafRef = useRef<number | null>(null);
  const sessionAnchorRestoreRafRef = useRef<number | null>(null);
  const sessionBottomRafRef = useRef<number | null>(null);
  const paginationRequestInFlightRef = useRef(false);
  const historyLoadingRef = useRef(historyLoading);
  historyLoadingRef.current = historyLoading;

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

  const captureScrollMetrics = useCallback((recordAnchor = false) => {
    const el = parentRef.current;
    if (!el) return;
    lastScrollMetricsRef.current = {
      height: el.scrollHeight,
      top: el.scrollTop,
      clientHeight: el.clientHeight,
    };
    // Layout, virtualizer, and restoration scrolls are useful for follow
    // bookkeeping, but they are not a new reading position. Only callers that
    // can attribute the movement to the reader should replace the saved anchor.
    if (recordAnchor) rememberScrollPosition(el);
  }, [rememberScrollPosition]);

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
    upwardPaginationArmedRef.current = false;
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

  const markUserScrollInput = useCallback((event?: Event, direction?: 'older' | 'newer') => {
    ignoreUnattributedScrollRef.current = false;
    if (!direction && event instanceof WheelEvent) {
      direction = event.deltaY < 0 ? 'older' : event.deltaY > 0 ? 'newer' : undefined;
    }
    if (!direction && event instanceof KeyboardEvent) {
      direction = ['ArrowUp', 'PageUp', 'Home'].includes(event.key)
        ? 'older'
        : ['ArrowDown', 'PageDown', 'End', ' '].includes(event.key)
          ? 'newer'
          : undefined;
    }
    if (!direction && event instanceof TouchEvent && event.touches.length > 0) {
      const currentY = event.touches[0]?.clientY;
      const previousY = lastTouchYRef.current;
      if (currentY !== undefined && previousY !== null) {
        direction = currentY > previousY ? 'older' : currentY < previousY ? 'newer' : undefined;
      }
      if (currentY !== undefined) lastTouchYRef.current = currentY;
    }
    if (sessionBottomRafRef.current !== null) {
      cancelAnimationFrame(sessionBottomRafRef.current);
      sessionBottomRafRef.current = null;
    }
    // A new explicit input always wins over an older layout suppression. This
    // prevents a bounded correction window from swallowing the next gesture.
    clearProgrammaticSuppression();
    // A previous prepend/route restore may still be correcting its anchor on
    // animation frames. Letting that stale correction run after this gesture
    // can pull the viewport away from the top before the debounced pagination
    // check, making the next history page impossible to request.
    if (paginationRestoreRafRef.current !== null) {
      cancelAnimationFrame(paginationRestoreRafRef.current);
      paginationRestoreRafRef.current = null;
    }
    if (sessionAnchorRestoreRafRef.current !== null) {
      cancelAnimationFrame(sessionAnchorRestoreRafRef.current);
      sessionAnchorRestoreRafRef.current = null;
    }
    paginationAnchorRef.current = null;
    restoreRef.current = null;
    isRestoringRef.current = false;
    if (direction === 'older') upwardPaginationArmedRef.current = true;
    else if (direction === 'newer') upwardPaginationArmedRef.current = false;
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

  const restorePaginationAnchor = useCallback(() => {
    const el = parentRef.current;
    const anchor = paginationAnchorRef.current;
    if (!el || !anchor) return;

    // Re-resolve stable identities on every correction instead of trusting a
    // connected DOM node: a virtualizer may recycle that node for another row.
    if (anchor.rowKey) {
      const current = [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find(
        (node) => node.dataset.scrollAnchorKey === anchor.rowKey,
      );
      if (current) {
        anchor.element = current;
        anchor.measurable = current.getBoundingClientRect().height > 0;
      } else {
        anchor.element = null;
        anchor.measurable = false;
        const anchorIndex = groupedRef.current.findIndex((item, index) =>
          measuredRowKey(currentSessionIdRef.current, item, index) === anchor.rowKey,
        );
        if (anchorIndex >= 0) {
          if (!userScrollStateRef.current.active) markProgrammaticChange();
          virtualizerRef.current.scrollToIndex(anchorIndex, { align: 'start', behavior: 'auto' });
          return;
        }
        // The logical row was removed (for example by a filter or history
        // replacement). Fall back to the old content-height delta below.
        anchor.rowKey = undefined;
      }
    }

    if (anchor.identity) {
      const current = [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find(
        (node) => node.dataset.messageIdentity === anchor.identity,
      );
      if (current) {
        anchor.element = current;
        anchor.measurable = current.getBoundingClientRect().height > 0;
      } else {
        anchor.element = null;
        anchor.measurable = false;
        const anchorIndex = groupedRef.current.findIndex((item) => {
          if ('type' in item && item.type === 'tool_group') return false;
          return getMessageIdentity(item as import('@/types').Message) === anchor.identity;
        });
        if (anchorIndex >= 0) {
          if (!userScrollStateRef.current.active) markProgrammaticChange();
          virtualizerRef.current.scrollToIndex(anchorIndex, { align: 'center', behavior: 'auto' });
          return;
        }
      }
    }

    if (anchor.element?.isConnected && anchor.measurable) {
      // The identity check above makes this safe even when the virtualizer
      // reuses a connected element for another index.
      const delta = anchor.element.getBoundingClientRect().top - anchor.top;
      if (Math.abs(delta) > 0.5) {
        if (!userScrollStateRef.current.active) markProgrammaticChange();
        el.scrollTop += delta;
      }
      return;
    }

    // jsdom and a detached non-route virtual row have no usable geometry. Keep
    // the existing height-delta fallback for that case and for the test seam.
    if (!anchor.identity) el.scrollTop = anchor.scrollTop + el.scrollHeight - anchor.height;
  }, [markProgrammaticChange]);

  const restoreSessionViewportAnchor = useCallback((): boolean => {
    const el = parentRef.current;
    const anchor = sessionAnchorRef.current;
    if (
      !el ||
      !anchor ||
      anchor.sessionId !== currentSessionIdRef.current ||
      sessionSwitchWaitingForSettingsRef.current ||
      shouldFollowBottomRef.current
    ) return false;

    let row = anchor.identity
      ? [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find(
          (node) => node.dataset.messageIdentity === anchor.identity,
        ) ?? null
      : null;
    if (!row && anchor.rowKey) {
      row = [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find(
        (node) => node.dataset.scrollAnchorKey === anchor.rowKey,
      ) ?? null;
    }
    if (row && row.getBoundingClientRect().height <= 0 && anchor.rowKey) {
      row = [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find(
        (node) => node.dataset.scrollAnchorKey === anchor.rowKey,
      ) ?? row;
    }
    if (!row || row.getBoundingClientRect().height <= 0) {
      const index = anchor.rowKey
        ? groupedRef.current.findIndex((item, itemIndex) =>
            measuredRowKey(currentSessionIdRef.current, item, itemIndex) === anchor.rowKey,
          )
        : anchor.identity
          ? groupedRef.current.findIndex((item) => {
              if ('type' in item && item.type === 'tool_group') return false;
              return getMessageIdentity(item as import('@/types').Message) === anchor.identity;
            })
          : -1;
      if (index >= 0) {
        if (!userScrollStateRef.current.active) markProgrammaticChange();
        virtualizerRef.current.scrollToIndex(index, { align: 'start', behavior: 'auto' });
      }
      return false;
    }

    const viewport = el.getBoundingClientRect();
    const delta = row.getBoundingClientRect().top - (viewport.top + anchor.offset);
    if (Math.abs(delta) > 0.5) {
      if (!userScrollStateRef.current.active) markProgrammaticChange();
      el.scrollTop += delta;
    }
    return true;
  }, [markProgrammaticChange]);

  const scheduleSessionAnchorRestore = useCallback(() => {
    if (sessionAnchorRestoreRafRef.current !== null) return;
    let frameCount = 0;
    let stableFrames = 0;
    let previousSignature = '';
    const tick = () => {
      sessionAnchorRestoreRafRef.current = null;
      const el = parentRef.current;
      const anchor = sessionAnchorRef.current;
      if (
        !el ||
        !anchor ||
        anchor.sessionId !== currentSessionIdRef.current ||
        sessionSwitchWaitingForSettingsRef.current ||
        shouldFollowBottomRef.current
      ) return;

      const resolved = restoreSessionViewportAnchor();
      const row = anchor.identity
        ? [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find(
            (node) => node.dataset.messageIdentity === anchor.identity,
          ) ?? null
        : anchor.rowKey
          ? [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find(
          (node) => node.dataset.scrollAnchorKey === anchor.rowKey,
        ) ?? null
          : null;
      const rowTop = resolved && row ? row.getBoundingClientRect().top : null;
      const signature = `${el.scrollHeight}:${el.scrollTop}:${rowTop}`;
      stableFrames = resolved && signature === previousSignature ? stableFrames + 1 : 0;
      previousSignature = signature;
      frameCount += 1;
      // Virtual row ResizeObserver measurements can land several frames after
      // a presentation/CSS change. Keep the session anchor live through that
      // settling window; real scroll input cancels this correction loop.
      if (resolved && !historyLoadingRef.current && stableFrames >= 24) return;
      if (frameCount >= 90) return;
      sessionAnchorRestoreRafRef.current = requestAnimationFrame(tick);
    };
    sessionAnchorRestoreRafRef.current = requestAnimationFrame(tick);
  }, [restoreSessionViewportAnchor]);


  const schedulePaginationRestore = useCallback(() => {
    if (paginationRestoreRafRef.current !== null) return;

    let stableFrames = 0;
    let frameCount = 0;
    let previousSignature = '';
    const tick = () => {
      paginationRestoreRafRef.current = null;
      const el = parentRef.current;
      const anchor = paginationAnchorRef.current;
      if (!el || !anchor) return;

      restorePaginationAnchor();
      // An anchor that is not rendered yet is *unresolved*, not stable: counting
      // those frames lets the loop quit before the row ever appears (the row
      // then lands wherever the entry positioning left it, hundreds of px off).
      const resolved = Boolean((anchor.identity || anchor.rowKey) && anchor.element?.isConnected && anchor.measurable);
      const anchorTop = resolved ? anchor.element!.getBoundingClientRect().top : null;
      const signature = `${el.scrollHeight}:${el.scrollTop}:${anchorTop}`;
      stableFrames = resolved && signature === previousSignature ? stableFrames + 1 : 0;
      previousSignature = signature;
      frameCount += 1;

      // A route can be re-entered while the jump is still fetching several
      // pages. Keep the same logical anchor pinned for the whole in-flight
      // load; otherwise the short settle window can end between pages and
      // each prepend visibly moves the reader before the next render. A few
      // quiet frames are retained after a request completes because
      // ensureMessageLoaded starts the next page immediately after the prior
      // response, while React may render the intermediate state first.
      const routeRestore = Boolean(restoreRef.current);
      const quietLimit = routeRestore ? 12 : 2;
      if (resolved && !historyLoadingRef.current && stableFrames >= quietLimit) {
        paginationAnchorRef.current = null;
        if (routeRestore) restoreRef.current = null;
        return;
      }
      if (resolved && !routeRestore && frameCount >= 30) {
        paginationAnchorRef.current = null;
        return;
      }
      // Safety ceiling: never spin forever waiting for an anchor that may never
      // render (a removed message, or a window that keeps evicting it).
      if (frameCount >= 90) {
        paginationAnchorRef.current = null;
        if (routeRestore) restoreRef.current = null;
        return;
      }
      paginationRestoreRafRef.current = requestAnimationFrame(tick);
    };

    paginationRestoreRafRef.current = requestAnimationFrame(tick);
  }, [restorePaginationAnchor]);

  // Auto-scroll on new messages / measurement-driven size changes — but only
  // when the user hasn't scrolled away from the bottom. This is ALSO what
  // lands the view at the bottom after entering a session: the session-change
  // effect below sets the initial-scroll flag, so when the asynchronously-
  // loaded history arrives (currentMessages changes) — and again once the
  // virtualizer measures the real heights (totalSize changes) — we scroll only
  // when the container is truly at the bottom, except for that initial history
  // render.
  useEffect(() => {
    if (sessionSwitchWaitingForSettingsRef.current) return;
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
      (
        initialScrollPendingRef.current ||
        nearBottom ||
        grewWhilePinned ||
        (layoutDrivenUpdate && !userScrollStateRef.current.active)
      )
    ) {
      initialScrollPendingRef.current = false;
      scrollToBottom();
      followedBottom = true;
    }
    setIsNearBottom(followedBottom || nearBottom || grewWhilePinned);
    captureScrollMetrics();
  }, [currentMessages, totalSize, captureScrollMetrics, isNearBottomPosition, markLayoutChange, scrollToBottom, settingsLoaded]);

  // Coming back from another route: put the remembered row back under the
  // reader's eyes before the first paint, then hand the anchor to the shared
  // restore loop so the virtualizer's re-measurement cannot move it either.
  const restoreAttemptsRef = useRef(0);
  useLayoutEffect(() => {
    const memory = restoreRef.current;
    const el = parentRef.current;
    if (!memory || !el || grouped.length === 0) return;
    if (restoreAttemptsRef.current > 8) {
      restoreRef.current = null;
      return;
    }
    restoreAttemptsRef.current += 1;
    const fingerprintMatches = memory.fingerprint === listFingerprint(grouped);

    const desiredTop = el.getBoundingClientRect().top + memory.anchorOffset;
    const anchorRow = (memory.identity
      ? [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find(
          (node) => node.dataset.messageIdentity === memory.identity,
        )
      : undefined) ?? (memory.rowKey
      ? [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find(
          (node) => node.dataset.scrollAnchorKey === memory.rowKey,
        )
      : undefined) ?? null;
    sessionAnchorRef.current = {
      sessionId: currentSessionId ?? '',
      rowKey: memory.rowKey,
      identity: memory.identity,
      offset: memory.anchorOffset,
    };
    if (!anchorRow) {
      // The virtualizer has not rendered that window yet. Resolve the stable
      // display-row key first (which also covers grouped TUI/Bubble rows), then
      // fall back to the first logical message identity.
      const anchorIndex = memory.rowKey
        ? grouped.findIndex((item, index) => measuredRowKey(currentSessionId, item, index) === memory.rowKey)
        : memory.identity
          ? grouped.findIndex((item) => {
              if ('type' in item && item.type === 'tool_group') return false;
              return getMessageIdentity(item as import('@/types').Message) === memory.identity;
            })
          : -1;
      if (anchorIndex >= 0) {
        markProgrammaticChange();
        virtualizer.scrollToIndex(anchorIndex, { align: 'center', behavior: 'auto' });
        scheduleSessionAnchorRestore();
      } else {
        // The anchor message is no longer in the list (an edit replaces the
        // Message object, and with it the identity), so the remembered content
        // offset is the only hint left. Take the approximate jump: a rough
        // position is strictly better than not moving at all, because staying at
        // the top of the history loses the reader's place entirely. A stale
        // offset can only be off by the content delta, and the identity path
        // above already covers every case where the row still exists.
        markProgrammaticChange();
        el.scrollTop = Math.max(0, memory.contentOffset - memory.anchorOffset);
      }
      return;
    }

    const delta = anchorRow.getBoundingClientRect().top - desiredTop;
    if (Math.abs(delta) > 0.5) {
      markProgrammaticChange();
      el.scrollTop += delta;
    }
    // Keep correcting the same identity while an off-screen history jump is
    // still loading pages. Clearing this after the first stable frame would
    // allow the next prepend to move the reader before the next render.
    const keepRouteRestore = hasMoreMessages || historyLoading || !fingerprintMatches;
    if (!keepRouteRestore) restoreRef.current = null;
    scheduleSessionAnchorRestore();

    const nearBottom = isNearBottomPosition();
    shouldFollowBottomRef.current = nearBottom;
    setIsNearBottom(nearBottom);
    captureScrollMetrics();
  }, [
    grouped,
    captureScrollMetrics,
    hasMoreMessages,
    historyLoading,
    currentSessionId,
    isNearBottomPosition,
    markProgrammaticChange,
    scheduleSessionAnchorRestore,
    settingsLoaded,
    virtualizer,
  ]);

  useLayoutEffect(() => {
    if (sessionSwitchWaitingForSettingsRef.current) return;
    const presentationChanged = lastTuiViewEnabledRef.current !== tuiViewEnabled;
    lastTuiViewEnabledRef.current = tuiViewEnabled;
    if (presentationChanged) {
      // Row measurements are presentation-specific. Do not seed Bubble from
      // TUI heights (or vice versa); measure the committed DOM before the
      // session anchor correction loop settles.
      if (currentSessionId) measuredHeights.delete(currentSessionId);
      virtualizerRef.current.measure();
    }
    const restored = restoreSessionViewportAnchor();
    if (restored) {
      captureScrollMetrics(true);
    }
    scheduleSessionAnchorRestore();
  }, [
    currentMessages,
    totalSize,
    currentSessionId,
    captureScrollMetrics,
    restoreSessionViewportAnchor,
    scheduleSessionAnchorRestore,
    settingsLoaded,
    tuiViewEnabled,
  ]);

  // Prepending first commits estimated rows, then the virtualizer measures
  // them. Restore before paint and keep correcting for the short measurement
  // window so neither phase can visibly move the user's anchor.
  useLayoutEffect(() => {
    if (!paginationAnchorRef.current) return;
    restorePaginationAnchor();
    schedulePaginationRestore();
  }, [currentMessages, totalSize, restorePaginationAnchor, schedulePaginationRestore]);

  // Keep this session's measured row heights for the next mount.
  useEffect(() => {
    const parentElement = parentRef.current;
    return () => {
      // The user can leave in the same tick as a navigation jump. Capture at
      // unmount as a final safety net instead of relying only on scroll events
      // or the jump's delayed rAF callback.
      // Read the session id from the render that produced these rows (not from
      // an effect-assigned variable): a switch that unmounts in the same tick
      // must file the heights under the session whose rows are on screen.
      const sid = currentSessionIdRef.current;
      if (parentElement && (!sid || !scrollSnapshots.has(sid))) {
        rememberScrollPosition(parentElement);
      }
      const cache = virtualizerRef.current.measurementsCache;
      if (!sid || !Array.isArray(cache)) return;
      const heights = new Map<string, number>();
      for (const measurement of cache) heights.set(String(measurement.key), measurement.size);
      if (heights.size > 0) measuredHeights.set(sid, heights);
    };
  }, [rememberScrollPosition]);

  const loadOlderFromViewport = useCallback((requireUpwardGesture: boolean) => {
    const el = parentRef.current;
    if (
      !el ||
      !currentSessionIdRef.current ||
      !hasMoreMessages ||
      historyLoadingRef.current ||
      paginationRequestInFlightRef.current ||
      sessionSwitchWaitingForSettingsRef.current ||
      (requireUpwardGesture && !upwardPaginationArmedRef.current)
    ) {
      upwardPaginationArmedRef.current = false;
      return;
    }

    const viewport = el.getBoundingClientRect();
    const messageElement = [...el.querySelectorAll<HTMLElement>('[data-message-identity]')].find((node) => {
      const rect = node.getBoundingClientRect();
      return rect.bottom > viewport.top && rect.top < viewport.bottom;
    });
    const anchorRow = messageElement?.closest<HTMLElement>('[data-scroll-anchor-key]') ??
      [...el.querySelectorAll<HTMLElement>('[data-scroll-anchor-key]')].find((node) => {
        const rect = node.getBoundingClientRect();
        return rect.bottom > viewport.top && rect.top < viewport.bottom;
      });
    const anchorElement = messageElement ?? anchorRow;
    const anchorRect = anchorElement?.getBoundingClientRect();
    const identity = messageElement?.dataset.messageIdentity ?? anchorRow?.dataset.messageIdentity ??
      anchorRow?.querySelector<HTMLElement>('[data-message-identity]')?.dataset.messageIdentity ??
      anchorElement?.querySelector<HTMLElement>('[data-message-identity]')?.dataset.messageIdentity;
    paginationAnchorRef.current = {
      top: anchorRect?.top ?? 0,
      scrollTop: el.scrollTop,
      height: el.scrollHeight,
      element: anchorElement ?? null,
      measurable: Boolean(anchorRect && anchorRect.height > 0),
      rowKey: anchorRow?.dataset.scrollAnchorKey,
      ...(identity ? { identity } : {}),
    };
    if (anchorRow?.dataset.scrollAnchorKey && anchorRect) {
      sessionAnchorRef.current = {
        sessionId: currentSessionIdRef.current,
        rowKey: anchorRow.dataset.scrollAnchorKey,
        ...(identity ? { identity } : {}),
        offset: anchorRect.top - viewport.top,
      };
    }

    upwardPaginationArmedRef.current = false;
    paginationRequestInFlightRef.current = true;
    void loadOlderMessages().then(
      () => {
        paginationRequestInFlightRef.current = false;
        upwardPaginationArmedRef.current = false;
        markProgrammaticChange();
        restorePaginationAnchor();
        schedulePaginationRestore();
      },
      () => {
        paginationRequestInFlightRef.current = false;
        paginationAnchorRef.current = null;
      },
    );
  }, [
    hasMoreMessages,
    loadOlderMessages,
    markProgrammaticChange,
    restorePaginationAnchor,
    schedulePaginationRestore,
  ]);

  // Lazy load older messages on scroll to top
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    lastScrollTopRef.current = el.scrollTop;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const markUserScrollKey = (event: KeyboardEvent) => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
        markUserScrollInput(event);
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
      const previousTop = lastScrollTopRef.current;
      const movedOlder = previousTop !== null && el.scrollTop < previousTop;
      const movedNewer = previousTop !== null && el.scrollTop > previousTop;
      lastScrollTopRef.current = el.scrollTop;
      const nearBottom = isNearBottomPosition();
      if (ignoreUnattributedScrollRef.current) {
        setIsNearBottom(nearBottom);
        return;
      }
      initialScrollPendingRef.current = false;
      const programmaticSuppressed = programmaticSuppressionRef.current !== null;
      const userDrivenScroll = !programmaticSuppressed && refreshUserScrollActivity();
      if (programmaticSuppressed) {
        // Consume the correction window on the first resulting scroll. A new
        // wheel/touch/pointer/key input cancels this suppression first, so a
        // later genuine gesture can never be permanently ignored.
        clearProgrammaticSuppression();
      } else if (userDrivenScroll) {
        shouldFollowBottomRef.current = nearBottom;
        if (movedOlder) upwardPaginationArmedRef.current = true;
        else if (movedNewer) upwardPaginationArmedRef.current = false;
      } else if (nearBottom) {
        // Returning to the bottom is an explicit recovery path even when the
        // browser emits the final scroll without another input event.
        shouldFollowBottomRef.current = nearBottom;
      }
      setIsNearBottom(nearBottom);
      captureScrollMetrics(userDrivenScroll);

      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        if (
          el.scrollTop <= 200 &&
          hasMoreMessages &&
          !historyLoading &&
          userScrollStateRef.current.active &&
          upwardPaginationArmedRef.current &&
          programmaticSuppressionRef.current === null
        ) {
          loadOlderFromViewport(true);
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
    loadOlderFromViewport,
    loadOlderMessages,
    markProgrammaticChange,
    markUserScrollInput,
    refreshUserScrollActivity,
    scheduleUserScrollExpiry,
    restorePaginationAnchor,
    schedulePaginationRestore,
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
  const handledSessionRef = useRef<string | null>(isRestoringRef.current ? currentSessionId : null);
  const measuredSessionRef = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (sessionSwitchWaitingForSettingsRef.current && !settingsLoaded) return;
    if (handledSessionRef.current !== currentSessionId) {
      clearUserScrollActivity();
      clearProgrammaticSuppression();
      paginationAnchorRef.current = null;
      paginationRequestInFlightRef.current = false;
      if (paginationRestoreRafRef.current !== null) {
        cancelAnimationFrame(paginationRestoreRafRef.current);
        paginationRestoreRafRef.current = null;
      }
      if (sessionBottomRafRef.current !== null) {
        cancelAnimationFrame(sessionBottomRafRef.current);
        sessionBottomRafRef.current = null;
      }
    }
    // Same session and no session switch: this run is the mount of a route
    // re-entry, whose restored position must not be reset to the newest message.
    // Its folded disclosures still need fresh measurements: cached heights
    // came from the previous visit, when a group may have been expanded.
    if (handledSessionRef.current === currentSessionId) {
      if (isRestoringRef.current && measuredSessionRef.current !== currentSessionId) {
        if (currentSessionId) invalidateDisclosureHeights(currentSessionId, groupedRef.current);
        virtualizerRef.current.measure();
        measuredSessionRef.current = currentSessionId;
      }
      return;
    }
    handledSessionRef.current = currentSessionId;
    // A session selected from the sidebar may have a saved anchor. The layout
    // restore already handled it; remeasure folded rows without clearing its
    // anchor or resetting scroll position.
    if (isRestoringRef.current) {
      if (currentSessionId) invalidateDisclosureHeights(currentSessionId, groupedRef.current);
      virtualizerRef.current.measure();
      measuredSessionRef.current = currentSessionId;
      isRestoringRef.current = false;
      return;
    }
    // A genuine session switch without a saved anchor starts at the newest
    // message. Do not discard snapshots belonging to other sessions.
    if (currentSessionId) measuredHeights.delete(currentSessionId);
    // A Session may have been visited with its non-body disclosure expanded.
    // The component remounts folded on this switch, but TanStack can still
    // hold the prior expanded row size under the same session-scoped key.
    // Rebuild the virtual measurements against the committed (folded) DOM.
    virtualizerRef.current.measure();
    measuredSessionRef.current = currentSessionId;
    shouldFollowBottomRef.current = true;
    setIsNearBottom(true);
    initialScrollPendingRef.current = true;
    lastScrollMetricsRef.current = null;
    paginationAnchorRef.current = null;
    layoutChangePendingRef.current = false;
    clearUserScrollActivity();
    clearProgrammaticSuppression();
    if (paginationRestoreRafRef.current !== null) {
      cancelAnimationFrame(paginationRestoreRafRef.current);
      paginationRestoreRafRef.current = null;
    }
    if (sessionAnchorRestoreRafRef.current !== null) {
      cancelAnimationFrame(sessionAnchorRestoreRafRef.current);
      sessionAnchorRestoreRafRef.current = null;
    }
    scrollToBottom();
    // If this session already has a mounted message container, the session
    // switch itself performed the initial positioning. Keep later updates
    // subject to the strict bottom check; leave the flag pending only when
    // history is still empty and its container has not mounted yet.
    if (parentRef.current) {
      initialScrollPendingRef.current = false;
    }
    if (sessionBottomRafRef.current !== null) cancelAnimationFrame(sessionBottomRafRef.current);
    sessionBottomRafRef.current = requestAnimationFrame(() => {
      sessionBottomRafRef.current = null;
      scrollToBottom();
    });
    return () => {
      if (sessionBottomRafRef.current !== null) {
        cancelAnimationFrame(sessionBottomRafRef.current);
        sessionBottomRafRef.current = null;
      }
      if (layoutChangeRafRef.current !== null) cancelAnimationFrame(layoutChangeRafRef.current);
      clearUserScrollActivity();
      clearProgrammaticSuppression();
    };
  }, [
    clearProgrammaticSuppression,
    clearUserScrollActivity,
    currentSessionId,
    settingsLoaded,
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
            onClick={() => loadOlderFromViewport(false)}
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
    // `min-w-0` is load-bearing. As a flex item this column defaults to
    // `min-width: auto`, so a row holding a long unbreakable token (URL, path,
    // inline code) can stretch the column to its min-content width. The bubble's
    // `max-width: 75%/85%` then resolves against that inflated row instead of
    // the real viewport, painting the bubble past the right screen edge.
    <div className="flex-1 flex flex-col min-h-0 min-w-0 relative">
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
                data-scroll-anchor-key={measuredRowKey(currentSessionId, item, vItem.index)}
                data-message-identity={
                  'type' in item && item.type === 'tool_group'
                    ? undefined
                    : getMessageIdentity(item as import('@/types').Message)
                }
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
                {!('type' in item && item.type === 'tool_group') &&
                highlightedTarget?.identity === getMessageIdentity(item as import('@/types').Message) ? (
                  <div
                    className="chat-message-jump-highlight"
                    data-index={highlightedTarget.historyIndex ?? vItem.index}
                  >
                    <MessageDisplayItem
                      item={item}
                      prevRole={prevRole}
                      onTimestampFlashConsumed={clearTimestampFlash}
                    />
                  </div>
                ) : (
                  <MessageDisplayItem
                    item={item}
                    prevRole={prevRole}
                    onTimestampFlashConsumed={clearTimestampFlash}
                  />
                )}
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
});
