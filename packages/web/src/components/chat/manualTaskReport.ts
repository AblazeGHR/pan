import type { Message, Session, TerminalIdentity } from '@/types';

/**
 * Identity binding for one task's tail text block.  Everything a click needs is
 * captured here at render time — the handler never re-reads live Session state,
 * so a late terminal event or a Session switch can never re-target the click at
 * a different task.
 */
export interface ManualReportTarget {
  sessionId: string;
  /** Formal assign task identity (durable in the task's dispatch row). */
  taskId: string;
  /** The completed run's sequence, taken from its durable terminal. */
  taskSeq: number | null;
  /** The bound text block's canonical identity. */
  messageId: string;
  resultCursor?: number;
  terminalKey?: string;
}

/** Session facts the binding needs; a cold summary may omit them. */
export type ManualReportSessionView = Pick<
  Session,
  'id' | 'managedBy' | 'terminalResults' | 'historyEpoch'
>;

/**
 * A task boundary in the transcript: a formal dispatch row (agent assign) that
 * carries a durable taskId.  Non-assign user rows start a different unit of
 * work (plain turn, report, notice) and end the current task's segment.
 */
function isFormalAssign(message: Message): message is Message & { taskId: string } {
  return message.role === 'user'
    && message.source === 'agent'
    && message.taskIdSource !== 'active'
    && typeof message.taskId === 'string'
    && message.taskId.length > 0;
}

/** An agent follow-up that inherits the current task's id (same task scope). */
function isSameTaskFollowUp(message: Message, taskId: string): boolean {
  return message.role === 'user'
    && message.source === 'agent'
    && message.taskIdSource === 'active'
    && (!message.taskId || message.taskId === taskId);
}

/** Whether the run's final text block can be proven from this Session. */
function isBlockProvable(
  session: ManualReportSessionView,
  terminal: TerminalIdentity,
): boolean {
  if (!session.historyEpoch || terminal.historyEpoch !== session.historyEpoch) return false;
  return typeof terminal.historyRevision === 'number' && terminal.historyRevision > 0;
}

/**
 * Resolve the explicit report action for EVERY completed task in the loaded
 * transcript, keyed by the bound block's messageId.
 *
 * The requirement is one action per task — "the last text block of that task" —
 * not only for the newest task: an earlier task's final answer stays reportable
 * after newer tasks finish or while a newer task is still running.  A task is
 * bound only when its block↔run relation is provable on BOTH sides:
 * - the loaded transcript proves exactly one dispatch segment for that taskId,
 *   with a settled tail text block that has a stable messageId, and
 * - the Session's durable terminal index holds exactly one terminal for that
 *   taskId, and that run's block boundary is comparable (same history epoch,
 *   known revision).
 *
 * Anything else is left without an action instead of being bound to "the newest
 * terminal" of that task id: two runs of one taskId, a window that holds only
 * one of several runs, a terminal whose history was rewritten, an identity-less
 * tail or a still-streaming tail all stay unbound.  The server re-validates the
 * same relation and refuses when it cannot prove it.
 */
export function findManualReportTargets(
  messages: readonly Message[],
  session: ManualReportSessionView | undefined,
): Map<string, ManualReportTarget> {
  const targets = new Map<string, ManualReportTarget>();
  if (!session?.managedBy) return targets;
  const terminals = (session.terminalResults ?? []).filter(
    (terminal): terminal is TerminalIdentity => !!terminal,
  );
  if (terminals.length === 0) return targets;

  // Count both sides per taskId so an ambiguous task is never bound.
  const segments = new Map<string, Array<Message>>();
  const terminalCounts = new Map<string, TerminalIdentity[]>();
  for (const terminal of terminals) {
    const taskId = typeof terminal.taskId === 'string' ? terminal.taskId : null;
    if (!taskId) continue;
    const bucket = terminalCounts.get(taskId);
    if (bucket) bucket.push(terminal);
    else terminalCounts.set(taskId, [terminal]);
  }

  let taskId: string | null = null;
  let tail: Message | null = null;
  const finish = () => {
    if (taskId && tail && !tail.streaming && tail.messageId) {
      const bucket = segments.get(taskId);
      if (bucket) bucket.push(tail);
      else segments.set(taskId, [tail]);
    }
    taskId = null;
    tail = null;
  };

  for (const message of messages) {
    if (isFormalAssign(message)) {
      finish();
      taskId = message.taskId;
      continue;
    }
    if (!taskId) continue;
    if (message.role === 'user') {
      // A user row that is not this task's inherited follow-up begins another
      // unit of work; its answer must never be attributed to this task.
      if (!isSameTaskFollowUp(message, taskId)) finish();
      continue;
    }
    if (message.role === 'assistant') tail = message;
  }
  finish();

  for (const [segmentTaskId, tails] of segments) {
    if (tails.length !== 1) continue;              // several runs in the window
    const runs = terminalCounts.get(segmentTaskId) ?? [];
    if (runs.length !== 1) continue;               // several runs in the index
    const terminal = runs[0]!;
    if (!isBlockProvable(session, terminal)) continue;
    const boundTail = tails[0]!;
    targets.set(boundTail.messageId!, {
      sessionId: session.id,
      taskId: segmentTaskId,
      taskSeq: typeof terminal.taskSeq === 'number' ? terminal.taskSeq : null,
      messageId: boundTail.messageId!,
      ...(typeof terminal.resultCursor === 'number'
        ? { resultCursor: terminal.resultCursor } : {}),
      ...(typeof terminal.terminalKey === 'string'
        ? { terminalKey: terminal.terminalKey } : {}),
    });
  }
  return targets;
}
