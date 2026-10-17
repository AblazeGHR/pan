import { useJobStore, selectRunningJobCount } from '@/stores/jobStore';
import '@/components/session/sessionQuickActions.css';

/**
 * Running-job count badge for the Jobs entry. Floating overlay pinned to
 * the entry's bottom-right corner: it never occupies layout space nor
 * resizes the entry (0 running renders nothing at all). Reuses the
 * Session queue badge style (accent blue, 99+ cap); has no pointer
 * handling of its own, so clicks pass through to the entry.
 */
export function ViewEntryBadge() {
  const runningJobCount = useJobStore(selectRunningJobCount);
  if (runningJobCount <= 0) return null;
  return (
    <span
      className="session-queue-badge absolute bottom-0 right-0"
      data-testid="jobs-running-badge"
      aria-hidden="true"
      title={`${runningJobCount} job(s) running`}
    >
      {runningJobCount > 99 ? '99+' : runningJobCount}
    </span>
  );
}
