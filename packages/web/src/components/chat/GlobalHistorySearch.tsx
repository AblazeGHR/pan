import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { Globe, Loader2, X } from 'lucide-react';
import { ApiRequestError, fetchHistorySearch, prepareHistorySearch } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { ApiHistorySearchHit, HistorySearchRole } from '@/types';
import type { ChatMessagesHandle } from './ChatMessages';
import { HistorySearchRoles } from './HistorySearchRoles';
import { ALL_SEARCH_ROLES } from './searchRoleOptions';
import { HistorySearchPopup } from './HistorySearchPopup';
import { HistorySearchOverview } from './HistorySearchOverview';
import { SessionHistorySearch } from './SessionHistorySearch';
import { useHistorySearchViewStore } from '@/stores/historySearchViewStore';

const GLOBAL_SEARCH_PAGE_SIZE = 50;
const GLOBAL_SEARCH_DEBOUNCE_MS = 250;

interface GlobalHistorySearchProps {
  popupContainer?: HTMLElement | null;
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onHighlightMessage: (sessionId: string | null, messageId: string | null, query?: string, occurrence?: number) => void;
}

type SearchStatus = 'idle' | 'loading' | 'preparing' | 'loading-more' | 'ready' | 'expired' | 'error';
type NavigationStatus = 'idle' | 'loading' | 'ready' | 'expired' | 'error';

interface ActiveSearch {
  query: string;
  controller: AbortController;
  generation: number;
}

