import { ChatLayout } from '@/components/layout/ChatLayout';
import { ChatMessages, type ChatMessagesHandle } from '@/components/chat/ChatMessages';
import { MessageNavigationDock, MESSAGE_NAVIGATION_PANEL_ID } from '@/components/chat/MessageNavigationDock';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { InputRow } from '@/components/chat/InputRow';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';
import { UserInputBanner } from '@/components/chat/UserInputBanner';
import { ElicitationBanner } from '@/components/chat/ElicitationBanner';
import { TerminalInteractionBanner } from '@/components/chat/TerminalInteractionBanner';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Search, X } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import type { Message } from '@/types';
import { searchSessionHistory } from '@/services/api';

interface SearchResult {
  message?: Message;
  messageIndex?: number;
  historyIndex?: number;
  fromEnd?: number;
  total?: number;
  snippet?: string;
  matchCount: number;
  firstMatch: number;
}

function countMatches(text: string, query: string): { count: number; firstMatch: number } {
  const normalizedText = text.toLocaleLowerCase();
  const normalizedQuery = query.toLocaleLowerCase();
  if (!normalizedQuery) return { count: 0, firstMatch: -1 };
  let count = 0;
  let firstMatch = -1;
  let offset = 0;
  while (offset <= normalizedText.length - normalizedQuery.length) {
    const index = normalizedText.indexOf(normalizedQuery, offset);
    if (index < 0) break;
    if (firstMatch < 0) firstMatch = index;
    count += 1;
    offset = index + Math.max(1, normalizedQuery.length);
  }
  return { count, firstMatch };
}

function highlightText(text: string, query: string): ReactNode {
  const trimmedQuery = query.trim();
  if (!trimmedQuery) return text;
  const normalizedText = text.toLocaleLowerCase();
  const normalizedQuery = trimmedQuery.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const match = normalizedText.indexOf(normalizedQuery, cursor);
    if (match < 0) {
      parts.push(text.slice(cursor));
      break;
    }
    if (match > cursor) parts.push(text.slice(cursor, match));
    parts.push(<mark key={`${match}-${cursor}`} className="chat-search-match">{text.slice(match, match + trimmedQuery.length)}</mark>);
    cursor = match + Math.max(1, trimmedQuery.length);
  }
  return parts;
}

function messageText(message: Message): string {
  if (typeof message.content === 'string') return message.content;
  return (message.parts ?? [])
    .filter((part) => part.type === 'text')
    .map((part) => part.type === 'text' ? part.text : '')
    .join('\n');
}

function resultSnippet(message: Message, firstMatch: number): string {
  const content = messageText(message).replace(/\s+/g, ' ').trim();
  if (content.length <= 180) return content;
  const start = Math.max(0, Math.min(firstMatch - 60, content.length - 180));
  return `${start > 0 ? '... ' : ''}${content.slice(start, start + 180)}${start + 180 < content.length ? ' ...' : ''}`;
}

function ChatSearchPanel({
  query,
  setQuery,
  results,
  totalMatches,
  activeIndex,
  onSelect,
  onPrevious,
  onNext,
  onClose,
  limited,
  searching,
}: {
  query: string;
  setQuery: (value: string) => void;
  results: SearchResult[];
  totalMatches: number;
  activeIndex: number;
  onSelect: (index: number) => void;
  onPrevious: () => void;
  onNext: () => void;
  onClose: () => void;
  limited: boolean;
  searching: boolean;
}) {
  return (
    <div className="chat-search-panel border-b border-border-default bg-bg-secondary px-3 py-2" data-testid="chat-search-panel">
      <div className="flex items-center gap-2">
        <Search size={16} className="shrink-0 text-text-tertiary" />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search this session"
          aria-label="Search this session"
          className="min-w-0 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
        />
        <span className="shrink-0 text-xs text-text-secondary" aria-live="polite">
          {query ? `Found ${totalMatches}${searching ? ' …' : ''}` : 'Type to search'}
        </span>
        <button type="button" onClick={onPrevious} disabled={results.length === 0} className="chat-search-nav" aria-label="Previous match" title="Previous match"><ChevronUp size={15} /></button>
        <button type="button" onClick={onNext} disabled={results.length === 0} className="chat-search-nav" aria-label="Next match" title="Next match"><ChevronDown size={15} /></button>
        <button type="button" onClick={onClose} className="chat-search-nav" aria-label="Close search" title="Close search"><X size={15} /></button>
      </div>
      {limited && <div className="mt-1 text-[11px] text-text-tertiary">Only loaded history is searched. Load older messages to include them.</div>}
      {query && results.length > 0 && (
        <div className="mt-2 max-h-52 overflow-y-auto border-t border-border-muted pt-1">
          {results.map((result, index) => (
            <button
              key={`${result.historyIndex ?? result.messageIndex ?? index}-${result.message?.messageId ?? index}`}
              type="button"
              onClick={() => onSelect(index)}
              className={`chat-search-result ${activeIndex === index ? 'is-active' : ''}`}
            >
              <span className="chat-search-result-role">{result.message?.role === 'user' ? 'You' : 'AI'}</span>
              <span className="chat-search-result-text">{highlightText(result.snippet ?? (result.message ? resultSnippet(result.message, result.firstMatch) : ''), query)}</span>
              <span className="chat-search-result-count">{result.matchCount}</span>
            </button>
          ))}
        </div>
      )}
      {query && results.length === 0 && <div className="mt-2 text-xs text-text-tertiary">No matches</div>}
    </div>
  );
}

