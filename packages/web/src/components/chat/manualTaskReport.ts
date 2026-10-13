import type { Message, Session, TerminalIdentity } from '@/types';

/**
 * Identity binding for one task run's tail text block.  Everything a click
 * needs is captured here at render time — the handler never re-reads live
 * Session state, so a late terminal event or a Session switch can never
 * re-target the click at a different task.
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
 * Canonical proof inputs for one loaded history window: its epoch/revision/
 * total, and an `offsetOf` that resolves a row to its absolute storage offset
 * only when the window owns that row.
 */
export interface HistoryBoundaryProof {
  epoch: string | null;
  revision: number;
  total: number;
  offsetOf: (message: Message) => number | undefined;
}

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

/**
 * Resolve the explicit report action for every completed task run whose
 * block↔run relation is provable, keyed by the bound block's messageId.
 *
 * The requirement is one action per task — "the last text block of that task" —
 * not only for the newest task: an earlier task's final answer stays reportable
 * after newer tasks finish or while a newer task is still running.  A run is
 * bound only when its identity is proven on BOTH sides with canonical offsets:
 *
 * - the transcript proves a formal dispatch segment for that taskId whose tail
 *   text block is a settled assistant row, and
 * - the durable terminal for that exact run (taskId + taskSeq) recovers the
 *   exclusive end of the rows it covered from the loaded window
 *   (`total - (revision - terminalRevision)`), and the bound block's absolute
 *   offset is exactly that end minus one — the run's own final row.
 *
 * A block at any other offset is never bound: two runs of one taskId each bind
 * their own row, an evicted index (only the newest terminal left) leaves an
 * older segment unbound instead of reporting the newest run, a terminal whose
 * history was rewritten shows nothing, and runtime-only rows without a canonical
 * offset stay unbound until their canonical rows converge.  The server
 * re-validates the same relation and refuses when it cannot prove it.
 */
export function findManualReportTargets(
  messages: readonly Message[],
  session: ManualReportSessionView | undefined,
  proof: HistoryBoundaryProof | undefined,
): Map<string, ManualReportTarget> {
  const targets = new Map<string, ManualReportTarget>();
  if (!session?.managedBy || !proof) return targets;
  const terminals = (session.terminalResults ?? []).filter(
    (terminal): terminal is TerminalIdentity => !!terminal,
  );
  if (terminals.length === 0) return targets;

  // Recover the exclusive end of the rows each run covered, from the loaded
  // window's own epoch/revision/total.  A run whose boundary cannot be
  // recovered here (other epoch, unknown revision) is unprovable.
  const boundaries = new Map<TerminalIdentity, number>();
  for (const terminal of terminals) {
    const taskId = typeof terminal.taskId === 'string' ? terminal.taskId : null;
    if (!taskId) continue;
    if (terminal.historyEpoch !== proof.epoch) continue;
    if (typeof terminal.historyRevision !== 'number'
        || !Number.isSafeInteger(terminal.historyRevision)
        || terminal.historyRevision <= 0
        || terminal.historyRevision > proof.revision) continue;
    const coveredEnd = proof.total - (proof.revision - terminal.historyRevision);
    if (!Number.isSafeInteger(coveredEnd) || coveredEnd <= 0
        || coveredEnd > proof.total) continue;
    boundaries.set(terminal, coveredEnd);
  }

  // A run's final row is the last covered row; its absolute offset is unique.
  // Two terminals claiming the same offset make the pairing ambiguous, so
  // neither may be bound.
  const claims = new Map<number, number>();
  for (const coveredEnd of boundaries.values()) {
    const tailOffset = coveredEnd - 1;
    claims.set(tailOffset, (claims.get(tailOffset) ?? 0) + 1);
  }
  const provable = new Map<number, TerminalIdentity>();
  for (const [terminal, coveredEnd] of boundaries) {
    if (claims.get(coveredEnd - 1) === 1) provable.set(coveredEnd - 1, terminal);
  }

  let taskId: string | null = null;
  let tail: Message | null = null;

  const finish = () => {
    if (!taskId || !tail || tail.streaming || !tail.messageId) {
      taskId = null;
      tail = null;
      return;
    }
    // The bound block must be the run's own final row, proven by its absolute
    // offset — never by segment order, content or "newest terminal".
    const offset = proof.offsetOf(tail);
    if (offset !== undefined) {
      const terminal = provable.get(offset);
      if (terminal?.taskId === taskId) {
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
