import type { Message } from '@/types';
import { memo, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Bookmark, BookmarkMinus, BookmarkPlus, Check, Copy, Send, Undo2 } from 'lucide-react';
import { copyText } from '@/utils/clipboard';
import { useUIStore } from '@/stores/uiStore';
import { fetchSessionMessageText } from '@/services/api';
import { useSessionStore, manualTaskReportKey } from '@/stores/sessionStore';
import { isPanMessageId, selectBookmarkMark, useBookmarkStore } from '@/stores/bookmarkStore';
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
import { useCapability, usePlatformStore } from '@/stores/platformStore';
import { platformLabel } from '@/utils/platformCapabilities';
import type { ManualReportTarget } from './manualTaskReport';
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
  /** Binding for this completed task run's tail text block. */
  manualReportTarget?: ManualReportTarget | null;
}

/** User-facing copy for the durable source tags (classification: messageFilter). */
const SOURCE_TAG_LABELS: Record<MessageSourceTag, string> = {
  'ta-report': 'TA report',
  'ma-assign': 'MA assign',
  system: 'System',
};

/** Tooltip/state copy for the explicit task-report action. */
function manualReportCopy(
  state: 'sending' | 'sent' | 'failed' | 'uncertain' | undefined,
  error: string | undefined,
): { label: string; title: string } {
  if (state === 'sending') {
    return { label: '汇报中…', title: '正在向 manager 汇报该任务' };
  }
  if (state === 'sent') {
    return { label: '已汇报', title: '该任务已汇报给 manager（不影响自动汇报订阅）' };
  }
  if (state === 'failed') {
    return { label: '重试汇报', title: error || '汇报失败，点击重试' };
  }
  if (state === 'uncertain') {
    return {
      label: '重试汇报',
      title: '汇报结果不确定（可能已送达）：重试会按同一身份幂等确认，不会重复发送',
    };
  }
  return { label: '汇报', title: '向 manager 手动汇报该任务（不影响自动汇报订阅）' };
}

