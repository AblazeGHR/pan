import { useRef, useCallback, useEffect, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { groupMessages, MessageDisplayItem, getItemRole } from './MessageBubble';
import { filterVisibleMessages } from './messageFilter';
import { ArrowDown, Loader2 } from 'lucide-react';
const SCROLL_BOTTOM_THRESHOLD = 120;

export function ChatMessages() {
  const parentRef = useRef<HTMLDivElement>(null);
  const currentMessages = useSessionStore((s) => s.currentMessages);
  const hasMoreMessages = useSessionStore((s) => s.hasMoreMessages);
  const historyLoading = useSessionStore((s) => s.historyLoading);
  const initialLoading = useSessionStore((s) => s.initialLoading);
  const loadOlderMessages = useSessionStore((s) => s.loadOlderMessages);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const bubbleViewEnabled = useUIStore((s) => s.bubbleViewEnabled);
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
  const grouped = groupMessages(visibleMessages);

  const virtualizer = useVirtualizer({
    count: grouped.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100,
    overscan: 5,
  });
  // Virtualized content height. Changes when messages are added/removed or
  // when items get measured after layout. Re-scrolling on this (while the user
  // is pinned to the bottom) is what lands the view at the *true* bottom once
  // the virtualizer's measurements settle, instead of the initial estimate.
  const totalSize = virtualizer.getTotalSize();

  // Whether the user is "pinned" to the bottom of the chat (within the
  // threshold). Starts true so the first message load of a session scrolls
  // down; updated on every scroll event. A session switch resets it to true.
  const isPinnedRef = useRef(true);

  // Scroll-to-bottom when new messages arrive if already near bottom
  const isNearBottom = useCallback((): boolean => {
    const el = parentRef.current;
    if (!el) return true;
    return (
      el.scrollHeight - el.scrollTop - el.clientHeight <
      SCROLL_BOTTOM_THRESHOLD
    );
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = parentRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  // Auto-scroll on new messages / measurement-driven size changes — but only
  // when the user hasn't scrolled away from the bottom. This is ALSO what
  // lands the view at the bottom after entering a session: the session-change
  // effect below resets isPinnedRef=true, so when the asynchronously-loaded
  // history arrives (currentMessages changes) — and again once the virtualizer
  // measures the real heights (totalSize changes) — we force the scroll down
  // even though the fresh container's scrollTop starts at 0.
  useEffect(() => {
    if (isPinnedRef.current || isNearBottom()) {
      scrollToBottom();
    }
  }, [currentMessages, totalSize, isNearBottom, scrollToBottom]);

  // Lazy load older messages on scroll to top
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const handler = () => {
      // Track the user's scroll anchor: pinned when within the bottom
      // threshold, unpinned once they scroll up past it. Programmatic scrolls
      // (scrollToBottom) also fire scroll events and correctly re-pin.
      isPinnedRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight <
        SCROLL_BOTTOM_THRESHOLD;

      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        if (el.scrollTop <= 200 && hasMoreMessages && !historyLoading) {
          const prevScroll = el.scrollHeight;
          loadOlderMessages().then(() => {
            // Preserve scroll position after DOM has updated
            requestAnimationFrame(() => {
              el.scrollTop = el.scrollHeight - prevScroll;
            });
          });
        }
      }, 150);
    };

    el.addEventListener('scroll', handler);
    return () => {
      el.removeEventListener('scroll', handler);
      if (timer) clearTimeout(timer);
    };
  }, [hasMoreMessages, historyLoading, loadOlderMessages]);

  // Scroll to bottom when the session changes. Reset the pinned anchor first
  // so the auto-scroll effect above forces us down once this session's history
  // loads (async) and again after the virtualizer measures the real heights.
  // The rAF re-scroll covers the same-frame layout of the freshly swapped DOM.
  useEffect(() => {
    isPinnedRef.current = true;
    scrollToBottom();
    const raf = requestAnimationFrame(scrollToBottom);
    return () => cancelAnimationFrame(raf);
  }, [currentSessionId, scrollToBottom]);

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

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      <div
        ref={parentRef}
        className={`flex-1 min-h-0 overflow-auto ${!bubbleViewEnabled ? 'tui-mode' : ''}`}
      >
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {virtualizer.getVirtualItems().map((vItem) => {
            const item = grouped[vItem.index];
            if (!item) return null;
            const prevItem = grouped[vItem.index - 1];
            const prevRole = prevItem ? getItemRole(prevItem) : null;
            return (
              <div
                key={vItem.key}
                data-index={vItem.index}
                ref={virtualizer.measureElement}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${vItem.start}px)`,
                }}
              >
                <MessageDisplayItem item={item} prevRole={prevRole} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Scroll-to-bottom button */}
      {!isNearBottom() && (
        <button
          onClick={scrollToBottom}
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
