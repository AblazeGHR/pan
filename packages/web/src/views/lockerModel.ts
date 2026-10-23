import { useAutomationAuth } from '@/stores/automationStore';

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
  createdAt: string;
  read: boolean;
  revision: number;
  status: 'open' | 'responded' | 'rejected';
  definition: {
    title: string;
    body: string;
    summaryBody: string;
    attachments?: { id: string; name: string }[];
    answerInput?: { label?: string; maxLength?: number };
    choices?: { id: string; label: string; triggerRef?: { id: string; revision?: number } }[];
  };
  response: null | { answer?: string; choiceId?: string };
  delivery: { state: string };
  rejectionDelivery?: { state: string };
}
/** Preserve status so a proven pre-admission validation rejection is distinct
 * from a network failure or a response whose persistence is uncertain. */
export class LockerRequestError extends Error {
  constructor(public readonly status: number, message: string, public readonly code?: string) { super(message); }
}
export async function lockerRequest<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const token = useAutomationAuth.getState().token;
  const response = await fetch(path, { method, credentials: 'same-origin', signal,
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && useAutomationAuth.getState().token === token) useAutomationAuth.getState().clear();
    throw new LockerRequestError(response.status, result.detail?.code || result.detail?.message || `HTTP ${response.status}`, result.detail?.code);
  }
  return result as T;
}
/** This is a projection over real Lockers; it persists no aggregate identity. */
export function lockerProjection(lockers: Locker[], cards: InteractionCard[], workspace: string, selected: string) {
  const scope = lockers.filter(l => !workspace || l.workspaceIds.includes(workspace));
  const ids = new Set(scope.filter(l => selected === 'aggregate' || selected === l.id).map(l => l.id));
  return cards.filter(c => ids.has(c.lockerId)).sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
}
