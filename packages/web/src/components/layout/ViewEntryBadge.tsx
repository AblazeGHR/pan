import { useEffect, useSyncExternalStore } from 'react';
import { useJobStore, selectRunningJobCount } from '@/stores/jobStore';
import { useSessionStore } from '@/stores/sessionStore';
import { lockerRequest } from '@/views/lockerModel';
import { useAutomationAuth } from '@/stores/automationStore';
import '@/components/session/sessionQuickActions.css';

let lockerUnread = 0;
let lockerTimer: ReturnType<typeof setTimeout> | null = null;
let lockerLoading = false;
let lockerBackoff = 30_000;
const lockerListeners = new Set<() => void>();
const emit = () => lockerListeners.forEach((listener) => listener());
async function refreshLockerUnread() {
  if (lockerLoading || document.visibilityState === 'hidden') return;
  lockerLoading = true;
  try {
    const result = await lockerRequest<{ unread: number }>('/api/locker-unread-summary');
    lockerUnread = Math.max(0, Math.floor(result.unread ?? 0));
    emit();
    lockerBackoff = 30_000;
  } catch { /* keep last known count and retry with bounded backoff */
    lockerBackoff = Math.min(lockerBackoff * 2, 120_000);
  }
  finally {
    lockerLoading = false;
    if (lockerTimer) clearTimeout(lockerTimer);
    if (lockerListeners.size > 0) lockerTimer = setTimeout(() => void refreshLockerUnread(), lockerBackoff);
  }
}
function subscribeLocker(listener: () => void) {
  lockerListeners.add(listener);
  if (lockerListeners.size === 1) void refreshLockerUnread();
  return () => { lockerListeners.delete(listener); if (lockerListeners.size === 0 && lockerTimer) { clearTimeout(lockerTimer); lockerTimer = null; } };
}
function getLockerSnapshot() { return lockerUnread; }
export function useLockerUnread() {
  useEffect(() => {
    const onVisibility = () => { if (document.visibilityState === 'visible') void refreshLockerUnread(); };
    document.addEventListener('visibilitychange', onVisibility);
    const unsubscribeAuth = useAutomationAuth.subscribe((state, previous) => {
      if (state.token !== previous.token) {
        if (lockerTimer) clearTimeout(lockerTimer);
        lockerTimer = null;
        lockerBackoff = 30_000;
        void refreshLockerUnread();
      }
    });
    return () => { document.removeEventListener('visibilitychange', onVisibility); unsubscribeAuth(); };
  }, []);
  return useSyncExternalStore(subscribeLocker, getLockerSnapshot, getLockerSnapshot);
}

export function ViewEntryBadge({ kind = 'jobs' }: { kind?: 'jobs' | 'chat' | 'locker' }) {
  const jobs = useJobStore(selectRunningJobCount);
  const sessions = useSessionStore((state) => state.sessions);
  const locker = useLockerUnread();
  const value = kind === 'jobs' ? jobs : kind === 'chat'
    ? sessions.reduce((sum, session) => sum + Math.max(0, Math.floor(session.unreadDoneCount ?? 0)), 0)
    : locker;
  if (value <= 0) return null;
  const label = kind === 'jobs' ? `${value} job(s) running` : `${value} unread ${kind === 'chat' ? 'session result' : 'Locker item'}${value === 1 ? '' : 's'}`;
  return <span className={`${kind === 'jobs' ? 'session-queue-badge' : 'session-unread-entry-badge'} absolute bottom-0 right-0`} data-testid={kind === 'jobs' ? 'jobs-running-badge' : `${kind}-unread-badge`} {...(kind === 'jobs' ? { 'aria-hidden': true } : { role: 'img', 'aria-label': label })} title={label}>{value > 99 ? '99+' : value}</span>;
}

export function ViewEntryDirectionDot({ direction }: { direction: 'left' | 'right' }) {
  return <span className={`absolute top-0 h-1.5 w-1.5 rounded-full bg-warning ${direction === 'left' ? 'left-0' : 'right-0'}`} role="img" aria-label={`更${direction === 'left' ? '前' : '后'}的导航组有未读`} title={`更${direction === 'left' ? '前' : '后'}的导航组有未读`} />;
}
