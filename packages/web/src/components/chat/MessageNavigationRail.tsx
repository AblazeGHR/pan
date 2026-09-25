import { useEffect, useMemo, useState, type MouseEvent, type RefObject } from 'react';
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
}

type IndexStatus = 'idle' | 'loading' | 'ready' | 'error';

const USER_LABEL = '\u7528\u6237';
const RAIL_LABEL = '\u5feb\u901f\u5b9a\u4f4d';
const PREVIEW_FALLBACK = '\u65e0\u9884\u89c8\u5185\u5bb9';
const INDEX_PAGE_SIZE = 200;

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

export function MessageNavigationRail({ chatRef }: MessageNavigationRailProps) {
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
  const [hovered, setHovered] = useState<{ target: QuickJumpIndexItem; top: number; left: number } | null>(null);
  const [fullIndex, setFullIndex] = useState<QuickJumpIndexItem[]>([]);
  const [indexStatus, setIndexStatus] = useState<IndexStatus>('idle');
  const [indexTotal, setIndexTotal] = useState(0);
  const [indexMetrics, setIndexMetrics] = useState({ requests: 0, durationMs: 0 });

  useEffect(() => {
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
    };
  }, [currentSessionId, settings]);

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

  const showPreview = (event: MouseEvent<HTMLButtonElement>, target: QuickJumpIndexItem) => {
    const marker = event.currentTarget.getBoundingClientRect();
    setHovered({ target, top: marker.top + marker.height / 2, left: marker.left - 9 });
  };

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
            onClick={() => setActiveKind(kind)}
            title={`${RAIL_LABEL}: ${label}`}
          >
            <MarkerIcon kind={kind} />
            <span className="sr-only">{label}</span>
            <span className="message-navigation-count">{counts[kind]}</span>
          </button>
        ))}
      </div>

      {hovered && createPortal(
        <div className="message-navigation-tooltip message-navigation-tooltip-floating" role="tooltip" style={{ top: hovered.top, left: hovered.left }}>
          <strong>{hovered.target.kind === 'user' ? USER_LABEL : 'Worker report'}</strong>
          <span>{hovered.target.preview || PREVIEW_FALLBACK}</span>
        </div>,
        document.body,
      )}

      <div className="message-navigation-list">
        {targets.map((target) => {
          const label = target.kind === 'user' ? USER_LABEL : 'Worker report';
          return (
            <div className="message-navigation-marker-wrap" key={`${target.kind}-${target.fromEnd}`}>
              <button
                type="button"
                className={`message-navigation-marker message-navigation-marker-${target.kind}${activeFromEnd === target.fromEnd ? ' is-jumped' : ''}`}
                onClick={() => void jumpTo(target)}
                onMouseEnter={(event) => showPreview(event, target)}
                onMouseLeave={() => setHovered(null)}
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
