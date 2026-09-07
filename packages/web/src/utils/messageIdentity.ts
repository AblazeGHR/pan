import type { Message } from '@/types';

// React and TanStack Virtual need an identity that survives a streaming
// replacement of the Message object. Keep the fallback identity out of the
// wire/persisted shape: nativeItemId is authoritative when present, while the
// WeakMap covers legacy adapters whose deltas have no native id.
const messageIdentities = new WeakMap<Message, string>();
let nextLocalIdentity = 0;

export function rememberMessageIdentity(message: Message): void {
  if (messageIdentities.has(message)) return;
  const nativeId = message.nativeItemId;
  messageIdentities.set(message, nativeId ? `native:${nativeId}` : `local:${nextLocalIdentity++}`);
}

export function inheritMessageIdentity(next: Message, previous: Message): void {
  const previousIdentity = messageIdentities.get(previous);
  if (previousIdentity) {
    messageIdentities.set(next, previousIdentity);
  } else {
    rememberMessageIdentity(next);
  }
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
