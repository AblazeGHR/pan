import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { ChevronDown, ChevronUp, Loader2, Search, X } from 'lucide-react';
import { fetchSessionHistory } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message } from '@/types';
import type { ChatMessagesHandle } from './ChatMessages';
import {
  scanSessionHistory,
  StaleSessionHistoryError,
  type HistoryVersion,
  type SessionSearchHit,
  type SessionSearchScan,
} from './sessionSearchIndex';

interface SessionHistorySearchProps {
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile: boolean;
  onHighlightMessage: (messageId: string | null) => void;
}

type SearchStatus = 'idle' | 'loading' | 'ready' | 'stale' | 'error';

interface SearchState {
  status: SearchStatus;
  scan: SessionSearchScan | null;
}

function readSessionVersion(sessionId: string): HistoryVersion {
  const session = useSessionStore.getState().sessions.find((candidate) => candidate.id === sessionId);
  return {
    total: session?.historyTotal,
    historyEpoch: session?.historyEpoch,
    historyRevision: session?.historyRevision,
  };
}

function versionChanged(scan: SessionSearchScan, current: HistoryVersion): boolean {
  return (
    (typeof current.total === 'number' && current.total !== scan.total) ||
    (current.historyEpoch != null && scan.historyEpoch != null && current.historyEpoch !== scan.historyEpoch) ||
    (typeof current.historyRevision === 'number' && typeof scan.historyRevision === 'number' &&
      current.historyRevision !== scan.historyRevision)
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (
    target.isContentEditable ||
    Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
  );
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

export function SessionHistorySearch({ chatRef, isMobile, onHighlightMessage }: SessionHistorySearchProps) {
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const summaryTotal = useSessionStore((state) => {
    const session = state.sessions.find((candidate) => candidate.id === state.currentSessionId);
    return session?.historyTotal;
  });
  const summaryEpoch = useSessionStore((state) =>
    state.sessions.find((candidate) => candidate.id === state.currentSessionId)?.historyEpoch,
  );
  const summaryRevision = useSessionStore((state) =>
    state.sessions.find((candidate) => candidate.id === state.currentSessionId)?.historyRevision,
  );
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<SearchState>({ status: 'idle', scan: null });
  const [activeIndex, setActiveIndex] = useState(0);
  const [navigating, setNavigating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const generationRef = useRef(0);
  const jumpAbortRef = useRef<AbortController | null>(null);

  const stopJump = useCallback(() => {
    generationRef.current += 1;
    jumpAbortRef.current?.abort();
    jumpAbortRef.current = null;
    setNavigating(false);
  }, []);

  const closeSearch = useCallback(() => {
    stopJump();
    setOpen(false);
    setSearch({ status: 'idle', scan: null });
    onHighlightMessage(null);
  }, [onHighlightMessage, stopJump]);

  useEffect(() => {
    const handleShortcut = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && open) {
        event.preventDefault();
        closeSearch();
        return;
      }
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'f') return;
      if (event.defaultPrevented) return;
      const targetIsSearch = popupRef.current?.contains(event.target as Node) ?? false;
      if (!targetIsSearch && isEditableTarget(event.target)) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [closeSearch, open]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [open]);

  useEffect(() => {
    if (!open || !currentSessionId || !query.trim()) {
      setSearch({ status: 'idle', scan: null });
      setActiveIndex(0);
      onHighlightMessage(null);
      return;
    }

    const sessionId = currentSessionId;
    const controller = new AbortController();
    const generation = ++generationRef.current;
    const expectedVersion = {
      total: summaryTotal,
      historyEpoch: summaryEpoch,
      historyRevision: summaryRevision,
    };
    setSearch({ status: 'loading', scan: null });
    setActiveIndex(0);
    onHighlightMessage(null);
    jumpAbortRef.current?.abort();
    setNavigating(false);

    void scanSessionHistory(fetchSessionHistory, sessionId, query, {
      signal: controller.signal,
      expectedVersion,
    }).then(async (scan) => {
      if (controller.signal.aborted || generationRef.current !== generation) return;
      const latest = useSessionStore.getState();
      if (latest.currentSessionId !== sessionId || versionChanged(scan, readSessionVersion(sessionId))) {
        setSearch({ status: 'stale', scan: null });
        return;
      }
      setSearch({ status: 'ready', scan });
      if (scan.hits.length > 0) {
        await navigateToResult(scan.hits[0]!, 0, scan, query, generation);
      }
    }).catch((error: unknown) => {
      if (controller.signal.aborted || generationRef.current !== generation || isAbortError(error)) return;
      setSearch({
        status: error instanceof StaleSessionHistoryError ? 'stale' : 'error',
        scan: null,
      });
    });

    return () => {
      controller.abort();
      if (generationRef.current === generation) generationRef.current += 1;
      jumpAbortRef.current?.abort();
      jumpAbortRef.current = null;
    };
    // `summaryVersion` is deliberately a primitive snapshot from the active
    // Session. Any epoch/revision/total update cancels this scan and starts a
    // fresh one against the new history version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, query, currentSessionId, summaryTotal, summaryEpoch, summaryRevision]);

  const navigateToResult = useCallback(async (
    initialHit: SessionSearchHit,
    resultIndex: number,
    initialScan: SessionSearchScan,
    activeQuery: string,
    generation: number,
  ) => {
    const sessionId = currentSessionId;
    if (!sessionId || generationRef.current !== generation) return;
    const controller = new AbortController();
    jumpAbortRef.current?.abort();
    jumpAbortRef.current = controller;
    setNavigating(true);

    const stillCurrent = () => !controller.signal.aborted &&
      generationRef.current === generation &&
      useSessionStore.getState().currentSessionId === sessionId;

    try {
      let target = initialHit;
      let total = initialScan.total;
      let scan = initialScan;
      let needsRelocation = versionChanged(scan, readSessionVersion(sessionId));
      let message: Message | null = null;

      const loadTarget = async (hit: SessionSearchHit, historyTotal: number) => {
        if (!stillCurrent()) return null;
        const alreadyLoaded = useSessionStore.getState().currentMessages.find(
          (candidate) => candidate.messageId === hit.messageId,
        );
        if (alreadyLoaded) return alreadyLoaded;
        const fromEnd = historyTotal - 1 - hit.messageIndex;
        return fromEnd < 0 ? null : useSessionStore.getState().ensureMessageLoaded(fromEnd, historyTotal);
      };

      if (!needsRelocation) {
        message = await loadTarget(target, total);
        if (!stillCurrent()) return;
        needsRelocation = !message || message.messageId !== target.messageId || target.messageId.startsWith('legacy:');
      }

      if (needsRelocation) {
        scan = await scanSessionHistory(fetchSessionHistory, sessionId, activeQuery, {
          signal: controller.signal,
        });
        if (!stillCurrent()) return;
        const relocated = scan.hits.find((hit) => hit.messageId === initialHit.messageId);
        if (!relocated) {
          setSearch({ status: 'stale', scan: null });
          onHighlightMessage(null);
          return;
        }
        target = relocated;
        total = scan.total;
        message = await loadTarget(target, total);
        if (!stillCurrent()) return;
      }

      if (!message || message.messageId !== target.messageId || target.messageId.startsWith('legacy:')) {
        setSearch({ status: 'stale', scan: null });
        onHighlightMessage(null);
        return;
      }

      onHighlightMessage(target.messageId);
      await nextPaint();
      if (!stillCurrent()) return;
      const currentTarget = useSessionStore.getState().currentMessages.find(
        (candidate) => candidate.messageId === target.messageId,
      );
      if (!currentTarget) {
        setSearch({ status: 'stale', scan: null });
        onHighlightMessage(null);
        return;
      }
      message = currentTarget;
      let didScroll = chatRef.current?.scrollToMessage(message, target.messageIndex) ?? false;
      if (!didScroll) {
        await nextPaint();
        if (!stillCurrent()) return;
        didScroll = chatRef.current?.scrollToMessage(message, target.messageIndex) ?? false;
      }
      if (!didScroll) {
        setSearch({ status: 'stale', scan: null });
        onHighlightMessage(null);
        return;
      }
      setActiveIndex(resultIndex);
    } catch (error) {
      if (!stillCurrent() || isAbortError(error)) return;
      if (error instanceof StaleSessionHistoryError) {
        setSearch({ status: 'stale', scan: null });
        onHighlightMessage(null);
      } else {
        setSearch({ status: 'error', scan: null });
        onHighlightMessage(null);
      }
    } finally {
      if (generationRef.current === generation && useSessionStore.getState().currentSessionId === sessionId) {
        setNavigating(false);
      }
    }
  }, [chatRef, currentSessionId, onHighlightMessage]);

  const hits = search.scan?.hits ?? [];
  const move = (delta: number) => {
    if (hits.length === 0 || !search.scan) return;
    const nextIndex = (activeIndex + delta + hits.length) % hits.length;
    setActiveIndex(nextIndex);
    void navigateToResult(hits[nextIndex]!, nextIndex, search.scan, query, generationRef.current);
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      move(event.shiftKey ? -1 : 1);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeSearch();
    }
  };

  const resultCount = hits.length > 0 ? `${activeIndex + 1} / ${hits.length}` : '0 / 0';
  const statusText = search.status === 'loading'
    ? 'Searching full Session history…'
    : search.status === 'stale'
      ? 'Results expired. Search again.'
      : search.status === 'error'
        ? 'History search failed.'
        : search.status === 'ready' && hits.length === 0
          ? 'No results'
          : !currentSessionId
            ? 'Select a Session to search.'
            : '';

  return (
    <div
      className={`session-history-search${isMobile ? ' is-mobile' : ''}`}
      data-testid="session-history-search"
      data-layout={isMobile ? 'mobile-below-session-title' : 'desktop-chat-top-right'}
    >
      <button
        type="button"
        className="session-history-search__toggle"
        aria-label={open ? 'Close Session history search' : 'Search Session history'}
        title="Search Session history (Ctrl+F)"
        data-testid="session-history-search-toggle"
        onClick={() => open ? closeSearch() : setOpen(true)}
      >
        <Search size={16} />
      </button>
      {open && (
        <div ref={popupRef} className="session-history-search__popup" role="search" aria-label="Session history search">
          <div className="session-history-search__controls">
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Search this Session…"
              aria-label="Search this Session's history"
              data-testid="session-history-search-input"
            />
            <span className="session-history-search__count" aria-live="polite" data-testid="session-history-search-count">
              {search.status === 'loading' ? <Loader2 size={13} className="animate-spin" /> : resultCount}
            </span>
            <button type="button" aria-label="Previous result" title="Previous result (Shift+Enter)" disabled={hits.length === 0 || navigating} onClick={() => move(-1)}>
              <ChevronUp size={15} />
            </button>
            <button type="button" aria-label="Next result" title="Next result (Enter)" disabled={hits.length === 0 || navigating} onClick={() => move(1)}>
              <ChevronDown size={15} />
            </button>
            <button type="button" aria-label="Close search" title="Close (Esc)" onClick={closeSearch}>
              <X size={15} />
            </button>
          </div>
          {statusText && <div className="session-history-search__status" role="status">{statusText}</div>}
          {hits[activeIndex] && (
            <div className="session-history-search__snippet" data-testid="session-history-search-snippet">
              {hits[activeIndex]!.snippet}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
