// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { NativeTerminal } from './NativeTerminal';

const terminal = vi.hoisted(() => ({ created: 0, disposed: 0 }));
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  constructor() { terminal.created += 1; }
  loadAddon() {} open() {} focus() {} reset() {}
  onData() { return { dispose() {} }; }
  onResize() { return { dispose() {} }; }
  dispose() { terminal.disposed += 1; }
} }));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@/services/nativeTerminalPolling', () => ({ createNativeTerminalPoller: () => ({ wake() {}, pause() {}, stop() {} }) }));
afterEach(() => vi.unstubAllGlobals());

it('recreates the terminal and replay cursor after restart with the same public Worker id', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  terminal.created = terminal.disposed = 0;
  const before = { workerId: 'same-id', workerGeneration: 4, mode: 'native' as const, generation: 7, error: null };
  const view = render(<NativeTerminal sessionId="s" state={before} />);
  expect(terminal.created).toBe(1);
  act(() => view.rerender(<NativeTerminal sessionId="s" state={{ ...before, workerGeneration: 5, mode: 'pan', generation: 0 }} />));
  expect(terminal.disposed).toBe(1);
  expect(terminal.created).toBe(2);
  act(() => view.rerender(<NativeTerminal sessionId="s" state={{ ...before, workerGeneration: 5, generation: 1 }} />));
  expect(terminal.created).toBe(2); // Ownership switch alone preserves screen.
  view.unmount();
  expect(terminal.disposed).toBe(2);
});
