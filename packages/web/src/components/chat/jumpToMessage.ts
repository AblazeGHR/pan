import type { RefObject } from 'react';
import type { ChatMessagesHandle } from './ChatMessages';
import { useSessionStore } from '@/stores/sessionStore';

const paint = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

/** Shared rail/context jump: canonical pagination, identity guard, existing
 * virtualized scroll/highlight engine, and one post-mount retry. */
export async function jumpToMessage(
  chatRef: RefObject<ChatMessagesHandle | null>,
  target: { offset: number; total: number; messageId?: string },
  current: () => boolean,
  signal?: AbortSignal,
) {
  const load = useSessionStore.getState().ensureMessageLoaded;
  const fromEnd = target.total - 1 - target.offset;
  const message = await (signal ? load(fromEnd, target.total, signal) : load(fromEnd, target.total));
  if (!current()) return false;
  if (!message || (target.messageId && message.messageId !== target.messageId))
    throw new Error('相关消息已被替换或索引已失效，请返回 Locker 刷新。');
  await paint();
  if (!current()) return false;
  let jumped = chatRef.current?.scrollToMessage(message, target.offset) ?? false;
  if (!jumped) {
    await paint();
    if (!current()) return false;
    jumped = chatRef.current?.scrollToMessage(message, target.offset) ?? false;
  }
  if (!jumped) throw new Error('相关消息已加载，但当前视图无法定位此消息。');
  return true;
}
