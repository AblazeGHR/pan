import { useState } from 'react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';

interface ThinkingBlockProps {
  message: Message;
}

export function ThinkingBlock({ message }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());
  const hasUnread = unread.has(message.content);

  const toggle = () => {
    setIsOpen(!isOpen);
  };

  return (
    <div className={`msg thinking ${isOpen ? 'open' : ''}`}>
      <button
        onClick={toggle}
        className="flex items-center gap-2 text-xs text-text-secondary hover:text-text-primary transition-colors"
      >
        <span className="text-accent">CAD</span>
        <span>{isOpen ? 'hide thinking' : 'show thinking'}</span>
        <span className="text-[10px]">{isOpen ? '▲' : '▼'}</span>
        {hasUnread && !isOpen && (
          <span className="w-2 h-2 rounded-full bg-accent" title="unread" />
        )}
      </button>
      {isOpen && (
        <pre className="mt-2 p-3 rounded bg-bg-tertiary border border-border-muted text-xs text-text-secondary whitespace-pre-wrap max-h-96 overflow-y-auto">
          {message.content}
        </pre>
      )}
    </div>
  );
}
