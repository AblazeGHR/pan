import type { Session } from '@/types';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { MessageSquare, Folder, Monitor, MoreVertical } from 'lucide-react';

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isSelected?: boolean;
  multiSelectMode?: boolean;
  onSelect?: () => void;
  onMenu?: (e: React.MouseEvent) => void;
}

function shortWorkdir(workdir?: string): string {
  if (!workdir) return '';
  const parts = workdir.replace(/\\/g, '/').split('/');
  parts.pop();
  const lastTwo = parts.slice(-2);
  if (lastTwo.length < parts.length) {
    return `…/${lastTwo.join('/')}`;
  }
  return `/${lastTwo.join('/')}`;
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
      ? lastMsg.content.length > 50
        ? lastMsg.content.slice(0, 50) + '...'
        : lastMsg.content
      : null;
  const credit = session.totalUsage?.credit ?? null;

  const handleClick = () => {
    if (isPending) return;
    onSelect?.();
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
          className="shrink-0 accent-accent"
        />
      ) : null}

      <WorkerDot status={session.workerStatus} />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-text-primary truncate font-medium">
            {session.name || 'Untitled'}
          </span>
          {session.adapter && (
            <span className="text-[10px] text-text-tertiary bg-bg-tertiary rounded px-1 py-px shrink-0">
              {session.adapter}
            </span>
          )}
        </div>

        {preview && (
          <div className="text-xs text-text-tertiary truncate mt-0.5 leading-tight">
            {preview}
          </div>
        )}

        <div className="flex items-center gap-2 mt-1 text-xs text-text-secondary">
          <span className="flex items-center gap-0.5">
            <MessageSquare size={10} />
            {messages.length}
          </span>
          {session.model && (
            <span className="flex items-center gap-0.5 truncate">
              <Monitor size={10} />
              {session.model}
            </span>
          )}
          {session.workdir && (
            <span className="flex items-center gap-0.5 truncate text-text-tertiary" title={session.workdir}>
              <Folder size={10} />
              {shortWorkdir(session.workdir)}
            </span>
          )}
          {credit !== null && (
            <span className="shrink-0 text-text-tertiary">
              {credit.toFixed(2)} cr
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
          className="shrink-0 p-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
          title="Session menu"
        >
          <MoreVertical size={14} />
        </button>
      )}
    </div>
  );
}
