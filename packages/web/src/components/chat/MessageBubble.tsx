import type { Message } from '@/types';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolGroup } from './ToolGroup';

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const role = message.role;

  // Thinking blocks get their own component
  if (role === 'thinking') {
    return <ThinkingBlock message={message} />;
  }

  // Tool blocks are handled by ToolGroup — they shouldn't appear standalone
  if (role === 'tool') {
    return null;
  }

  // System messages
  if (role === 'system') {
    return (
      <div className="flex justify-center py-2">
        <span className="msg system text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
          {message.content}
        </span>
      </div>
    );
  }

  // User messages — right aligned, kimi-code style
  if (role === 'user') {
    return (
      <div className="px-4 mb-4 flex justify-end">
        <div className="msg user max-w-[78%] bg-accent/15 border border-accent/30 px-[15px] py-[11px] rounded-tl-[16px] rounded-tr-[16px] rounded-bl-[6px] rounded-br-[16px]">
          <MarkdownRenderer
            content={message.content}
            className="text-sm"
          />
        </div>
      </div>
    );
  }

  // Assistant messages — left aligned, no bubble
  return (
    <div className="px-4 mb-2.5">
      <div className="msg assistant max-w-[94%] text-[15px] leading-relaxed font-medium">
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
  item: Message | { type: 'tool_group'; items: Message[] };
}

export function MessageDisplayItem({ item }: MessageDisplayItemProps) {
  if ('type' in item && item.type === 'tool_group') {
    return <ToolGroup items={(item as { items: Message[] }).items} />;
  }
  return <MessageBubble message={item as Message} />;
}
