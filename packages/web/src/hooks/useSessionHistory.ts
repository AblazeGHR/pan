import { useEffect, useRef, useCallback } from 'react';
import { useSessionStore } from '@/stores/sessionStore';

const SCROLL_BOTTOM_THRESHOLD = 120;

/**
 * Handles infinite-scroll history loading and scroll-to-bottom behavior.
 */
export function useSessionHistory() {
  const {
    loadOlderMessages,
    historyLoading,
    hasMoreMessages,
    currentSessionId,
  } = useSessionStore();
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  const isNearBottom = useCallback((): boolean => {
    const el = containerRef.current;
    if (!el) return true;
    return (
      el.scrollHeight - el.scrollTop - el.clientHeight <
      SCROLL_BOTTOM_THRESHOLD
    );
  }, []);

  // Auto-scroll when new messages arrive, only if already near bottom
  useEffect(() => {
    if (isNearBottom()) {
      scrollToBottom();
    }
  });

  // Scroll handler for lazy-loading older messages
  const handleScroll = useCallback(() => {
    if (scrollTimer.current) return;
    scrollTimer.current = setTimeout(() => {
      scrollTimer.current = null;
      const el = containerRef.current;
      if (el && el.scrollTop <= 200 && hasMoreMessages && !historyLoading) {
        loadOlderMessages();
      }
    }, 150);
  }, [hasMoreMessages, historyLoading, loadOlderMessages]);

  // Reset scroll when session changes
  useEffect(() => {
    scrollToBottom();
  }, [currentSessionId, scrollToBottom]);

  return {
    containerRef,
    handleScroll,
    scrollToBottom,
    isNearBottom,
  };
}
