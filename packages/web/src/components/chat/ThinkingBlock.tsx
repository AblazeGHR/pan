import { memo, useState, useEffect, useRef, type TransitionEvent } from 'react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { MarkdownRenderer } from './MarkdownRenderer';
import { isLongBlockContent } from './lazyBlockContent';

interface ThinkingBlockProps {
  message: Message;
}

export const ThinkingBlock = memo(function ThinkingBlock({ message }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [hasLoadedLongContent, setHasLoadedLongContent] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());
  const hasUnread = unread.has(message.content);
  const contentRef = useRef<HTMLDivElement>(null);
  const deferContent = isLongBlockContent(message.content);

  // Auto-scroll to bottom when streaming (hasUnread) and open
  useEffect(() => {
    if (isOpen && hasUnread && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [isOpen, hasUnread, message.content]);

  // Toggle ONLY the inline expand/collapse. Previously this also opened the
  // right-side DetailPanel, which resized the main grid column and made the
  // virtualized message list reflow — the chat scroll jumped instead of the
  // block toggling.
  const toggle = () => {
    if (!isOpen && deferContent) setHasLoadedLongContent(true);
    setIsOpen(!isOpen);
  };

  const handleContentTransitionEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (
      deferContent &&
      !isOpen &&
      event.target === event.currentTarget &&
      event.propertyName === 'max-height'
    ) {
      setHasLoadedLongContent(false);
    }
  };

  return (
    <div className="thinking">
      <button
        onClick={toggle}
        aria-expanded={isOpen}
        className="flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
      >
        {isOpen ? (
          <ChevronUp className="h-4 w-4" />
        ) : (
          <ChevronDown className="h-4 w-4" />
        )}
        <span>thinking</span>
        {hasUnread && !isOpen && (
          <span className="w-2 h-2 rounded-full bg-accent" title="unread" />
        )}
      </button>
      <div
        data-testid="thinking-content-window"
        onTransitionEnd={handleContentTransitionEnd}
        className={`transition-all duration-150 overflow-hidden ${
          isOpen ? 'max-h-48' : 'max-h-0'
        }`}
      >
        {(!deferContent || isOpen || hasLoadedLongContent) && (
          <div
            ref={contentRef}
            className="rounded-lg bg-bg-tertiary border border-border-default text-sm text-text-secondary leading-relaxed px-4 py-3 max-h-40 overflow-y-auto"
          >
            <MarkdownRenderer content={message.content} />
          </div>
        )}
      </div>
    </div>
  );
});
