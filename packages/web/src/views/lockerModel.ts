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
export interface TrustedBinding {
  summary: string;
  actionId: string;
  actionDigest: string;
  triggerRevision: number;
  targetIds: string[];
  arguments: Record<string, unknown>;
  authorization?: { producerSessionId: string; basis: string };
}
export type QuestionAnswer =
  | { rejected: true; reason?: string }
  | { text: string }
  | { choiceId: string }
  | { choiceIds: string[] };
export interface CardQuestion {
  id: string;
  prompt: string;
  type: 'single_choice' | 'multi_choice' | 'text';
  required?: boolean;
  choices?: { id: string; label: string; triggerRef?: { id: string; revision?: number } }[];
}
export function cardChoiceBinding(
  card: InteractionCard,
  choiceId: string,
  questionId?: string,
): TrustedBinding | undefined {
  const all = card.trustedChoices;
  const bindings =
    questionId === undefined
      ? all
      : all && Object.hasOwn(all, questionId)
        ? (all[questionId] as Record<string, TrustedBinding>)
        : undefined;
  return bindings && Object.hasOwn(bindings, choiceId)
    ? (bindings[choiceId] as TrustedBinding)
    : undefined;
}
export interface InteractionCard {
  schemaVersion?: 1 | 2;
  id: string;
  lockerId: string;
  ownerSessionId: string;
  relatedMessageId?: string | null;
  recipientId?: string;
  createdAt: string;
  read: boolean;
  revision: number;
  status: 'open' | 'responded' | 'rejected' | 'no_answer_needed';
  definition: {
    schemaVersion?: 1 | 2;
    questions?: CardQuestion[];
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
    answers?: Record<string, QuestionAnswer>;
    answer?: string;
    choiceId?: string;
    outcome?: 'submit' | 'reject';
    createdAt?: string;
  };
  delivery: { state: string; receiptId?: string };
  rejectionDelivery?: { state: string; receiptId?: string };
  trustedChoices?: Record<string, TrustedBinding | Record<string, TrustedBinding>>;
}
export interface LockerMessage {
  kind: 'message';
  revision: number;
  id: string;
  lockerId: string;
  ownerSessionId: string;
  relatedMessageId?: string | null;
  recipientId: string;
  createdAt: string;
  read: boolean;
  message: Message;
}
export interface LockerParent {
  type: 'locker_message' | 'interaction_card' | 'user_card';
  id: string;
}
export function lockerParent(item: LockerItem): LockerParent {
  return {
    type: isUserCard(item)
      ? 'user_card'
      : 'message' in item
        ? 'locker_message'
        : 'interaction_card',
    id: item.id,
  };
}
export interface UserCard {
  parent?: LockerParent | null;
  direction: 'user';
  kind: 'question' | 'task';
  id: string;
  lockerId: string;
  ownerSessionId: string;
  createdAt: string;
  answeredAt: string | null;
  body: string;
  attachments: { id: string; name: string; mimeType?: string; size?: number }[];
  revision: number;
  status: 'pending' | 'answered';
  answer: string | null;
  read: boolean;
}
export function isUserCard(item: LockerItem): item is UserCard {
  return 'direction' in item && item.direction === 'user';
}
export type LockerItem = InteractionCard | LockerMessage | UserCard;
export function lockerItemPreview(item: LockerItem | undefined): string {
  return item
    ? isUserCard(item)
      ? item.body
      : 'message' in item
        ? item.message.content
        : item.definition.title
    : 'No messages';
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

/** Iterative preorder: attachments never enter chronological sorting, even at arbitrary depth.
 * Missing parents/corrupt cycles are displayed once, with their parent link retained.
 */
export function lockerTimeline(items: LockerItem[]) {
  const keyOf = (item: LockerItem) => `${lockerParent(item).type}:${item.id}`;
  const keys = new Set(items.map(keyOf));
  const children = new Map<string, LockerItem[]>();
  const roots: LockerItem[] = [];
  for (const item of [...items].sort(compareLockerItems)) {
    const parent = isUserCard(item) ? item.parent : null;
    const key = parent ? `${parent.type}:${parent.id}` : '';
    if (key && keys.has(key)) {
      const siblings = children.get(key) ?? [];
      siblings.push(item);
      children.set(key, siblings);
    } else roots.push(item);
  }
  const result: {
    item: LockerItem;
    position: 'created';
    displayedAt: string;
    key: string;
    rootId: string;
    parent?: LockerParent | null;
  }[] = [];
  const visited = new Set<string>();
  const append = (root: LockerItem) => {
    const stack = [root];
    while (stack.length) {
      const item = stack.pop()!;
      const key = keyOf(item);
      if (visited.has(key)) continue;
      visited.add(key);
      result.push({
        item,
        position: 'created',
        displayedAt: item.createdAt,
        key: `${item.id}:created`,
        rootId: root.id,
        parent: isUserCard(item) ? item.parent : null,
      });
      const descendants = children.get(key) ?? [];
      for (let i = descendants.length - 1; i >= 0; i--) stack.push(descendants[i]!);
    }
  };
  for (const root of roots) append(root);
  for (const item of items) if (!visited.has(keyOf(item))) append(item);
  return result;
}

/** One initial tail page plus at most five older pages. Attachments consume no slots. */
export function lockerEntryWindow(roots: LockerItem[], pageSize = 50, maxOlderPages = 5) {
  const firstUnread = roots.findIndex((item) => !item.read);
  const needed = firstUnread < 0 ? pageSize : roots.length - firstUnread;
  const pages = Math.min(maxOlderPages, Math.max(0, Math.ceil(needed / pageSize) - 1));
  const size = pageSize * (1 + pages);
  return {
    size,
    firstUnreadId: firstUnread < 0 ? null : roots[firstUnread]!.id,
    limited: firstUnread >= 0 && firstUnread < Math.max(0, roots.length - size),
  };
}
