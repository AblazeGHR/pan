// @vitest-environment jsdom
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PluginPanelView, { AccountSwitcher, formatClockDuration } from './PluginPanelView';
import { invokePluginPanel } from '@/services/api';
import { resetPluginPanelRuntime } from './pluginPanelRuntime';
import { ViewEntryRail } from '@/components/layout/ViewEntryRail';
import { ViewEntryGroups } from '@/components/layout/ViewEntryGroups';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import type { AccountQuota, AccountSnapshot, PluginPanel } from '@/types/pluginPanels';

vi.mock('@/services/api', () => ({ invokePluginPanel: vi.fn(), fetchPluginPanels: vi.fn() }));
const panel: PluginPanel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch',
  renderer: 'account-switcher/v1', path: '/plugins/cc-switch-bridge/accounts', actions: {
    list: { capability: 'cc-switch-bridge.panel_list', effect: 'read' },
    quota: { capability: 'cc-switch-bridge.panel_quota', effect: 'read' },
    switch: { capability: 'cc-switch-bridge.panel_switch', effect: 'write_external' },
    config: { capability: 'cc-switch-bridge.panel_config', effect: 'write_external' },
    default_account: { capability: 'cc-switch-bridge.default_account', effect: 'write_external' },
  } };

const NOW = new Date('2026-10-19T12:00:00Z').getTime();
const NAMES: Record<string, string> = { 'p-a': 'Main', 'p-b': 'Secondary', 'p-c': 'Third',
  'p-d': 'Fourth', 'p-e': 'Fifth', 'c-a': 'Claude Main', 'g-a': 'Gemini Main' };

const quotaAt = (queriedAt: number | null, usedPercent = 50, resetIn = 90 * 60_000): AccountQuota => ({
  status: 'ok', available: true, queriedAt, message: null, balances: [],
  tiers: [{ name: 'five_hour', usedPercent, resetsAt: new Date(NOW + resetIn).toISOString() }] });

function snapshot(app: string, providers: string[], quotas: Record<string, AccountQuota | null>,
  current: string | null = providers[0] ?? null): AccountSnapshot {
  return { app, currentProviderId: current, observedAt: NOW, revision: 1, updatedAt: NOW,
    providers: providers.map((id) => ({ id, name: NAMES[id] ?? id, origin: 'https://api.example.test',
      isCurrent: id === current, quota: quotas[id] ?? null, nonManualDisabled: false })),
    accounts: app === 'codex'
      ? [{ id: 'acc-a', login: 'alice@example.test', isDefault: true },
        { id: 'acc-b', login: 'bob@example.test', isDefault: false }]
      : [], accountError: null };
}

const freshCodex = (now: number) => snapshot('codex', ['p-a', 'p-b'],
  { 'p-a': quotaAt(now - 60_000), 'p-b': quotaAt(now - 60_000, 50, 120 * 60_000) });

let states: Record<'codex' | 'claude' | 'gemini', AccountSnapshot>;

/** Default fixture behaves like a tiny stateful bridge: list returns the
 *  current snapshot, a quota read simulates a real query (fresh queriedAt and
 *  updated cache), writes mutate current state. Tests override single calls. */
beforeEach(() => {
  resetPluginPanelRuntime();
  const now = Date.now();
  states = { codex: freshCodex(now),
    claude: snapshot('claude', ['c-a'], { 'c-a': quotaAt(now - 60_000) }, 'c-a'),
    gemini: snapshot('gemini', ['g-a'], { 'g-a': quotaAt(now - 60_000) }, 'g-a') };
  vi.mocked(invokePluginPanel).mockReset();
  vi.mocked(invokePluginPanel).mockImplementation(async (_plugin, _panel, action, args) => {
    const targetApp = String(args.app) as keyof typeof states;
    const target = states[targetApp];
    if (action === 'list') return structuredClone(target ?? snapshot(targetApp, [], {}));
    if (action === 'quota') {
      const id = String(args.id);
      const provider = target?.providers.find((item) => item.id === id);
      const value = quotaAt(Date.now(), provider?.quota?.tiers[0]?.usedPercent ?? 0);
      if (provider) provider.quota = value;
      return { id, app: targetApp, quota: value };
    }
    if (!target) throw new Error('missing app state');
    if (action === 'config') {
      const id = String(args.id);
      states[targetApp] = { ...target, revision: (target.revision ?? 0) + 1, updatedAt: Date.now(),
        providers: target.providers.map((provider) => provider.id === id
          ? { ...provider, nonManualDisabled: Boolean(args.nonManualDisabled) } : provider) };
    } else if (action === 'switch') {
      const id = String(args.id);
      states[targetApp] = { ...target, currentProviderId: id,
        providers: target.providers.map((provider) => ({ ...provider, isCurrent: provider.id === id })) };
    } else if (action === 'default_account') {
      const id = String(args.id);
      states[targetApp] = { ...target,
        accounts: target.accounts.map((account) => ({ ...account, isDefault: account.id === id })) };
    }
    return structuredClone(states[targetApp]);
  });
});
afterEach(() => {
  cleanup(); vi.useRealTimers(); resetPluginPanelRuntime();
  usePluginPanelStore.setState({ panels: [], loaded: false, loading: false, error: null });
});

