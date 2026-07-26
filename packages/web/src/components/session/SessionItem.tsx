import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isSelected?: boolean;
  multiSelectMode?: boolean;
  onSelect?: () => void;
  onMenu?: (e: React.MouseEvent) => void;
}

export function SessionItem({
  session,
  isActive,
  isSelected = false,
  multiSelectMode = false,
  onSelect,
  onMenu,
}: SessionItemProps) {
  const isPending = session.id.startsWith('__pending_');
  const messages = session.history || [];
  const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
  const preview =
    lastMsg && lastMsg.content
      ? lastMsg.content.length > 40
        ? lastMsg.content.slice(0, 40) + '...'
        : lastMsg.content
      : null;
  const credit = session.totalUsage?.credit ?? null;

  const handleClick = () => {
    if (isPending) return;
    if (multiSelectMode && onSelect) {
      onSelect();
    }
  };

  return (
    <div
      className={`flex items-center gap-2 px-3 py-2 cursor-pointer border-b border-border-default transition-colors ${
        isActive ? 'bg-bg-tertiary' : 'bg-bg-secondary hover:bg-bg-tertiary/60'
      } ${isPending ? 'opacity-50' : ''}`}
      onClick={handleClick}
    >
      {multiSelectMode ? (
        <input
          type="checkbox"
          checked={isSelected}
          onChange={(e) => {
            e.stopPropagation();
            onSelect?.();
          }}
          className="shrink-0"
        />
      ) : null}

      <WorkerDot status={session.workerStatus} />

      <div className="flex-1 min-w-0">
        <div className="text-sm text-text-primary truncate">
          {session.name || 'Untitled'}
        </div>

        {preview && (
          <div className="text-xs text-text-tertiary truncate mt-0.5">
            {preview}
          </div>
        )}

        <div className="flex items-center gap-2 mt-1 text-xs text-text-secondary">
          {session.model && <span className="truncate">{session.model}</span>}
          <span className="shrink-0">{messages.length} msgs</span>
          {credit !== null && (
            <span className="shrink-0 text-text-tertiary">
              {credit.toFixed(2)} credits
            </span>
          )}
        </div>
      </div>

      {!multiSelectMode && !isPending && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMenu?.(e);
          }}
          className="shrink-0 px-1 text-text-tertiary hover:text-text-primary transition-colors"
          title="Session menu"
        >
          ⚙
        </button>
      )}
    </div>
  );
}
