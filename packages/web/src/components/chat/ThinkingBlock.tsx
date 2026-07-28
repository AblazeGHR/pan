import { useState, useEffect, useRef } from 'react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface ThinkingBlockProps {
  message: Message;
}

export function ThinkingBlock({ message }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());
  const hasUnread = unread.has(message.content);
  const contentRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when streaming (hasUnread) and open
  useEffect(() => {
    if (isOpen && hasUnread && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [isOpen, hasUnread, message.content]);

  const toggle = () => {
    setIsOpen(!isOpen);
  };

  return (
    <div className="thinking">
      <button
        onClick={toggle}
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
        className={`transition-all duration-150 overflow-hidden ${
          isOpen ? 'max-h-48' : 'max-h-0'
        }`}
      >
        <div
          ref={contentRef}
          className="rounded-lg bg-bg-tertiary border border-border-default text-sm text-text-secondary leading-relaxed whitespace-pre-wrap px-4 py-3 max-h-40 overflow-y-auto"
        >
          {message.content}
        </div>
      </div>
    </div>
  );
}
