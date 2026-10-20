import { setReportsToManager, setSessionMsgBridge } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

/**
 * The selected-Session card's Stop report / notification semantics, shared by
 * the card and the configurable shortcut action.
 *
 * Both actions are server-confirmed: there is no optimistic flip.  A failed
 * request is never retried automatically, and every refresh uses the captured
 * Session id rather than whichever Session happens to be selected later.
 */

export type ShortcutSessionToggle = 'stopReport' | 'notification';

export function reportToManagerEnabled(session: Session): boolean {
  return session.reportsToManager === false && !!session.managedBy;
}

export function reportToManagerDisabled(session: Session): boolean {
  return !session.managedBy || typeof session.reportsToManager !== 'boolean';
}

export function notificationEnabled(session: Session): boolean {
  return session.msgBridgeEnabled === true;
}

export function notificationDisabled(session: Session): boolean {
  return session.readonlySession === true || typeof session.msgBridgeEnabled !== 'boolean';
}

export type SessionToggleOutcome = 'ok' | 'disabled' | 'failed';

export async function runSessionToggleShortcut(
  sessionId: string,
  toggle: ShortcutSessionToggle,
): Promise<SessionToggleOutcome> {
  const store = useSessionStore.getState();
  const current = store.sessions.find((session) => session.id === sessionId);
  if (!current) return 'disabled';
  if (toggle === 'stopReport' && reportToManagerDisabled(current)) return 'disabled';
  if (toggle === 'notification' && notificationDisabled(current)) return 'disabled';

  const managerAtStart = current.managedBy;
  try {
    if (toggle === 'stopReport' && managerAtStart) {
      await setReportsToManager(current.id, managerAtStart, !current.reportsToManager);
    } else if (toggle === 'notification') {
      await setSessionMsgBridge(current.id, !current.msgBridgeEnabled);
    } else {
      return 'disabled';
    }
  } catch (error) {
    // The request may have reached the server before the connection failed.
    // Re-read authoritative state, but never replay the write automatically.
    try {
      await useSessionStore.getState().loadSessions({ throwOnError: true });
    } catch {
      // The next Session snapshot/remount will recover the card state.
    }
    useUIStore.getState().showToast(
      error instanceof Error
        ? error.message
        : `${toggle === 'stopReport' ? 'Stop report' : 'Notification'} update failed`,
      'error',
    );
    return 'failed';
  }

  try {
    await useSessionStore.getState().loadSessions({ throwOnError: true });
  } catch {
    useUIStore.getState().showToast('Saved, but the Session list could not refresh', 'error');
  }
  return 'ok';
}
