import { memo, useEffect, useRef, useState, type TransitionEvent } from 'react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { MarkdownRenderer } from './MarkdownRenderer';
import { isLongBlockContent, LONG_BLOCK_CONTENT_THRESHOLD } from './lazyBlockContent';
import { getMessageIdentity } from '@/utils/messageIdentity';

interface ThinkingGroupProps {
  items: Message[];
}

/** A stable disclosure row for one or more adjacent thinking blocks. */
export const ThinkingGroup = memo(function ThinkingGroup({ items }: ThinkingGroupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [hasLoadedLongContent, setHasLoadedLongContent] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());
  const hasUnread = items.some((message) => unread.has(message.content));
  const contentRef = useRef<HTMLDivElement>(null);
  const combinedLength = items.reduce((length, message) => length + message.content.length, 0)
    + Math.max(0, items.length - 1) * 2;
  const deferContent = items.some((message) => isLongBlockContent(message.content))
    || (items.length > 1 && combinedLength > LONG_BLOCK_CONTENT_THRESHOLD);

  // Keep a streamed group pinned to its latest thinking content while it is
  // open; appending a member keeps the first member's display identity stable.
  useEffect(() => {
    if (isOpen && hasUnread && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [isOpen, hasUnread, items]);

  const toggle = () => {
    if (!isOpen && deferContent) setHasLoadedLongContent(true);
    setIsOpen(!isOpen);
  };

  const handleContentTransitionEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (
      deferContent &&
      !isOpen &&
      event.target === event.currentTarget
    ) {
      setHasLoadedLongContent(false);
    }
  };

  const label = items.length === 1 ? 'thinking' : `${items.length} thinking blocks`;
  const shouldRenderContent = !deferContent || isOpen || hasLoadedLongContent;
  const singleItem = items[0];

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
        <span>{label}</span>
        {hasUnread && !isOpen && (
          <span className="w-2 h-2 rounded-full bg-accent" title="unread" />
        )}
      </button>
      <div
        data-testid="thinking-content-window"
        onTransitionEnd={handleContentTransitionEnd}
        className={`transition-all duration-150 overflow-hidden ${isOpen ? 'max-h-48' : 'max-h-0'}`}
      >
        {shouldRenderContent && (
          <div
            ref={contentRef}
            className="rounded-lg bg-bg-tertiary border border-border-default text-sm text-text-secondary leading-relaxed px-4 py-3 max-h-40 overflow-y-auto"
          >
            {items.length === 1 && singleItem ? (
              <MarkdownRenderer content={singleItem.content} />
            ) : (
              items.map((message, index) => (
                <div
                  key={getMessageIdentity(message)}
                  data-testid="thinking-group-message"
                  className={index > 0 ? 'border-t border-border-default mt-2 pt-2' : undefined}
                >
                  <MarkdownRenderer content={message.content} />
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
});
