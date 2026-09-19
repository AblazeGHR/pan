import type { Message } from '@/types';

// React and TanStack Virtual need an identity that survives a streaming
// replacement of the Message object. Keep it out of the wire/persisted shape.
// A native item can expand into several visible blocks, so nativeItemId alone
// is not a safe React key; every displayed Message gets a unique local key and
// immutable replacements explicitly inherit that key.
const messageIdentities = new WeakMap<Message, string>();
let nextLocalIdentity = 0;

export function rememberMessageIdentity(message: Message): void {
  if (messageIdentities.has(message)) return;
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

/**
 * The key belongs to the logical display item, not its current array index.
 * A tool group uses its first tool because appending/replacing later tools
 * must not remount the whole group or discard its measurement/expanded state.
 */
export function getDisplayItemKey(
  item: Message | ToolGroupDisplayItem | undefined,
  index: number,
): string {
  if (!item) return `missing:${index}`;
  if ('type' in item && item.type === 'tool_group') {
    const firstTool = item.items[0];
    return firstTool ? `tool-group:${getMessageIdentity(firstTool)}` : `tool-group:empty:${index}`;
  }
  return `message:${getMessageIdentity(item as Message)}`;
}