/** Flush the mocked invoke promise chains without waitFor (which needs real timers). */
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
/** Several await hops happen between list -> scheduler -> quota -> state. */
const flushAll = async () => {
  for (let index = 0; index < 10; index += 1) await act(async () => { await Promise.resolve(); });
};
const tick = (ms: number) => { act(() => { vi.advanceTimersByTime(ms); }); };
const callsOf = (action: string) => vi.mocked(invokePluginPanel).mock.calls
  .filter((call) => call[2] === action);
const quotaIds = () => callsOf('quota').map((call) => String(call[3].id)).sort();

describe('plugin account panel', () => {
  it('shows reauthentication requirements and prevents selecting an incomplete account', async () => {
    states.codex.accounts = states.codex.accounts.map((account) => ({ ...account, reauthRequired: !account.isDefault }));
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('需要重新登录');
    expect(screen.getByRole('button', { name: '设为默认账号' }).hasAttribute('disabled')).toBe(true);
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
  });

  it('shows accounts and refreshes one quota only through the explicit button', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('alice@example.test');
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('50%')).toHaveLength(2);
    vi.mocked(invokePluginPanel).mockResolvedValueOnce({ id: 'p-a', app: 'codex', quota: {
      status: 'ok', available: true, queriedAt: NOW - (2 * 3600_000 + 15 * 60_000 + 30_000), message: null,
      tiers: [{ name: '5小时', usedPercent: 70, resetsAt: new Date(NOW + 90 * 60_000 + 30_000).toISOString() }], balances: [],
    } });
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await screen.findByText('70%');
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'quota', { app: 'codex', id: 'p-a' });
  });

  it('switches a provider directly from one click, sends the observed current ID and admits only once', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    expect(invokePluginPanel).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alertdialog')).toBe(null);
    await flush();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    let finish: (value: AccountSnapshot) => void = () => {};
    vi.mocked(invokePluginPanel).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    expect(invokePluginPanel).toHaveBeenCalledTimes(3);
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'switch',
      { app: 'codex', id: 'p-a', expectedCurrentId: 'p-b' }, expect.any(String));
    finish({ ...states.codex, currentProviderId: 'p-a',
      providers: states.codex.providers.map((provider) => ({ ...provider, isCurrent: provider.id === 'p-a' })) });
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新列表' }).hasAttribute('disabled')).toBe(false));
  });

  it('blocks another switch after an unknown outcome until a fresh state read succeeds', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(new Error('连接中断，结果不确定'));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await screen.findByText(/切换结果尚未确认/);
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(false));
  });

  it('keeps default-account switching separate from provider selection', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('bob@example.test');
    fireEvent.click(screen.getByRole('button', { name: '设为默认账号' }));
    await waitFor(() => expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'default_account',
      { app: 'codex', id: 'acc-b', expectedCurrentId: 'acc-a' }, expect.any(String)));
  });

  it('adds and removes declared entries from the shared collapsed navigation', () => {
    usePluginPanelStore.setState({ panels: [panel], loaded: true });
    render(<MemoryRouter><ViewEntryRail /></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'CC Switch' }).getAttribute('href')).toBe(panel.path);
  });

  it('hosts a web-app panel in an iframe rooted at the panel asset path', () => {
    const webPanel: PluginPanel = { id: 'console', pluginId: 'local-console', title: 'Local Console',
      renderer: 'web-app/v1', path: '/plugins/local-console/console',
      assetPath: '/panel-assets/local-console/console/', actions: {} };
    usePluginPanelStore.setState({ panels: [webPanel], loaded: true });
    render(<MemoryRouter initialEntries={['/plugins/local-console/console']}>
      <Routes><Route path="/plugins/:pluginId/:panelId" element={<PluginPanelView />} /></Routes>
    </MemoryRouter>);
    const frame = screen.getByTitle('Local Console');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame.getAttribute('src')).toBe('/panel-assets/local-console/console/');
  });

  it('reveals an active plugin route after async catalog load and handles removal', () => {
    const panels = [panel, { ...panel, pluginId: 'second', title: 'Second', path: '/plugins/second/accounts' },
      { ...panel, pluginId: 'third', title: 'Third', path: '/plugins/third/accounts' }];
    render(<MemoryRouter initialEntries={['/plugins/third/accounts']}><ViewEntryGroups isMobile={false} /></MemoryRouter>);
    act(() => usePluginPanelStore.setState({ panels, loaded: true }));
    expect(screen.getByRole('link', { name: 'Third' }).getAttribute('href')).toBe('/plugins/third/accounts');
    expect(screen.getByLabelText('界面入口，第 4 组，共 4 组')).toBeTruthy();
    act(() => usePluginPanelStore.setState({ panels: [] }));
    expect(screen.getByRole('link', { name: 'Chat' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '上一组界面入口' }).hasAttribute('disabled')).toBe(true);
  });
});

