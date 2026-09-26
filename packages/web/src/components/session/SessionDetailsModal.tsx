import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Copy } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import { fetchCodexQuota, fetchSession, fetchSessionUsage } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';
import type { Session, SessionUsageView } from '@/types';
import { copyText } from '@/utils/clipboard';
import { normalizeCodexQuotaProjection, type CodexQuotaWindow } from '@/utils/codexRateLimits';
import { getSessionUsageCache, setSessionUsageCache } from './sessionUsageCache';
import { FreshnessSkeleton, FreshnessStatus, type FreshnessState } from './FreshnessStatus';

interface SessionDetailsModalProps {
  session: Session | null;
  onClose: () => void;
}

// Session Details is mounted across close/open transitions. Keep the last
// successful projection by Session so a slow provider refresh cannot blank an
// already-known Usage view on the next open. Every open still starts a fresh
// request; this cache is a render fast path, not an offline truth source.
function usageWithQuotaRefreshError(
  usage: SessionUsageView,
  message: string,
): SessionUsageView {
  if (!usage.codexQuota) return usage;
  return {
    ...usage,
    codexQuota: {
      ...usage.codexQuota,
      stale: true,
      refreshError: message,
    },
  };
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
  const [usageRefreshing, setUsageRefreshing] = useState(false);
  const [quotaLoading, setQuotaLoading] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [systemPromptExpanded, setSystemPromptExpanded] = useState(false);
  const [detailSession, setDetailSession] = useState<Session | null>(null);
  const usageRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const detailCache = useRef(new Map<string, Session>());
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRefreshing, setDetailRefreshing] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRetrySeq, setDetailRetrySeq] = useState(0);
  const [usageRetrySeq, setUsageRetrySeq] = useState(0);

  useEffect(() => {
    usageRequestId.current += 1;
    const cached = sessionId ? getSessionUsageCache(sessionId) : undefined;
    setUsageExpanded(false);
    setUsage(cached?.usage ?? null);
    setUsageLoading(false);
    setUsageRefreshing(false);
    setQuotaLoading(false);
    setUsageError(null);
    setSystemPromptExpanded(false);
    setDetailSession(sessionId ? detailCache.current.get(sessionId) ?? null : null);
    setDetailLoading(false);
    setDetailRefreshing(false);
    setDetailError(null);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    const requestId = ++detailRequestId.current;
    const cached = detailCache.current.get(sessionId);
    let active = true;
    setDetailLoading(!cached);
    setDetailRefreshing(Boolean(cached));
    setDetailError(null);
    fetchSession(sessionId)
      .then((full) => {
        if (!active || detailRequestId.current !== requestId || full.id !== sessionId) return;
        detailCache.current.set(sessionId, full);
        setDetailSession(full);
        setDetailLoading(false);
        setDetailRefreshing(false);
      })
      .catch((error) => {
        if (!active || detailRequestId.current !== requestId) return;
        setDetailLoading(false);
        setDetailRefreshing(false);
        setDetailError(error instanceof Error ? error.message : 'Session details unavailable');
      });
    return () => {
      active = false;
    };
  }, [sessionId, detailRetrySeq]);

  useEffect(() => {
    if (!usageExpanded || !sessionId) return;
    const requestId = ++usageRequestId.current;
    const cached = getSessionUsageCache(sessionId);
    let active = true;
    const isCurrent = () => active && usageRequestId.current === requestId;
    setUsageLoading(!cached);
    setUsageRefreshing(Boolean(cached));
    setQuotaLoading(false);
    setUsageError(null);

    fetchSessionUsage(sessionId)
      .then((result) => {
        if (!isCurrent() || result.sessionId !== sessionId) return;
        // Do not discard a last-known quota if a cache-only backend read is
        // temporarily unavailable while the independent refresh is running.
        const merged = result.codexQuota || !cached?.usage.codexQuota
          ? result
          : { ...result, codexQuota: cached.usage.codexQuota };
        setSessionUsageCache(sessionId, merged);
        setUsage(merged);
        setUsageLoading(false);
        setUsageRefreshing(false);

        if (result.adapter !== 'codex') return;
        setQuotaLoading(true);
        fetchCodexQuota(sessionId)
          .then((quota) => {
            if (!isCurrent()) return;
            setUsage((current) => {
              if (!current || current.sessionId !== sessionId) return current;
              const refreshed = { ...current, codexQuota: quota };
              setSessionUsageCache(sessionId, refreshed);
              return refreshed;
            });
          })
          .catch((error) => {
            if (!isCurrent()) return;
            const message = error instanceof Error ? error.message : 'Quota 刷新失败';
            setUsage((current) => {
              if (!current || current.sessionId !== sessionId) return current;
              const stale = usageWithQuotaRefreshError(current, message);
              setSessionUsageCache(sessionId, stale);
              return stale;
            });
            setUsageError(`Quota 刷新失败：${message}`);
          })
          .finally(() => {
            if (isCurrent()) setQuotaLoading(false);
          });
      })
      .catch((error) => {
        if (!isCurrent()) return;
        setUsageError(error instanceof Error ? error.message : 'Usage 加载失败');
        setUsageLoading(false);
        setUsageRefreshing(false);
      })
      .finally(() => {
        if (isCurrent()) setUsageLoading(false);
      });
    return () => { active = false; };
  }, [sessionId, usageExpanded, usageRetrySeq]);

  if (!session) return null;

  // Sidebar sessions come from summary=1 and intentionally omit detail-only
  // fields such as systemPrompt. Render that snapshot immediately, then use
  // the complete session fetched for this open modal once it arrives.
  const displayedSession = detailSession ?? session;
  const usageView = usage ?? fallbackUsage(displayedSession);
  const quotaRefreshError = usageView.codexQuota?.refreshError;
  const detailFreshness: FreshnessState = detailError
    ? 'error'
    : detailLoading
      ? 'loading'
      : detailRefreshing
        ? 'refreshing'
        : detailSession
          ? 'updated'
          : 'cached';
  const detailSource = detailSession ? 'session metadata' : 'session summary cache';
  const usageCache = sessionId ? getSessionUsageCache(sessionId) : undefined;
  const usageFreshness: FreshnessState = usageError
    ? 'error'
    : usageLoading
      ? usageRefreshing
        ? 'refreshing'
        : 'loading'
      : usage
        ? 'updated'
        : 'unknown';
  const usageSource = usage
    ? typeof usage.source === 'string'
      ? usage.source
      : (usage.source as { kind?: string } | null | undefined)?.kind || 'persisted usage projection'
    : usageCache
      ? 'session usage cache'
      : 'unknown';
  const usageUpdatedAt = usage?.updatedAt ?? usageCache?.cachedAt ?? null;
  const retryUsage = () => setUsageRetrySeq((value) => value + 1);
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
    { label: 'Session name', value: displayValue(displayedSession.name), rawValue: displayedSession.name || undefined },
    { label: '工作目录', value: displayValue(displayedSession.workdir), rawValue: displayedSession.workdir },
    { label: 'Session ID', value: displayedSession.id, rawValue: displayedSession.id },
    { label: 'CLI ID', value: displayValue(displayedSession.cliSessionId), rawValue: displayedSession.cliSessionId ?? undefined },
  ];

  const isCodex = displayedSession.adapter === 'codex';
  const normalizedQuotaWindows = isCodex ? normalizeCodexQuotaProjection(usageView.codexQuota) : {};
  const quotaWindows = {
    fiveHour: hasQuotaDetails(normalizedQuotaWindows.fiveHour) ? normalizedQuotaWindows.fiveHour : undefined,
    weekly: hasQuotaDetails(normalizedQuotaWindows.weekly) ? normalizedQuotaWindows.weekly : undefined,
    monthly: hasQuotaDetails(normalizedQuotaWindows.monthly) ? normalizedQuotaWindows.monthly : undefined,
  };
  const quotaIsCached = Boolean(usageView.codexQuota);

  const renderQuotaWindow = (label: string, window: CodexQuotaWindow | undefined) => (
    <div key={label} className="min-w-0">
      <div className="text-xs text-text-tertiary mb-1">{label}</div>
      <div className="text-sm text-text-primary break-words">
        {window ? quotaDetails(window).join(' · ') : null}
      </div>
    </div>
  );

  return (
    // Mobile: the shared Modal renders this as a viewport-filling page instead
    // of a centered window (< md breakpoint only). Desktop keeps size="lg".
    <Modal open title="Session Details" onClose={onClose} size="lg" mobileFullscreen>
      <div className="space-y-3">
        <FreshnessStatus
          state={detailFreshness}
          updatedAt={displayedSession.updatedAt}
          source={detailSource}
          error={detailError}
          onRetry={detailError ? () => setDetailRetrySeq((value) => value + 1) : undefined}
        />
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
              {systemPromptExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </button>
          {systemPromptExpanded && (
            <div id="session-system-prompt" role="region" aria-label="System prompt content" className="border-t border-border-default px-3 py-3">
              {displayedSession.systemPrompt?.trim() ? (
                <div className="whitespace-pre-wrap break-words text-sm text-text-primary">{displayedSession.systemPrompt}</div>
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
              {usageLoading ? '加载中…' : usageRefreshing || quotaLoading ? '更新中…' : usageExpanded ? '收起' : '展开'}
              {usageExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </button>
          {usageExpanded && (
            <div id="session-usage-details" role="region" aria-label="Usage details" className="space-y-3 border-t border-border-default px-3 py-3">
              <FreshnessStatus
                state={usageFreshness}
                updatedAt={usageUpdatedAt}
                source={usageSource}
                error={null}
                onRetry={usageError || quotaRefreshError ? retryUsage : undefined}
              />
              {usageLoading && !usage && displayedSession.totalUsage == null && (
                <FreshnessSkeleton label="Loading usage" />
              )}
              {usageError && !quotaRefreshError && <div className="text-xs text-text-tertiary">{usageError}，当前显示已有数据</div>}
              {isCodex ? (
                <div className="space-y-3" role="region" aria-label="Codex quota">
                  <div className="text-xs text-text-tertiary">
                    {quotaIsCached
                      ? `Quota${usageView.codexQuota?.stale ? '（最近缓存，可能已过期）' : '（最近缓存）'}`
                      : 'Quota'}
                  </div>
                  {quotaRefreshError && (
                    <div className="text-xs text-text-tertiary">
                      Quota 刷新失败：{quotaRefreshError}，当前显示缓存
                    </div>
                  )}
                  {quotaLoading && !quotaIsCached ? (
                    <div className="text-sm text-text-tertiary">Quota 加载中…</div>
                  ) : quotaWindows.fiveHour || quotaWindows.weekly || quotaWindows.monthly ? (
                    <>
                      {quotaWindows.fiveHour && renderQuotaWindow('五小时额度', quotaWindows.fiveHour)}
                      {quotaWindows.weekly && renderQuotaWindow('周额度', quotaWindows.weekly)}
                      {quotaWindows.monthly && renderQuotaWindow('月额度', quotaWindows.monthly)}
                    </>
                  ) : (
                    <div className="text-sm text-text-tertiary">当前没有可用的五小时/周/月 quota 缓存</div>
                  )}
                </div>
              ) : (
                <div className="min-w-0">
                  <div className="text-xs text-text-tertiary mb-1">Credits（累计）</div>
                  <div className="text-sm text-text-primary break-words">{formatMetric(usageView.total.credit)}</div>
                </div>
              )}
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
