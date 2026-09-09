import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, Copy } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { fetchSessionUsage } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import type { Session, SessionUsageView } from '@/types';
import { copyText } from '@/utils/clipboard';
import { normalizeCodexQuotaProjection, normalizeCodexRateLimits, type CodexQuotaWindow } from '@/utils/codexRateLimits';

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

function formatMetric(value: number | null | undefined): string {
  return value == null ? '暂无数据' : formatAmount(value);
}

function fallbackUsage(session: Session): SessionUsageView {
  const total = session.totalUsage ?? {};
  const input = total.prompt_tokens ?? total.input_tokens ?? null;
  const output = total.completion_tokens ?? total.output_tokens ?? null;
  const cacheRead = total.prompt_cache_hit_tokens ?? total.cache_read_tokens ?? total.cached_input_tokens ?? null;
  const cacheWrite = total.prompt_cache_miss_tokens ?? total.cache_write_tokens ?? total.cache_write_input_tokens ?? null;
  return {
    sessionId: session.id,
    adapter: session.adapter || '',
    input,
    output,
    cache: {
      read: cacheRead,
      write: cacheWrite,
      total: cacheRead != null && cacheWrite != null ? cacheRead + cacheWrite : null,
    },
    total: {
      tokens: input != null && output != null ? input + output : null,
      credit: total.credit ?? total.cost ?? null,
    },
  };
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

function hasQuotaDetails(window: CodexQuotaWindow | undefined): window is CodexQuotaWindow {
  return Boolean(window && (
    window.usedPercent !== undefined ||
    window.remainingPercent !== undefined ||
    window.usedAmount !== undefined ||
    window.remainingAmount !== undefined ||
    window.resetsAt !== undefined
  ));
}

export function SessionDetailsModal({ session, onClose }: SessionDetailsModalProps) {
  const showToast = useUIStore((s) => s.showToast);
  const sessionId = session?.id;
  const [usageExpanded, setUsageExpanded] = useState(false);
  const [usage, setUsage] = useState<SessionUsageView | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [systemPromptExpanded, setSystemPromptExpanded] = useState(false);

  useEffect(() => {
    setUsageExpanded(false);
    setUsage(null);
    setUsageLoading(false);
    setUsageError(null);
    setSystemPromptExpanded(false);
  }, [sessionId]);

  useEffect(() => {
    if (!usageExpanded || !sessionId || usage) return;
    let active = true;
    setUsageLoading(true);
    fetchSessionUsage(sessionId)
      .then((result) => { if (active) setUsage(result); })
      .catch((error) => {
        if (active) setUsageError(error instanceof Error ? error.message : 'Usage 加载失败');
      })
      .finally(() => { if (active) setUsageLoading(false); });
    return () => { active = false; };
  }, [sessionId, usageExpanded, usage]);

  if (!session) return null;

  const usageView = usage ?? fallbackUsage(session);
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
  const worker = useWorkerStore((s) => (session ? s.workers[session.id] : undefined));
  const workerForSession = worker?.sessionId === undefined || worker.sessionId === session.id ? worker : undefined;
  const workerOnline = Boolean(workerForSession && workerForSession.status !== 'offline');
  const normalizedQuotaWindows = isCodex && workerOnline
    ? normalizeCodexRateLimits(workerForSession?.nativeRateLimits)
    : {};
  const liveQuotaWindows = {
    weekly: hasQuotaDetails(normalizedQuotaWindows.weekly) ? normalizedQuotaWindows.weekly : undefined,
    monthly: hasQuotaDetails(normalizedQuotaWindows.monthly) ? normalizedQuotaWindows.monthly : undefined,
  };
  const cachedQuotaWindows = isCodex ? normalizeCodexQuotaProjection(usageView.codexQuota) : {};
  const hasLiveQuota = Boolean(liveQuotaWindows.weekly || liveQuotaWindows.monthly);
  const quotaWindows = hasLiveQuota ? liveQuotaWindows : cachedQuotaWindows;
  const quotaIsCached = !hasLiveQuota && Boolean(usageView.codexQuota);

  const renderQuotaWindow = (label: string, window: CodexQuotaWindow | undefined) => (
    <div key={label} className="min-w-0">
      <div className="text-xs text-text-tertiary mb-1">{label}</div>
      <div className="text-sm text-text-primary break-words">
        {window ? quotaDetails(window).join(' · ') : null}
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
        <section className="rounded border border-border-default overflow-hidden" aria-label="System prompt">
          <button
            type="button"
            aria-expanded={systemPromptExpanded}
            aria-controls="session-system-prompt"
            onClick={() => setSystemPromptExpanded((expanded) => !expanded)}
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors"
          >
            <span className="font-medium">System prompt</span>
            <span className="flex items-center gap-1 text-xs text-text-tertiary">
              {systemPromptExpanded ? '收起' : '展开'}
              {systemPromptExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </span>
          </button>
          {systemPromptExpanded && (
            <div id="session-system-prompt" role="region" aria-label="System prompt content" className="border-t border-border-default px-3 py-3">
              {session.systemPrompt?.trim() ? (
                <div className="whitespace-pre-wrap break-words text-sm text-text-primary">{session.systemPrompt}</div>
              ) : (
                <div className="text-sm text-text-tertiary">暂无 / 未建立</div>
              )}
            </div>
          )}
        </section>
        <section className="rounded border border-border-default overflow-hidden" aria-label="Usage">
          <button
            type="button"
            aria-expanded={usageExpanded}
            aria-controls="session-usage-details"
            onClick={() => setUsageExpanded((expanded) => !expanded)}
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-tertiary transition-colors"
          >
            <span className="font-medium">Usage</span>
            <span className="flex items-center gap-1 text-xs text-text-tertiary">
              {usageLoading ? '加载中…' : usageExpanded ? '收起' : '展开'}
              {usageExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </span>
          </button>
          {usageExpanded && (
            <div id="session-usage-details" role="region" aria-label="Usage details" className="space-y-3 border-t border-border-default px-3 py-3">
              {usageError && <div className="text-xs text-text-tertiary">{usageError}，当前显示已有数据</div>}
              {isCodex && (quotaWindows.weekly || quotaWindows.monthly || !workerOnline) ? (
                <div className="space-y-3" role="region" aria-label="Codex quota">
                  <div className="text-xs text-text-tertiary">
                    {quotaIsCached
                      ? `Quota${usageView.codexQuota?.stale ? '（最近缓存，可能已过期）' : '（最近缓存）'}`
                      : 'Quota（当前 Worker 快照）'}
                  </div>
                  {quotaWindows.weekly || quotaWindows.monthly ? (
                    <>
                      {quotaWindows.weekly && renderQuotaWindow('周额度', quotaWindows.weekly)}
                      {quotaWindows.monthly && renderQuotaWindow('月额度', quotaWindows.monthly)}
                    </>
                  ) : (
                    <div className="text-sm text-text-tertiary">当前没有可用的周/月 quota 缓存</div>
                  )}
                </div>
              ) : !isCodex ? (
                <div className="min-w-0">
                  <div className="text-xs text-text-tertiary mb-1">Credits（累计）</div>
                  <div className="text-sm text-text-primary break-words">{formatMetric(usageView.total.credit)}</div>
                </div>
              ) : null}
              <div className="grid grid-cols-1 gap-3 border-t border-border-muted pt-3 sm:grid-cols-3">
                <div><div className="text-xs text-text-tertiary mb-1">输入 Token</div><div className="text-sm text-text-primary">{formatMetric(usageView.input)}</div></div>
                <div><div className="text-xs text-text-tertiary mb-1">输出 Token</div><div className="text-sm text-text-primary">{formatMetric(usageView.output)}</div></div>
                <div><div className="text-xs text-text-tertiary mb-1">缓存 Token</div><div className="text-sm text-text-primary break-words">{[usageView.cache.total != null ? `总计 ${formatMetric(usageView.cache.total)}` : null, usageView.cache.read != null ? `读 ${formatMetric(usageView.cache.read)}` : null, usageView.cache.write != null ? `写 ${formatMetric(usageView.cache.write)}` : null].filter(Boolean).join(' · ') || '暂无数据'}</div></div>
              </div>
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}
