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
  'id' | 'managedBy' | 'terminalResults'
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

/** Newest durable terminal identity for one task, or undefined. */
function terminalForTask(
  terminals: readonly TerminalIdentity[],
  taskId: string,
): TerminalIdentity | undefined {
  let found: TerminalIdentity | undefined;
  for (const terminal of terminals) {
    if (terminal?.taskId === taskId) found = terminal;
  }
  return found;
}

/**
 * Resolve the explicit report action for EVERY completed task in the loaded
 * transcript, keyed by the bound block's messageId.
 *
 * The requirement is one action per task — "the last text block of that task" —
 * not only for the newest task: an earlier task's final answer stays reportable
 * after newer tasks finish or while a newer task is still running.  A task is
 * bound only when both sides are provable:
 * - the transcript proves its dispatch boundary (a formal assign row with a
 *   taskId) and its tail text block (the last settled assistant row of that
 *   segment with a stable messageId), and
 * - the Session's durable terminal index proves that task reached a terminal
 *   state, which is what the server resolves the report content from.
 *
 * A task whose terminal is absent from the (bounded) index, whose tail block is
 * still streaming, or whose tail block has no stable identity is left without an
 * action instead of guessing which task a block or a result belongs to.
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

  let taskId: string | null = null;
  let tail: Message | null = null;

  const finish = () => {
    if (taskId && tail) {
      // The tail must be a settled block with a stable identity; a streaming or
      // identity-less tail proves nothing and stays without an action.
      if (!tail.streaming && tail.messageId) {
        const terminal = terminalForTask(terminals, taskId);
        if (terminal) {
          targets.set(tail.messageId, {
            sessionId: session.id,
            taskId,
            taskSeq: typeof terminal.taskSeq === 'number' ? terminal.taskSeq : null,
            messageId: tail.messageId,
            ...(typeof terminal.resultCursor === 'number'
              ? { resultCursor: terminal.resultCursor } : {}),
            ...(typeof terminal.terminalKey === 'string'
              ? { terminalKey: terminal.terminalKey } : {}),
          });
        }
      }
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
  return targets;
}
