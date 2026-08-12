import type { Message } from '@/types';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolGroup } from './ToolGroup';

type GroupedItem = Message | { type: 'tool_group'; items: Message[] };
type PrevRole = Message['role'] | 'tool' | null;

/** Role used for spacing decisions. Tool groups behave like 'tool'. */
export function getItemRole(item: GroupedItem): PrevRole {
  if ('type' in item && item.type === 'tool_group') return 'tool';
  return (item as Message).role;
}

/** Kimi-style variant-aware top margin.
 *  Mirrors kimi-cli's virtualized-message-list spacing rules:
 *  user mt-4, assistant-after-user mt-2, consecutive-assistant mt-1,
 *  tool mt-1.5, thinking mt-1. */
function marginTopClass(role: PrevRole, prevRole: PrevRole): string {
  if (!prevRole) return '';
  if (role === 'user') return 'mt-4';
  if (role === 'assistant') return prevRole === 'user' ? 'mt-2' : 'mt-1';
  if (role === 'tool') return 'mt-1.5';
  if (role === 'thinking') return 'mt-1';
  return 'mt-1';
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
      <div className={mt}>
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
      <div className={`flex justify-center py-2 ${mt}`}>
        <span className="msg system text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
          {message.content}
        </span>
      </div>
    );
  }

  // User messages — Kimi-style: left-aligned, neutral grey bubble, rounded-2xl
  if (role === 'user') {
    return (
      <div className={`${mt} px-3 sm:px-6 lg:px-8`}>
        <div className="msg user w-fit max-w-[85%] rounded-2xl bg-bg-secondary/50 border border-border-default/60 px-4 py-3 text-sm">
          <MarkdownRenderer content={message.content} className="text-sm" />
        </div>
      </div>
    );
  }

  // Assistant messages — no bubble, left-aligned, full-width markdown flow
  return (
    <div className={`${mt} px-3 sm:px-6 lg:px-8`}>
      <div className="msg assistant text-sm leading-relaxed">
        <MarkdownRenderer
          content={message.content}
          className="prose prose-base max-w-none break-words"
        />
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
      <div className={marginTopClass('tool', prevRole)}>
        <ToolGroup items={(item as { items: Message[] }).items} />
      </div>
    );
  }
  return <MessageBubble message={item as Message} prevRole={prevRole} />;
}
