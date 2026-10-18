// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AccountSwitcher, formatClockDuration } from './PluginPanelView';
import { invokePluginPanel } from '@/services/api';
import { ViewEntryRail } from '@/components/layout/ViewEntryRail';
import { ViewEntryGroups } from '@/components/layout/ViewEntryGroups';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import type { AccountQuota, AccountSnapshot, PluginPanel } from '@/types/pluginPanels';

vi.mock('@/services/api', () => ({ invokePluginPanel: vi.fn(), fetchPluginPanels: vi.fn() }));
const panel: PluginPanel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch',
  renderer: 'account-switcher/v1', path: '/plugins/cc-switch-bridge/accounts', actions: {
    list: { capability: 'cc-switch-bridge.list', effect: 'read' },
    quota: { capability: 'cc-switch-bridge.quota', effect: 'read' },
    switch: { capability: 'cc-switch-bridge.switch', effect: 'write_external' },
    default_account: { capability: 'cc-switch-bridge.default_account', effect: 'write_external' },
  } };
const state: AccountSnapshot = { app: 'codex', currentProviderId: 'p-a', observedAt: 1792254007000,
  providers: [{ id: 'p-a', name: 'Main', origin: 'https://api.example.test', isCurrent: true, quota: null },
    { id: 'p-b', name: 'Secondary', origin: null, isCurrent: false, quota: null }],
  accounts: [{ id: 'acc-a', login: 'alice@example.test', isDefault: true },
    { id: 'acc-b', login: 'bob@example.test', isDefault: false }], accountError: null };

beforeEach(() => { vi.mocked(invokePluginPanel).mockReset().mockResolvedValue(structuredClone(state)); });
afterEach(() => { cleanup(); vi.useRealTimers(); usePluginPanelStore.setState({ panels: [], loaded: false, loading: false, error: null }); });

/** Flush the mocked invoke promise chains without waitFor (which needs real timers). */
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
/** Advance the controlled clock far enough for the 30s local tick to fire. */
const tick = (ms: number) => { act(() => { vi.advanceTimersByTime(ms); }); };

describe('plugin account panel', () => {
  it('shows reauthentication requirements and prevents selecting an incomplete account', async () => {
    const incomplete = structuredClone(state);
    incomplete.accounts = incomplete.accounts.map((account) => ({ ...account, reauthRequired: !account.isDefault }));
    vi.mocked(invokePluginPanel).mockResolvedValueOnce(incomplete);
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('需要重新登录');
    expect(screen.getByRole('button', { name: '设为默认账号' }).hasAttribute('disabled')).toBe(true);
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
  });

  it('shows accounts and defers quota queries until the explicit refresh button', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('alice@example.test');
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('额度未查询')).toHaveLength(2);
    const queriedAt = Date.now() - (2 * 3600_000 + 15 * 60_000 + 30_000);
    vi.mocked(invokePluginPanel).mockResolvedValueOnce({ id: 'p-a', app: 'codex', quota: {
      status: 'ok', available: true, queriedAt, message: null,
      tiers: [{ name: '5小时', usedPercent: 70, resetsAt: new Date(Date.now() + 90 * 60_000 + 30_000).toISOString() }], balances: [],
    } });
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await screen.findByText('70%');
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'quota', { app: 'codex', id: 'p-a' }, undefined);
    expect(screen.getByText(/距现在已过去 2h 15min/)).toBeTruthy();
    expect(screen.getByText(/重置于 .+ · 距现在剩余 1h 30min/)).toBeTruthy();
  });

  it('switches a provider directly from one click, sends the observed current ID and admits only once', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    expect(invokePluginPanel).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alertdialog')).toBe(null);
    await flush();
    let finish: (value: AccountSnapshot) => void = () => {};
    vi.mocked(invokePluginPanel).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    expect(invokePluginPanel).toHaveBeenCalledTimes(3);
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'switch',
      { app: 'codex', id: 'p-b', expectedCurrentId: 'p-a' }, expect.any(String));
    finish({ ...state, currentProviderId: 'p-b', providers: state.providers.map((p) => ({ ...p, isCurrent: p.id === 'p-b' })) });
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

  it('reveals an active plugin route after async catalog load and handles removal', () => {
    const panels = [panel, { ...panel, pluginId: 'second', title: 'Second', path: '/plugins/second/accounts' },
      { ...panel, pluginId: 'third', title: 'Third', path: '/plugins/third/accounts' }];
    render(<MemoryRouter initialEntries={['/plugins/third/accounts']}><ViewEntryGroups isMobile={false} /></MemoryRouter>);
    act(() => usePluginPanelStore.setState({ panels, loaded: true }));
    expect(screen.getByRole('link', { name: 'Third' }).getAttribute('href')).toBe('/plugins/third/accounts');
    expect(screen.getByLabelText('界面入口，第 3 组，共 3 组')).toBeTruthy();
    act(() => usePluginPanelStore.setState({ panels: [] }));
    expect(screen.getByRole('link', { name: 'Chat' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '上一组界面入口' }).hasAttribute('disabled')).toBe(true);
  });
});

describe('quota relative times on a controlled clock', () => {
  const NOW = new Date('2026-10-18T12:00:00Z').getTime();
  const quota = (overrides: Partial<AccountQuota>): AccountQuota => ({
    status: 'ok', available: true, queriedAt: NOW, message: null, tiers: [], balances: [], ...overrides });

  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });

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
    const raw = screen.getAllByText(/重置于 /);
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
    const cached: AccountSnapshot = structuredClone(state);
    cached.providers = cached.providers.map((provider) => ({ ...provider, quota: provider.id === 'p-a'
      ? quota({ queriedAt: NOW - 3 * 3600_000, tiers: [{ name: 'five_hour', usedPercent: 42, resetsAt: new Date(NOW + 5 * 3600_000).toISOString() }] })
      : quota({ queriedAt: NOW - 25 * 60_000, tiers: [{ name: 'weekly', usedPercent: 8, resetsAt: new Date(NOW + 100 * 3600_000).toISOString() }] }) }));
    vi.mocked(invokePluginPanel).mockResolvedValueOnce(cached);
    render(<AccountSwitcher panel={panel} />);
    await flush();
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
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
    expect(screen.getByText('额度查询失败')).toBeTruthy();
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