export const MessageBubble = memo(function MessageBubble({ message, prevRole = null, manualReportTarget = null }: MessageBubbleProps) {
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
  // The explicit report action belongs to each task's tail text block (not only
  // the newest task).  The store entry is keyed by the binding's identity so a
  // virtualized remount keeps the in-flight/sent state instead of restarting or
  // losing it.
  const manualReportKey = manualReportTarget && manualReportTarget.messageId === message.messageId
    ? manualTaskReportKey(manualReportTarget.sessionId, manualReportTarget)
    : null;
  const manualReportEntry = useSessionStore((s) => (
    manualReportKey ? s.manualTaskReports[manualReportKey] : undefined
  ));
  const reportTaskManually = useSessionStore((s) => s.reportTaskManually);
  // ── Bookmark state and the shared block menu ──
  // The bookmark entry exists only for converged, durable, bookmarkable rows:
  // a streaming or unpersisted row has no addressable identity yet, and tool /
  // thinking / system rows are not bookmark content (the backend rejects them
  // with `message_not_bookmarkable`).  Proven store state wins over the
  // projected flag, including proven absence after a removal.
  const sessionId = useSessionStore((s) => s.currentSessionId);
  // Proven store state (including proven ABSENCE after a removal) wins over
  // the row's projected flag; the projection is only a fallback until this
  // client has any evidence for the id.
  const liftedBookmarked = useBookmarkStore(selectBookmarkMark(sessionId ?? '', message.messageId));
  const bookmarked = liftedBookmarked ?? Boolean(message.bookmarked);
  const addBookmark = useBookmarkStore((s) => s.addBookmark);
  const removeBookmark = useBookmarkStore((s) => s.removeBookmark);
  const bookmarkable = (role === 'user' || role === 'assistant')
    && !message.streaming
    && isPanMessageId(message.messageId);
  const [menuAt, setMenuAt] = useState<{ top: number; left: number } | null>(null);
  useEffect(() => {
    if (!menuAt) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuAt(null); };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('[data-message-block-menu]')) return;
      setMenuAt(null);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [menuAt]);
  const openReportCopy = manualReportCopy(manualReportEntry?.state, manualReportEntry?.error);
  const openRewind = useSessionStore((s) => s.openRewind);
  const sessionBusy = useSessionStore((s) => ['running', 'queued'].includes(
    s.sessions.find((session) => session.id === s.currentSessionId)?.workerStatus ?? '',
  ));
  // Rewind anchors are user-role only (worker report messages included, they
  // land in history as role=user). Busy sessions grey the button out; a server
  // without the rewind capability keeps the entry visible but disabled, so the
  // limitation is stated instead of discovered by clicking.
  const currentAdapter = useSessionStore((s) => s.sessions.find((session) => session.id === s.currentSessionId)?.adapter);
  const rewindSupported = useCapability('rewind');
  const platform = usePlatformStore((s) => s.platform);
  const showRewind = currentAdapter === 'cbc' && role === 'user' && !message.streaming;
  const rewindTitle = !rewindSupported
    ? `当前平台（${platformLabel(platform)}）暂不支持撤回`
    : sessionBusy ? '任务运行中，无法撤回' : '从此消息撤回并分叉';
  const actions = showRewind ? (
      <button type="button" onClick={() => openRewind(message)} disabled={sessionBusy || !rewindSupported}
        className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-text-primary disabled:opacity-50"
        data-rewind-supported={rewindSupported}
        title={rewindTitle}>
        <Undo2 size={13} /> 撤回
      </button>
  ) : null;
  // The row's left gutter is the only body-level hit target: from the
  // leftmost edge to the block's colored vertical line (CSS keeps its width
  // in step with the row padding).  Content, role rail and role glyph stay
  // untouched — no added bar.  It appears when the row has any action:
  // bookmarkable content, or a bound manual report (whose tail block may
  // carry a non-pan canonical id).
  // The report entry belongs to the assistant tail text block only (the
  // binding contract; user rows never carried it), while the bookmark entry
  // belongs to any bookmarkable row.
  const hasManualReport = role === 'assistant' && Boolean(manualReportKey && manualReportTarget);
  const showGutter = bookmarkable || hasManualReport;
  const bookmarkGutter = showGutter ? (
    <button
      type="button"
      className="chat-message-bookmark-gutter"
      data-message-bookmark-gutter={message.messageId}
      aria-label="Message actions"
      aria-expanded={menuAt !== null}
      onClick={(event) => {
        setMenuAt({
          top: Math.min(event.clientY + 8, Math.max(8, window.innerHeight - 132)),
          left: Math.min(event.clientX + 8, Math.max(8, window.innerWidth - 184)),
        });
      }}
    />
  ) : null;
  // Every bookmarked block carries the body bookmark tag below it.
  const bookmarkTag = bookmarked ? (
    <span className="chat-message-bookmark-tag" data-message-bookmark-tag="">
      <Bookmark size={11} /> Bookmark
    </span>
  ) : null;
  // One shared block menu: bookmark add/remove, plus 手动汇报 for the task's
  // tail block (the button used to live in the message meta row).
  const blockMenu = menuAt ? createPortal(
    <div
      className="message-block-menu"
      data-message-block-menu
      role="menu"
      style={{ top: menuAt.top, left: menuAt.left }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        role="menuitem"
        disabled={!message.content.trim() || !sessionId || !isPanMessageId(message.messageId)}
        title={!message.content.trim() ? '消息正文为空' : !isPanMessageId(message.messageId)
          ? '消息原文尚不可读取' : '复制消息原文（保留 Markdown）'}
        onClick={() => {
          setMenuAt(null);
          // Capture the clicked identity before an async read or Session
          // switch. Display content can contain rewritten editor links.
          const id = message.messageId;
          if (!message.content.trim() || !sessionId || !isPanMessageId(id)) return;
          void fetchSessionMessageText(sessionId, id!).then(body => {
            if (!body.trim()) throw new Error('Empty message body');
            return copyText(body);
          }).catch(() => {
            useUIStore.getState().showToast('复制失败，请重试', 'error');
          });
        }}
      >
        <Copy size={12} /> 复制全文
      </button>
      {bookmarkable ? (
        <button
          type="button"
          role="menuitem"
          data-bookmark-action={bookmarked ? 'remove' : 'add'}
          onClick={() => {
            setMenuAt(null);
            if (sessionId && message.messageId) {
              void (bookmarked
                ? removeBookmark(sessionId, message.messageId)
                : addBookmark(sessionId, message.messageId));
            }
          }}
        >
          {bookmarked ? <BookmarkMinus size={12} /> : <BookmarkPlus size={12} />}
          {bookmarked ? 'Remove bookmark' : 'Add bookmark'}
        </button>
      ) : null}
      {bookmarkable && hasManualReport ? (
        <div className="message-block-menu-divider" role="separator" />
      ) : null}
      {hasManualReport && manualReportKey && manualReportTarget ? (
        <button
          type="button"
          role="menuitem"
          data-manual-report={manualReportKey}
          disabled={manualReportEntry?.state === 'sending' || manualReportEntry?.state === 'sent'}
          title={openReportCopy.title}
            onClick={() => {
              setMenuAt(null);
              void reportTaskManually(manualReportTarget.sessionId, {
                taskId: manualReportTarget.taskId,
                taskSeq: manualReportTarget.taskSeq,
                messageId: manualReportTarget.messageId,
                // The run's durable cursor travels with the identity: a plain
                // run whose terminal has no taskSeq binds (and is reported)
                // by its cursor alone.
                ...(typeof manualReportTarget.resultCursor === 'number'
                  ? { resultCursor: manualReportTarget.resultCursor } : {}),
              });
            }}
        >
          {manualReportEntry?.state === 'sent' ? <Check size={12} /> : <Send size={12} />}
          {manualReportEntry?.state ? openReportCopy.label : '手动汇报'}
        </button>
      ) : null}
    </div>,
    document.body,
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
        <div className="inline-flex min-w-0 flex-col items-start">
          {sourceBadge}
          <span className="msg system text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
            {message.content}
          </span>
        </div>
      </div>
    );
  }

  // TUI-style message rows use flex alignment so the virtualized row can keep
  // its full width without changing the measured wrapper's layout.
  if (role === 'user') {
    return (
      <div
        className={`message-row message-row-user relative ${sourceTag ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}
        data-bookmarked={bookmarked ? 'true' : 'false'}
      >
        {bookmarkGutter}
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
        {bookmarkTag}
        <div className="mt-0.5 flex items-center justify-end gap-2 self-end" data-message-meta="">
          {actions}
          <MessageTimestamp ts={message.ts} />
        </div>
        {blockMenu}
      </div>
    );
  }

  // Assistant messages — no bubble, left-aligned, full-width markdown flow
  return (
    <div
      className={`message-row message-row-assistant relative ${sourceTag ? 'message-row-worker-report' : ''} ${mt} px-3 sm:px-6 lg:px-8`}
      data-bookmarked={bookmarked ? 'true' : 'false'}
    >
      {bookmarkGutter}
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
      {bookmarkTag}
      <MessageTimestamp ts={message.ts} className="mt-0.5" />
      {blockMenu}
    </div>
  );
}, (previous, next) => {
  // A streaming sibling rebuilds the binding map. Equal authority fields do
  // not require this settled row to rerender; changed bindings must still do so.
  const a = previous.manualReportTarget;
  const b = next.manualReportTarget;
  const sameReport = a === b || (!!a && !!b
    && a.sessionId === b.sessionId && a.taskId === b.taskId && a.taskSeq === b.taskSeq
    && a.messageId === b.messageId && a.resultCursor === b.resultCursor && a.terminalKey === b.terminalKey);
  return previous.message === next.message && previous.prevRole === next.prevRole && sameReport;
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
  manualReportTarget?: ManualReportTarget | null;
}

export const MessageDisplayItem = memo(function MessageDisplayItem({
  item,
  prevRole = null,
  onTimestampFlashConsumed,
  searchTargetMessageId,
  manualReportTarget = null,
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
  return <MessageBubble message={item as Message} prevRole={prevRole} manualReportTarget={manualReportTarget} />;
});