describe('non-manual gate', () => {
  const gate = (name: string) => screen.getByRole('switch', { name: `${name}：禁止非手动切换` });

  it('shows one gate per profile and marks a gated row as hidden from agents', async () => {
    states.codex.providers = states.codex.providers.map((provider) => provider.id === 'p-b'
      ? { ...provider, nonManualDisabled: true } : provider);
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    expect(gate('Secondary').getAttribute('aria-checked')).toBe('true');
    expect(gate('Main').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('已对 Agent 隐藏')).toBeTruthy();
    // Manual switching is the explicit user path: the gated row stays enabled.
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(false);
  });

  it('saves the gate with the observed revision, both flags absent, and a request id', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    fireEvent.click(gate('Main'));
    await flushAll();
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'config',
      { app: 'codex', id: 'p-a', nonManualDisabled: true, expectedRevision: 1 }, expect.any(String));
    expect(gate('Main').getAttribute('aria-checked')).toBe('true');
  });

  it('reports a revision conflict, reloads, and never pretends the save worked', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(Object.assign(
      new Error('设置已被其他窗口修改；请刷新后再操作'), { code: 'settings_conflict' }));
    fireEvent.click(gate('Main'));
    await flushAll();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('设置已被其他窗口修改');
    expect(alert.textContent).toContain('已重新读取列表');
    expect(gate('Main').getAttribute('aria-checked')).toBe('false');
    expect(callsOf('list')).toHaveLength(2);
  });

  it('keeps an ordinary save failure explicit and retryable', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(new Error('无法写入插件设置'));
    fireEvent.click(gate('Main'));
    await flushAll();
    expect(screen.getByRole('alert').textContent).toContain('保存设置失败：无法写入插件设置（可重试）');
    expect(gate('Main').getAttribute('aria-checked')).toBe('false');
    expect(callsOf('list')).toHaveLength(1);
  });
});

