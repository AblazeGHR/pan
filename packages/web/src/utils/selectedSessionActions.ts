import { ackSessionUnreadDone, setSessionMsgBridge } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import type { Session } from '@/types';

export interface SelectedSessionTarget {
  id: string;
  name: string;
  generation: number;
  /**
   * Click-time view of a live worker for this Session, using the same
   * effective-worker rule as the single-Session Worker menu (durable
   * workerId+status pair, or a live worker-store entry). Kill uses it to
   * report an honest no-op instead of issuing a pointless request.
   */
  hasWorker: boolean;
}
export type SelectedSessionAction =
  | 'read'
  | 'bell-on'
  | 'bell-off'
  | 'restart'
  | 'delayed-restart'
  | 'kill';
export interface TargetActionResult {
  id: string;
  name: string;
  error?: string;
  /** Honest per-target outcome, used by the lifecycle actions. */
  detail?: string;
}

export function snapshotSelectedSessions(
  ids: ReadonlySet<string>,
  sessions: Session[],
): SelectedSessionTarget[] {
  const liveWorkers = useWorkerStore.getState().workers;
  return [...ids].map((id) => {
    const session = sessions.find((item) => item.id === id);
    const live = liveWorkers[id];
    const hasWorker = Boolean(
      (session?.workerId && session?.workerStatus) ||
        (live && live.sessionId === id && live.id),
    );
    return {
      id,
      name: session?.name || id,
      generation: session?.unreadDoneGeneration ?? 0,
      hasWorker,
    };
  });
}

/** Each target settles independently; selection changes cannot retarget a request. */
export async function runSelectedSessionAction(
  targets: readonly SelectedSessionTarget[],
  action: SelectedSessionAction,
): Promise<TargetActionResult[]> {
  return Promise.all(
    targets.map(async (target) => {
      try {
        const store = useSessionStore.getState();
        const current = store.sessions.find((session) => session.id === target.id);
        if (!current) throw new Error('Session no longer exists');
        if (action === 'read') {
          if (target.generation <= 0 && (current.unreadDoneCount ?? 0) > 0)
            throw new Error('Unread generation unavailable; refresh and retry');
          if (target.generation > 0) {
            const result = await ackSessionUnreadDone(target.id, target.generation);
            store.updateSession(target.id, {
              unreadDoneCount: result.unreadDoneCount,
              unreadDoneGeneration: result.unreadDoneGeneration,
              unreadDoneReadGeneration: result.unreadDoneReadGeneration,
            });
          }
        } else if (action === 'bell-on' || action === 'bell-off') {
          const enabled = action === 'bell-on';
          if (!!current.msgBridgeEnabled !== enabled) await setSessionMsgBridge(target.id, enabled);
        } else if (action === 'restart') {
          // Reuses the single-Session Restart semantics: the session-level
          // endpoint atomically restarts a live worker or starts one, and the
          // worker-store action keeps the worker map/Session projection in
          // sync (no separate lifecycle implementation here).
          await useWorkerStore.getState().restart(target.id);
          return { id: target.id, name: target.name, detail: 'Restarted or started' };
        } else if (action === 'delayed-restart') {
          // Reuses the existing idle-boundary delayed-restart/cancel mechanism
          // (worker-store intent + server intent); this is not a client-side
          // delay timer. The returned intent says whether the backend will
          // restart the live worker or start a missing one at the boundary.
          const intent = await useWorkerStore.getState().delayedRestart(target.id);
          return {
            id: target.id,
            name: target.name,
            detail:
              intent?.action === 'start'
                ? 'Delayed start scheduled'
                : 'Delayed restart scheduled',
          };
        } else {
          // Kill mirrors the single-Session rule: without a live worker at
          // click time there is nothing to kill, so report the honest no-op
          // instead of issuing a request the backend would only no-op.
          if (!target.hasWorker) {
            return { id: target.id, name: target.name, detail: 'No worker (already offline)' };
          }
          await useWorkerStore.getState().killCurrent(target.id);
          return { id: target.id, name: target.name, detail: 'Kill sent' };
        }
        return { id: target.id, name: target.name };
      } catch (error) {
        return {
          id: target.id,
          name: target.name,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    }),
  );
}

export function describeActionResults(results: TargetActionResult[]): string {
  const failed = results.filter((result) => result.error);
  const entries = [
    ...failed.map((result) => `${result.name}: ${result.error}`),
    ...results
      .filter((result) => !result.error && result.detail)
      .map((result) => `${result.name}: ${result.detail}`),
  ];
  return `${results.length - failed.length} succeeded, ${failed.length} failed${
    entries.length ? ': ' + entries.join('; ') : ''
  }`;
}
