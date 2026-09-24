import type { Message } from '@/types';
import type { AppSettings } from '@/stores/appSettingsStore';

/** Meta-agent orchestration messages (`worker_send` auto-prepends this marker). */
export const META_AGENT_PREFIX = '////by agent';
/** Task-agent completion reports. */
export const TASK_AGENT_PREFIX = '@@@@by agent';
/** QQ-injected messages (inbox reminders / subscription pushes). */
export const QQ_PREFIX = '@@@@by qq';

export type MessageVisibilitySettings = Pick<
  AppSettings,
  'showMetaAgent' | 'showTaskAgent' | 'showQQ'
>;

/**
 * Frontend-only display filter. Drops messages whose source-marker prefix is
 * hidden by a disabled toggle. The input array is never mutated — the store
 * keeps every message and toggling a switch back on restores them.
 */
export function filterVisibleMessages(
  messages: Message[],
  settings: MessageVisibilitySettings,
): Message[] {
  const { showMetaAgent, showTaskAgent, showQQ } = settings;
  return messages.filter((m) => {
    const content = m.content.trimStart();
    if (!showMetaAgent && content.startsWith(META_AGENT_PREFIX)) return false;
    if (!showTaskAgent && content.startsWith(TASK_AGENT_PREFIX)) return false;
    if (!showQQ && content.startsWith(QQ_PREFIX)) return false;
    return true;
  });
}


export type QuickJumpKind = 'user' | 'worker';

export interface QuickJumpMessage {
  message: Message;
  /** Index in the filtered message list used by ChatMessages. */
  index: number;
  kind: QuickJumpKind;
  preview: string;
}

/** Compact target stored by the full-history navigation index. fromEnd is
 * stable while older pages are prepended to the chat window. */
export interface QuickJumpIndexItem {
  fromEnd: number;
  kind: QuickJumpKind;
  preview: string;
}

/** Return the marker category used by the quick-location rail, if any. */
export function getQuickJumpKind(message: Message): QuickJumpKind | null {
  const content = message.content.trimStart();
  // A task-agent report wins over the role because reports can be serialized
  // as user messages by some adapters.
  if (content.startsWith(TASK_AGENT_PREFIX)) return 'worker';
  if (message.role === 'user') return 'user';
  return null;
}

/** Remove transport/source headers before showing a compact hover preview. */
export function getQuickJumpPreview(content: string, maxLength = 120): string {
  const trimmed = content.trimStart();
  const withoutHeader = trimmed.replace(
    /^(?:@@@@by agent|\/\/\/\/by agent|@@@@by qq)\s*:\s*[^\r\n]*(?:\r?\n|$)/,
    '',
  ).trimStart();
  const normalized = withoutHeader.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}\u2026`;
}

/** Build compact navigation targets for a server history page. start is
 * the absolute index of the first message in messages. */
export function getQuickJumpIndexItems(
  messages: Message[],
  total: number,
  start: number,
  settings: MessageVisibilitySettings,
): QuickJumpIndexItem[] {
  return messages.flatMap((message, index) => {
    if (filterVisibleMessages([message], settings).length === 0) return [];
    const kind = getQuickJumpKind(message);
    return kind
      ? [{
          fromEnd: total - 1 - (start + index),
          kind,
          preview: getQuickJumpPreview(message.content),
        }]
      : [];
  });
}

/** Build the visible user/worker targets shown by the navigation rail. */
export function getQuickJumpMessages(
  messages: Message[],
  settings: MessageVisibilitySettings,
): QuickJumpMessage[] {
  return filterVisibleMessages(messages, settings).flatMap((message, index) => {
    const kind = getQuickJumpKind(message);
    return kind
      ? [{ message, index, kind, preview: getQuickJumpPreview(message.content) }]
      : [];
  });
}
