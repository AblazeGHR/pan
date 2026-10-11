import { useEffect } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import type { Session } from '@/types';

/**
 * The upgrade target awaiting its idle boundary. Module-level (not hook state)
 * so an upgrade scheduled from the Session Settings "More" entry is still
 * completed by the TopBar's poller after the popover closes — the contract
 * (upgrade → pending idle → auto-enter Terminal TUI) must survive whichever
 * surface initiated it.
 */
let pendingAutoEnter: string | null = null;
/**
 * Intent generation for in-flight upgrades. Bumped by every cancellation so a
 * configure response that resolves after the user opened a history view (or
 * otherwise cancelled) cannot revive the auto-enter it was waiting for.
 */
let autoEnterIntent = 0;

export type TerminalTuiStatus = 'upgrade' | 'upgrading' | 'error' | 'enabled' | 'available';

export interface TerminalTuiEntry {
  status: TerminalTuiStatus;
  /** "Upgrades" while the session is not yet native-capable, else "Terminal TUI". */
  label: string;
  /** Accessible/visual pressed state: the worker is currently owned by Terminal TUI. */
  active: boolean;
  /** A control switch or idle-boundary upgrade is in flight; the entry must not accept clicks. */
  busy: boolean;
  /** An upgrade was acknowledged and is waiting for the current task to finish. */
  pendingIdle: boolean;
  enterNative: () => Promise<void>;
  cancelAutoEnter: () => void;
}

export function terminalTuiTitle(status: TerminalTuiStatus): string {
  switch (status) {
    case 'upgrade':
      return 'Upgrades — upgrade Session to Terminal TUI';
    case 'upgrading':
      return 'Terminal TUI — upgrade or control switch pending';
    case 'error':
      return 'Terminal TUI — control error; retry';
    case 'enabled':
      return 'Terminal TUI — enabled';
    default:
      return 'Terminal TUI — available';
  }
}

interface TerminalTuiEntryOptions {
  /**
   * Only one consumer per page needs to drive the 1s refresh/auto-enter loop
   * (the always-mounted TopBar). Secondary consumers (Session Settings) pass
   * false and still get correct state: the module-level pending target above
   * is completed by whichever consumer polls.
   */
  poll?: boolean;
}

/**
 * Clears the module-level pending auto-enter target. The TopBar does this
 * whenever the user opens a history view; tests use it for isolation.
 */
export function resetTerminalTuiEntryForTests(): void {
  pendingAutoEnter = null;
  autoEnterIntent = 0;
}

/**
 * Shared Terminal TUI / Upgrades entry state for the TopBar button and the
 * mobile Session Settings "More" entry. Wraps the native control store and
 * preserves the existing contract: confirm-guarded upgrade, pending-idle
 * auto-enter on the next idle boundary, direct switch when already upgraded,
 * and manual retry after an unconfirmed ownership error.
 */
export function useTerminalTuiEntry(
  session: Pick<Session, 'id' | 'adapter' | 'nativeControl'> | null | undefined,
  { poll = false }: TerminalTuiEntryOptions = {},
): TerminalTuiEntry {
  const id = session?.id;
  const isCodex = session?.adapter === 'codex';
  const nativeState = useNativeControlStore((s) => (id ? s.sessions[id] : undefined));
  const nativeSwitching = useNativeControlStore((s) => (id ? s.switching[id] : false));
  const terminalView = useNativeControlStore((s) => (id ? s.terminalViews[id] : undefined));
  const nativeActive =
    terminalView ?? (nativeState?.mode === 'native' || nativeState?.mode === 'switching');
  const showToast = useUIStore((s) => s.showToast);

  useEffect(() => {
    if (!poll || !id || !isCodex) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        await useNativeControlStore.getState().refresh(id);
        const state = useNativeControlStore.getState().sessions[id];
        if (
          !stopped &&
          pendingAutoEnter === id &&
          state?.enabled === true &&
          typeof state.pendingEnabled !== 'boolean'
        ) {
          pendingAutoEnter = null;
          await useNativeControlStore.getState().switchMode(id, 'native');
        }
      } catch {
        /* keep last acknowledged ownership; manual retry stays available */
      }
      if (!stopped) timer = setTimeout(() => void refresh(), 1000);
    };
    void refresh();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [poll, id, isCodex]);

  const enterNative = async () => {
    if (!session || !isCodex) return;
    const intent = autoEnterIntent;
    try {
      if (!session.nativeControl) {
        if (
          !window.confirm(
            'Upgrade this Codex Session to Terminal TUI control? The current task will finish first; at the next idle boundary the Worker will restart using the same thread. History and queue are retained.',
          )
        )
          return;
        const config = await useNativeControlStore.getState().configureSession(session.id, true);
        // The configure request can be gated for a while. If the user opened a
        // history view meanwhile, the intent was cancelled: the late response
        // must neither arm the deferred auto-enter nor issue the switch.
        if (autoEnterIntent !== intent) return;
        if (typeof config.pendingEnabled === 'boolean') {
          pendingAutoEnter = session.id;
          showToast('Session upgrade scheduled: waiting for the current task to finish', 'info');
          return;
        }
      }
      await useNativeControlStore.getState().switchMode(session.id, 'native');
    } catch (error) {
      showToast(
        error instanceof Error
          ? error.message
          : 'Session upgrade or native control switch was not confirmed',
        'error',
      );
    }
  };

  const status: TerminalTuiStatus = nativeState?.error
    ? 'error'
    : nativeSwitching || typeof nativeState?.pendingEnabled === 'boolean'
      ? 'upgrading'
      : (nativeState?.enabled ?? session?.nativeControl) === true
        ? nativeActive
          ? 'enabled'
          : 'available'
        : 'upgrade';

  return {
    status,
    label: status === 'upgrade' ? 'Upgrades' : 'Terminal TUI',
    active: nativeActive,
    busy: nativeSwitching || typeof nativeState?.pendingEnabled === 'boolean',
    pendingIdle: typeof nativeState?.pendingEnabled === 'boolean',
    enterNative,
    cancelAutoEnter: () => {
      autoEnterIntent += 1;
      pendingAutoEnter = null;
    },
  };
}
