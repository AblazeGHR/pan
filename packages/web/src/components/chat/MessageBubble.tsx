import type { Message } from '@/types';
import { memo, useMemo } from 'react';
import { Undo2 } from 'lucide-react';
import { useSessionStore } from '@/stores/sessionStore';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ThinkingBlock } from './ThinkingBlock';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { NonBodyGroup } from './NonBodyGroup';
import type { GroupDisplayItem } from '@/utils/messageIdentity';
import { getMessageIdentity } from '@/utils/messageIdentity';
import { isValidMessageTs } from '@/utils/messageTimestamp';
import { getMessageSourceTag, type MessageSourceTag } from './messageFilter';
import { MessageTimestamp } from './MessageTimestamp';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
export { formatMessageTs } from '@/utils/messageTimestamp';

export type GroupedItem = Message | GroupDisplayItem;
type PrevRole = Message['role'] | 'tool' | null;

/**
 * Raw view body: the message text is a plain React text node inside a
 * `white-space: pre-wrap` block. Newlines, runs of spaces, Markdown markers,
 * code fences and literal HTML are preserved verbatim — nothing is parsed,
 * highlighted or injected as markup. Copying the block yields the original
 * text, including its line breaks. Long unbreakable tokens wrap with
 * `overflow-wrap: anywhere` instead of widening the virtualized row.
 */
function RawMessageText({ content, className = '' }: { content: string; className?: string }) {
  return (
    <div
      data-raw-message-text=""
      className={`whitespace-pre-wrap [overflow-wrap:anywhere] ${className}`.trim()}
    >
      {content}
    </div>
  );
}

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

/** User-facing copy for the durable source tags (classification: messageFilter). */
const SOURCE_TAG_LABELS: Record<MessageSourceTag, string> = {
  'ta-report': 'TA report',
  'ma-assign': 'MA assign',
};

export const MessageBubble = memo(function MessageBubble({ message, prevRole = null }: MessageBubbleProps) {
  const role = message.role;
  const mt = marginTopClass(role, prevRole);
  // Raw view keeps the TUI rows and role bars but swaps the rendered Markdown
  // body for the message's original text. Read straight from the store so a
  // mode switch re-renders only this body — no prop drilling, no new context.
  const rawViewEnabled = useAppSettingsStore((s) => s.chatViewStyle === 'raw');
  const attachmentIds = useMemo(
    () => message.parts?.flatMap((part) => part.type === 'attachment' ? [part.attachmentId] : []),
    [message.parts],
  );
  const sourceTag = getMessageSourceTag(message);
  const sourceBadge = sourceTag ? (
    <span
      className="worker-report-label"
      data-source-tag={sourceTag}
      aria-label={SOURCE_TAG_LABELS[sourceTag]}
    >
      {SOURCE_TAG_LABELS[sourceTag]}
    </span>
  ) : null;
  const openRewind = useSessionStore((s) => s.openRewind);
  const currentSession = useSessionStore((s) => s.sessions.find((session) => session.id === s.currentSessionId));
  const sessionBusy = ['running', 'queued'].includes(currentSession?.workerStatus ?? '');
  // Rewind anchors are user-role only (worker report messages included, they
  // land in history as role=user). Busy sessions grey the button out.
  const showRewind = role === 'user' && !message.streaming;
  const actions = showRewind ? (
    <div className="mt-1 flex items-center gap-2">
      <button type="button" onClick={() => openRewind(message)} disabled={sessionBusy}
        className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-text-primary disabled:opacity-50"
        title={sessionBusy ? '任务运行中，无法撤回' : '从此消息撤回并分叉'}>
        <Undo2 size={13} /> 撤回
      </button>
    </div>
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
      <div className={`message-row message-row-user ${sourceTag ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}>
        {sourceBadge}
        <div className="msg user text-sm">
          {rawViewEnabled ? (
            <RawMessageText content={message.content} className="text-sm" />
          ) : (
            <MarkdownRenderer
              content={message.content}
              attachmentIds={attachmentIds}
              className="text-sm"
            />
          )}
        </div>
        <MessageTimestamp ts={message.ts} className="mt-0.5" />
        {actions}
      </div>
    );
  }

  // Assistant messages — no bubble, left-aligned, full-width markdown flow
  return (
    <div className={`message-row message-row-assistant ${sourceTag ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}>
      {sourceBadge}
      <div className="msg assistant text-sm leading-relaxed">
        {rawViewEnabled ? (
          <RawMessageText content={message.content} />
        ) : (
          <MarkdownRenderer
            content={message.content}
            attachmentIds={attachmentIds}
          />
        )}
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
  searchTargetMessageId?: string | null,
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
      if (searchTargetMessageId && msg.messageId === searchTargetMessageId) {
        currentNonBodyGroup = null;
        grouped.push(msg);
        continue;
      }
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
    if (searchTargetMessageId && msg.messageId === searchTargetMessageId) {
      currentToolGroup = null;
      currentThinkingGroup = null;
      grouped.push(msg);
      continue;
    }
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
  searchTargetMessageId?: string | null;
}

export const MessageDisplayItem = memo(function MessageDisplayItem({
  item,
  prevRole = null,
  onTimestampFlashConsumed,
  searchTargetMessageId,
}: MessageDisplayItemProps) {
  if (!('type' in item) && item.messageId === searchTargetMessageId &&
      (item.role === 'tool' || item.role === 'thinking')) {
    return <div className="px-3 sm:px-6 lg:px-8 py-2" data-search-expanded-role={item.role}>
      <div className="text-xs text-text-tertiary">{item.role}</div>
      <MarkdownRenderer content={item.content} />
    </div>;
  }
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
