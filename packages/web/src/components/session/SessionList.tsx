import { useSessionStore } from '@/stores/sessionStore';
import { SessionItem } from './SessionItem';

interface SessionListProps {
  onSessionClick?: (id: string) => void;
  onSessionMenu?: (e: React.MouseEvent, id: string) => void;
}

export function SessionList({ onSessionClick, onSessionMenu }: SessionListProps) {
  const sessions = useSessionStore((s) => s.sessions);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const multiSelectMode = useSessionStore((s) => s.multiSelectMode);
  const selectedIds = useSessionStore((s) => s.selectedIds);
  const selectSession = useSessionStore((s) => s.selectSession);
  const toggleSelection = useSessionStore((s) => s.toggleSelection);

  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-text-tertiary">
        No sessions
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {sessions.map((session) => (
        <SessionItem
          key={session.id}
          session={session}
          isActive={session.id === currentSessionId}
          isSelected={selectedIds.has(session.id)}
          multiSelectMode={multiSelectMode}
          onSelect={() => {
            if (multiSelectMode) {
              toggleSelection(session.id);
            } else if (!session.id.startsWith('__pending_')) {
              selectSession(session.id);
              onSessionClick?.(session.id);
            }
          }}
          onMenu={(e) => onSessionMenu?.(e, session.id)}
        />
      ))}
    </div>
  );
}
