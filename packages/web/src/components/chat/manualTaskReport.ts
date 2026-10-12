import type { Message, Session } from '@/types';

/**
 * Identity binding for the one message block allowed to report a task
 * explicitly.  Everything the click needs is captured here at render time —
 * the handler never re-reads live Session state, so a late terminal event or a
 * Session switch can never re-target the click at a different task.
 */
export interface ManualReportTarget {
  sessionId: string;
  /** Task identity of the latest terminal; null for a plain (non-assign) turn. */
  taskId: string | null;
  messageId: string;
}

/** Session facts the binding needs; a cold summary may omit them. */
export type ManualReportSessionView = Pick<
  Session,
  'id' | 'managedBy' | 'workerStatus' | 'lastResult'
>;

/**
 * Resolve the single text block that may carry the manual report action: the
 * last settled assistant text row of the latest task.
 *
 * Returns null when the action must not appear at all:
 * - the Session has no manager (no report target),
 * - a worker is running/queued (the tail block is still streaming),
 * - there is no durable terminal result to report,
 * - the tail assistant row carries no stable messageId (identity unprovable).
 *
 * Only the tail block is ever eligible: earlier tasks' final answers never
 * show the action, so exactly one task — the latest — can be reported.
 */
export function findManualReportTarget(
  messages: readonly Message[],
  session: ManualReportSessionView | undefined,
): ManualReportTarget | null {
  if (!session?.managedBy) return null;
  const status = session.workerStatus;
  if (status === 'running' || status === 'queued') return null;
  const lastResult = session.lastResult;
  if (!lastResult || typeof lastResult !== 'object') return null;
  const rawTaskId = (lastResult as Record<string, unknown>).taskId;
  const taskId = typeof rawTaskId === 'string' && rawTaskId ? rawTaskId : null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message || message.role !== 'assistant') continue;
    // The tail assistant row is the latest task's final block.  A streaming
    // or identity-less tail proves nothing: no action anywhere.
    if (message.streaming || !message.messageId) return null;
    return { sessionId: session.id, taskId, messageId: message.messageId };
  }
  return null;
}