export default function ChatView() {
  const chatRef = useRef<ChatMessagesHandle>(null);
  const chatStageRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  // Unmounting (rather than hiding) the rail is the point of the switch: the
  // dock (including its cached index) is removed when the master switch is off.
  const showMessageNavigationRail = useAppSettingsStore((s) => s.showMessageNavigationRail);
  const { isMobile } = useMediaQuery();
  const [mobileExpanded, setMobileExpanded] = useState(false);
  const currentMessages = useSessionStore((s) => s.currentMessages);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const historyWindowStart = useSessionStore((s) => s.historyWindowStarts[s.currentSessionId ?? ''] ?? 0);
  const sessionTotal = useSessionStore((s) => s.sessions.find((item) => item.id === s.currentSessionId)?.historyTotal ?? currentMessages.length);
  const ensureMessageLoaded = useSessionStore((s) => s.ensureMessageLoaded);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);
  const [remoteResults, setRemoteResults] = useState<SearchResult[] | null>(null);
  const [remoteTotalMatches, setRemoteTotalMatches] = useState(0);
  const [searching, setSearching] = useState(false);

  const localSearchResults = useMemo(() => {
    const query = searchQuery.trim();
    if (!query) return [];
    return currentMessages.flatMap((message, messageIndex) => {
      if (message.role !== 'user' && message.role !== 'assistant') return [];
      const match = countMatches(message.content, query);
      return match.count > 0 ? [{
        message,
        messageIndex,
        historyIndex: historyWindowStart + messageIndex,
        fromEnd: Math.max(0, sessionTotal - 1 - (historyWindowStart + messageIndex)),
        total: sessionTotal,
        matchCount: match.count,
        firstMatch: match.firstMatch,
        snippet: resultSnippet(message, match.firstMatch),
      }] : [];
    });
  }, [currentMessages, searchQuery, historyWindowStart, sessionTotal]);
  const searchResults = remoteResults ?? localSearchResults;
  const localTotalMatches = useMemo(() => localSearchResults.reduce((total, result) => total + result.matchCount, 0), [localSearchResults]);
  const totalMatches = remoteResults ? remoteTotalMatches : localTotalMatches;

  useEffect(() => {
    const query = searchQuery.trim();
    setRemoteResults(null);
    setRemoteTotalMatches(0);
    if (!currentSessionId || query.length < 2) {
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const response = await searchSessionHistory(currentSessionId, query, 500, controller.signal);
        if (!controller.signal.aborted) {
          setRemoteResults(response.matches.map((item) => ({
            message: item.message,
            historyIndex: item.index,
            fromEnd: item.fromEnd,
            total: response.total,
            matchCount: item.matchCount,
            firstMatch: item.firstMatch,
            snippet: resultSnippet(item.message, item.firstMatch),
          })));
          setRemoteTotalMatches(response.totalMatches);
        }
      } catch (error) {
        if (!controller.signal.aborted) console.warn('[ChatView] session search failed', error);
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 180);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [currentSessionId, searchQuery]);

  useEffect(() => {
    setActiveSearchIndex((index) => searchResults.length === 0 ? 0 : Math.min(index, searchResults.length - 1));
  }, [searchResults.length]);

  const jumpToSearchResult = useCallback((index: number) => {
    const result = searchResults[index];
    if (!result) return;
    setActiveSearchIndex(index);
    const loaded = result.message?.messageId
      ? currentMessages.find((message) => message.messageId === result.message?.messageId)
      : (result.historyIndex !== undefined
        ? currentMessages[result.historyIndex - historyWindowStart]
        : undefined);
    if (loaded) {
      chatRef.current?.scrollToMessage(loaded, result.historyIndex);
      return;
    }
    if (result.fromEnd === undefined || result.total === undefined) return;
    void ensureMessageLoaded(result.fromEnd, result.total).then((message) => {
      if (message) chatRef.current?.scrollToMessage(message, result.historyIndex);
    });
  }, [searchResults, currentMessages, historyWindowStart, ensureMessageLoaded]);

  useEffect(() => {
    // Enabling the master switch and changing viewport modes both start folded.
    setMobileExpanded(false);
  }, [showMessageNavigationRail, isMobile]);

  const restoreChatFocus = useCallback(() => {
    chatStageRef.current?.focus();
  }, []);

  const closeMobileNavigation = useCallback(() => {
    if (dockRef.current?.contains(document.activeElement)) {
      mobileToggleRef.current?.focus();
    }
    setMobileExpanded(false);
  }, []);

  const toggleMobileNavigation = () => {
    if (mobileExpanded) {
      closeMobileNavigation();
      return;
    }
    setMobileExpanded(true);
  };

  const topBarRightAction = (
    <div className="flex items-center gap-1">
      <button type="button" className="chat-search-toggle" aria-label="Search this session" title="Search this session" aria-expanded={searchOpen} onClick={() => setSearchOpen((open) => !open)}>
        <Search size={17} />
      </button>
      {showMessageNavigationRail && isMobile && (
        <button
          ref={mobileToggleRef}
          type="button"
          className="message-navigation-mobile-toggle"
          aria-label={mobileExpanded ? 'Close message navigation rail' : 'Open message navigation rail'}
          aria-expanded={mobileExpanded}
          aria-controls={MESSAGE_NAVIGATION_PANEL_ID}
          title={mobileExpanded ? 'Close message navigation rail' : 'Open message navigation rail'}
          data-testid="mobile-message-navigation-toggle"
          onClick={toggleMobileNavigation}
        >
          {mobileExpanded ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      )}
    </div>
  );

  return (
    <ChatLayout topBarRightAction={topBarRightAction}>
      <div className="flex flex-col h-full min-h-0">
        <ApprovalBanner />
        <UserInputBanner />
        <ElicitationBanner />
        <TerminalInteractionBanner />
        {searchOpen && (
          <ChatSearchPanel
            query={searchQuery}
            setQuery={(value) => { setSearchQuery(value); setActiveSearchIndex(0); }}
            results={searchResults}
            totalMatches={totalMatches}
            activeIndex={activeSearchIndex}
            onSelect={jumpToSearchResult}
            onPrevious={() => jumpToSearchResult(searchResults.length ? (activeSearchIndex - 1 + searchResults.length) % searchResults.length : 0)}
            onNext={() => jumpToSearchResult(searchResults.length ? (activeSearchIndex + 1) % searchResults.length : 0)}
            onClose={() => { setSearchOpen(false); setSearchQuery(''); }}
            limited={false}
            searching={searching}
          />
        )}
        <div ref={chatStageRef} className="chat-view-stage flex flex-1 min-h-0 min-w-0" tabIndex={-1}>
          <ChatMessages
            ref={chatRef}
            hideScrollToBottom={showMessageNavigationRail && isMobile && mobileExpanded}
          />
          {showMessageNavigationRail && (
            <MessageNavigationDock
              chatRef={chatRef}
              dockRef={dockRef}
              isMobile={isMobile}
              mobileExpanded={mobileExpanded}
              onMobileClose={closeMobileNavigation}
              onRestoreFocus={restoreChatFocus}
            />
          )}
        </div>
        <InputRow />
      </div>
    </ChatLayout>
  );
}
