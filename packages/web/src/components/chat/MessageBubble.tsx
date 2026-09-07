import type { Message } from '@/types';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolGroup } from './ToolGroup';
import type { ToolGroupDisplayItem } from '@/utils/messageIdentity';

export type GroupedItem = Message | ToolGroupDisplayItem;
type PrevRole = Message['role'] | 'tool' | null;

/** Role used for spacing decisions. Tool groups behave like 'tool'. */
export function getItemRole(item: GroupedItem): PrevRole {
  if ('type' in item && item.type === 'tool_group') return 'tool';
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

export function MessageBubble({ message, prevRole = null }: MessageBubbleProps) {
  const role = message.role;
  const mt = marginTopClass(role, prevRole);

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

  // Retained TUI-style view: full-width green user box (3px green left bar +
  // green top/bottom separator + ">" prefix), styled via .msg.user CSS.
  if (role === 'user') {
    return (
      <div className={`${mt} px-3 sm:px-6 lg:px-8`}>
        <div className="msg user w-full text-sm">
          <MarkdownRenderer content={message.content} className="text-sm" />
        </div>
      </div>
    );
  }

  // Assistant messages — no bubble, left-aligned, full-width markdown flow
  return (
    <div className={`${mt} px-3 sm:px-6 lg:px-8`}>
      <div className="msg assistant text-sm leading-relaxed">
        <MarkdownRenderer content={message.content} />
      </div>
    </div>
  );
}

/**
 * Group consecutive messages into display items.
 * Consecutive tool messages are grouped into a single ToolGroup.
 */
export function groupMessages(
  messages: Message[],
): Array<Message | { type: 'tool_group'; items: Message[] }> {
  const grouped: Array<Message | { type: 'tool_group'; items: Message[] }> = [];
  let currentToolGroup: Message[] | null = null;

  for (const msg of messages) {
    if (msg.role === 'tool') {
      if (!currentToolGroup) {
        currentToolGroup = [];
        grouped.push({
          type: 'tool_group',
          items: currentToolGroup,
        });
      }
      currentToolGroup.push(msg);
    } else {
      currentToolGroup = null;
      grouped.push(msg);
    }
  }

  return grouped;
}

interface MessageDisplayItemProps {
  item: GroupedItem;
  prevRole?: PrevRole;
}

export function MessageDisplayItem({ item, prevRole = null }: MessageDisplayItemProps) {
  if ('type' in item && item.type === 'tool_group') {
    return (
      <div className={`${marginTopClass('tool', prevRole)} pb-3 px-3 sm:px-6 lg:px-8`}>
        <ToolGroup items={item.items} />
      </div>
    );
  }
  return <MessageBubble message={item as Message} prevRole={prevRole} />;
}
