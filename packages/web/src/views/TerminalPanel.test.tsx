// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TerminalPanel from './TerminalPanel';

const fixtures = vi.hoisted(() => ({ request: vi.fn(), closeFails: false, removeFails: false, removed: false, archived: false, pending: false, scope: {} as { workspace_id?: string | null; session_id?: string | null }, listStatus: 'running', binds: 0,
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
  fixtures.removeFails = false;
  fixtures.removed = false;
  fixtures.archived = false;
  fixtures.pending = false;
  fixtures.scope = {};
  fixtures.listStatus = 'running';
  fixtures.binds = 0;
  fixtures.request.mockReset().mockImplementation(async (suffix = '', body?: object) => {
    if (suffix.endsWith('/archive')) {
      fixtures.archived = (body as { archived: boolean }).archived;
      return { terminal_id: 'term_test', status: fixtures.listStatus, archived: fixtures.archived };
    }
    if (suffix.endsWith('/scope')) {
      const binding = body as { workspace_id?: string; session_id?: string };
      fixtures.scope = binding.session_id ? { workspace_id: 'ws_session', session_id: binding.session_id } : binding;
      return { terminal_id: 'term_test', status: fixtures.listStatus, scope: fixtures.scope };
    }
    if (suffix.endsWith('/remove')) {
      if (fixtures.removeFails) throw new Error('cleanup-unconfirmed');
      fixtures.removed = true;
      return { terminal_id: 'term_test', removed: true };
    }
    if (suffix.endsWith('/close')) {
      if (fixtures.closeFails) throw new Error('cleanup-unconfirmed');
      return { terminal_id: 'term_test', status: 'exited' };
    }
    if (body) return { terminal_id: 'term_test', status: 'running' };
    return fixtures.removed ? [] : [{ terminal_id: 'term_test', status: fixtures.listStatus, archived: fixtures.archived, cleanup_pending: fixtures.pending, scope: fixtures.scope }];
  });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('WebSocket', class {});
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('archives an unconfirmed externally exited record without stopping or deleting it and can restore it', async () => {
  fixtures.listStatus = 'exited';
  fixtures.pending = true;
  render(<TerminalPanel />);
  await screen.findByRole('option', { name: 'term_test · exited' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  expect(screen.getByText(/剩余资源清理尚未确认/)).toBeTruthy();
  fireEvent.click(screen.getByText('归档终端'));
  await waitFor(() => expect(screen.queryByRole('option', { name: 'term_test · exited' })).toBeNull());
  expect(fixtures.request.mock.calls.some(([url]) => /\/(close|remove)$/.test(url))).toBe(false);
  fireEvent.click(screen.getByLabelText('已归档'));
  await screen.findByRole('option', { name: 'term_test · exited' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  fireEvent.click(screen.getByText('恢复到列表'));
  await waitFor(() => expect(fixtures.archived).toBe(false));
});

it('inherits the Session workspace and manual workspace binding clears the Session', async () => {
  await controlledPanel();
  fireEvent.change(screen.getByLabelText('关联 Session ID'), { target: { value: 'ses_bound' } });
  fireEvent.click(screen.getByText('应用绑定'));
  await waitFor(() => expect((screen.getByLabelText('关联工作区 ID') as HTMLInputElement).value).toBe('ws_session'));
  expect((screen.getByLabelText('关联 Session ID') as HTMLInputElement).value).toBe('ses_bound');
  fireEvent.change(screen.getByLabelText('关联工作区 ID'), { target: { value: 'ws_manual' } });
  expect((screen.getByLabelText('关联 Session ID') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByText('应用绑定'));
  await waitFor(() => expect(fixtures.scope).toEqual({ workspace_id: 'ws_manual', session_id: null }));
});

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

it('terminates before deleting a running terminal and clears the selection', async () => {
  await controlledPanel();
  fireEvent.click(screen.getByText('删除终端'));
  await waitFor(() => expect(screen.queryByRole('option', { name: 'term_test · running' })).toBeNull());
  const actions = fixtures.request.mock.calls.map(([path]) => path).filter((path: string) => path);
  expect(actions).toEqual(['/term_test/close', '/term_test/remove']);
  expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe('');
  expect(screen.getByRole('status').textContent).toContain('请选择终端');
});

it('deletes an exited record without issuing a stop', async () => {
  fixtures.listStatus = 'exited';
  render(<TerminalPanel />);
  await screen.findByRole('option', { name: 'term_test · exited' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  fireEvent.click(screen.getByText('删除终端'));
  await waitFor(() => expect(fixtures.removed).toBe(true));
  expect(fixtures.request.mock.calls.map(([path]) => path).filter(Boolean)).toEqual(['/term_test/remove']);
});

it('does not delete when termination is unconfirmed', async () => {
  fixtures.closeFails = true;
  await controlledPanel();
  fireEvent.click(screen.getByText('删除终端'));
  await screen.findByRole('alert');
  expect(fixtures.request.mock.calls.some(([path]) => path?.endsWith('/remove'))).toBe(false);
  expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe('term_test');
});

it('retains an exited record when removal fails and honours cancellation', async () => {
  fixtures.listStatus = 'exited';
  fixtures.removeFails = true;
  render(<TerminalPanel />);
  await screen.findByRole('option', { name: 'term_test · exited' });
  fireEvent.change(screen.getByLabelText('选择终端'), { target: { value: 'term_test' } });
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  fireEvent.click(screen.getByText('删除终端'));
  expect(fixtures.request.mock.calls.some(([path]) => path?.endsWith('/remove'))).toBe(false);
  fireEvent.click(screen.getByText('删除终端'));
  await screen.findByRole('alert');
  expect(screen.getByRole('option', { name: 'term_test · exited' })).toBeTruthy();
  expect((screen.getByLabelText('选择终端') as HTMLSelectElement).value).toBe('term_test');
});
