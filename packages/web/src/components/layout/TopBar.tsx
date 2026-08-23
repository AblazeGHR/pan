import { useEffect } from 'react';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useUIStore } from '@/stores/uiStore';
import { WorkerDot } from '@/components/worker/WorkerDot';
import { Button } from '@/components/ui/Button';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import {
  MessageSquare,
  Monitor,
  Copy,
  RotateCw,
  Ban,
  Download,
  X,
} from 'lucide-react';

export function TopBar() {
  const currentSession = useCurrentSession();
  const currentWorker = useWorkerStore((s) => s.currentWorker);
  const { showToast, toggleBubbleView, bubbleViewEnabled } =
    useUIStore();
  const { restart, startWorker, killCurrent, interrupt, takeover } =
    useWorkerStore();
  const { isMobile } = useMediaQuery();

  const config = useAdapterStore((s) => s.getConfig());
  const loadConfig = useAdapterStore((s) => s.loadConfig);
  const applySettings = useAdapterStore((s) => s.applySettings);
  const { loadSessions } = useSessionStore();

  // Load adapter config for the effort dropdown
  useEffect(() => {
    if (currentSession) {
      loadConfig(currentSession.adapter || 'cbc');
    }
  }, [currentSession?.id, loadConfig]);

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

  // Effective worker for the CURRENT session. Prefer the server-reported
  // session.workerId (authoritative after page load); fall back to the live
  // WS-tracked worker (workerStore keeps currentWorkerId synced per-session
  // via refresh/syncToSession) only if it actually belongs to this session.
  const effectiveWorkerId =
    currentSession.workerId ||
    (currentWorker && currentWorker.sessionId === currentSession.id
      ? currentWorker.id
      : null) ||
    null;

  const handleEffortChange = async (value: string) => {
    if (!currentSession) return;
    try {
      await applySettings(currentSession.id, effectiveWorkerId || undefined, {
        effort: value,
      });
      await loadSessions();
      showToast(`Effort: ${value}`);
    } catch (e) {
      showToast((e as Error).message || 'Failed to set effort', 'error');
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => showToast(`Copied: ${text}`))
      .catch(() => showToast('Copy failed', 'error'));
  };

  return (
    <div className="flex items-center justify-between pl-10 pr-3 md:pl-4 md:pr-4 py-2 border-b border-border-default bg-bg-primary gap-2 flex-wrap shrink-0">
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
            {bubbleViewEnabled ? <MessageSquare size={16} /> : <Monitor size={16} />}
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
            {currentSession.id?.slice(0, 12)} <Copy size={11} className="inline" />
          </span>
          {currentSession.cliSessionId && (
            <span
              className="cursor-pointer hover:text-text-primary"
              onClick={() => handleCopy(currentSession.cliSessionId || '')}
              title="Copy CLI session ID"
            >
              {currentSession.cliSessionId.slice(0, 8)}{' '}
              <Copy size={11} className="inline" />
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-shrink-0">
        {config?.supportedSettings?.includes('effort') &&
          config.effortValues.length > 0 && (
            <select
              value={
                currentSession.effort ||
                config.effortValues[1] ||
                config.effortValues[0] ||
                ''
              }
              onChange={(e) => handleEffortChange(e.target.value)}
              className="text-xs rounded-md border border-border-default bg-bg-tertiary text-text-primary px-1 py-1 focus:outline-none focus:border-accent"
              title="Effort"
            >
              {config.effortValues.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          )}
        <span className="hidden md:inline text-xs text-text-tertiary mr-1">
          {status}
          {effectiveWorkerId ? ` (${effectiveWorkerId})` : ' (no worker)'}
        </span>
        {effectiveWorkerId && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => restart(effectiveWorkerId)}
              title="Restart worker"
            >
              <RotateCw size={14} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => interrupt(effectiveWorkerId)}
              title="Interrupt"
            >
              <Ban size={14} />
            </Button>
            {!isMobile && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  takeover(effectiveWorkerId)
                    .then(() =>
                      showToast('PowerShell opened for takeover'),
                    )
                    .catch((e) => showToast(e.message, 'error'));
                }}
                title="Takeover"
              >
                <Download size={14} />
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (!confirm(`Kill worker ${effectiveWorkerId}?`)) return;
                killCurrent(effectiveWorkerId).catch((e) =>
                  showToast(e.message, 'error'),
                );
              }}
              title="Kill worker"
            >
              <X size={14} />
            </Button>
          </>
        )}
        {!effectiveWorkerId && (
          <Button
            variant="primary"
            size="sm"
            onClick={() =>
              startWorker(currentSession.id || '').catch((e) =>
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
