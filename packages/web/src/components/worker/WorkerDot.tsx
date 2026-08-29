interface WorkerDotProps {
  status?: string | null;
  className?: string;
}

const statusColors: Record<string, string> = {
  idle: 'bg-success',
  running: 'bg-accent',
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
