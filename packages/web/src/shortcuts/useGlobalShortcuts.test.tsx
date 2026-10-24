// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { registerLockedEnqueueHandler } from './composerBridge';
import {
  setShortcutRecording,
  shouldIgnoreShortcutEvent,
  useGlobalShortcuts,
} from './useGlobalShortcuts';

function Harness({ navigate }: { navigate: (to: string) => void }) {
  useGlobalShortcuts(navigate);
  return null;
}

const panel = {
  pluginId: 'alpha',
  id: 'one',
  title: 'Alpha One',
  renderer: 'account-switcher/v1' as const,
  path: '/plugins/alpha/one',
  actions: {},
};

function press(init: KeyboardEventInit, target: Element | Window | Document = document): void {
  fireEvent.keyDown(target, init);
}

beforeEach(() => {
  useUIStore.setState({ sidebarCollapsed: false });
  useAppSettingsStore.setState({ shortcuts: {}, loaded: true });
  useSessionStore.setState({
    currentSessionId: 's1',
    sessions: [{ id: 's1', name: 's1' }] as never,
  });
  usePluginPanelStore.setState({ panels: [], loading: false, loaded: true, error: null });
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  setShortcutRecording(false);
  document.body.innerHTML = '';
});

describe('central shortcut dispatcher', () => {
  it('fires exactly one action for the default sidebar binding', () => {
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    press({ key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    press({ key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it('ignores input, textarea, contenteditable, Monaco and terminal targets', () => {
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    const input = document.createElement('input');
    const textarea = document.createElement('textarea');
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    const monaco = document.createElement('div');
    monaco.className = 'monaco-editor';
    const monacoInput = document.createElement('textarea');
    monaco.appendChild(monacoInput);
    const xterm = document.createElement('div');
    xterm.className = 'xterm';
    const xtermInput = document.createElement('textarea');
    xterm.appendChild(xtermInput);
    document.body.append(input, textarea, editable, monaco, xterm);

    for (const target of [input, textarea, editable, monacoInput, xtermInput]) {
      press({ key: 'b', code: 'KeyB', ctrlKey: true }, target);
    }
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it('allows queue/session actions in the composer scope but not other inputs', () => {
    useAppSettingsStore.setState({
      shortcuts: { 'queue.enqueueLocked': 'Mod+Shift+F7' },
    });
    const handler = vi.fn(() => true);
    const unregister = registerLockedEnqueueHandler(handler);
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    const composer = document.createElement('div');
    composer.className = 'composer-editor';
    composer.setAttribute('contenteditable', 'true');
    const input = document.createElement('input');
    document.body.append(composer, input);
    try {
      press({ key: 'F7', code: 'F7', ctrlKey: true, shiftKey: true }, composer);
      expect(handler).toHaveBeenCalledWith('s1');
      handler.mockClear();
      press({ key: 'F7', code: 'F7', ctrlKey: true, shiftKey: true }, input);
      expect(handler).not.toHaveBeenCalled();
    } finally {
      unregister();
    }
  });

  it('ignores IME, repeat, defaultPrevented and modifier-only events', () => {
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    press({ key: 'b', code: 'KeyB', ctrlKey: true, isComposing: true });
    press({ key: 'b', code: 'KeyB', ctrlKey: true, repeat: true });
    press({ key: 'Control', code: 'ControlLeft', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);

    const prevented = new KeyboardEvent('keydown', {
      key: 'b', code: 'KeyB', ctrlKey: true, cancelable: true,
    });
    prevented.preventDefault();
    expect(shouldIgnoreShortcutEvent(prevented)).toBe(true);
  });

  it('does not run business actions while a modal is open or a recorder is active', () => {
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    const modal = document.createElement('div');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.appendChild(modal);

    press({ key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);

    modal.remove();
    setShortcutRecording(true);
    press({ key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    setShortcutRecording(false);
  });

  it('refuses to execute a conflicting binding until it is resolved', () => {
    useAppSettingsStore.setState({ shortcuts: { 'app.commandPalette': 'Mod+B' } });
    const navigate = vi.fn();
    const palette = vi.fn();
    window.addEventListener('pan:shortcut:command-palette', palette);
    render(<Harness navigate={navigate} />);
    press({ key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    expect(palette).not.toHaveBeenCalled();
    window.removeEventListener('pan:shortcut:command-palette', palette);
  });

  it('uses the live plugin registry and stops after removal', () => {
    usePluginPanelStore.setState({ panels: [panel] });
    useAppSettingsStore.setState({
      shortcuts: { 'panel:plugin:alpha/one': 'Mod+3' },
    });
    const navigate = vi.fn();
    render(<Harness navigate={navigate} />);
    press({ key: '3', code: 'Digit3', ctrlKey: true });
    expect(navigate).toHaveBeenCalledWith('/plugins/alpha/one');

    usePluginPanelStore.setState({ panels: [] });
    navigate.mockClear();
    press({ key: '3', code: 'Digit3', ctrlKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  it('dispatches command palette and history search only through the shell listener', () => {
    const navigate = vi.fn();
    const palette = vi.fn();
    const history = vi.fn();
    window.addEventListener('pan:shortcut:command-palette', palette);
    window.addEventListener('pan:shortcut:history-search', history);
    render(<Harness navigate={navigate} />);
    press({ key: 'k', code: 'KeyK', ctrlKey: true });
    press({ key: 'f', code: 'KeyF', ctrlKey: true });
    expect(palette).toHaveBeenCalledTimes(1);
    expect(history).toHaveBeenCalledTimes(1);
    window.removeEventListener('pan:shortcut:command-palette', palette);
    window.removeEventListener('pan:shortcut:history-search', history);
  });

  it('dispatches global history search on Mod+G but never from an editable target', () => {
    const navigate = vi.fn();
    const globalHistory = vi.fn();
    window.addEventListener('pan:shortcut:global-history-search', globalHistory);
    render(<Harness navigate={navigate} />);
    press({ key: 'g', code: 'KeyG', ctrlKey: true });
    expect(globalHistory).toHaveBeenCalledTimes(1);

    // Typing in either search input must never reach the rail or switch modes.
    const input = document.createElement('input');
    document.body.append(input);
    press({ key: 'g', code: 'KeyG', ctrlKey: true }, input);
    expect(globalHistory).toHaveBeenCalledTimes(1);
    window.removeEventListener('pan:shortcut:global-history-search', globalHistory);
  });
});
