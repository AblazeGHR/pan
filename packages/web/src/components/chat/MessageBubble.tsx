import type { Message } from '@/types';
import { memo, useMemo } from 'react';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ThinkingBlock } from './ThinkingBlock';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { NonBodyGroup } from './NonBodyGroup';
import type { GroupDisplayItem } from '@/utils/messageIdentity';
import { getMessageIdentity } from '@/utils/messageIdentity';
import { isValidMessageTs } from '@/utils/messageTimestamp';
import { getQuickJumpKind } from './messageFilter';
import { MessageTimestamp } from './MessageTimestamp';
export { formatMessageTs } from '@/utils/messageTimestamp';

export type GroupedItem = Message | GroupDisplayItem;
type PrevRole = Message['role'] | 'tool' | null;

/** Role used for spacing decisions. Groups use the role of their member blocks. */
export function getItemRole(item: GroupedItem): PrevRole {
  if ('type' in item) {
    if (item.type === 'non_body_group') return item.items[item.items.length - 1]?.role ?? 'tool';
    return item.type === 'tool_group' ? 'tool' : 'thinking';
  }
  return (item as Message).role;
}

/** Kimi-style variant-aware top spacing.
 *  Mirrors kimi-cli's virtualized-message-list spacing rules:
 *  user pt-4, assistant-after-user pt-2, consecutive-assistant pt-1,
 *  tool pt-1.5, thinking pt-1.
 *
 * Padding is intentional here. A margin inside a measured virtual row can
 * collapse outside the row, making its cached height smaller than its painted
 * content and allowing the next row to overlap it. */
function marginTopClass(role: PrevRole, prevRole: PrevRole): string {
  if (!prevRole) return '';
  if (role === 'user') return 'pt-4';
  if (role === 'assistant') return prevRole === 'user' ? 'pt-2' : 'pt-1';
  if (role === 'tool') return 'pt-1.5';
  if (role === 'thinking') return 'pt-1';
  return 'pt-1';
}

interface MessageBubbleProps {
  message: Message;
  prevRole?: PrevRole;
}

