import { ackSessionUnreadDone, setSessionMsgBridge } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

export interface SelectedSessionTarget {
  id: string;
  name: string;
  generation: number;
}
export type SelectedSessionAction = 'read' | 'bell-on' | 'bell-off';
export interface TargetActionResult {
  id: string;
  name: string;
  error?: string;
}

export function snapshotSelectedSessions(
  ids: ReadonlySet<string>,
  sessions: Session[],
): SelectedSessionTarget[] {
  return [...ids].map((id) => {
    const session = sessions.find((item) => item.id === id);
    return { id, name: session?.name || id, generation: session?.unreadDoneGeneration ?? 0 };
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
        } else {
          const enabled = action === 'bell-on';
          if (!!current.msgBridgeEnabled !== enabled) await setSessionMsgBridge(target.id, enabled);
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
  return `${results.length - failed.length} succeeded, ${failed.length} failed${failed.length ? ': ' + failed.map((result) => `${result.name}: ${result.error}`).join('; ') : ''}`;
}
