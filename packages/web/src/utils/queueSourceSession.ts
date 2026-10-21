import type { AgentQueueItem } from '@/types';

/**
 * Identity rules for the structured origin of a queued message.
 *
 * The origin is ONLY the server-provided `meta.sourceSessionId`. Nothing is
 * parsed out of the message text and no name is inferred from a prefix, so a
 * message can never claim an origin it was not stored with.
 */

/**
 * Does this queue item claim a Session origin at all?
 *
 * Agent-sourced messages always originate from a Session; a report may carry
 * the producing Session too (only when the backend already stored it). Every
 * other source (user/qq/wechat) has no agent origin, and showing one would be
 * a wrong identity — worse than showing none.
 */
export function hasSessionOrigin(item: AgentQueueItem): boolean {
  return item.source === 'agent' || item.kind === 'report';
}

/** The stored, structured origin id — never inferred from the message body. */
export function storedSourceSessionId(item: AgentQueueItem): string | null {
  const value = item.meta?.sourceSessionId;
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}
