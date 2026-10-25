// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { useQueueStore } from '@/stores/queueStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { AgentQueueItem } from '@/types';
import { registerLockedEnqueueHandler } from './composerBridge';
import { registerQueueSelectedEditHandler } from './queueBridge';
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

/** Returns false when a listener called preventDefault (i.e. the key was consumed). */
function press(init: KeyboardEventInit, target: Element | Window | Document = document): boolean {
  return fireEvent.keyDown(target, init);
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

function queueRow(id: string): AgentQueueItem {
  return {
    id,
    queueItemId: id,
    text: id,
    source: 'user',
    kind: 'task',
    createdAt: 1,
    meta: { dispatchState: 'queued', revision: 1 },
  };
}

function queueSelection(): string | null {
  return useQueueStore.getState().queueSelection.s1 ?? null;
}

describe('queue panel selection shortcuts', () => {
  beforeEach(() => {
    useQueueStore.setState({
      queues: {},
      queueSelection: {},
      edits: {},
      panelOpen: false,
      queueLockUpdating: {},
      agentReportsPauseUpdating: {},
    });
  });

  it('moves the highlight only while the panel is open and releases the chord otherwise', () => {
    render(<Harness navigate={vi.fn()} />);
    useQueueStore.setState({
      queues: { s1: [queueRow('q1'), queueRow('q2')] },
      panelOpen: true,
    });

    // fireEvent returns false when the dispatcher consumed the event.
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true })).toBe(false);
    expect(queueSelection()).toBe('q1');
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true })).toBe(false);
    expect(queueSelection()).toBe('q2');
    // Boundary clamp is still "handled", but never wraps to the first row.
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true })).toBe(false);
    expect(queueSelection()).toBe('q2');

    // Plain arrows keep their native caret/scroll behavior.
    expect(press({ key: 'ArrowDown', code: 'ArrowDown' })).toBe(true);
    expect(queueSelection()).toBe('q2');

    useQueueStore.setState({ panelOpen: false });
    expect(press({ key: 'ArrowUp', code: 'ArrowUp', altKey: true })).toBe(true);
    expect(queueSelection()).toBe('q2');
  });

  it('follows a user rebinding and stops firing the default chord', () => {
    useAppSettingsStore.setState({
      shortcuts: { 'queue.selectNext': 'Mod+Shift+J' },
    });
    render(<Harness navigate={vi.fn()} />);
    useQueueStore.setState({ queues: { s1: [queueRow('q1')] }, panelOpen: true });

    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true })).toBe(true);
    expect(queueSelection()).toBeNull();

    expect(press({ key: 'j', code: 'KeyJ', ctrlKey: true, shiftKey: true })).toBe(false);
    expect(queueSelection()).toBe('q1');

    // Clearing the binding releases the chord entirely.
    useAppSettingsStore.setState({ shortcuts: { 'queue.selectNext': null } });
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true })).toBe(true);
    expect(queueSelection()).toBe('q1');
  });

  it('does not execute a persisted conflict between selection chords', () => {
    useAppSettingsStore.setState({
      shortcuts: { 'queue.selectNext': 'Alt+ArrowUp' },
    });
    render(<Harness navigate={vi.fn()} />);
    useQueueStore.setState({ queues: { s1: [queueRow('q1')] }, panelOpen: true });

    expect(press({ key: 'ArrowUp', code: 'ArrowUp', altKey: true })).toBe(true);
    expect(queueSelection()).toBeNull();
  });

  it('works from the composer, ignores other inputs, IME and open modals', () => {
    render(<Harness navigate={vi.fn()} />);
    useQueueStore.setState({
      queues: { s1: [queueRow('q1'), queueRow('q2')] },
      panelOpen: true,
    });
    const composer = document.createElement('div');
    composer.className = 'composer-editor';
    composer.setAttribute('contenteditable', 'true');
    const input = document.createElement('input');
    document.body.append(composer, input);

    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true }, composer)).toBe(false);
    expect(queueSelection()).toBe('q1');

    // A different input owns the chord: nothing is consumed and nothing moves.
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true }, input)).toBe(true);
    expect(queueSelection()).toBe('q1');

    // IME composition and open modals keep their own keys.
    expect(press({
      key: 'ArrowDown', code: 'ArrowDown', altKey: true, isComposing: true,
    }, composer)).toBe(true);
    expect(queueSelection()).toBe('q1');

    const modal = document.createElement('div');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.appendChild(modal);
    expect(press({ key: 'ArrowDown', code: 'ArrowDown', altKey: true }, composer)).toBe(true);
    expect(queueSelection()).toBe('q1');
  });

  it('consumes Alt+Enter and Alt+L only with a selected row', () => {
    const edit = vi.fn(() => true);
    const unregisterEdit = registerQueueSelectedEditHandler(edit);
    const lock = vi.fn(async () => undefined);
    const originalLock = useQueueStore.getState().setQueueItemLocked;
    useQueueStore.setState({ setQueueItemLocked: lock });
    try {
      render(<Harness navigate={vi.fn()} />);
      useQueueStore.setState({ queues: { s1: [queueRow('q1')] }, panelOpen: true });

      // No selection: both chords stay native.
      expect(press({ key: 'Enter', code: 'Enter', altKey: true })).toBe(true);
      expect(press({ key: 'l', code: 'KeyL', altKey: true })).toBe(true);
      expect(edit).not.toHaveBeenCalled();
      expect(lock).not.toHaveBeenCalled();

      useQueueStore.getState().setQueueSelection('s1', 'q1');
      expect(press({ key: 'Enter', code: 'Enter', altKey: true })).toBe(false);
      expect(edit).toHaveBeenCalledWith('s1', 'q1');
      expect(press({ key: 'l', code: 'KeyL', altKey: true })).toBe(false);
      expect(lock).toHaveBeenCalledWith('s1', 'q1', true);

      lock.mockClear();
      useQueueStore.setState({ queueLockUpdating: { s1: true } });
      expect(press({ key: 'l', code: 'KeyL', altKey: true })).toBe(true);
      expect(lock).not.toHaveBeenCalled();
    } finally {
      unregisterEdit();
      useQueueStore.setState({ setQueueItemLocked: originalLock });
    }
  });
});
