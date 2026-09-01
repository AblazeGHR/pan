import { Copy } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

interface SessionDetailsModalProps {
  session: Session | null;
  onClose: () => void;
}

function displayValue(value: string | null | undefined, empty = '暂无 / 未建立'): string {
  return value || empty;
}

export function SessionDetailsModal({ session, onClose }: SessionDetailsModalProps) {
  const showToast = useUIStore((s) => s.showToast);

  if (!session) return null;

  const credit = session.totalUsage?.credit;
  const copyValue = (label: string, value: string | undefined) => {
    if (!value) {
      showToast(`${label} 暂无可复制内容`, 'error');
      return;
    }
    try {
      const writeText = navigator.clipboard?.writeText;
      if (!writeText) {
        showToast('当前环境不支持复制', 'error');
        return;
      }
      Promise.resolve(writeText.call(navigator.clipboard, value))
        .then(() => showToast(`${label} 已复制`))
        .catch(() => showToast('复制失败', 'error'));
    } catch {
      showToast('复制失败', 'error');
    }
  };

  const rows: Array<{ label: string; value: string; rawValue?: string }> = [
    {
      label: '额度（累计消费 credit）',
      value: credit === undefined ? '暂无 usage 数据' : credit.toFixed(2),
    },
    { label: '工作目录', value: displayValue(session.workdir), rawValue: session.workdir },
    { label: 'Session ID', value: session.id, rawValue: session.id },
    { label: 'CLI ID', value: displayValue(session.cliSessionId), rawValue: session.cliSessionId ?? undefined },
  ];

  return (
    <Modal open title="Session Details" onClose={onClose} size="lg">
      <div className="space-y-3">
        {rows.map((row) => {
          const copyable = row.label !== '额度（累计消费 credit）';
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
      </div>
    </Modal>
  );
}
