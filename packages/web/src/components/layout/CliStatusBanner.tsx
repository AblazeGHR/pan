import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { fetchCliStatus } from '@/services/api';
import type { ApiCliStatusResponse, CliDiagnostic } from '@/types';

/**
 * Explain missing optional CLIs before the user tries to start a Worker.
 * The backend remains the source of truth; a failed request is deliberately
 * silent so a temporary API/network issue does not create a second warning.
 */
export function CliStatusBanner() {
  const [status, setStatus] = useState<ApiCliStatusResponse | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let active = true;
    fetchCliStatus()
      .then((next) => {
        if (active) setStatus(next);
      })
      .catch(() => {
        // The normal connection/error UI handles an unavailable backend.
      });
    return () => {
      active = false;
    };
  }, []);

  if (!status || dismissed || status.adapters.every((adapter) => adapter.available)) {
    return null;
  }

  const unavailable = status.adapters.filter((adapter) => !adapter.available);
  const missingNames = unavailable.map((adapter) => adapter.label).join('、');
  const title = status.hasAvailable
    ? '部分 Agent CLI 不可用'
    : '未检测到可用 Agent CLI';

  return (
    <div
      role="alert"
      className="shrink-0 flex items-start gap-3 border-b border-warning/50 bg-warning/10 px-4 py-3 text-sm"
    >
      <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" />
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-text-primary">{title}</div>
        <div className="mt-1 text-text-secondary">
          {missingNames}。请在启动 Pan 的同一终端中运行
          <code className="mx-1 rounded bg-bg-tertiary px-1.5 py-0.5 text-xs">
            cbc --version
          </code>
          等命令确认全局安装；安装或修改 PATH 后请重启 Pan。
        </div>
        <div className="mt-1 text-xs text-text-tertiary">
          {unavailable.map((adapter: CliDiagnostic) => (
            <span key={adapter.name} className="mr-3 inline-block">
              {adapter.name}: {adapter.missing.length ? `缺少 ${adapter.missing.join(', ')}` : '无法解析启动入口'}
            </span>
          ))}
        </div>
      </div>
      <button
        type="button"
        aria-label="关闭 CLI 提示"
        title="关闭"
        onClick={() => setDismissed(true)}
        className="shrink-0 rounded p-1 text-text-tertiary hover:bg-bg-hover hover:text-text-primary"
      >
        <X size={16} />
      </button>
    </div>
  );
}
