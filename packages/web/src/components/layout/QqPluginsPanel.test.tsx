// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QqPluginsPanel } from './QqPluginsPanel';

const { fetchMock, selectMock, autostartMock, accountsMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  selectMock: vi.fn(),
  autostartMock: vi.fn(),
  accountsMock: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchQqGatewayPlugins: fetchMock,
  selectQqGatewayPlugin: selectMock,
  setQqGatewayPluginAutostart: autostartMock,
  setQqGatewayPluginToken: vi.fn(),
  setSnowLumaAccounts: accountsMock,
  startQqGatewayPlugin: vi.fn(),
  stopQqGatewayPlugin: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('selects a registered QQ gateway and shows the restart requirement', async () => {
  fetchMock.mockResolvedValue({
    selected: 'napcat',
    manifestPath: 'D:/project/Pan-main/data/qq_plugins/manifest.json',
    plugins: [
      { id: 'napcat', name: 'NapCat', channel: 'napcat', wsUrl: 'ws://127.0.0.1:3001',
        cwd: 'D:/project/NapCat', command: ['D:/project/NapCat/gateway.exe'],
        autoStart: false, running: false, endpointReachable: false, installed: true },
      { id: 'snowluma', name: 'SnowLuma', channel: 'snowluma', wsUrl: 'ws://127.0.0.1:3003',
        cwd: 'D:/project/SnowLuma', command: ['D:/project/SnowLuma/node.exe'],
        autoStart: false, running: false, endpointReachable: false, installed: true },
    ],
  });
  selectMock.mockResolvedValue({ ok: true, requiresPanRestart: true });
  render(<QqPluginsPanel />);
  await screen.findByText('SnowLuma');
  const selectButtons = screen.getAllByRole('button', { name: 'Select' });
  fireEvent.click(selectButtons[1]!);
  await waitFor(() => expect(selectMock).toHaveBeenCalledWith('snowluma'));
  await screen.findByText(/Restart Pan to reconnect/);
});

it('saves an arbitrary SnowLuma account list', async () => {
  fetchMock.mockResolvedValue({
    selected: 'snowluma', manifestPath: 'D:/project/Pan/data/qq_plugins/manifest.json',
    snowlumaAccounts: [{ bot_uin: '111111', ws_url: 'ws://127.0.0.1:3003' }],
    plugins: [{ id: 'snowluma', name: 'SnowLuma', channel: 'snowluma', wsUrl: 'ws://127.0.0.1:3003',
      cwd: 'D:/project/SnowLuma', command: ['D:/project/SnowLuma/node.exe'],
      autoStart: true, running: true, endpointReachable: true, installed: true, tokenConfigured: true }],
  });
  accountsMock.mockResolvedValue({ ok: true, requiresPanRestart: true });
  render(<QqPluginsPanel />);
  await screen.findByDisplayValue('111111');
  fireEvent.click(screen.getByRole('button', { name: 'Add account' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'QQ number 2' }), { target: { value: '222222' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save accounts' }));
  await waitFor(() => expect(accountsMock).toHaveBeenCalledWith([
    { bot_uin: '111111', ws_url: 'ws://127.0.0.1:3003' },
    { bot_uin: '222222', ws_url: 'ws://127.0.0.1:3004' },
  ]));
});
