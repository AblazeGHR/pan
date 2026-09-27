import { memo, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { Message } from '@/types';
import { getMessageIdentity } from '@/utils/messageIdentity';
import { getLatestMessageTs } from '@/utils/messageTimestamp';
import { groupChildren, type ChildGroup } from './nonBodyGroupUtils';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { MessageTimestamp } from './MessageTimestamp';

interface NonBodyGroupProps {
  items: Message[];
  latestTs?: string;
  timestampsComputed?: boolean;
  flashKey?: string;
  flashKeys?: string[];
  onTimestampFlashConsumed?: (flashKeys: readonly string[]) => void;
}

const CHILD_GROUP_ESTIMATE = 28;
const CHILD_GROUP_GAP = 8;

function VirtualizedChildGroups({ groups }: { groups: ChildGroup[] }) {
  const scrollElementRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: groups.length,
    getScrollElement: () => scrollElementRef.current,
    estimateSize: () => CHILD_GROUP_ESTIMATE,
    gap: CHILD_GROUP_GAP,
    overscan: 4,
    getItemKey: (index) => {
      const group = groups[index];
      const firstItem = group?.items[0];
      return firstItem ? `${group.role}:${getMessageIdentity(firstItem)}` : `missing:${index}`;
    },
  });
  const virtualItems = virtualizer.getVirtualItems();
  const totalSize = virtualizer.getTotalSize();

  return (
    <div
      ref={scrollElementRef}
      data-testid="non-body-group-window"
      data-group-count={groups.length}
      className="px-2 pb-2 max-h-[20rem] overflow-y-auto"
    >
      <div style={{ height: `${totalSize}px`, width: '100%', position: 'relative' }}>
        {virtualItems.map((virtualItem) => {
          const group = groups[virtualItem.index];
          if (!group) return null;
          const firstItem = group.items[0]!;

          return (
            <div
              key={virtualItem.key}
              ref={virtualizer.measureElement}
              data-index={virtualItem.index}
              data-child-group={`${group.role}:${getMessageIdentity(firstItem)}`}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${virtualItem.start}px)`,
              }}
            >
              {group.role === 'tool'
                ? <ToolGroup items={group.items} latestTs={group.latestTs} timestampsComputed />
                : <ThinkingGroup items={group.items} latestTs={group.latestTs} timestampsComputed />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function pluralize(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

/** One outer disclosure for a contiguous run, preserving existing child groups. */
export const NonBodyGroup = memo(function NonBodyGroup({
  items,
  latestTs,
  timestampsComputed,
  flashKey,
  flashKeys,
  onTimestampFlashConsumed,
}: NonBodyGroupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const childGroups = useMemo(() => isOpen ? groupChildren(items) : [], [isOpen, items]);

  if (items.length === 0) return null;

  const toolCount = items.filter((item) => item.role === 'tool').length;
  const thinkingCount = items.filter((item) => item.role === 'thinking').length;
  const summary = [
    toolCount > 0 ? pluralize(toolCount, 'tool') : null,
    thinkingCount > 0 ? pluralize(thinkingCount, 'thinking block') : null,
  ].filter(Boolean).join(' · ');
  const consumeTimestampFlash = () => {
    const keys = flashKeys?.length ? flashKeys : flashKey ? [flashKey] : [];
    if (keys.length > 0) onTimestampFlashConsumed?.(keys);
  };

  return (
    <div className="non-body-group border border-border-default rounded-lg bg-bg-secondary">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover/30 transition-colors text-left select-none"
      >
        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>{items.length} non-body blocks</span>
        <span className="text-text-tertiary">{summary}</span>
        <MessageTimestamp
          ts={timestampsComputed ? latestTs : (latestTs ?? getLatestMessageTs(items))}
          flashKey={flashKey}
          onFlashConsumed={consumeTimestampFlash}
          className="ml-auto"
        />
      </button>
      {isOpen && (
        <VirtualizedChildGroups groups={childGroups} />
      )}
    </div>
  );
});
