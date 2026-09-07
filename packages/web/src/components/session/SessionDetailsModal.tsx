import { Copy } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import type { Session } from '@/types';
import { copyText } from '@/utils/clipboard';
import { normalizeCodexRateLimits, type CodexQuotaWindow } from '@/utils/codexRateLimits';

interface SessionDetailsModalProps {
  session: Session | null;
  onClose: () => void;
}

function displayValue(value: string | null | undefined, empty = '暂无 / 未建立'): string {
  return value || empty;
}

function formatAmount(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString('en-US') : value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatResetTime(value: number | undefined): string | undefined {
  if (value === undefined) return undefined;
  const milliseconds = value < 1_000_000_000_000 ? value * 1000 : value;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? undefined : date.toLocaleString();
}

function quotaDetails(window: CodexQuotaWindow): string[] {
  const details: string[] = [];
  if (window.usedPercent !== undefined) details.push(`已使用 ${window.usedPercent}%`);
  if (window.remainingPercent !== undefined) details.push(`剩余 ${window.remainingPercent}%`);
  if (window.usedAmount) details.push(`已使用 ${formatAmount(window.usedAmount.value)} ${window.usedAmount.unit}`);
  if (window.remainingAmount) details.push(`剩余 ${formatAmount(window.remainingAmount.value)} ${window.remainingAmount.unit}`);
  const reset = formatResetTime(window.resetsAt);
  if (reset) details.push(`重置于 ${reset}`);
  return details.length > 0 ? details : ['暂无可用额度字段'];
}

export function SessionDetailsModal({ session, onClose }: SessionDetailsModalProps) {
  const showToast = useUIStore((s) => s.showToast);
  const worker = useWorkerStore((s) => (session ? s.workers[session.id] : undefined));

  if (!session) return null;

  const credit = session.totalUsage?.credit;
  const copyValue = (label: string, value: string | undefined) => {
    if (!value) {
      showToast(`${label} 暂无可复制内容`, 'error');
      return;
    }
    try {
      copyText(value)
        .then(() => showToast(`${label} 已复制`))
        .catch(() => showToast('复制失败', 'error'));
    } catch {
      showToast('复制失败', 'error');
    }
  };

  const rows: Array<{ label: string; value: string; rawValue?: string }> = [
    { label: 'Session name', value: displayValue(session.name), rawValue: session.name || undefined },
    { label: '工作目录', value: displayValue(session.workdir), rawValue: session.workdir },
    { label: 'Session ID', value: session.id, rawValue: session.id },
    { label: 'CLI ID', value: displayValue(session.cliSessionId), rawValue: session.cliSessionId ?? undefined },
  ];

  const isCodex = session.adapter === 'codex';
  const workerForSession = worker?.sessionId === undefined || worker.sessionId === session.id ? worker : undefined;
  const workerOnline = Boolean(workerForSession && workerForSession.status !== 'offline');
  const quotaWindows = isCodex && workerOnline
    ? normalizeCodexRateLimits(workerForSession?.nativeRateLimits)
    : {};

  const renderQuotaWindow = (label: string, window: CodexQuotaWindow | undefined) => (
    <div key={label} className="min-w-0">
      <div className="text-xs text-text-tertiary mb-1">{label}</div>
      <div className="text-sm text-text-primary break-words">
        {window ? quotaDetails(window).join(' · ') : '暂无数据'}
      </div>
    </div>
  );

  return (
    <Modal open title="Session Details" onClose={onClose} size="lg">
      <div className="space-y-3">
        {rows.map((row) => {
          const copyable = true;
          return (
            <div key={row.label} className="min-w-0">
              <div className="text-xs text-text-tertiary mb-1">{row.label}</div>
              <div className="flex items-start gap-2 min-w-0">
                <div className="flex-1 min-w-0 text-sm text-text-primary break-words whitespace-pre-wrap" title={row.rawValue}>
                  {row.value}
                </div>
                {copyable && (
                  <button
                    type="button"
                    aria-label={`复制${row.label}`}
                    title={`复制${row.label}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      copyValue(row.label, row.rawValue);
                    }}
                    className="shrink-0 p-1 text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary rounded transition-colors"
                  >
                    <Copy size={14} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {isCodex ? (
          <div className="space-y-3" aria-label="Codex quota">
            <div className="text-xs text-text-tertiary">Codex 额度（当前 Worker 快照）</div>
            {!workerForSession || !workerOnline ? (
              <div className="text-sm text-text-tertiary">当前 Worker 不可用，暂无额度数据</div>
            ) : (
              <>
                {renderQuotaWindow('周额度', quotaWindows.weekly)}
                {renderQuotaWindow('月额度', quotaWindows.monthly)}
              </>
            )}
          </div>
        ) : (
          <div className="min-w-0">
            <div className="text-xs text-text-tertiary mb-1">额度（累计消费 credit）</div>
            <div className="text-sm text-text-primary break-words">
              {credit === undefined ? '暂无 usage 数据' : credit.toFixed(2)}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