describe('CLI availability guidance', () => {
  const withCode = (message: string, code: string) => Object.assign(new Error(message), { code });

  it('shows install guidance and a safe repository link when the CLI is missing', async () => {
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('未找到 GUI Fork 的 cc-switch 入口', 'cli_not_found'));
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByRole('heading', { name: '未找到 cc-switch CLI' })).toBeTruthy();
    expect(screen.getByText(/Pan 不捆绑 CLI，也不会自动下载或安装/)).toBeTruthy();
    const link = screen.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ });
    expect(link.getAttribute('href')).toBe('https://github.com/AblazeGHR/cc-switch-pan');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.queryByText(/读取列表失败/)).toBe(null);
  });

  it('distinguishes an incompatible CLI from an ordinary execution failure', async () => {
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('此入口不是兼容的 cc-switch GUI Fork', 'cli_incompatible'));
    const view = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByRole('heading', { name: '当前 cc-switch CLI 不兼容' })).toBeTruthy();
    view.unmount(); cleanup(); resetPluginPanelRuntime();

    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('cc-switch 执行失败（退出码 1）', 'cli_failed'));
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByText(/读取列表失败：cc-switch 执行失败（退出码 1）/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
  });

  it('does not show install guidance after a successful list or an ordinary quota error', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('cc-switch 执行失败（退出码 1）', 'cli_failed'));
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    expect(screen.getByText(/额度刷新失败：cc-switch 执行失败（退出码 1）/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
  });

  it('shows guidance when quota cannot start the CLI after a successful list and clears it after recovery', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('无法启动已配置的 cc-switch CLI', 'cli_unavailable'));
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    expect(screen.getByRole('heading', { name: '无法启动 cc-switch CLI' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ }).getAttribute('href'))
      .toBe('https://github.com/AblazeGHR/cc-switch-pan');

    vi.mocked(invokePluginPanel).mockResolvedValueOnce({
      id: 'p-a', app: 'codex', quota: quotaAt(Date.now(), 80) });
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    expect(screen.queryByText(/额度刷新失败/)).toBe(null);
    expect(screen.getByText('80%')).toBeTruthy();
  });

  it('does not leak a quota CLI issue to another app and clears guidance after recovery', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('无法启动已配置的 cc-switch CLI', 'cli_unavailable'));
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    expect(screen.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Claude' }));
    await flushAll();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    fireEvent.click(screen.getByRole('button', { name: 'Codex' }));
    await flushAll();
    // A successful list proves the CLI is available again: the stale install
    // guidance is gone, while the ordinary quota message remains retryable.
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    expect(screen.getByText(/额度刷新失败：无法启动已配置的 cc-switch CLI/)).toBeTruthy();
  });

  it('shows guidance when a write cannot start the CLI and preserves the unknown-outcome fence', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('无法启动已配置的 cc-switch CLI', 'cli_unavailable'));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await flushAll();
    expect(screen.getByRole('heading', { name: '无法启动 cc-switch CLI' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBeTruthy();
    expect(screen.getByText(/切换结果尚未确认/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await flushAll();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    expect(screen.getByText('无法启动已配置的 cc-switch CLI')).toBeTruthy();
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(false);
  });

  it('shows the same guidance when default-account selection cannot start the CLI', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('bob@example.test');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('无法启动已配置的 cc-switch CLI', 'cli_unavailable'));
    fireEvent.click(screen.getByRole('button', { name: '设为默认账号' }));
    await flushAll();
    expect(screen.getByRole('heading', { name: '无法启动 cc-switch CLI' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBeTruthy();
    expect(screen.getByText(/切换结果尚未确认/)).toBeTruthy();
  });

  it('keeps an ordinary write failure as a normal error without repository guidance', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(withCode('cc-switch 执行失败（退出码 1）', 'cli_failed'));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await flushAll();
    expect(screen.getByText('cc-switch 执行失败（退出码 1）')).toBeTruthy();
    expect(screen.queryByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBe(null);
    expect(screen.getByText(/切换结果尚未确认/)).toBeTruthy();
  });
});

describe('quota relative times on a controlled clock', () => {
  const quota = (overrides: Partial<AccountQuota>): AccountQuota => ({
    status: 'ok', available: true, queriedAt: NOW, message: null, tiers: [], balances: [], ...overrides });

  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime(NOW);
    states = { codex: freshCodex(NOW),
      claude: snapshot('claude', ['c-a'], { 'c-a': quotaAt(NOW - 60_000) }, 'c-a'),
      gemini: snapshot('gemini', ['g-a'], { 'g-a': quotaAt(NOW - 60_000) }, 'g-a') };
  });

  /** Load the panel, then refresh one provider's quota with the given value. */
  const showQuota = async (value: AccountQuota, id = 'p-a') => {
    render(<AccountSwitcher panel={panel} />);
    await flush();
    vi.mocked(invokePluginPanel).mockResolvedValueOnce({ id, app: 'codex', quota: value });
    fireEvent.click(screen.getByRole('button', { name: `刷新 ${id === 'p-a' ? 'Main' : 'Secondary'} 额度` }));
    await flush();
  };

  it('formats durations with hour and minute parts across boundaries', () => {
    expect(formatClockDuration(0)).toBe('0min');
    expect(formatClockDuration(59 * 60_000)).toBe('59min');
    expect(formatClockDuration(60 * 60_000)).toBe('1h 0min');
    expect(formatClockDuration(90 * 60_000)).toBe('1h 30min');
    expect(formatClockDuration(96 * 3600_000)).toBe('96h 0min');
    expect(formatClockDuration(-1)).toBe(null);
    expect(formatClockDuration(Number.NaN)).toBe(null);
  });

  it('counts every reset window down from now and keeps the raw reset time viewable', async () => {
    await showQuota(quota({ queriedAt: NOW - 15 * 60_000, tiers: [
      { name: 'five_hour', usedPercent: 70, resetsAt: new Date(NOW + 90 * 60_000).toISOString() },
      { name: 'weekly', usedPercent: 30, resetsAt: new Date(NOW + 96 * 3600_000).toISOString() },
      { name: 'monthly', usedPercent: 10, resetsAt: new Date(NOW + 59 * 60_000).toISOString() }] }));
    expect(screen.getByText(/距现在剩余 1h 30min/)).toBeTruthy();
    expect(screen.getByText(/距现在剩余 96h 0min/)).toBeTruthy();
    expect(screen.getByText(/距现在剩余 59min/)).toBeTruthy();
    expect(screen.getByText(/距现在已过去 15min/)).toBeTruthy();
    const article = screen.getByRole('heading', { name: 'Main' }).closest('article');
    expect(article).toBeTruthy();
    const raw = within(article as HTMLElement).getAllByText(/重置于 /);
    expect(raw).toHaveLength(3);
    for (const line of raw) expect(line.textContent).toMatch(/重置于 \d/);
  });

  it('advances the relative times from the local clock alone, without new requests', async () => {
    await showQuota(quota({ queriedAt: NOW - 135 * 60_000, tiers: [
      { name: 'five_hour', usedPercent: 70, resetsAt: new Date(NOW + 90 * 60_000).toISOString() }] }));
    expect(screen.getByText(/距现在剩余 1h 30min/)).toBeTruthy();
    expect(screen.getByText(/距现在已过去 2h 15min/)).toBeTruthy();
    const calls = vi.mocked(invokePluginPanel).mock.calls.length;
    tick(90_000);
    expect(screen.getByText(/距现在剩余 1h 28min/)).toBeTruthy();
    expect(screen.getByText(/距现在已过去 2h 16min/)).toBeTruthy();
    expect(vi.mocked(invokePluginPanel).mock.calls.length).toBe(calls);
  });

  it('states expiry exactly at the reset boundary and unknown reset times explicitly', async () => {
    await showQuota(quota({ tiers: [
      { name: 'five_hour', usedPercent: 99, resetsAt: new Date(NOW + 90 * 60_000).toISOString() },
      { name: 'weekly', usedPercent: 5, resetsAt: null },
      { name: 'monthly', usedPercent: 5, resetsAt: 'not-a-timestamp' }] }));
    tick(90 * 60_000);
    expect(screen.getByText(/距现在剩余 0min/)).toBeTruthy();
    tick(30_000);
    expect(screen.getByText(/重置于 .+ · 已到期/)).toBeTruthy();
    expect(screen.getAllByText('重置时间未知')).toHaveLength(2);
  });

  it('measures the query age from the quota queriedAt, not from mount or cache-read time', async () => {
    const cached: AccountSnapshot = structuredClone(states.codex);
    cached.providers = cached.providers.map((provider) => ({ ...provider, quota: provider.id === 'p-a'
      ? quota({ queriedAt: NOW - 3 * 3600_000, tiers: [{ name: 'five_hour', usedPercent: 42, resetsAt: new Date(NOW + 5 * 3600_000).toISOString() }] })
      : quota({ queriedAt: NOW - 25 * 60_000, tiers: [{ name: 'weekly', usedPercent: 8, resetsAt: new Date(NOW + 100 * 3600_000).toISOString() }] }) }));
    // Display-only test: hold the automatic stale reads so the cached
    // timestamps stay on screen while their age is asserted.
    const base = vi.mocked(invokePluginPanel).getMockImplementation();
    vi.mocked(invokePluginPanel).mockImplementation((plugin, panelId, action, args, requestId) =>
      action === 'quota' ? new Promise(() => {}) : base!(plugin, panelId, action, args, requestId));
    vi.mocked(invokePluginPanel).mockResolvedValueOnce(cached);
    render(<AccountSwitcher panel={panel} />);
    await flush();
    expect(callsOf('list')).toHaveLength(1);
    expect(screen.getByText(/距现在已过去 3h 0min/)).toBeTruthy();
    expect(screen.getByText(/距现在已过去 25min/)).toBeTruthy();
    expect(screen.getByText(/距现在剩余 5h 0min/)).toBeTruthy();
    expect(screen.getByText(/距现在剩余 100h 0min/)).toBeTruthy();
  });

  it('keeps the last quota visible and stays retryable when a refresh fails', async () => {
    await showQuota(quota({ queriedAt: NOW - 45 * 60_000, tiers: [
      { name: 'five_hour', usedPercent: 70, resetsAt: new Date(NOW + 90 * 60_000).toISOString() }] }));
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(new Error('额度查询失败'));
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flush();
    expect(screen.getByText(/额度查询失败/)).toBeTruthy();
    expect(screen.getByText(/距现在剩余 1h 30min/)).toBeTruthy();
    expect(screen.getByText(/距现在已过去 45min/)).toBeTruthy();
    expect(screen.queryByText(/切换结果尚未确认/)).toBe(null);
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(false);
  });

  it('states an unknown query time instead of inventing an elapsed duration', async () => {
    await showQuota(quota({ queriedAt: null, tiers: [{ name: 'five_hour', usedPercent: 70, resetsAt: null }] }));
    expect(screen.getByText(/查询时间：未知 · ok/)).toBeTruthy();
    expect(screen.getByText('重置时间未知')).toBeTruthy();
  });
});

