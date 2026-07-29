import { useCurrentSession } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { Button } from '@/components/ui/Button';
import { useMediaQuery } from '@/hooks/useMediaQuery';

export function TopBar() {
  const currentSession = useCurrentSession();
  const currentWorkerId = useWorkerStore((s) => s.currentWorkerId);
  const { showToast, toggleBubbleView, bubbleViewEnabled } = useUIStore();
  const { restart, killCurrent, interrupt, takeover } = useWorkerStore();
  const { isMobile } = useMediaQuery();

  if (!currentSession) {
    return (
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-default bg-bg-primary">
        <span className="text-sm text-text-tertiary">
          Select a session to start
        </span>
      </div>
    );
  }

  const status = currentSession.workerStatus || 'offline';

  const handleCopy = (text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => showToast(`Copied: ${text}`))
      .catch(() => showToast('Copy failed', 'error'));
  };

  return (
    <div className="flex items-center justify-between pl-10 pr-3 md:pl-4 md:pr-4 py-2 border-b border-border-default bg-bg-primary gap-2 flex-wrap">
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex items-center gap-2">
          <WorkerDot status={status} />
          <span className="text-sm font-medium text-text-primary truncate max-w-[120px] md:max-w-[200px]">
            {currentSession.name || currentSession.id?.slice(0, 12)}
          </span>
          <button
            onClick={toggleBubbleView}
            className="text-sm text-text-tertiary hover:text-text-primary p-0.5 rounded transition-colors"
            title={bubbleViewEnabled ? 'Switch to TUI view' : 'Switch to Bubble view'}
          >
            {bubbleViewEnabled ? '💬' : '🖥️'}
          </button>
        </div>
        <span className="hidden md:inline text-xs text-text-tertiary">
          {currentSession.model || '?'}
        </span>
        <div className="hidden md:flex items-center gap-1 text-xs text-text-secondary">
          <span
            className="cursor-pointer hover:text-text-primary"
            onClick={() => handleCopy(currentSession.id || '')}
            title="Copy session ID"
          >
            {currentSession.id?.slice(0, 12)} ⧉
          </span>
          {currentSession.cliSessionId && (
            <span
              className="cursor-pointer hover:text-text-primary"
              onClick={() => handleCopy(currentSession.cliSessionId || '')}
              title="Copy CLI session ID"
            >
              {currentSession.cliSessionId.slice(0, 8)} ⧉
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-shrink-0">
        <span className="hidden md:inline text-xs text-text-tertiary mr-1">
          {status}
          {currentWorkerId ? ` (${currentWorkerId})` : ' (no worker)'}
        </span>
        {currentWorkerId && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => restart(currentWorkerId)}
              title="Restart worker"
            >
              ⟳
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => interrupt(currentWorkerId)}
              title="Interrupt"
            >
              ⊘
            </Button>
            {!isMobile && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  takeover(currentWorkerId)
                    .then(() =>
                      showToast('PowerShell opened for takeover'),
                    )
                    .catch((e) => showToast(e.message, 'error'));
                }}
                title="Takeover"
              >
                ⤓
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (!confirm(`Kill worker ${currentWorkerId}?`)) return;
                killCurrent(currentWorkerId).catch((e) =>
                  showToast(e.message, 'error'),
                );
              }}
              title="Kill worker"
            >
              ✕
            </Button>
          </>
        )}
        {!currentWorkerId && (
          <Button
            variant="primary"
            size="sm"
            onClick={() =>
              restart(currentSession.id || '').catch((e) =>
                showToast(e.message, 'error'),
              )
            }
          >
            Start
          </Button>
        )}
      </div>
    </div>
  );
}
