import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw, Check, Play, Plug, Search, UserRound, Layers3 } from 'lucide-react';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { invokePluginPanel } from '@/services/api';
import type { AccountQuota, AccountSnapshot, PluginPanel } from '@/types/pluginPanels';

const button = 'inline-flex items-center justify-center gap-2 rounded-lg border border-border-default px-3 py-2 text-sm disabled:opacity-40 hover:bg-bg-hover';

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

export function AccountSwitcher({ panel }: { panel: PluginPanel }) {
  const [app, setApp] = useState('codex');
  const [snapshot, setSnapshot] = useState<AccountSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [confirm, setConfirm] = useState<{ action: string; id: string; name: string } | null>(null);
  const [query, setQuery] = useState('');
  const admitted = useRef(false);

  useEffect(() => {
    let active = true;
    setSnapshot(null); setError(null); setConfirm(null); setBusy(true);
    void invokePluginPanel<AccountSnapshot>(panel.pluginId, panel.id, 'list', { app })
      .then((value) => { if (active) { setSnapshot(value); setUncertain(false); } })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : '读取失败'); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [app, panel.pluginId, panel.id]);

  const act = async (action: string, id?: string) => {
    if (admitted.current || busy) return;
    admitted.current = true; setBusy(true); setError(null); setConfirm(null);
    const writing = panel.actions[action]?.effect === 'write_external';
    try {
      const args: Record<string, unknown> = { app, ...(id ? { id } : {}) };
      if (writing) args.expectedCurrentId = action === 'default_account'
        ? snapshot?.accounts.find((a) => a.isDefault)?.id ?? null : snapshot?.currentProviderId ?? null;
      const value = await invokePluginPanel<AccountSnapshot | { id: string; app: string; quota: AccountQuota }>(
        panel.pluginId, panel.id, action, args, writing ? crypto.randomUUID() : undefined);
      if ('providers' in value) { setSnapshot(value); setUncertain(false); }
      else setSnapshot((previous) => previous ? { ...previous, providers: previous.providers.map((p) =>
        p.id === value.id ? { ...p, quota: value.quota } : p) } : previous);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败');
      if (writing) setUncertain(true);
    } finally { admitted.current = false; setBusy(false); }
  };

  return <section className="h-full overflow-auto p-4 sm:p-6 text-text-primary" aria-label={panel.title}>
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-xl font-semibold flex items-center gap-2"><Plug size={22} />{panel.title}</h1>
          <p className="mt-1 text-sm text-text-tertiary">管理应用配置与 ChatGPT 账号，和 CC Switch 共享账号数据。</p></div>
        <button className={button} disabled={busy} onClick={() => void act('list')}><RefreshCw size={15} />刷新列表</button>
      </header>
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border-default bg-bg-secondary p-2" role="group" aria-label="应用">
        {['codex', 'claude', 'gemini'].map((value) => <button key={value} className={button + (app === value ? ' bg-accent/15 text-accent' : '')}
          aria-pressed={app === value} disabled={busy} onClick={() => { setApp(value); setConfirm(null); }}>{value === 'codex' ? 'Codex' : value === 'claude' ? 'Claude' : 'Gemini'}</button>)}
      </div>
      <label className="flex items-center gap-2 rounded-xl border border-border-default bg-bg-secondary px-3 focus-within:border-accent">
        <Search size={16} className="shrink-0 text-text-tertiary" />
        <input aria-label="搜索配置或账号" placeholder="搜索配置名称、备注、账号或地址…" value={query} onChange={(event) => setQuery(event.target.value)}
          className="min-w-0 w-full bg-transparent py-3 text-sm outline-none" />
      </label>
      {error && <div role="alert" className="rounded-lg border border-red-500/40 p-3 text-sm break-words">{error}</div>}
      {uncertain && <p role="status" className="text-sm text-amber-500">切换结果尚未确认。请先刷新列表并核对实际状态，再发起新的切换。</p>}
      {confirm && <div className="rounded-xl border border-accent p-4 flex flex-wrap items-center gap-3" role="alertdialog" aria-label="确认切换">
        <p className="min-w-0 flex-1 break-words">确认{confirm.action === 'switch' ? '全局启用配置' : '设置默认账号'}「{confirm.name}」？</p>
        <button className={button} onClick={() => setConfirm(null)}>取消</button>
        <button className={button + ' bg-accent text-white'} disabled={busy} onClick={() => void act(confirm.action, confirm.id)}>确认切换</button>
      </div>}
      {!snapshot && <p role="status" className="text-text-tertiary">{busy ? '正在读取账号信息…' : '尚未读取到数据'}</p>}
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
                <button className={button + ' text-accent'} disabled={busy || uncertain} onClick={() => setConfirm({ action: 'switch', id: provider.id, name: provider.name })}><Play size={14} />启用配置</button>}
              <button className={button} aria-label={`刷新 ${provider.name} 额度`} disabled={busy} onClick={() => void act('quota', provider.id)}><RefreshCw size={14} />额度</button>
            </div>
            <Quota quota={provider.quota} />
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
              onClick={() => setConfirm({ action: 'default_account', id: account.id, name: account.login })}>设为默认账号</button>}
          </div>)}
        </section>}
        <p className="text-xs text-text-tertiary">列表读取于 {new Date(snapshot.observedAt).toLocaleString()}。切换不会自动重启 Pan Worker。</p>
      </>}
    </div>
  </section>;
}

function Quota({ quota }: { quota: AccountQuota | null }) {
  if (!quota) return <p className="mt-4 text-sm text-text-tertiary">额度未查询</p>;
  return <div className="mt-4 space-y-2 text-sm">
    {quota.tiers.map((tier, index) => <div key={index}>
      <div className="flex flex-wrap justify-between gap-2"><span>{tier.name} · 已用 <strong className={tier.usedPercent >= 90 ? 'text-red-400' : 'text-accent'}>{tier.usedPercent}%</strong></span>
        <span className="text-xs text-text-tertiary">{tier.resetsAt ? `重置于 ${new Date(tier.resetsAt).toLocaleString()}` : '重置时间未知'}</span></div>
      <div className="mt-1 h-1.5 rounded-full bg-bg-primary overflow-hidden"><div className="h-full bg-accent" style={{ width: `${tier.usedPercent}%` }} /></div>
    </div>)}
    {quota.balances.map((balance, index) => <p key={index}>{balance.planName || '余额'}：剩余 {balance.remaining ?? '未知'} / 总量 {balance.total ?? '未知'} {balance.unit || ''}</p>)}
    {quota.tiers.length === 0 && quota.balances.length === 0 && <p className="text-text-tertiary">{quota.message || (quota.available ? '未返回额度明细' : '此配置暂无可用额度数据')}</p>}
    <p className="text-xs text-text-tertiary">缓存查询时间：{quota.queriedAt ? new Date(quota.queriedAt).toLocaleString() : '未知'} · {quota.status}</p>
  </div>;
}
