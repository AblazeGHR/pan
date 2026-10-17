// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AccountSwitcher } from './PluginPanelView';
import { invokePluginPanel } from '@/services/api';
import { ViewEntryRail } from '@/components/layout/ViewEntryRail';
import { ViewEntryGroups } from '@/components/layout/ViewEntryGroups';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import type { AccountSnapshot, PluginPanel } from '@/types/pluginPanels';

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
afterEach(() => { cleanup(); usePluginPanelStore.setState({ panels: [], loaded: false, loading: false, error: null }); });

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
    vi.mocked(invokePluginPanel).mockResolvedValueOnce({ id: 'p-a', app: 'codex', quota: {
      status: 'ok', available: true, queriedAt: 1792254007000, message: null,
      tiers: [{ name: '5小时', usedPercent: 70, resetsAt: '2026-10-19T12:00:00Z' }], balances: [],
    } });
    fireEvent.click(screen.getByRole('button', { name: '刷新 Main 额度' }));
    await screen.findByText('70%');
    expect(invokePluginPanel).toHaveBeenLastCalledWith('cc-switch-bridge', 'accounts', 'quota', { app: 'codex', id: 'p-a' }, undefined);
    expect(screen.getByText(/缓存查询时间/)).toBeTruthy();
  });

  it('confirms a global switch, sends the observed current ID and admits only once', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('Secondary');
    fireEvent.click(screen.getByRole('button', { name: '启用配置' }));
    expect(invokePluginPanel).toHaveBeenCalledTimes(1);
    let finish: (value: AccountSnapshot) => void = () => {};
    vi.mocked(invokePluginPanel).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const confirm = screen.getByRole('button', { name: '确认切换' });
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(invokePluginPanel).toHaveBeenCalledTimes(2);
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
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }));
    await screen.findByText(/切换结果尚未确认/);
    expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '启用配置' }).hasAttribute('disabled')).toBe(false));
  });

  it('keeps default-account switching separate from provider selection', async () => {
    render(<AccountSwitcher panel={panel} />);
    await screen.findByText('bob@example.test');
    fireEvent.click(screen.getByRole('button', { name: '设为默认账号' }));
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }));
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