interface ActiveNavigation {
  sessionId: string;
  query: string;
  controller: AbortController;
  generation: number;
  sessionSelected: boolean;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

function mergeHits(current: ApiHistorySearchHit[], incoming: ApiHistorySearchHit[]): ApiHistorySearchHit[] {
  const seen = new Set(current.map((hit) => `${hit.sessionId}\u0000${hit.messageId}`));
  return [...current, ...incoming.filter((hit) => {
    const key = `${hit.sessionId}\u0000${hit.messageId}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  })];
}

export function GlobalHistorySearch({
  chatRef,
  isMobile,
  open,
  onOpenChange,
  onHighlightMessage,
  popupContainer,
}: GlobalHistorySearchProps) {
  const sessions = useSessionStore((state) => state.sessions);
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const [query, setQuery] = useState('');
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [localRefreshKey, setLocalRefreshKey] = useState(0);
  const currentNavigationRef = useRef<{ move: (delta: number) => void }>(null);
  const highlightCurrent = useCallback((messageId: string | null, word?: string, occurrence?: number) => {
    const sessionId = useSessionStore.getState().currentSessionId;
    onHighlightMessage(messageId ? sessionId : null, messageId, word, occurrence);
  }, [onHighlightMessage]);
  const [roles, setRoles] = useState<HistorySearchRole[]>(ALL_SEARCH_ROLES);
  const [totalMatches, setTotalMatches] = useState<number | null>(null);
  const [hits, setHits] = useState<ApiHistorySearchHit[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [status, setStatus] = useState<SearchStatus>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [preparationProgress, setPreparationProgress] = useState('');
  const [navigationStatus, setNavigationStatus] = useState<NavigationStatus>('idle');
  const [navigationMessage, setNavigationMessage] = useState('');
  const [navigatingMessageId, setNavigatingMessageId] = useState<string | null>(null);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const openRef = useRef(open);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchGenerationRef = useRef(0);
  const activeSearchRef = useRef<ActiveSearch | null>(null);
  const navigationGenerationRef = useRef(0);
  const activeNavigationRef = useRef<ActiveNavigation | null>(null);
  const pageInFlightRef = useRef(false);
  const queryRef = useRef('');
  const wasOpenRef = useRef(false);
  openRef.current = open;

  const isCurrentSearch = useCallback((active: ActiveSearch) =>
    activeSearchRef.current === active &&
    !active.controller.signal.aborted &&
    queryRef.current.trim() === active.query,
  []);

  const loadPage = useCallback(async (
    active: ActiveSearch,
    pageCursor: string | null,
    append: boolean,
  ) => {
    if (!isCurrentSearch(active) || pageInFlightRef.current) return;
    pageInFlightRef.current = true;
    if (append) setStatus('loading-more');
    try {
      const response = await fetchHistorySearch(
        active.query,
        GLOBAL_SEARCH_PAGE_SIZE,
        pageCursor ?? undefined,
        active.controller.signal,
        { roles, countMode: 'content' },
      );
      if (!isCurrentSearch(active)) return;
      setHits((current) => append ? mergeHits(current, response.hits) : response.hits);
      setTotalMatches(response.totalMatches ?? null);
      setCursor(response.nextCursor);
      setHasMore(response.hasMore && Boolean(response.nextCursor));
      setStatus('ready');
      setErrorMessage('');
      useHistorySearchViewStore.getState().update(response.matchingSessionIds ?? [...new Set(response.hits.map((hit) => hit.sessionId))], !append);
      if (!append) {
        setStatus('preparing');
        setPreparationProgress('Preparing old history…');
        setCursor(null);
        setHasMore(false);
        await prepareHistorySearch(active.query, roles, GLOBAL_SEARCH_PAGE_SIZE,
          active.controller.signal, (event) => {
            if (!isCurrentSearch(active)) return;
            setHits(event.result.hits);
            setTotalMatches(event.result.totalMatches ?? null);
            useHistorySearchViewStore.getState().update(event.result.matchingSessionIds ?? [...new Set(event.result.hits.map((hit) => hit.sessionId))], !event.done || event.failedSessions.length > 0);
            setPreparationProgress(`${event.completed}/${event.total} Sessions checked`);
            if (event.done) {
              setLocalRefreshKey((value) => value + 1);
              const incomplete = event.failedSessions.length > 0;
              setCursor(incomplete ? null : event.result.nextCursor);
              setHasMore(!incomplete && event.result.hasMore && Boolean(event.result.nextCursor));
              setStatus(incomplete ? 'error' : 'ready');
              if (incomplete) setErrorMessage(`Results incomplete: ${event.failedSessions.length} Sessions could not be prepared. Retry search.`);
            }
          });
      }
    } catch (error) {
      if (!isCurrentSearch(active) || isAbortError(error)) return;
      if (error instanceof ApiRequestError && error.status === 409) {
        setStatus('expired');
        setErrorMessage('Search results expired. Search again.');
      } else if (error instanceof ApiRequestError && error.status === 503) {
        setStatus('error');
        setErrorMessage('History search is temporarily unavailable. Try again.');
      } else {
        setStatus('error');
        setErrorMessage(error instanceof Error ? error.message : 'History search failed. Try again.');
      }
    } finally {
      if (activeSearchRef.current === active) pageInFlightRef.current = false;
    }
  }, [isCurrentSearch, roles]);

  useEffect(() => {
    if (!open || !query.trim() || roles.length === 0) {
      useHistorySearchViewStore.getState().update(null);
      return;
    }
    useHistorySearchViewStore.getState().update([], true);
    return () => useHistorySearchViewStore.getState().update(null);
  }, [open, query, roles]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const normalizedQuery = query.trim();
    if (!normalizedQuery || roles.length === 0) {
      setStatus('idle');
      setHits([]);
      setCursor(null);
      setHasMore(false);
      setErrorMessage('');
      return;
    }

    const active: ActiveSearch = {
      query: normalizedQuery,
      controller: new AbortController(),
      generation: ++searchGenerationRef.current,
    };
    activeSearchRef.current = active;
    pageInFlightRef.current = false;
    setHits([]);
    setTotalMatches(null);
    setCursor(null);
    setHasMore(false);
    setStatus('loading');
    setPreparationProgress('');
    setErrorMessage('');
    const timer = window.setTimeout(() => {
      void loadPage(active, null, false);
    }, GLOBAL_SEARCH_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      if (activeSearchRef.current === active) {
        active.controller.abort();
        activeSearchRef.current = null;
        searchGenerationRef.current += 1;
      }
    };
  }, [loadPage, open, query, retryGeneration, roles]);

  useEffect(() => {
    if (open) {
      wasOpenRef.current = true;
      return;
    }
    if (!wasOpenRef.current) return;
    wasOpenRef.current = false;
    activeNavigationRef.current?.controller.abort();
    activeNavigationRef.current = null;
    navigationGenerationRef.current += 1;
    setNavigatingMessageId(null);
    setNavigationStatus('idle');
    setNavigationMessage('');
    setHits([]);
    setCursor(null);
    setHasMore(false);
    setStatus('idle');
    setErrorMessage('');
    onHighlightMessage(null, null);
  }, [onHighlightMessage, open]);

  useEffect(() => () => {
    activeSearchRef.current?.controller.abort();
    activeNavigationRef.current?.controller.abort();
    useHistorySearchViewStore.getState().update(null);
  }, []);

  useEffect(() => {
    const navigation = activeNavigationRef.current;
    if (!navigation?.sessionSelected || currentSessionId === navigation.sessionId) return;
    navigation.controller.abort();
    activeNavigationRef.current = null;
    navigationGenerationRef.current += 1;
    setNavigatingMessageId(null);
    setNavigationStatus('idle');
    setNavigationMessage('');
    onHighlightMessage(null, null);
  }, [currentSessionId, onHighlightMessage]);

  const cancelNavigation = useCallback(() => {
    activeNavigationRef.current?.controller.abort();
    activeNavigationRef.current = null;
    navigationGenerationRef.current += 1;
    setNavigatingMessageId(null);
    setNavigationStatus('idle');
    setNavigationMessage('');
    onHighlightMessage(null, null);
  }, [onHighlightMessage]);

  const closeSearch = () => {
    useHistorySearchViewStore.getState().update(null);
    activeSearchRef.current?.controller.abort();
    activeSearchRef.current = null;
    searchGenerationRef.current += 1;
    pageInFlightRef.current = false;
    cancelNavigation();
    onOpenChange(false);
  };

  const handleQueryChange = (value: string) => {
    setSelectedMessageId(null);
    queryRef.current = value;
    activeSearchRef.current?.controller.abort();
    activeSearchRef.current = null;
    searchGenerationRef.current += 1;
    pageInFlightRef.current = false;
    cancelNavigation();
    setQuery(value);
    setHits([]);
    setCursor(null);
    setHasMore(false);
    setStatus('idle');
    setErrorMessage('');
  };

  const restartSearch = () => {
    activeSearchRef.current?.controller.abort();
    activeSearchRef.current = null;
    searchGenerationRef.current += 1;
    pageInFlightRef.current = false;
    setHits([]);
    setCursor(null);
    setHasMore(false);
    setStatus('idle');
    setErrorMessage('');
    setRetryGeneration((generation) => generation + 1);
  };

  const isCurrentNavigation = (navigation: ActiveNavigation) =>
    activeNavigationRef.current === navigation &&
    !navigation.controller.signal.aborted &&
    openRef.current &&
    queryRef.current.trim() === navigation.query &&
    useSessionStore.getState().currentSessionId === navigation.sessionId;

  const navigateToHit = async (hit: ApiHistorySearchHit) => {
    if (!queryRef.current.trim() || !open) return;
    activeNavigationRef.current?.controller.abort();
    const navigation: ActiveNavigation = {
      sessionId: hit.sessionId,
      query: queryRef.current.trim(),
      controller: new AbortController(),
      generation: ++navigationGenerationRef.current,
      sessionSelected: false,
    };
    activeNavigationRef.current = navigation;
    setNavigatingMessageId(hit.messageId);
    setNavigationStatus('loading');
    setNavigationMessage('');
    onHighlightMessage(null, null);

    const stillCurrent = () => isCurrentNavigation(navigation);
    const signal = navigation.controller.signal;
    try {
      const store = useSessionStore.getState();
      if (!store.sessions.some((session) => session.id === hit.sessionId)) {
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
        return;
      }
      if (store.currentSessionId !== hit.sessionId) {
        const selection = store.selectSession(hit.sessionId, signal);
        navigation.sessionSelected = true;
        await selection;
      } else {
        navigation.sessionSelected = true;
      }
      if (!signal.aborted && activeNavigationRef.current === navigation &&
        useSessionStore.getState().currentSessionId !== hit.sessionId) {
        navigation.sessionSelected = false;
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
        return;
      }
      if (!stillCurrent()) return;

      let targetIndex = hit.messageIndex;
      let targetTotal = hit.historyTotal;
      let message = targetTotal > targetIndex && targetIndex >= 0
        ? await useSessionStore.getState().ensureMessageLoaded(
          targetTotal - 1 - targetIndex,
          targetTotal,
          signal,
        )
        : null;
      if (!stillCurrent()) return;

      if (!message || message.messageId !== hit.messageId) {
        if (message && (!message.messageId || message.messageId.startsWith('legacy:'))) {
          await useSessionStore.getState().refreshCurrentSessionHistory();
          if (!stillCurrent()) return;
        }
        const relocated = await fetchHistorySearch(navigation.query, 1, undefined, signal,
          { sessionId: hit.sessionId, roles, countMode: 'content', messageId: hit.messageId });
        if (!stillCurrent()) return;
        const currentHit = relocated.hits.find((candidate) => candidate.messageId === hit.messageId);
        if (!currentHit) {
          onHighlightMessage(null, null);
          setNavigationStatus('expired');
          setNavigationMessage('This result expired. Search again.');
          return;
        }
        targetIndex = currentHit.messageIndex;
        targetTotal = currentHit.historyTotal;
        message = targetIndex < targetTotal
          ? await useSessionStore.getState().ensureMessageLoaded(
            targetTotal - 1 - targetIndex,
            targetTotal,
            signal,
          )
          : null;
        if (!stillCurrent()) return;
      }

      if (!message || message.messageId !== hit.messageId) {
        onHighlightMessage(null, null);
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
        return;
      }

      onHighlightMessage(hit.sessionId, hit.messageId, navigation.query);
      await nextPaint();
      if (!stillCurrent()) return;
      const mountedMessage = useSessionStore.getState().currentMessages.find(
        (candidate) => candidate.messageId === hit.messageId,
      );
      if (!mountedMessage) {
        onHighlightMessage(null, null);
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
        return;
      }
      let didScroll = chatRef.current?.scrollToMessage(mountedMessage, targetIndex) ?? false;
      if (!didScroll) {
        await nextPaint();
        if (!stillCurrent()) return;
        didScroll = chatRef.current?.scrollToMessage(mountedMessage, targetIndex) ?? false;
      }
      if (!didScroll) {
        onHighlightMessage(null, null);
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
        return;
      }
      const sessionName = useSessionStore.getState().sessions.find(
        (session) => session.id === hit.sessionId,
      )?.name ?? hit.sessionId;
      setNavigationStatus('ready');
      setNavigationMessage(`Opened in ${sessionName}`);
      setSelectedMessageId(hit.messageId);
    } catch (error) {
      if (!stillCurrent() || isAbortError(error)) return;
      onHighlightMessage(null, null);
      if (error instanceof ApiRequestError && error.status === 409) {
        setNavigationStatus('expired');
        setNavigationMessage('This result expired. Search again.');
      } else {
        setNavigationStatus('error');
        setNavigationMessage('Could not open this result. Try again.');
      }
    } finally {
      if (activeNavigationRef.current === navigation) setNavigatingMessageId(null);
    }
  };

  const loadMore = () => {
    const active = activeSearchRef.current;
    if (active && cursor && hasMore) void loadPage(active, cursor, true);
  };

  const retry = () => {
    if (status === 'error' && hits.length > 0 && cursor && hasMore) {
      loadMore();
      return;
    }
    restartSearch();
  };

  const statusText = status === 'loading'
    ? 'Searching history…'
    : status === 'preparing'
      ? `${totalMatches ?? 0} occurrences found so far · ${preparationProgress} · still searching…`
    : status === 'loading-more'
      ? `${hits.length} messages shown · loading more…`
      : status === 'expired' || status === 'error'
        ? errorMessage
        : status === 'ready' && hits.length === 0
          ? hasMore ? 'No messages on this page · more results available' : 'No matching messages.'
          : status === 'ready'
            ? `${hits.length} messages shown${totalMatches !== null ? ` · ${totalMatches} occurrences` : ''}${hasMore ? ' · More results available' : ''}`
            : query.trim()
              ? ''
              : 'Enter a word to search all Sessions.';

  const navigationText = navigationStatus === 'loading'
    ? 'Opening result…'
    : navigationStatus === 'expired' || navigationStatus === 'error' || navigationStatus === 'ready'
      ? navigationMessage
      : '';

  const handleInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeSearch();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (status === 'error' || status === 'expired') retry();
      else currentNavigationRef.current?.move(event.shiftKey ? -1 : 1);
    }
  };

  return (
    <div
      className={`global-history-search${isMobile ? ' is-mobile' : ''}`}
      data-testid="global-history-search"
      data-layout={isMobile ? 'mobile-chat-top-right' : 'desktop-chat-top-right'}
    >
      <button
        type="button"
        className="global-history-search__toggle"
        aria-label={open ? 'Close global history search' : 'Search all Session history'}
        title="Search all Sessions"
        data-testid="global-history-search-toggle"
        onClick={() => open ? closeSearch() : onOpenChange(true)}
      >
        <Globe size={16} />
      </button>
      {open && (<HistorySearchPopup container={popupContainer}>
        <div className="global-history-search__popup" role="search" aria-label="Global history search">
          <div className="global-history-search__controls">
            <input
              ref={inputRef}
              type="search"
              value={query}
              onChange={(event) => handleQueryChange(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Search all Sessions…"
              aria-label="Search all Session history"
              data-testid="global-history-search-input"
            />
            {status === 'loading' || status === 'preparing' || status === 'loading-more'
              ? <Loader2 size={14} className="animate-spin" aria-label="Searching" />
              : null}
            {query && (
              <button
                type="button"
                aria-label="Clear global search"
                title="Clear query"
                onClick={() => handleQueryChange('')}
              >
                <X size={14} />
              </button>
            )}
            <button type="button" aria-label="Close global search" title="Close" onClick={closeSearch}>
              <X size={15} />
            </button>
          </div>
          <HistorySearchRoles roles={roles} onChange={(next) => {
            activeNavigationRef.current?.controller.abort();
            activeNavigationRef.current = null;
            setNavigatingMessageId(null);
            setNavigationStatus('idle');
            onHighlightMessage(null, null);
            setRoles(next);
          }} />
          <SessionHistorySearch navigationOnly isOpen={open} isMobile={isMobile} chatRef={chatRef}
            refreshKey={localRefreshKey}
            navigationEnabled={!navigatingMessageId}
            externalQuery={query} externalRoles={roles} selectedMessageId={selectedMessageId}
            navigationRef={currentNavigationRef} onHighlightMessage={highlightCurrent} />
          <div className="global-history-search__status" role="status" aria-live="polite">
            {statusText}
          </div>
          {navigationText && (
            <div className={`global-history-search__navigation-status is-${navigationStatus}`} role="status">
              {navigationText}
            </div>
          )}
          {hits.length > 0 && <HistorySearchOverview hits={hits} query={query}
            sessionName={(id) => sessions.find((session) => session.id === id)?.name ?? id}
            disabled={Boolean(navigatingMessageId)} onSelect={(hit) => void navigateToHit(hit)}>
            {hasMore && status !== 'expired' && status !== 'error' && <button type="button" className="global-history-search__load-more"
              disabled={status !== 'ready' || Boolean(navigatingMessageId)} onClick={loadMore} data-testid="global-history-search-load-more">{status === 'loading-more' ? 'Loading…' : 'Load more'}</button>}
          </HistorySearchOverview>}
          {(status === 'expired' || status === 'error') && (
            <button type="button" className="global-history-search__retry" onClick={retry}>
              {status === 'expired' ? 'Search again' : hits.length > 0 && hasMore ? 'Retry loading more' : 'Retry search'}
            </button>
          )}
        </div>
      </HistorySearchPopup>)}
    </div>
  );
}
