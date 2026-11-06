import type { Session } from '@/types';

export interface BulkTarget {
  id: string;
  name: string;
}

/**
 * Selected Sessions in the stable list order (the Session store's own order),
 * skipping placeholders and ids that no longer exist. The first entry is the
 * representative whose current settings the bulk windows display.
 */
export function stableSelectedSessions(
  ids: ReadonlySet<string>,
  sessions: readonly Session[],
): BulkTarget[] {
  return sessions
    .filter((session) => ids.has(session.id) && !session.id.startsWith('__pending_'))
    .map((session) => ({ id: session.id, name: session.name || session.id }));
}

export type BulkRegisterUndo = (undo: () => Promise<void>) => void;

export interface BulkApplyResult {
  /** Targets fully applied before the failing one. */
  applied: number;
  /** Undo steps that completed during rollback. */
  rolledBack: number;
  /** Rollback steps that failed, with the owning Session name. */
  rollbackFailures: string[];
  failedName?: string;
  error?: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Apply one target at a time, registering the inverse of every successful
 * write. The first failure stops the run and undoes all registered steps in
 * reverse order — including partial steps of the failing target, which is why
 * callers must register each step immediately after it succeeds.
 *
 * A failed rollback is reported instead of being counted as success: callers
 * keep the modal open and surface the honest per-target outcome.
 */
export async function runBulkWithRollback(
  targets: readonly BulkTarget[],
  applyOne: (target: BulkTarget, registerUndo: BulkRegisterUndo) => Promise<void>,
): Promise<BulkApplyResult> {
  const undoStack: { name: string; undo: () => Promise<void> }[] = [];
  let applied = 0;
  for (const target of targets) {
    try {
      await applyOne(target, (undo) => undoStack.push({ name: target.name, undo }));
      applied += 1;
    } catch (error) {
      const rollbackFailures: string[] = [];
      let rolledBack = 0;
      for (const entry of [...undoStack].reverse()) {
        try {
          await entry.undo();
          rolledBack += 1;
        } catch (rollbackError) {
          rollbackFailures.push(`${entry.name}: ${message(rollbackError)}`);
        }
      }
      return {
        applied,
        rolledBack,
        rollbackFailures,
        failedName: target.name,
        error: message(error),
      };
    }
  }
  return { applied, rolledBack: 0, rollbackFailures: [] };
}
