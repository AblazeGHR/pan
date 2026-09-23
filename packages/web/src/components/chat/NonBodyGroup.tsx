import { memo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { getMessageIdentity } from '@/utils/messageIdentity';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';

interface NonBodyGroupProps {
  items: Message[];
}

interface ChildGroup {
  role: 'tool' | 'thinking';
  items: Message[];
}

function groupChildren(items: Message[]): ChildGroup[] {
  const groups: ChildGroup[] = [];
  let current: ChildGroup | null = null;

  for (const item of items) {
    if (item.role !== 'tool' && item.role !== 'thinking') continue;
    if (!current || current.role !== item.role) {
      current = { role: item.role, items: [] };
      groups.push(current);
    }
    current.items.push(item);
  }

  return groups;
}

function pluralize(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

/** One outer disclosure for a contiguous run, preserving existing child groups. */
export const NonBodyGroup = memo(function NonBodyGroup({ items }: NonBodyGroupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());

  if (items.length === 0) return null;

  const toolCount = items.filter((item) => item.role === 'tool').length;
  const thinkingCount = items.filter((item) => item.role === 'thinking').length;
  const summary = [
    toolCount > 0 ? pluralize(toolCount, 'tool') : null,
    thinkingCount > 0 ? pluralize(thinkingCount, 'thinking block') : null,
  ].filter(Boolean).join(' · ');
  const hasUnread = items.some((item) => unread.has(item.content));

  return (
    <div className="non-body-group border border-border-default rounded-lg bg-bg-secondary">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover/30 transition-colors text-left select-none"
      >
        {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        <span>{items.length} non-body blocks</span>
        <span className="text-text-tertiary">{summary}</span>
        {hasUnread && !isOpen && (
          <span className="ml-auto w-2 h-2 rounded-full bg-accent" title="unread" />
        )}
      </button>
      {isOpen && (
        <div data-testid="non-body-group-window" className="flex flex-col gap-2 px-2 pb-2 max-h-[20rem] overflow-y-auto overscroll-contain">
          {groupChildren(items).map((group) => {
            const firstItem = group.items[0]!;
            const key = `${group.role}:${getMessageIdentity(firstItem)}`;
            return group.role === 'tool'
              ? <ToolGroup key={key} items={group.items} />
              : <ThinkingGroup key={key} items={group.items} />;
          })}
        </div>
      )}
    </div>
  );
});