describe('automatic quota refresh on a controlled clock', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime(NOW);
    states = { codex: freshCodex(NOW),
      claude: snapshot('claude', ['c-a'], { 'c-a': quotaAt(NOW - 60_000) }, 'c-a'),
      gemini: snapshot('gemini', ['g-a'], { 'g-a': quotaAt(NOW - 60_000) }, 'g-a') };
  });

  it('queries exactly when the previous query is strictly older than five minutes', async () => {
    const cases: [number, number][] = [[299_999, 0], [300_000, 0], [300_001, 1], [-60_000, 0]];
    for (const [age, expected] of cases) {
      cleanup(); resetPluginPanelRuntime();
      vi.mocked(invokePluginPanel).mockClear();
      states.codex = snapshot('codex', ['p-a'], { 'p-a': quotaAt(NOW - age) });
      render(<AccountSwitcher panel={panel} />);
      await flushAll();
      expect(callsOf('quota')).toHaveLength(expected);
    }
  });

  it('queries unknown times, treats future clock skew as fresh, and skips fresh siblings', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b', 'p-c'], {
      'p-a': null, 'p-b': quotaAt(NOW + 60_000), 'p-c': quotaAt(NOW - 300_001) });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(quotaIds()).toEqual(['p-a', 'p-c']);
    expect(callsOf('switch')).toHaveLength(0);
  });

  it('shows the cached snapshot immediately on re-entry and does not re-query fresh quotas', async () => {
    states.codex = snapshot('codex', ['p-a'], { 'p-a': quotaAt(NOW - 10 * 60_000, 40) });
    const view = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByText('40%')).toBeTruthy();
    expect(callsOf('quota')).toHaveLength(1);
    view.unmount(); cleanup();
    vi.mocked(invokePluginPanel).mockClear();
    render(<AccountSwitcher panel={panel} />);
    // The cached value is rendered by the first pass, before the list responds.
    expect(screen.getByText('40%')).toBeTruthy();
    expect(screen.queryByText('正在读取账号信息…')).toBe(null);
    expect(callsOf('list')).toHaveLength(1);
    await flushAll();
    expect(screen.getByText('40%')).toBeTruthy();
    expect(callsOf('quota')).toHaveLength(0);
  });

  it('deduplicates mount, StrictMode replay and the same quota request', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b'], { 'p-a': null, 'p-b': null });
    render(<StrictMode><AccountSwitcher panel={panel} /></StrictMode>);
    await flushAll();
    expect(callsOf('list')).toHaveLength(1);
    expect(quotaIds()).toEqual(['p-a', 'p-b']);
    expect(callsOf('switch')).toHaveLength(0);
  });

  it('isolates cached data per app and ignores a late response from the previous app', async () => {
    states.codex = snapshot('codex', ['p-a'], { 'p-a': null });
    states.claude = snapshot('claude', ['c-a'], { 'c-a': null }, 'c-a');
    let releaseCodex: (value: unknown) => void = () => {};
    const base = vi.mocked(invokePluginPanel).getMockImplementation();
    vi.mocked(invokePluginPanel).mockImplementation((plugin, panelId, action, args, requestId) => {
      if (action === 'quota' && args.app === 'codex' && args.id === 'p-a') {
        return new Promise((resolve) => { releaseCodex = resolve; });
      }
      return base!(plugin, panelId, action, args, requestId);
    });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    fireEvent.click(screen.getByRole('button', { name: 'Claude' }));
    await flushAll();
    expect(screen.getByRole('heading', { name: 'Claude Main' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Main' })).toBe(null);
    const provider = states.codex.providers.find((item) => item.id === 'p-a');
    if (provider) provider.quota = quotaAt(NOW, 66);
    releaseCodex({ id: 'p-a', app: 'codex', quota: quotaAt(NOW, 66) });
    await flushAll();
    expect(screen.getByRole('heading', { name: 'Claude Main' })).toBeTruthy();
    expect(screen.queryByText('66%')).toBe(null);
    fireEvent.click(screen.getByRole('button', { name: 'Codex' }));
    await flushAll();
    expect(screen.getByText('66%')).toBeTruthy();
  });

  it('does not let a late list response roll back a newer provider switch', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b'], { 'p-a': quotaAt(NOW - 60_000), 'p-b': quotaAt(NOW - 60_000) });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    const stale = structuredClone(states.codex);
    let releaseList: (value: AccountSnapshot) => void = () => {};
    vi.mocked(invokePluginPanel).mockImplementationOnce(() => new Promise<AccountSnapshot>((resolve) => { releaseList = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    releaseList(stale);
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    expect(callsOf('switch')).toHaveLength(1);
  });

  it('keeps the old quota, reports a failure once and retries only on demand', async () => {
    states.codex = snapshot('codex', ['p-a'], { 'p-a': quotaAt(NOW - 20 * 60_000, 70) });
    let attempts = 0;
    const base = vi.mocked(invokePluginPanel).getMockImplementation();
    vi.mocked(invokePluginPanel).mockImplementation((plugin, panelId, action, args, requestId) => {
      if (action === 'quota') {
        attempts += 1;
        if (attempts === 1) return Promise.reject(new Error('额度查询失败'));
        const provider = states.codex.providers.find((item) => item.id === 'p-a');
        if (provider) provider.quota = quotaAt(NOW, 80);
        return Promise.resolve({ id: 'p-a', app: 'codex', quota: quotaAt(NOW, 80) });
      }
      return base!(plugin, panelId, action, args, requestId);
    });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByText('70%')).toBeTruthy();
    expect(screen.getByText(/额度刷新失败：额度查询失败（可重试）/)).toBeTruthy();
    expect(callsOf('quota')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    expect(screen.getByText('80%')).toBeTruthy();
    expect(screen.queryByText(/额度刷新失败/)).toBe(null);
    expect(callsOf('quota')).toHaveLength(2);
  });

  it('limits concurrent automatic quota reads and lets one failure finish the rest', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b', 'p-c', 'p-d', 'p-e'],
      Object.fromEntries(['p-a', 'p-b', 'p-c', 'p-d', 'p-e'].map((id) => [id, null])));
    let active = 0;
    let peak = 0;
    const started: string[] = [];
    const pending = new Map<string, () => void>();
    const base = vi.mocked(invokePluginPanel).getMockImplementation();
    vi.mocked(invokePluginPanel).mockImplementation((plugin, panelId, action, args, requestId) => {
      if (action === 'quota') {
        const id = String(args.id);
        started.push(id);
        active += 1; peak = Math.max(peak, active);
        return new Promise((resolve, reject) => {
          pending.set(id, () => {
            active -= 1;
            if (id === 'p-b') reject(new Error('单配置失败'));
            else resolve({ id, app: 'codex', quota: quotaAt(NOW, 10) });
          });
        });
      }
      return base!(plugin, panelId, action, args, requestId);
    });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(started).toHaveLength(2);
    expect(peak).toBe(2);
    pending.get('p-a')?.();
    await flushAll();
    expect(started).toHaveLength(3);
    expect(peak).toBe(2);
    pending.get('p-b')?.();
    pending.get('p-c')?.();
    await flushAll();
    expect(started).toHaveLength(5);
    pending.get('p-d')?.();
    pending.get('p-e')?.();
    await flushAll();
    expect(peak).toBe(2);
    expect(callsOf('switch')).toHaveLength(0);
    expect(screen.getByText(/额度刷新失败：单配置失败/)).toBeTruthy();
  });

  it('does not run an automatic quota pass after a manual list refresh', async () => {
    states.codex = snapshot('codex', ['p-a'], { 'p-a': quotaAt(NOW - 60_000) });
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    states.codex = snapshot('codex', ['p-a'], { 'p-a': quotaAt(NOW - 30 * 60_000) });
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await flushAll();
    expect(callsOf('quota')).toHaveLength(0);
  });

  it('keeps cached data and surfaces the error when a background list refresh fails', async () => {
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    vi.mocked(invokePluginPanel).mockRejectedValueOnce(new Error('列表读取失败'));
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await flushAll();
    expect(screen.getByText('Main')).toBeTruthy();
    expect(screen.getByText(/读取列表失败：列表读取失败（已保留上次数据，可重试）/)).toBeTruthy();
  });

  it('does not write an old mount quota response back over a switch made by a newer mount', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b'],
      { 'p-a': quotaAt(NOW - 60_000, 40), 'p-b': quotaAt(NOW - 60_000) });
    let releaseQuota: (value: unknown) => void = () => {};
    const base = vi.mocked(invokePluginPanel).getMockImplementation();
    vi.mocked(invokePluginPanel).mockImplementation((plugin, panelId, action, args, requestId) => {
      if (action === 'quota' && args.id === 'p-a') return new Promise((resolve) => { releaseQuota = resolve; });
      return base!(plugin, panelId, action, args, requestId);
    });
    const first = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    // A manual read keeps a quota promise in flight while the panel unmounts.
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await flushAll();
    first.unmount();

    // The newer mount has a fresh cached quota, so it does not await that
    // promise; it switches to p-b and writes the shared cache.
    const second = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();

    // The old mount's quota finally lands: it may only patch the quota onto
    // the latest shared snapshot, never restore its own old current ID.
    const provider = states.codex.providers.find((item) => item.id === 'p-a');
    if (provider) provider.quota = quotaAt(NOW, 77);
    releaseQuota({ id: 'p-a', app: 'codex', quota: quotaAt(NOW, 77) });
    await flushAll();
    second.unmount();

    render(<AccountSwitcher panel={panel} />);
    // Read the cache before the entry list resolves: it must already be p-b.
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    expect(screen.getByText('77%')).toBeTruthy();
  });

  it('does not let a shared list started before a switch overwrite it on later re-entry', async () => {
    states.codex = snapshot('codex', ['p-a', 'p-b'],
      { 'p-a': quotaAt(NOW - 60_000), 'p-b': quotaAt(NOW - 60_000) });
    const seed = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    const stale = structuredClone(states.codex);
    let releaseList: (value: AccountSnapshot) => void = () => {};
    vi.mocked(invokePluginPanel).mockImplementationOnce(() =>
      new Promise<AccountSnapshot>((resolve) => { releaseList = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    seed.unmount();

    // Two more mounts share the same pending list; the second one switches.
    const middle = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    middle.unmount();
    const switching = render(<AccountSwitcher panel={panel} />);
    await flushAll();
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    switching.unmount();

    // Re-entry must not adopt the pre-switch list; a fresh list is requested.
    const latest = render(<AccountSwitcher panel={panel} />);
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    releaseList(stale);
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
    expect(callsOf('switch')).toHaveLength(1);
    latest.unmount();
    render(<AccountSwitcher panel={panel} />);
    await flushAll();
    expect(screen.getByText('当前：Secondary')).toBeTruthy();
  });
});
