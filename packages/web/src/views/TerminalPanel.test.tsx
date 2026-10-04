// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TerminalPanel from './TerminalPanel';

const fixtures = vi.hoisted(() => ({ request: vi.fn(), closeFails: false, listStatus: 'running', binds: 0,
  changed: (_state: object) => {} }));
vi.mock('@/services/terminal', () => ({ terminalRequest: fixtures.request }));
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  rows = 24; cols = 80;
  loadAddon() {} open() {} attachCustomKeyEventHandler() {} dispose() {}
  onData() { return { dispose() {} }; }
} }));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@/services/terminalWs', () => ({ TerminalStream: class {
  constructor(_id: string, _screen: unknown, private changed: (state: object) => void) { fixtures.changed = changed; }
  bind() { fixtures.binds++; queueMicrotask(() => this.changed({ connected: true, control: false, recovering: false, message: 'ready' })); }
  claim() { this.changed({ connected: true, control: true, recovering: false, message: 'claimed' }); }
  resize() {} dispose() {}
} }));

beforeEach(() => {
  fixtures.closeFails = false;
  fixtures.listStatus = 'running';
  fixtures.binds = 0;
  fixtures.request.mockReset().mockImplementation(async (suffix = '', body?: object) => {
    if (suffix.endsWith('/close')) {
      if (fixtures.closeFails) throw new Error('cleanup-unconfirmed');
      return { terminal_id: 'term_test', status: 'exited' };
    }
    if (body) return { terminal_id: 'term_test', status: 'running' };
    return [{ terminal_id: 'term_test', status: fixtures.listStatus }];
  });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('WebSocket', class {});
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function controlledPanel() {
  render(<TerminalPanel />);
  await screen.findByRole('option', { name: 'term_test · running' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  await waitFor(() => expect((screen.getByText('取得输入控制权') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText('取得输入控制权'));
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('控制模式'));
}

it('clears stale control/display state after confirmed explicit close', async () => {
  await controlledPanel();
  fireEvent.click(screen.getByText('终止终端'));
  await waitFor(() => expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe(''));
  expect(screen.getByRole('status').textContent).toBe('只观察 · 请选择终端');
  expect((screen.getByText('释放控制权') as HTMLButtonElement).disabled).toBe(true);
});

it('retains the selected terminal for retry when close is unconfirmed', async () => {
  fixtures.closeFails = true;
  await controlledPanel();
  fireEvent.click(screen.getByText('终止终端'));
  await screen.findByRole('alert');
  expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe('term_test');
  expect(screen.getByRole('alert').textContent).toContain('cleanup-unconfirmed');
  expect((screen.getByText('终止终端') as HTMLButtonElement).disabled).toBe(false);
});

it('refreshes the authoritative record after natural end without erasing the tail view', async () => {
  await controlledPanel();
  fixtures.listStatus = 'exited';
  act(() => fixtures.changed({ connected: false, control: false, recovering: false,
    terminalStatus: 'exited', message: '终端状态：exited（退出码 7）' }));
  await screen.findByRole('option', { name: 'term_test · exited' });
  expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe('term_test');
  expect(screen.getByRole('status').textContent).toContain('退出码 7');
  expect((screen.getByText('持久脱离') as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByText('重连') as HTMLButtonElement).disabled).toBe(true);
});

it('does not try to reconnect a selected exited record or promise archived output', async () => {
  fixtures.listStatus = 'exited';
  render(<TerminalPanel />);
  await screen.findByRole('option', { name: 'term_test · exited' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('历史屏幕未持久化'));
  expect(fixtures.binds).toBe(0);
  expect((screen.getByText('重连') as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByText('持久脱离') as HTMLButtonElement).disabled).toBe(true);
});