export const MessageBubble = memo(function MessageBubble({ message, prevRole = null }: MessageBubbleProps) {
  const role = message.role;
  const mt = marginTopClass(role, prevRole);
  const attachmentIds = useMemo(
    () => message.parts?.flatMap((part) => part.type === 'attachment' ? [part.attachmentId] : []),
    [message.parts],
  );
  const isWorkerReport = getQuickJumpKind(message) === 'worker';
  const workerReportLabel = isWorkerReport ? (
    <span className="worker-report-label" aria-label="Worker report">Worker report</span>
  ) : null;

  // Thinking blocks get their own component
  if (role === 'thinking') {
    return (
      <div className={`${mt} px-3 sm:px-6 lg:px-8`}>
        <ThinkingBlock message={message} />
      </div>
    );
  }

  // Tool blocks are handled by ToolGroup — they shouldn't appear standalone
  if (role === 'tool') {
    return null;
  }

  // System messages
  if (role === 'system') {
    return (
      <div className={`system-message flex justify-center py-2 ${mt}`}>
        <span className="msg system text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
          {message.content}
        </span>
      </div>
    );
  }

  // TUI-style message rows use flex alignment so the virtualized row can keep
  // its full width without changing the measured wrapper's layout.
  if (role === 'user') {
    return (
      <div className={`message-row message-row-user ${isWorkerReport ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}>
        {workerReportLabel}
        <div className="msg user text-sm">
          <MarkdownRenderer
            content={message.content}
            attachmentIds={attachmentIds}
            className="text-sm"
          />
        </div>
        <MessageTimestamp ts={message.ts} className="mt-0.5" />
      </div>
    );
  }

  // Assistant messages — no bubble, left-aligned, full-width markdown flow
  return (
    <div className={`message-row message-row-assistant ${isWorkerReport ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}>
      {workerReportLabel}
      <div className="msg assistant text-sm leading-relaxed">
        <MarkdownRenderer
          content={message.content}
          attachmentIds={attachmentIds}
        />
      </div>
      <MessageTimestamp ts={message.ts} className="mt-0.5" />
    </div>
  );
});

/**
 * Group consecutive tool and thinking messages into semantic display rows.
 * Other roles end the current group so blocks never cross a message boundary.
 */
export function groupMessages(
  messages: Message[],
  mergeConsecutiveNonBodyBlocks = false,
  timestampFlashMessages?: ReadonlySet<Message>,
): GroupedItem[] {
  const grouped: GroupedItem[] = [];

  const appendToGroup = (group: GroupDisplayItem, message: Message) => {
    group.items.push(message);
    const validTs = message.ts && isValidMessageTs(message.ts) ? message.ts : undefined;
    if (validTs) group.latestTs = validTs;
    if (timestampFlashMessages?.has(message) && validTs) {
      const flashKey = getMessageIdentity(message);
      group.flashKey = flashKey;
      group.flashKeys = [...(group.flashKeys ?? []), flashKey];
    }
  };

  if (mergeConsecutiveNonBodyBlocks) {
    let currentNonBodyGroup: Message[] | null = null;

    for (const msg of messages) {
      if (msg.role === 'tool' || msg.role === 'thinking') {
        if (!currentNonBodyGroup) {
          currentNonBodyGroup = [];
          grouped.push({ type: 'non_body_group', items: currentNonBodyGroup });
        }
        appendToGroup(grouped[grouped.length - 1] as GroupDisplayItem, msg);
      } else {
        currentNonBodyGroup = null;
        grouped.push(msg);
      }
    }

    return grouped;
  }

  let currentToolGroup: Message[] | null = null;
  let currentThinkingGroup: Message[] | null = null;

  for (const msg of messages) {
    if (msg.role === 'tool') {
      currentThinkingGroup = null;
      if (!currentToolGroup) {
        currentToolGroup = [];
        grouped.push({ type: 'tool_group', items: currentToolGroup });
      }
      appendToGroup(grouped[grouped.length - 1] as GroupDisplayItem, msg);
    } else if (msg.role === 'thinking') {
      currentToolGroup = null;
      if (!currentThinkingGroup) {
        currentThinkingGroup = [];
        grouped.push({ type: 'thinking_group', items: currentThinkingGroup });
      }
      appendToGroup(grouped[grouped.length - 1] as GroupDisplayItem, msg);
    } else {
      currentToolGroup = null;
      currentThinkingGroup = null;
      grouped.push(msg);
    }
  }

  return grouped;
}

interface MessageDisplayItemProps {
  item: GroupedItem;
  prevRole?: PrevRole;
  onTimestampFlashConsumed?: (flashKeys: readonly string[]) => void;
}

export const MessageDisplayItem = memo(function MessageDisplayItem({
  item,
  prevRole = null,
  onTimestampFlashConsumed,
}: MessageDisplayItemProps) {
  if ('type' in item) {
    if (item.type === 'non_body_group') {
      const firstRole = item.items[0]?.role === 'thinking' ? 'thinking' : 'tool';
      return (
        <div className={`${marginTopClass(firstRole, prevRole)} pb-3 px-3 sm:px-6 lg:px-8`}>
          <NonBodyGroup
            items={item.items}
            latestTs={item.latestTs}
            timestampsComputed
            flashKey={item.flashKey}
            flashKeys={item.flashKeys}
            onTimestampFlashConsumed={onTimestampFlashConsumed}
          />
        </div>
      );
    }
    if (item.type === 'tool_group') {
      return (
        <div className={`${marginTopClass('tool', prevRole)} pb-3 px-3 sm:px-6 lg:px-8`}>
          <ToolGroup
            items={item.items}
            latestTs={item.latestTs}
            timestampsComputed
            flashKey={item.flashKey}
            flashKeys={item.flashKeys}
            onTimestampFlashConsumed={onTimestampFlashConsumed}
          />
        </div>
      );
    }
    return (
      <div className={`${marginTopClass('thinking', prevRole)} px-3 sm:px-6 lg:px-8`}>
        <ThinkingGroup
          items={item.items}
          latestTs={item.latestTs}
          timestampsComputed
          flashKey={item.flashKey}
          flashKeys={item.flashKeys}
          onTimestampFlashConsumed={onTimestampFlashConsumed}
        />
      </div>
    );
  }
  return <MessageBubble message={item as Message} prevRole={prevRole} />;
});
