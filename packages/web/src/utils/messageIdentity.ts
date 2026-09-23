import type { Message } from '@/types';

// React and TanStack Virtual need an identity that survives a streaming
// replacement of the Message object. Keep it out of the wire/persisted shape.
// A native item can expand into several visible blocks, so nativeItemId alone
// is not a safe React key; every displayed Message gets a unique local key and
// immutable replacements explicitly inherit that key.
const messageIdentities = new WeakMap<Message, string>();
const persistentIdentities = new Map<string, string>();
let nextLocalIdentity = 0;

export function rememberMessageIdentity(message: Message): void {
  if (messageIdentities.has(message)) return;
  const persistentKey = message.blockId
    ? `block:${message.blockId}`
    : message.messageId
      ? `message:${message.messageId}`
      : null;
  if (persistentKey) {
    const existing = persistentIdentities.get(persistentKey);
    if (existing) {
      messageIdentities.set(message, existing);
      return;
    }
    const identity = `${persistentKey}:${nextLocalIdentity++}`;
    persistentIdentities.set(persistentKey, identity);
    messageIdentities.set(message, identity);
    return;
  }
  const nativeId = message.nativeItemId;
  const prefix = nativeId ? `native:${nativeId}` : 'local';
  messageIdentities.set(message, `${prefix}:${nextLocalIdentity++}`);
}

export function inheritMessageIdentity(next: Message, previous: Message): void {
  messageIdentities.set(next, getMessageIdentity(previous));
}

export function cloneMessageWithIdentity(message: Message): Message {
  const clone = { ...message };
  inheritMessageIdentity(clone, message);
  return clone;
}

export function getMessageIdentity(message: Message): string {
  const existing = messageIdentities.get(message);
  if (existing) return existing;
  rememberMessageIdentity(message);
  return messageIdentities.get(message)!;
}

export type ToolGroupDisplayItem = { type: 'tool_group'; items: Message[] };
export type ThinkingGroupDisplayItem = { type: 'thinking_group'; items: Message[] };
export type GroupDisplayItem = ToolGroupDisplayItem | ThinkingGroupDisplayItem;

/**
 * The key belongs to the logical display item, not its current array index or
 * its current role. A provider may expose one tool first as an assistant/text
 * delta and then finalize that same native item as a tool. Keeping a different
 * `message:`/`tool-group:` prefix for that role transition remounts the row at
 * exactly the moment its neighboring streamed delta is changing height, which
 * loses the virtualizer measurement and can move the reader's anchor. The
 * identity is already unique per displayed Message, so one display namespace
 * is sufficient for both shapes. A group uses its first member because
 * appending/replacing later members must not remount the row or discard its
 * measurement/expanded state.
 */
export function getDisplayItemKey(
  item: Message | GroupDisplayItem | undefined,
  index: number,
): string {
  if (!item) return `missing:${index}`;
  if ('type' in item) {
    const firstItem = item.items[0];
    return firstItem ? `display:${getMessageIdentity(firstItem)}` : `display:empty:${index}`;
  }
  return `display:${getMessageIdentity(item as Message)}`;
}
