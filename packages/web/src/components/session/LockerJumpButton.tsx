import { Archive } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

/**
 * Per-Session Locker jump button. Rendered only while this Session has unread
 * Locker items (the caller removes it entirely at 0, so it never reserves
 * space). It sits in the bottom-right info row, left of the adapter badge, and
 * is a fixed-size pill inside the 72px rail—it never changes the title or
 * model line widths at 390/320.
 */
export function LockerJumpButton({
  sessionId,
  sessionName,
  count,
}: {
  sessionId: string;
  sessionName: string;
  count: number;
}) {
  const navigate = useNavigate();
  const name = sessionName || 'Session';
  const label = `Locker：未读 ${count} 条 · ${name} · 打开该 Session 的 Locker`;
  return (
    <button
      type="button"
      className="session-locker-jump"
      data-testid="session-locker-jump"
      data-locker-unread={count}
      aria-label={label}
      title={label}
      onPointerDown={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        navigate(`/locker?session=${encodeURIComponent(sessionId)}`);
      }}
    >
      <Archive size={9} aria-hidden="true" />
      <span aria-hidden="true">{count > 99 ? '99+' : count}</span>
    </button>
  );
}
