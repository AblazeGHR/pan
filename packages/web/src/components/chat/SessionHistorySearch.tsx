import { useCallback, useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { ChevronDown, ChevronUp, Loader2, Search, X } from 'lucide-react';
import { ApiRequestError, fetchHistorySearch } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { ApiHistorySearchHit, ApiHistorySearchResponse, HistorySearchRole } from '@/types';
import type { ChatMessagesHandle } from './ChatMessages';
import { HistorySearchRoles } from './HistorySearchRoles';
import { ALL_SEARCH_ROLES } from './searchRoleOptions';
import { HistorySearchPopup } from './HistorySearchPopup';
import { HistorySearchOverview } from './HistorySearchOverview';

interface SessionHistorySearchProps {
  popupContainer?: HTMLElement | null;
  chatRef: RefObject<ChatMessagesHandle | null>;
  isMobile: boolean;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  onHighlightMessage: (messageId: string | null, query?: string, occurrence?: number) => void;
  navigationOnly?: boolean;
  navigationEnabled?: boolean;
  refreshKey?: number;
  externalQuery?: string;
  externalRoles?: HistorySearchRole[];
  selectedMessageId?: string | null;
  navigationRef?: RefObject<{ move: (delta: number) => void } | null>;
}

type Status = 'idle' | 'loading' | 'ready' | 'stale' | 'error';
const paint = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const countOf = (hit: ApiHistorySearchHit) => hit.matchCount ?? 1;
const startOf = (hit: ApiHistorySearchHit, index: number) => hit.matchStart ?? index;

export function SessionHistorySearch({ chatRef, isMobile, isOpen, onOpenChange, onHighlightMessage, popupContainer,
  navigationOnly = false, navigationEnabled = true, refreshKey = 0, externalQuery, externalRoles, selectedMessageId, navigationRef }: SessionHistorySearchProps) {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const sessionVersion = useSessionStore((state) => {
    const session = state.sessions.find((item) => item.id === state.currentSessionId);
    return `${session?.historyEpoch}:${session?.historyRevision}:${session?.historyTotal}`;
  });
  const [localOpen, setLocalOpen] = useState(false);
  const open = isOpen ?? localOpen;
  const [localQuery, setQuery] = useState('');
  const [localRoles, setRoles] = useState<HistorySearchRole[]>(ALL_SEARCH_ROLES);
  const query = externalQuery ?? localQuery;
  const roles = externalRoles ?? localRoles;
  const [overviewPage, setOverviewPage] = useState<ApiHistorySearchResponse | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [response, setResponse] = useState<ApiHistorySearchResponse | null>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [occurrence, setOccurrence] = useState(0);
  const [navigating, setNavigating] = useState(false);
  const [retry, setRetry] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const searchController = useRef<AbortController | null>(null);
  const jumpController = useRef<AbortController | null>(null);
  const highlight = useRef(onHighlightMessage);
  highlight.current = onHighlightMessage;

  const cancel = useCallback(() => {
    generation.current += 1;
    searchController.current?.abort();
    jumpController.current?.abort();
    setNavigating(false);
    if (!navigationOnly) highlight.current(null);
  }, [navigationOnly]);
  const setOpen = useCallback((value: boolean) => {
    if (isOpen === undefined) setLocalOpen(value);
    onOpenChange?.(value);
  }, [isOpen, onOpenChange]);
  const close = useCallback(() => { cancel(); setOpen(false); setResponse(null); setStatus('idle'); }, [cancel, setOpen]);

  useEffect(() => {
    if (navigationOnly) return;
    const shortcut = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && open) { event.preventDefault(); close(); return; }
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'f') return;
      const target = event.target;
      if (target instanceof HTMLElement && !popup.current?.contains(target) &&
          (target.isContentEditable || target.closest('input,textarea,select,[contenteditable="true"]'))) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [open, close, setOpen, navigationOnly]);
  useEffect(() => { if (open) { input.current?.focus(); input.current?.select(); } }, [open]);
  useEffect(() => () => {
    generation.current += 1;
    searchController.current?.abort();
    jumpController.current?.abort();
    highlight.current(null);
  }, []);

  const navigate = useCallback(async (ordinal: number, page: ApiHistorySearchResponse, token: number) => {
    if (!sessionId || generation.current !== token) return;
    const controller = new AbortController();
    jumpController.current?.abort();
    jumpController.current = controller;
    const valid = () => !controller.signal.aborted && generation.current === token &&
      useSessionStore.getState().currentSessionId === sessionId;
    setNavigating(true);
    try {
      let result = page;
      let hit = page.hits.find((item, index) => startOf(item, index) <= ordinal && startOf(item, index)+countOf(item) > ordinal);
      if (!hit) {
        result = await fetchHistorySearch(query, 100, undefined, controller.signal,
          { sessionId, roles, countMode: 'content', matchIndex: ordinal, ...(navigationOnly ? { prepareLegacy: false } : {}) });
        if (!valid()) return;
        hit = result.hits[0];
        setResponse(result);
      }
      if (!hit) { setStatus('stale'); highlight.current(null); return; }
      const originalHit = hit;
      const load = (target: ApiHistorySearchHit) => useSessionStore.getState().ensureMessageLoaded(
        target.historyTotal-1-target.messageIndex, target.historyTotal, controller.signal);
      let message = useSessionStore.getState().currentMessages.find((item) => item.messageId === hit!.messageId) ?? await load(hit);
      if (!valid()) return;
      if (!message || message.messageId !== hit.messageId) {
        const relocated = await fetchHistorySearch(query, 1, undefined, controller.signal,
          { sessionId, roles, countMode: 'content', messageId: hit.messageId, ...(navigationOnly ? { prepareLegacy: false } : {}) });
        if (!valid()) return;
        hit = relocated.hits[0];
        if (!hit) { setStatus('stale'); highlight.current(null); return; }
        ordinal = (hit.matchStart ?? 0) + Math.min(countOf(hit)-1, Math.max(0, ordinal-(originalHit.matchStart ?? 0)));
        message = await load(hit);
        if (!valid()) return;
        setResponse(relocated);
      }
      if (!message || message.messageId !== hit.messageId) { setStatus('stale'); highlight.current(null); return; }
      highlight.current(hit.messageId, query.trim(), ordinal-(hit.matchStart ?? 0));
      await paint();
      if (!valid()) return;
      let scrolled = chatRef.current?.scrollToMessage(message, hit.messageIndex) ?? false;
      if (!scrolled) { await paint(); if (!valid()) return; scrolled = chatRef.current?.scrollToMessage(message, hit.messageIndex) ?? false; }
      if (!scrolled) { setStatus('stale'); highlight.current(null); return; }
      setOccurrence(ordinal);
    } catch (error) {
      if (!valid()) return;
      setStatus(error instanceof ApiRequestError && error.status === 409 ? 'stale' : 'error');
      highlight.current(null);
    } finally { if (valid()) setNavigating(false); }
  }, [chatRef, query, roles, sessionId, navigationOnly]);

  useEffect(() => {
    cancel();
    setResponse(null);
    setOverviewPage(null);
    setOverviewLoading(false);
    setOccurrence(0);
    if (!open || !navigationEnabled || !sessionId || !query.trim() || roles.length === 0) { setStatus('idle'); return; }
    const controller = new AbortController();
    searchController.current = controller;
    const token = generation.current;
    setStatus('loading');
    const timer = window.setTimeout(() => {
      void fetchHistorySearch(query, 100, undefined, controller.signal, { sessionId, roles, countMode: 'content', ...(navigationOnly ? { prepareLegacy: false } : {}) })
        .then(async (page) => {
          if (controller.signal.aborted || generation.current !== token) return;
          if (page.preparedIdentities) {
            await useSessionStore.getState().refreshCurrentSessionHistory();
            if (controller.signal.aborted || generation.current !== token) return;
          }
          setResponse(page);
          setOverviewPage(page);
          setStatus('ready');
          if (navigationOnly && selectedMessageId) {
            let selected = page.hits.find((hit) => hit.messageId === selectedMessageId);
            if (!selected) {
              const located = await fetchHistorySearch(query, 1, undefined, controller.signal,
                { sessionId, roles, countMode: 'content', messageId: selectedMessageId, prepareLegacy: false });
              if (controller.signal.aborted || generation.current !== token) return;
              selected = located.hits[0];
              if (selected) setResponse(located);
            }
            if (selected) setOccurrence(selected.matchStart ?? 0);
          } else if (!navigationOnly && page.hits.length) await navigate(0, page, token);
        }).catch((error: unknown) => {
          if (controller.signal.aborted || generation.current !== token) return;
          setStatus(error instanceof ApiRequestError && error.status === 409 ? 'stale' : 'error');
        });
    }, 120);
    return () => { clearTimeout(timer); controller.abort(); jumpController.current?.abort(); };
  }, [open, sessionId, sessionVersion, query, roles, retry, cancel, navigate, navigationOnly, selectedMessageId, navigationEnabled, refreshKey]);

  const total = response?.totalMatches ?? response?.hits.reduce((sum, hit) => sum+countOf(hit), 0) ?? 0;
  const move = (delta: number) => {
    if (!response || !total || status !== 'ready') return;
    const next = (occurrence+delta+total)%total;
    setOccurrence(next);
    void navigate(next, response, generation.current);
  };
  useImperativeHandle(navigationRef, () => ({ move }));
  const loadOverview = async () => {
    if (!overviewPage?.nextCursor || overviewLoading) return;
    const token = generation.current;
    setOverviewLoading(true);
    try {
      const page = await fetchHistorySearch(query, 100, overviewPage.nextCursor, searchController.current?.signal,
        { sessionId: sessionId ?? undefined, roles, countMode: 'content' });
      if (generation.current !== token) return;
      setOverviewPage({ ...page, hits: [...overviewPage.hits, ...page.hits] });
    } catch {
      if (generation.current === token) setStatus('stale');
    } finally { if (generation.current === token) setOverviewLoading(false); }
  };
  const keyboard = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') { event.preventDefault(); move(event.shiftKey ? -1 : 1); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
  };
  const text = status === 'loading' ? 'Searching full Session history…'
    : status === 'stale' ? 'Results expired. Search again.'
      : status === 'error' ? 'History search failed.'
        : status === 'ready' && !total ? 'No results in searchable history'
          : roles.length === 0 ? 'Select content types to search.' : '';
  const navigationControls = <>
    <span data-testid={navigationOnly ? 'global-current-session-count' : 'session-history-search-count'} className="session-history-search__count" aria-live="polite">{total ? occurrence+1 : 0} / {total}</span>
    <button type="button" aria-label="Previous result" disabled={!total || navigating || status !== 'ready'} onClick={() => move(-1)}><ChevronUp size={16} /></button>
    <button type="button" aria-label="Next result" disabled={!total || navigating || status !== 'ready'} onClick={() => move(1)}><ChevronDown size={16} /></button>
  </>;
  if (navigationOnly) return <div className="history-search-current-session" aria-label="Current Session matches">
    {navigationControls}
    {(text || navigating) && <span role="status">{navigating ? 'Opening result…' : text}
      {(status === 'stale' || status === 'error') && <button type="button" onClick={() => setRetry((value) => value+1)}>Search again</button>}
    </span>}
  </div>;
  return (
    <div className={`session-history-search${isMobile ? ' is-mobile' : ''}`} data-testid="session-history-search"
      data-layout={isMobile ? 'mobile-below-session-title' : 'desktop-chat-top-right'}>
      <button type="button" className="session-history-search__toggle" data-testid="session-history-search-toggle"
        aria-label={open ? 'Close Session history search' : 'Search Session history'} title="Search Session history (Ctrl+F)"
        onClick={() => open ? close() : setOpen(true)}><Search size={16} /></button>
      {open && <HistorySearchPopup container={popupContainer}><div ref={popup} className="session-history-search__popup" role="search" aria-label="Session history search">
        <div className="session-history-search__controls">
          <Search size={16} />
          <input ref={input} data-testid="session-history-search-input" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={keyboard}
            aria-label="Search Session history" placeholder="Search history…" maxLength={512} />
          {navigationControls}
          <button type="button" aria-label="Close search" onClick={close}><X size={16} /></button>
        </div>
        <HistorySearchRoles roles={roles} onChange={setRoles} />
        {status === 'ready' && total > 0 && overviewPage && <HistorySearchOverview hits={overviewPage.hits} query={query}
          disabled={navigating} onSelect={(hit) => void navigate(hit.matchStart ?? 0, { ...overviewPage, hits: [hit] }, generation.current)}>
          {overviewPage.nextCursor && <button type="button" disabled={overviewLoading} onClick={() => void loadOverview()}>{overviewLoading ? 'Loading…' : 'Load more'}</button>}
        </HistorySearchOverview>}
        {(text || navigating) && <div className="session-history-search__status" role="status">
          {(status === 'loading' || navigating) && <Loader2 size={14} className="animate-spin" />}
          {navigating ? 'Opening result…' : text}
          {(status === 'stale' || status === 'error') && <button type="button" onClick={() => setRetry((value) => value+1)}>Search again</button>}
        </div>}
      </div></HistorySearchPopup>}
    </div>
  );
}
