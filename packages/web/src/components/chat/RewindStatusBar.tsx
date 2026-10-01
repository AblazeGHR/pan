import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import { REWIND_STAGE_LABELS } from './RewindConfirmModal';
import { useSessionStore, type ActiveRewind } from '@/stores/sessionStore';
import type { Session } from '@/types';

function sessionLabel(item: ActiveRewind, sessions: Session[]): string {
  return sessions.find((session) => session.id === item.sessionId)?.name || item.sessionId;
}

/** Persistent entry point for rewind jobs that outlive the confirmation modal.
 * It intentionally renders above the composer, not inside the message list, so
 * it stays visible while a minimized job continues in the background. */
export function RewindStatusBar() {
  const activeRewinds = useSessionStore((state) => state.activeRewinds);
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const sessions = useSessionStore((state) => state.sessions);
  const openRewindProgress = useSessionStore((state) => state.openRewindProgress);
  const completeRewind = useSessionStore((state) => state.completeRewind);
  const dismissRewind = useSessionStore((state) => state.dismissRewind);

  if (activeRewinds.length === 0) return null;

  const viewEntry = async (item: ActiveRewind) => {
    if (item.status === 'completed') {
      await completeRewind(item.jobId);
      return;
    }
    await openRewindProgress(item.jobId, item.sessionId);
  };

  return (
    <div
      className="border-t border-border-default bg-bg-secondary/80 px-3 py-2"
      data-testid="rewind-status-bar"
      aria-label="关闭撤回错误"
    >
      <div className="mx-auto flex max-w-4xl flex-col gap-1.5">
        {activeRewinds.map((item) => {
          const isCurrent = item.sessionId === currentSessionId;
          const stageLabel = REWIND_STAGE_LABELS[item.stage] || item.stage || '准备中';
          const label = item.status === 'failed'
            ? (item.error || '撤回失败')
            : item.status === 'completed'
              ? '撤回完成'
              : `撤回进行中：${stageLabel}`;
          const prefix = isCurrent ? '' : `会话「${sessionLabel(item, sessions)}」：`;
          return (
            <div
              key={`${item.sessionId}:${item.jobId || 'pending'}`}
              className={`flex min-h-8 items-center gap-2 rounded border px-2.5 py-1.5 text-xs ${
                item.status === 'failed'
                  ? 'border-danger/40 bg-danger/5 text-danger'
                  : item.status === 'completed'
                    ? 'border-success/40 bg-success/5 text-success'
                    : 'border-border-default bg-bg-primary text-text-secondary'
              }`}
              data-testid={`rewind-status-${item.jobId || item.sessionId}`}
            >
              {item.status === 'running' && <Loader2 size={14} className="shrink-0 animate-spin" aria-hidden="true" />}
              {item.status === 'completed' && <CheckCircle2 size={14} className="shrink-0" aria-hidden="true" />}
              {item.status === 'failed' && <AlertCircle size={14} className="shrink-0" aria-hidden="true" />}
              <span className="min-w-0 flex-1 truncate">{prefix}{label}</span>
              {item.status !== 'failed' && (
                <button
                  type="button"
                  className="shrink-0 rounded px-2 py-1 font-medium text-accent hover:bg-bg-hover"
                  onClick={() => void viewEntry(item)}
                >
                查看
                </button>
              )}
              {item.status === 'failed' && (
                <button
                  type="button"
                  className="shrink-0 rounded p-1 text-danger hover:bg-danger/10"
                  aria-label="关闭撤回错误"
                  title="关闭"
                  onClick={() => dismissRewind(item.jobId, item.sessionId)}
                >
                  <X size={14} />
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
