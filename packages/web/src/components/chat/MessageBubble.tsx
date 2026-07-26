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
        <span className="text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
          {message.content}
        </span>
      </div>
    );
  }

  // User messages — right aligned
  if (role === 'user') {
    return (
      <div className="flex justify-end px-4 py-2">
        <div className="max-w-[75%] rounded-lg rounded-br-sm bg-accent/20 px-4 py-2">
          <MarkdownRenderer content={message.content} />
        </div>
      </div>
    );
  }

  // Assistant messages — left aligned
  return (
    <div className="flex justify-start px-4 py-2">
      <div className="max-w-[85%] rounded-lg rounded-bl-sm bg-bg-tertiary px-4 py-2">
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
  item: Message | { type: 'tool_group'; items: Message[] };
}

export function MessageDisplayItem({ item }: MessageDisplayItemProps) {
  if ('type' in item && item.type === 'tool_group') {
    return <ToolGroup items={(item as { items: Message[] }).items} />;
  }
  return <MessageBubble message={item as Message} />;
}
