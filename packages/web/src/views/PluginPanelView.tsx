import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw, Check, Play, Plug, Search, UserRound, Layers3 } from 'lucide-react';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { invokePluginPanel } from '@/services/api';
import type { AccountQuota, AccountSnapshot, PluginPanel } from '@/types/pluginPanels';
import {
  QUOTA_REFRESH_CONCURRENCY, bumpSnapshotVersion, captureQuotaVersions, currentQuotaVersion,
  currentSnapshotVersion, dedupeQuotaRequest, markQuotaVersion, mergeSnapshots, needsQuotaRefresh,
  readCachedSnapshot, runLimited, startListRequest, writeCachedSnapshot,
  type PanelScope, type QuotaResult,
} from './pluginPanelRuntime';

const button = 'inline-flex items-center justify-center gap-2 rounded-lg border border-border-default px-3 py-2 text-sm disabled:opacity-40 hover:bg-bg-hover';

/** Local clock driving the relative quota times. Ticking only recomputes the
 *  displayed durations from Date.now(); it never triggers quota reads or any
 *  other request, so a stale tick can never look like a refresh. */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** "Xh Ymin" duration (the hour part is omitted below one hour); null when the
 *  value is negative or not finite, so callers can state expiry explicitly. */
export function formatClockDuration(milliseconds: number): string | null {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null;
  const minutes = Math.floor(milliseconds / 60_000);
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours}h ${minutes % 60}min` : `${minutes}min`;
}

export default function PluginPanelView() {
  const { pluginId, panelId } = useParams();
  const { panels, error, loaded, load } = usePluginPanelStore();
  useEffect(() => { void load(); }, [load]);
  const panel = panels.find((item) => item.pluginId === pluginId && item.id === panelId);
  if (!panel) return <div className="p-6 text-text-secondary" role="status">
    {error || (loaded ? '插件面板不存在或已卸载' : '正在加载插件面板…')}
    <button className={button + ' ml-3'} onClick={() => void load(true)}>重新加载</button>
  </div>;
  if (panel.renderer !== 'account-switcher/v1') return <div className="p-6">此版本不支持该面板</div>;
  return <AccountSwitcher key={panel.pluginId + '/' + panel.id} panel={panel} />;
}

type SnapshotMap = Record<string, AccountSnapshot | null>;

export function AccountSwitcher({ panel }: { panel: PluginPanel }) {
  const scope: PanelScope = useMemo(() => ({ pluginId: panel.pluginId, panelId: panel.id }),
    [panel.pluginId, panel.id]);
  const [app, setApp] = useState('codex');
  // Reading the module cache during the first render is what makes a re-entry
  // instant: the last snapshot of each app is shown before the list returns.
  const [snapshots, setSnapshots] = useState<SnapshotMap>(() => ({
    codex: readCachedSnapshot(scope, 'codex'),
    claude: readCachedSnapshot(scope, 'claude'),
    gemini: readCachedSnapshot(scope, 'gemini'),
  }));
  const [listLoading, setListLoading] = useState<Record<string, boolean>>({});
  const [listErrors, setListErrors] = useState<Record<string, string | null>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [refreshing, setRefreshing] = useState<string[]>([]);
  const [quotaErrors, setQuotaErrors] = useState<Record<string, string>>({});
  const [query, setQuery] = useState('');

  const admitted = useRef(false);
  const mounted = useRef(true);
  const appRef = useRef(app);
  appRef.current = app;
  const snapshotsRef = useRef(snapshots);
  /** One token per app and snapshot-producing request; a late list response
   *  cannot roll back a switch that was requested after it. */
  const viewTokens = useRef<Record<string, number>>({});
  /** Providers already auto-queried during this app visit, so a failed read is
   *  reported once and retried explicitly instead of looping. */
  const autoAttempted = useRef<Set<string>>(new Set());

  const snapshot = snapshots[app] ?? null;
  const now = useNow();

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const mergeInto = useCallback((next: AccountSnapshot, keepQuota?: (providerId: string) => boolean) => {
    // The shared cache is the source of truth: another mount may have switched
    // providers after this component captured its own last snapshot.
    const previous = readCachedSnapshot(scope, next.app) ?? snapshotsRef.current[next.app];
    const merged = mergeSnapshots(previous ?? null, next, keepQuota);
    snapshotsRef.current = { ...snapshotsRef.current, [next.app]: merged };
    writeCachedSnapshot(scope, merged);
    setSnapshots((current) => ({ ...current, [next.app]: merged }));
    return merged;
  }, [scope]);

  const refreshQuota = useCallback(async (targetApp: string, providerId: string) => {
    const key = `${targetApp}:${providerId}`;
    const { promise, reused } = dedupeQuotaRequest(scope, targetApp, providerId,
      () => invokePluginPanel<QuotaResult>(panel.pluginId, panel.id, 'quota', { app: targetApp, id: providerId }));
    if (!reused) setRefreshing((current) => current.includes(key) ? current : [...current, key]);
    try {
      const value = await promise;
      // Patch the latest shared snapshot, not this mount's last copy: the panel
      // may have been unmounted, and a newer mount may already have switched.
      const shared = readCachedSnapshot(scope, targetApp);
      const base = shared ?? (mounted.current ? snapshotsRef.current[targetApp] : null);
      if (base?.providers.some((provider) => provider.id === value.id)) {
        const merged = {
          ...base,
          providers: base.providers.map((provider) =>
            provider.id === value.id ? { ...provider, quota: value.quota } : provider),
        };
        writeCachedSnapshot(scope, merged);
        markQuotaVersion(scope, targetApp, value.id);
        if (mounted.current) {
          snapshotsRef.current = { ...snapshotsRef.current, [targetApp]: merged };
          setSnapshots((current) => ({ ...current, [targetApp]: merged }));
        }
      }
      if (mounted.current) {
        setQuotaErrors((current) => {
          if (!(key in current)) return current;
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
    } catch (reason) {
      if (mounted.current) {
        setQuotaErrors((current) => ({ ...current,
          [key]: reason instanceof Error ? reason.message : '额度刷新失败' }));
      }
    } finally {
      if (!reused && mounted.current) {
        setRefreshing((current) => current.filter((entry) => entry !== key));
      }
    }
  }, [scope, panel.pluginId, panel.id]);

  /** Query only providers whose last real query is unknown or older than five
   *  minutes. Called on entry and after the entry list, never on a timer. */
  const autoRefresh = useCallback((target: AccountSnapshot) => {
    const clock = Date.now();
    const stale = target.providers.filter((provider) =>
      needsQuotaRefresh(provider.quota, clock) && !autoAttempted.current.has(`${target.app}:${provider.id}`));
    if (stale.length === 0) return;
    for (const provider of stale) autoAttempted.current.add(`${target.app}:${provider.id}`);
    void runLimited(stale.map((provider) => () => refreshQuota(target.app, provider.id)),
      QUOTA_REFRESH_CONCURRENCY);
  }, [refreshQuota]);

  const loadSnapshot = useCallback(async (targetApp: string, refreshQuotaAfter: boolean) => {
    viewTokens.current[targetApp] = (viewTokens.current[targetApp] ?? 0) + 1;
    const token = viewTokens.current[targetApp];
    setListLoading((current) => ({ ...current, [targetApp]: true }));
    setListErrors((current) => ({ ...current, [targetApp]: null }));
    try {
      const ticket = startListRequest(scope, targetApp, () =>
        invokePluginPanel<AccountSnapshot>(panel.pluginId, panel.id, 'list', { app: targetApp }));
      const value = await ticket.promise;
      if (!mounted.current || viewTokens.current[targetApp] !== token) return;
      // A write completed after this request started, even when a newer mount
      // reused the same promise: its result must not restore the old state.
      if (ticket.snapshotVersion !== currentSnapshotVersion(scope, targetApp)) return;
      const keepQuota = (providerId: string) => currentQuotaVersion(scope, targetApp, providerId)
        > (ticket.quotaVersions[providerId] ?? 0);
      const merged = mergeInto(value, keepQuota);
      if (appRef.current === targetApp) setUncertain(false);
      if (refreshQuotaAfter) autoRefresh(merged);
    } catch (reason) {
      if (!mounted.current || viewTokens.current[targetApp] !== token) return;
      setListErrors((current) => ({ ...current,
        [targetApp]: reason instanceof Error ? reason.message : '读取失败' }));
    } finally {
      if (mounted.current && viewTokens.current[targetApp] === token) {
        setListLoading((current) => ({ ...current, [targetApp]: false }));
      }
    }
  }, [scope, panel.pluginId, panel.id, mergeInto, autoRefresh]);

  // Entry and app switch are the only automatic trigger. The cached snapshot is
  // already on screen; stale quotas start immediately while the list refreshes.
  useEffect(() => {
    autoAttempted.current = new Set();
    const cached = readCachedSnapshot(scope, app);
    if (cached) autoRefresh(cached);
    void loadSnapshot(app, true);
  }, [app, scope, autoRefresh, loadSnapshot]);

  const act = async (action: string, id?: string) => {
    if (action === 'quota') {
      if (id) await refreshQuota(app, id);
      return;
    }
    if (action === 'list') {
      await loadSnapshot(app, false);
      return;
    }
    if (admitted.current || busy) return;
    admitted.current = true; setBusy(true); setError(null);
    const targetApp = app;
    const writing = panel.actions[action]?.effect === 'write_external';
    viewTokens.current[targetApp] = (viewTokens.current[targetApp] ?? 0) + 1;
    const token = viewTokens.current[targetApp];
    const versions = captureQuotaVersions(scope, targetApp);
    const startVersion = currentSnapshotVersion(scope, targetApp);
    try {
      const current = readCachedSnapshot(scope, targetApp) ?? snapshotsRef.current[targetApp] ?? null;
      const args: Record<string, unknown> = { app: targetApp, ...(id ? { id } : {}) };
      if (writing) args.expectedCurrentId = action === 'default_account'
        ? current?.accounts.find((account) => account.isDefault)?.id ?? null
        : current?.currentProviderId ?? null;
      const value = await invokePluginPanel<AccountSnapshot>(
        panel.pluginId, panel.id, action, args, writing ? crypto.randomUUID() : undefined);
      // Another write finished first; its snapshot is the newer shared truth.
      if (currentSnapshotVersion(scope, targetApp) !== startVersion) return;
      bumpSnapshotVersion(scope, targetApp);
      const keepQuota = (providerId: string) => currentQuotaVersion(scope, targetApp, providerId)
        > (versions[providerId] ?? 0);
      mergeInto(value, keepQuota);
      if (mounted.current && viewTokens.current[targetApp] === token) setUncertain(false);
    } catch (reason) {
      if (!mounted.current) return;
      setError(reason instanceof Error ? reason.message : '操作失败');
      if (writing) setUncertain(true);
    } finally { admitted.current = false; setBusy(false); }
  };

  const listError = listErrors[app] ?? null;
  const refreshingHere = refreshing.filter((key) => key.startsWith(app + ':'));

  return <section className="h-full overflow-auto p-4 sm:p-6 text-text-primary" aria-label={panel.title}>
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-xl font-semibold flex items-center gap-2"><Plug size={22} />{panel.title}</h1>
          <p className="mt-1 text-sm text-text-tertiary">管理应用配置与 ChatGPT 账号，和 CC Switch 共享账号数据。</p></div>
        <button className={button} disabled={busy || listLoading[app] === true} onClick={() => void act('list')}><RefreshCw size={15} />刷新列表</button>
      </header>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border-default bg-bg-secondary p-2" role="group" aria-label="应用">
        {['codex', 'claude', 'gemini'].map((value) => <button key={value} className={button + (app === value ? ' bg-accent/15 text-accent' : '')}
          aria-pressed={app === value} disabled={busy} onClick={() => setApp(value)}>{value === 'codex' ? 'Codex' : value === 'claude' ? 'Claude' : 'Gemini'}</button>)}
      </div>
      <label className="flex items-center gap-2 rounded-xl border border-border-default bg-bg-secondary px-3 focus-within:border-accent">
        <Search size={16} className="shrink-0 text-text-tertiary" />
        <input aria-label="搜索配置或账号" placeholder="搜索配置名称、备注、账号或地址…" value={query} onChange={(event) => setQuery(event.target.value)}
          className="min-w-0 w-full bg-transparent py-3 text-sm outline-none" />
      </label>
      {error && <div role="alert" className="rounded-lg border border-red-500/40 p-3 text-sm break-words">{error}</div>}
      {listError && <div role="alert" className="rounded-lg border border-red-500/40 p-3 text-sm break-words">读取列表失败：{listError}（已保留上次数据，可重试）</div>}
      {uncertain && <p role="status" className="text-sm text-amber-500">切换结果尚未确认。请先刷新列表并核对实际状态，再发起新的切换。</p>}
      {refreshingHere.length > 0 && <p role="status" className="text-sm text-text-tertiary">正在刷新 {refreshingHere.length} 个过期额度…</p>}
      {listLoading[app] === true && snapshot && <p role="status" className="text-sm text-text-tertiary">正在后台更新列表…</p>}
      {!snapshot && <p role="status" className="text-text-tertiary">{listLoading[app] === true ? '正在读取账号信息…' : '尚未读取到数据'}</p>}
      {snapshot && <>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-secondary">
          <span className="inline-flex items-center gap-2"><Layers3 size={16} />{snapshot.providers.length} 个配置</span>
          {app === 'codex' && <span className="inline-flex items-center gap-2"><UserRound size={16} />{snapshot.accounts.length} 个账号</span>}
          <span>当前：{snapshot.providers.find((provider) => provider.isCurrent)?.name || '未启用配置'}</span>
        </div>
        <div className="space-y-3" aria-label="配置列表">
          {snapshot.providers.length === 0 && <p className="p-5 text-text-tertiary">此应用还没有配置，请在 cc-switch 中添加。</p>}
          {snapshot.providers.filter((provider) => `${provider.name} ${provider.id} ${provider.origin || ''} ${provider.notes || ''} ${provider.category || ''}`.toLowerCase().includes(query.toLowerCase())).map((provider) => <article key={provider.id} className={'rounded-xl border p-4 sm:p-5 bg-bg-secondary ' + (provider.isCurrent ? 'border-accent shadow-sm' : 'border-border-default')}>
            <div className="flex flex-wrap items-center gap-3">
              <div aria-hidden="true" className="hidden sm:flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent/10 text-lg font-semibold text-accent">{provider.name.slice(0, 1).toUpperCase()}</div>
              <div className="min-w-0 basis-full sm:basis-0 sm:flex-1"><h2 className="font-semibold break-words">{provider.name}</h2>
                <p className="mt-1 text-xs text-text-tertiary break-all">{provider.origin || provider.id}</p>
                {provider.notes && <p className="mt-1 text-sm text-text-secondary break-words">{provider.notes}</p>}
                {provider.category && <span className="mt-2 inline-block rounded bg-bg-primary px-2 py-0.5 text-xs text-text-tertiary">{provider.category}</span>}</div>
              {provider.isCurrent ? <span className="flex items-center gap-1 text-accent text-sm"><Check size={16} />当前配置</span> :
                <button className={button + ' text-accent'} disabled={busy || uncertain} onClick={() => void act('switch', provider.id)}><Play size={14} />启用配置</button>}
              <button className={button} aria-label={`刷新 ${provider.name} 额度`} disabled={busy || refreshing.includes(`${app}:${provider.id}`)} onClick={() => void act('quota', provider.id)}><RefreshCw size={14} />额度</button>
            </div>
            {quotaErrors[`${app}:${provider.id}`] && <p role="status" className="mt-2 text-sm text-amber-500">额度刷新失败：{quotaErrors[`${app}:${provider.id}`]}（可重试）</p>}
            <Quota quota={provider.quota} now={now} />
          </article>)}
        </div>
        {app === 'codex' && <section className="rounded-xl border border-border-default p-4 space-y-3" aria-label="ChatGPT 账号">
          <h2 className="font-semibold">ChatGPT 默认账号</h2>
          <p className="text-xs text-text-tertiary">仅影响使用默认账号的配置；绑定了特定账号的配置保持其绑定。额度在上方关联配置中查询。</p>
          {snapshot.accountError && <p role="status" className="text-sm text-text-tertiary">{snapshot.accountError}</p>}
          {snapshot.accounts.some((account) => account.reauthRequired) && <p role="status" className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-500">部分账号需要重新登录。请在 CC Switch GUI 中恢复有效备份或重新授权，之后刷新列表。</p>}
          {!snapshot.accountError && snapshot.accounts.length === 0 && <p className="text-sm text-text-tertiary">没有已登录账号，请在 cc-switch 中登录。</p>}
          {snapshot.accounts.filter((account) => `${account.login} ${account.id}`.toLowerCase().includes(query.toLowerCase())).map((account) => <div key={account.id} className="flex flex-wrap items-center gap-3 border-t border-border-default pt-3">
            <span className="min-w-0 flex-1 break-all text-sm">{account.login}</span>
            {account.reauthRequired && <span className="text-sm text-amber-500">需要重新登录</span>}
            {account.isDefault ? <span className="text-accent text-sm">默认账号</span> : <button className={button} disabled={busy || uncertain || account.reauthRequired}
              onClick={() => void act('default_account', account.id)}>设为默认账号</button>}
          </div>)}
        </section>}
        <p className="text-xs text-text-tertiary">列表读取于 {new Date(snapshot.observedAt).toLocaleString()}。切换不会自动重启 Pan Worker。</p>
      </>}
    </div>
  </section>;
}

function Quota({ quota, now }: { quota: AccountQuota | null; now: number }) {
  if (!quota) return <p className="mt-4 text-sm text-text-tertiary">额度未查询</p>;
  return <div className="mt-4 space-y-2 text-sm">
    {quota.tiers.map((tier, index) => <div key={index}>
      <div className="flex flex-wrap justify-between gap-2"><span>{tier.name} · 已用 <strong className={tier.usedPercent >= 90 ? 'text-red-400' : 'text-accent'}>{tier.usedPercent}%</strong></span>
        <span className="text-xs text-text-tertiary">{resetLabel(tier.resetsAt, now)}</span></div>
      <div className="mt-1 h-1.5 rounded-full bg-bg-primary overflow-hidden"><div className="h-full bg-accent" style={{ width: `${tier.usedPercent}%` }} /></div>
    </div>)}
    {quota.balances.map((balance, index) => <p key={index}>{balance.planName || '余额'}：剩余 {balance.remaining ?? '未知'} / 总量 {balance.total ?? '未知'} {balance.unit || ''}</p>)}
    {quota.tiers.length === 0 && quota.balances.length === 0 && <p className="text-text-tertiary">{quota.message || (quota.available ? '未返回额度明细' : '此配置暂无可用额度数据')}</p>}
    <p className="text-xs text-text-tertiary">{queriedLabel(quota, now)}</p>
  </div>;
}

/** One reset window: the raw absolute time stays viewable in the existing locale
 *  format, followed by a live countdown, an explicit expiry, or an explicit
 *  unknown when the provider returned no usable reset time. */
function resetLabel(resetsAt: string | null, now: number): string {
  const time = resetsAt == null ? NaN : new Date(resetsAt).getTime();
  if (Number.isNaN(time)) return '重置时间未知';
  const absolute = new Date(time).toLocaleString();
  const remaining = formatClockDuration(time - now);
  return remaining ? `重置于 ${absolute} · 距现在剩余 ${remaining}` : `重置于 ${absolute} · 已到期`;
}

/** Last quota query. The elapsed duration is measured from the quota's own
 *  queriedAt — the real query time carried by fresh results and cached entries
 *  alike — never from component mount or cache-read time. A queriedAt in the
 *  future (clock skew) shows no elapsed claim instead of a negative one. */
function queriedLabel(quota: AccountQuota, now: number): string {
  if (quota.queriedAt == null || !Number.isFinite(quota.queriedAt)) return `查询时间：未知 · ${quota.status}`;
  const absolute = new Date(quota.queriedAt).toLocaleString();
  const elapsed = formatClockDuration(now - quota.queriedAt);
  return `查询时间：${absolute}${elapsed ? ` · 距现在已过去 ${elapsed}` : ''} · ${quota.status}`;
}
