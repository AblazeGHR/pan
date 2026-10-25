import { useJobStore, selectRunningJobCount } from '@/stores/jobStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useLockerUnread } from '@/stores/lockerUnreadStore';
import '@/components/session/sessionQuickActions.css';

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
