// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePlatformStore } from '@/stores/platformStore';
import TerminalPanel from './TerminalPanel';

// The panel's workspace half owns xterm and the terminal transports; the gate
// test must not depend on either.
vi.mock('@xterm/xterm', () => ({ Terminal: class {} }));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class {} }));
vi.mock('@/services/terminal', () => ({ terminalRequest: vi.fn(async () => []) }));
vi.mock('@/services/terminalWs', () => ({ TerminalStream: class {} }));
vi.mock('@/services/terminalKeyboard', () => ({ terminalKeyHandler: vi.fn() }));

describe('TerminalPanel platform gate', () => {
  beforeEach(() => {
    usePlatformStore.setState({ platform: null, capabilities: null });
  });
  afterEach(cleanup);

  it('explains the limitation instead of rendering an empty terminal shell', () => {
    usePlatformStore.setState({ platform: 'darwin', capabilities: { 'terminal.native': false } });
    render(<TerminalPanel />);
    expect(screen.getByTestId('terminal-unsupported')).toBeTruthy();
    expect(screen.getByTestId('terminal-unsupported').textContent).toContain('macOS');
    expect(screen.getByTestId('terminal-unsupported').textContent).toContain('暂不支持');
    expect(screen.queryByTestId('terminal-screen')).toBeNull();
    expect(screen.queryByLabelText('工作目录')).toBeNull();
  });

  it('renders the terminal workspace while the platform is unknown', () => {
    render(<TerminalPanel />);
    expect(screen.queryByTestId('terminal-unsupported')).toBeNull();
    expect(screen.getByLabelText('全局终端')).toBeTruthy();
    expect(screen.getByTestId('terminal-screen')).toBeTruthy();
  });

  it('renders the terminal workspace when the server enables the capability', () => {
    usePlatformStore.setState({ platform: 'linux', capabilities: { 'terminal.native': true } });
    render(<TerminalPanel />);
    expect(screen.queryByTestId('terminal-unsupported')).toBeNull();
    expect(screen.getByTestId('terminal-screen')).toBeTruthy();
  });
});
