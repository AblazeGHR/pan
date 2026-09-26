import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { Bot, Loader2, UserRound } from 'lucide-react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useSessionStore } from '@/stores/sessionStore';
import { fetchSessionHistory } from '@/services/api';
import {
  getQuickJumpIndexItems,
  type QuickJumpIndexItem,
  type QuickJumpKind,
} from './messageFilter';
import type { ChatMessagesHandle } from './ChatMessages';

interface MessageNavigationRailProps {
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile?: boolean;
  mobileExpanded?: boolean;
}

type IndexStatus = 'idle' | 'loading' | 'ready' | 'error';

const USER_LABEL = '\u7528\u6237';
const RAIL_LABEL = '\u5feb\u901f\u5b9a\u4f4d';
const PREVIEW_FALLBACK = '\u65e0\u9884\u89c8\u5185\u5bb9';
const INDEX_PAGE_SIZE = 200;
const LONG_PRESS_MS = 450;
const PRE_LONG_PRESS_MOVE_PX = 10;

interface ScrubGesture {
  pointerId: number;
  startX: number;
  startY: number;
  fromEnd: number;
  active: boolean;
  moved: boolean;
  timer: number | null;
}

type PreviewMode = 'hover' | 'scrub';

const FILTERS: Array<{ kind: QuickJumpKind; label: string }> = [
  { kind: 'user', label: USER_LABEL },
  { kind: 'worker', label: 'Worker' },
];

