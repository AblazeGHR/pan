interface WorkerDotProps {
  status?: string | null;
  className?: string;
}

// Every state in the backend's `_LEGAL_WORKER_STATES` must map to a real
// colour. `done`, `queued` and `restarting` used to fall through to the
// `offline` fallback, which made a finished/queued worker look like it had
// never reported a state at all (grey) instead of "settled". `done` is a
// settled success: the backend only holds it transiently on `w.status` between
// a result broadcast and its reset to `idle`, and the live status may still
// carry it right after a reconnect.
const statusColors: Record<string, string> = {
  idle: 'bg-success',
  done: 'bg-success',
  running: 'bg-accent',
  queued: 'bg-accent',
  restarting: 'bg-warning',
  held: 'bg-warning',
  error: 'bg-danger',
  cancelled: 'bg-warning',
  offline: 'bg-text-tertiary',
};

export function WorkerDot({ status, className = '' }: WorkerDotProps) {
  const color = statusColors[status || 'offline'] || statusColors.offline;
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${color} ${className}`}
      title={status || 'offline'}
    />
  );
}
