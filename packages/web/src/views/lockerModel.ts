import type { Message } from '@/types';
import { useAutomationAuth } from '@/stores/automationStore';
import { ALL_WORKSPACES, UNGROUPED_WORKSPACES } from '@/utils/sessionFilters';

export interface Locker {
  id: string;
  sessionId: string;
  sourceName: string;
  workspaceIds: string[];
  available: boolean;
  total: number;
  unread: number;
}
export interface InteractionCard {
  id: string;
  lockerId: string;
  ownerSessionId: string;
  recipientId?: string;
  createdAt: string;
  read: boolean;
  revision: number;
  status: 'open' | 'responded' | 'rejected';
  definition: {
    title: string;
    body: string;
    summaryBody: string;
    attachments?: { id: string; name: string; mimeType?: string; size?: number }[];
    answerInput?: { label?: string; maxLength?: number };
    choices?: { id: string; label: string; triggerRef?: { id: string; revision?: number } }[];
  };
  response: null | {
    id?: string;
    requestId?: string;
    answer?: string;
    choiceId?: string;
    outcome?: 'submit' | 'reject';
    createdAt?: string;
  };
  delivery: { state: string; receiptId?: string };
  rejectionDelivery?: { state: string; receiptId?: string };
  trustedChoices?: Record<
    string,
    {
      summary: string;
      actionId: string;
      actionDigest: string;
      triggerRevision: number;
      targetIds: string[];
      arguments: Record<string, unknown>;
      authorization?: { producerSessionId: string; basis: string };
    }
  >;
}
export interface LockerMessage {
  kind: 'message';
  revision: number;
  id: string;
  lockerId: string;
  ownerSessionId: string;
  recipientId: string;
  createdAt: string;
  read: boolean;
  message: Message;
}
export type LockerItem = InteractionCard | LockerMessage;
export function lockerItemPreview(item: LockerItem | undefined): string {
  return item ? ('message' in item ? item.message.content : item.definition.title) : 'No messages';
}
export function compareLockerItems(a: LockerItem, b: LockerItem): number {
  return Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.id.localeCompare(b.id);
}
export async function loadLockerMessages(signal: AbortSignal): Promise<LockerMessage[]> {
  const messages: LockerMessage[] = [];
  const cursors = new Set<string>();
  let after: string | null = null;
  do {
    const page: { messages: LockerMessage[]; nextAfter: string | null } = await lockerRequest(
      `/api/user-locker-messages?limit=500${after ? `&after=${encodeURIComponent(after)}` : ''}`,
      'GET',
      undefined,
      signal,
    );
    messages.push(...page.messages);
    after = page.nextAfter;
    if (after && cursors.has(after))
      throw new Error('Locker message pagination did not advance. Retry refresh.');
    if (after) cursors.add(after);
  } while (after);
  return messages;
}
export class LockerRequestError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
  }
}
export async function lockerRequest<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const token = useAutomationAuth.getState().token;
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    signal,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (useAutomationAuth.getState().token !== token)
    throw new LockerRequestError(
      401,
      'Authorization changed. Discarding the previous response.',
      'obsolete_auth',
    );
  if (!response.ok) {
    if (response.status === 401 && useAutomationAuth.getState().token === token)
      useAutomationAuth.getState().clear();
    throw new LockerRequestError(
      response.status,
      result.detail?.code || result.detail?.message || `HTTP ${response.status}`,
      result.detail?.code,
    );
  }
  return result as T;
}
/** Frontend-only projection. It never creates an aggregate Locker or copies a Card. */
export function lockerProjection<T extends LockerItem>(
  lockers: Locker[],
  cards: T[],
  workspace: string,
  selected: string,
) {
  const scope =
    workspace === ALL_WORKSPACES || !workspace
      ? lockers
      : workspace === UNGROUPED_WORKSPACES
        ? lockers.filter((locker) => locker.workspaceIds.length === 0)
        : lockers.filter((locker) => locker.workspaceIds.includes(workspace));
  const ids = new Set(
    scope
      .filter((locker) => selected === 'aggregate' || selected === locker.id)
      .map((locker) => locker.id),
  );
  return cards.filter((card) => ids.has(card.lockerId)).sort(compareLockerItems);
}