function MarkerIcon({ kind }: { kind: QuickJumpKind }) {
  return kind === 'user' ? <UserRound size={13} strokeWidth={2.4} /> : <Bot size={13} strokeWidth={2.4} />;
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

export function MessageNavigationRail({
  chatRef,
  isMobile = false,
  mobileExpanded = false,
}: MessageNavigationRailProps) {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const currentMessages = useSessionStore((s) => s.currentMessages);
  const historyLoadEnd = useSessionStore((s) => s.historyLoadEnd);
  const currentHistoryTotal = useSessionStore((s) => {
    const session = s.sessions.find((item) => item.id === s.currentSessionId);
    return session?.historyTotal ?? (s.historyLoadEnd + s.currentMessages.length);
  });
  const ensureMessageLoaded = useSessionStore((s) => s.ensureMessageLoaded);
  const showMetaAgent = useAppSettingsStore((s) => s.showMetaAgent);
  const showTaskAgent = useAppSettingsStore((s) => s.showTaskAgent);
  const showQQ = useAppSettingsStore((s) => s.showQQ);
  const settings = useMemo(
    () => ({ showMetaAgent, showTaskAgent, showQQ }),
    [showMetaAgent, showTaskAgent, showQQ],
  );
  const [activeKind, setActiveKind] = useState<QuickJumpKind>('user');
  const [activeFromEnd, setActiveFromEnd] = useState<number | null>(null);
  const [jumpingFromEnd, setJumpingFromEnd] = useState<number | null>(null);
  const [jumpError, setJumpError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<{
    target: QuickJumpIndexItem;
    top: number;
    left: number;
    mode: PreviewMode;
  } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const scrubGestureRef = useRef<ScrubGesture | null>(null);
  const suppressNextClickRef = useRef(false);
  const [fullIndex, setFullIndex] = useState<QuickJumpIndexItem[]>([]);
  const [indexStatus, setIndexStatus] = useState<IndexStatus>('idle');
  const [indexTotal, setIndexTotal] = useState(0);
  const [indexMetrics, setIndexMetrics] = useState({ requests: 0, durationMs: 0 });

  const clearScrub = useCallback((suppressClick = false) => {
    const gesture = scrubGestureRef.current;
    if (gesture?.timer !== null && gesture?.timer !== undefined) {
      window.clearTimeout(gesture.timer);
    }
    scrubGestureRef.current = null;
    if (suppressClick && gesture && (gesture.active || gesture.moved)) {
      suppressNextClickRef.current = true;
    }
    setHovered(null);
  }, []);

  useEffect(() => {
    clearScrub(false);
    setHovered(null);
    setJumpError(null);
    setActiveFromEnd(null);
    // The jump lock is single-flight per session: a jump that started in the
    // previous session must not keep every marker disabled here. `jumpTo`'s
    // finally deliberately does not touch state after a session switch, so
    // this reset is the only thing that releases the lock.
    setJumpingFromEnd(null);
    setFullIndex([]);
    setIndexTotal(0);
    setIndexMetrics({ requests: 0, durationMs: 0 });
    if (!currentSessionId) {
      setIndexStatus('idle');
      return;
    }

    const controller = new AbortController();
    let disposed = false;
    const startedAt = performance.now();
    setIndexStatus('loading');

    void (async () => {
      let before = 0;
      let requests = 0;
      let indexed: QuickJumpIndexItem[] = [];
      let historyTotal = 0;
      try {
        do {
          const page = await fetchSessionHistory(
            currentSessionId,
            before,
            INDEX_PAGE_SIZE,
            controller.signal,
          );
          requests += 1;
          historyTotal = page.total;
          if (disposed) return;
          const pageItems = getQuickJumpIndexItems(
            page.history || [],
            page.total,
            page.start,
            settings,
          );
          indexed = [...pageItems, ...indexed];
          setFullIndex(indexed);
          setIndexTotal(page.total);
          setIndexMetrics({ requests, durationMs: performance.now() - startedAt });
          before = page.start;
        } while (before > 0);
        if (disposed) return;
        const durationMs = performance.now() - startedAt;
        setIndexStatus('ready');
        setIndexMetrics({ requests, durationMs });
        console.info('[message-navigation] full index ready', {
          sessionId: currentSessionId,
          total: historyTotal,
          targets: indexed.length,
          requests,
          durationMs: Math.round(durationMs),
        });
      } catch (error) {
        if (disposed || controller.signal.aborted) return;
        console.warn('[message-navigation] full index failed; using loaded window', error);
        setIndexStatus('error');
        setIndexMetrics({ requests, durationMs: performance.now() - startedAt });
      }
    })();

    return () => {
      disposed = true;
      controller.abort();
      clearScrub(true);
    };
  }, [clearScrub, currentSessionId, settings]);

  const loadedWindowTargets = useMemo(
    () => getQuickJumpIndexItems(
      currentMessages,
      currentHistoryTotal,
      historyLoadEnd,
      settings,
    ),
    [currentMessages, currentHistoryTotal, historyLoadEnd, settings],
  );

  const allTargets = fullIndex.length > 0 && indexStatus !== 'error'
    ? fullIndex
    : loadedWindowTargets;
  const targets = useMemo(
    () => allTargets.filter((target) => target.kind === activeKind),
    [allTargets, activeKind],
  );
  const targetsByFromEnd = useMemo(
    () => new Map(targets.map((target) => [target.fromEnd, target])),
    [targets],
  );
  const counts = useMemo(() => ({
    user: allTargets.filter((item) => item.kind === 'user').length,
    worker: allTargets.filter((item) => item.kind === 'worker').length,
  }), [allTargets]);

  const jumpTo = async (target: QuickJumpIndexItem) => {
    if (!currentSessionId || jumpingFromEnd !== null) return;
    const sessionAtClick = currentSessionId;
    setJumpError(null);
    setJumpingFromEnd(target.fromEnd);
    try {
      const total = indexTotal || currentHistoryTotal;
      const message = await ensureMessageLoaded(target.fromEnd, total);
      if (!message || useSessionStore.getState().currentSessionId !== sessionAtClick) {
        if (useSessionStore.getState().currentSessionId === sessionAtClick) {
          setJumpError('Unable to load this message.');
        }
        return;
      }
      await nextPaint();
      const historyIndex = total - 1 - target.fromEnd;
      let didJump = chatRef.current?.scrollToMessage(message, historyIndex) ?? false;
      if (!didJump) {
        await nextPaint();
        didJump = chatRef.current?.scrollToMessage(message, historyIndex) ?? false;
      }
      if (!didJump) {
        setJumpError('Message loaded, but its rendered row could not be located.');
        return;
      }
      setActiveFromEnd(target.fromEnd);
      window.setTimeout(() => setActiveFromEnd(null), 1200);
    } finally {
      if (useSessionStore.getState().currentSessionId === sessionAtClick) {
        setJumpingFromEnd(null);
      }
    }
  };

  const setMarkerPreview = useCallback((element: HTMLElement, target: QuickJumpIndexItem, mode: PreviewMode) => {
    const marker = element.getBoundingClientRect();
    setHovered({ target, top: marker.top + marker.height / 2, left: marker.left - 9, mode });
  }, []);

  const showPreview = (event: MouseEvent<HTMLButtonElement>, target: QuickJumpIndexItem) => {
    setMarkerPreview(event.currentTarget, target, 'hover');
  };

  const updateScrubPreview = useCallback((x: number, y: number, fallback: EventTarget | null) => {
    const gesture = scrubGestureRef.current;
    const list = listRef.current;
    if (!gesture?.active || !list) return;

    const bounds = list.getBoundingClientRect();
    if (
      bounds.width > 0 && bounds.height > 0 &&
      (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom)
    ) {
      clearScrub(true);
      return;
    }

    const pointTarget = (typeof document.elementFromPoint === 'function'
      ? document.elementFromPoint(x, y)
      : null) ?? fallback;
    const marker = pointTarget instanceof Element
      ? pointTarget.closest<HTMLButtonElement>('.message-navigation-marker')
      : null;
    if (!marker || !list.contains(marker)) return;

    const fromEnd = Number(marker.dataset.fromEnd);
    if (!Number.isInteger(fromEnd) || fromEnd === gesture.fromEnd) return;
    const target = targetsByFromEnd.get(fromEnd);
    if (!target) return;
    gesture.fromEnd = fromEnd;
    setMarkerPreview(marker, target, 'scrub');
  }, [clearScrub, setMarkerPreview, targetsByFromEnd]);

  const beginScrub = (event: ReactPointerEvent<HTMLButtonElement>, target: QuickJumpIndexItem) => {
    if (
      !isMobile || !mobileExpanded ||
      (event.pointerType && event.pointerType !== 'touch')
    ) return;
    clearScrub(false);
    suppressNextClickRef.current = false;
    const marker = event.currentTarget;
    const gesture: ScrubGesture = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      fromEnd: target.fromEnd,
      active: false,
      moved: false,
      timer: null,
    };
    gesture.timer = window.setTimeout(() => {
      if (
        scrubGestureRef.current !== gesture ||
        useSessionStore.getState().currentSessionId !== currentSessionId
      ) return;
      gesture.active = true;
      suppressNextClickRef.current = true;
      setMarkerPreview(marker, target, 'scrub');
    }, LONG_PRESS_MS);
    scrubGestureRef.current = gesture;
  };

  const moveScrub = (event: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = scrubGestureRef.current;
    if (!gesture || (Number.isFinite(event.pointerId) && gesture.pointerId !== event.pointerId)) return;
    if (!gesture.active) {
      if (Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) > PRE_LONG_PRESS_MOVE_PX) {
        if (gesture.timer !== null) window.clearTimeout(gesture.timer);
        gesture.timer = null;
        gesture.moved = true;
      }
      return;
    }
    updateScrubPreview(event.clientX, event.clientY, event.target);
  };

  const finishScrub = (event: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const gesture = scrubGestureRef.current;
    if (!gesture || (Number.isFinite(event.pointerId) && gesture.pointerId !== event.pointerId)) return;
    clearScrub(!cancelled && (gesture.active || gesture.moved));
  };

  const handleMarkerClick = (event: MouseEvent<HTMLButtonElement>, target: QuickJumpIndexItem) => {
    if (suppressNextClickRef.current && event.detail !== 0) {
      suppressNextClickRef.current = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    void jumpTo(target);
  };

  useEffect(() => {
    if (!isMobile || !mobileExpanded) {
      clearScrub(true);
      return;
    }
    const list = listRef.current;
    if (!list) return;
    const handleTouchMove = (event: globalThis.TouchEvent) => {
      if (!scrubGestureRef.current?.active) return;
      const touch = event.changedTouches[0] ?? event.touches[0];
      if (!touch) return;
      if (event.cancelable) event.preventDefault();
      updateScrubPreview(touch.clientX, touch.clientY, event.target);
    };
    const handleTouchCancel = () => clearScrub(false);
    list.addEventListener('touchmove', handleTouchMove, { passive: false });
    list.addEventListener('touchcancel', handleTouchCancel);
    return () => {
      list.removeEventListener('touchmove', handleTouchMove);
      list.removeEventListener('touchcancel', handleTouchCancel);
    };
  }, [clearScrub, isMobile, mobileExpanded, updateScrubPreview]);

  return (
    <aside
      className="message-navigation-rail"
      aria-label={RAIL_LABEL}
      data-index-status={indexStatus}
      data-indexed-targets={allTargets.length}
      data-history-total={indexTotal || currentHistoryTotal}
      data-index-requests={indexMetrics.requests}
      data-index-duration-ms={Math.round(indexMetrics.durationMs)}
    >
      <div className="message-navigation-filters" role="tablist" aria-label={RAIL_LABEL}>
        {FILTERS.map(({ kind, label }) => (
          <button
            key={kind}
            type="button"
            role="tab"
            aria-selected={activeKind === kind}
            className={`message-navigation-filter message-navigation-filter-${kind}${activeKind === kind ? ' is-active' : ''}`}
            onClick={() => {
              clearScrub(false);
              setActiveKind(kind);
            }}
            title={`${RAIL_LABEL}: ${label}`}
          >
            <MarkerIcon kind={kind} />
            <span className="sr-only">{label}</span>
            <span className="message-navigation-count">{counts[kind]}</span>
          </button>
        ))}
      </div>

      {hovered && createPortal(
        <div
          className="message-navigation-tooltip message-navigation-tooltip-floating"
          role="tooltip"
          data-preview-mode={hovered.mode}
          data-preview-from-end={hovered.target.fromEnd}
          style={{
            top: hovered.mode === 'scrub'
              ? `clamp(56px, ${hovered.top}px, calc(100dvh - 56px))`
              : hovered.top,
            left: hovered.left,
          }}
        >
          <strong>{hovered.target.kind === 'user' ? USER_LABEL : 'Worker report'}</strong>
          <span>{hovered.target.preview || PREVIEW_FALLBACK}</span>
        </div>,
        document.body,
      )}

      <div
        ref={listRef}
        className="message-navigation-list"
        onPointerMove={moveScrub}
        onPointerUp={(event) => finishScrub(event, false)}
        onPointerCancel={(event) => finishScrub(event, true)}
        onLostPointerCapture={(event) => finishScrub(event, true)}
      >
        {targets.map((target) => {
          const label = target.kind === 'user' ? USER_LABEL : 'Worker report';
          return (
            <div className="message-navigation-marker-wrap" key={`${target.kind}-${target.fromEnd}`}>
              <button
                type="button"
                className={`message-navigation-marker message-navigation-marker-${target.kind}${activeFromEnd === target.fromEnd ? ' is-jumped' : ''}`}
                onClick={(event) => handleMarkerClick(event, target)}
                onPointerDown={(event) => beginScrub(event, target)}
                onMouseEnter={(event) => showPreview(event, target)}
                onMouseLeave={() => {
                  if (!scrubGestureRef.current?.active) setHovered(null);
                }}
                aria-label={`${label}: ${target.preview || PREVIEW_FALLBACK}`}
                title={target.preview || PREVIEW_FALLBACK}
                data-from-end={target.fromEnd}
                disabled={jumpingFromEnd !== null}
              >
                {jumpingFromEnd === target.fromEnd
                  ? <Loader2 size={13} className="animate-spin" />
                  : <MarkerIcon kind={target.kind} />}
              </button>
            </div>
          );
        })}
        {targets.length === 0 && <span className="message-navigation-empty">{'\u00b7'}</span>}
      </div>

      {indexStatus === 'loading' && (
        <div className="message-navigation-status" title={`Indexing full history (${indexMetrics.requests} requests)`}>
          <Loader2 size={11} className="animate-spin" />
          <span className="sr-only">Indexing full history</span>
        </div>
      )}
      {indexStatus === 'error' && (
        <div className="message-navigation-status message-navigation-status-error" title="Full-history index failed; showing only loaded messages.">
          !
        </div>
      )}
      {jumpError && <div className="message-navigation-jump-error" role="status">{jumpError}</div>}
    </aside>
  );
}
