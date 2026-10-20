// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TopBar } from './TopBar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import * as api from '@/services/api';

function mockMatchMedia(matches = false) {
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

beforeEach(() => {
  mockMatchMedia();
  vi.spyOn(api, 'requestDelayedRestart').mockResolvedValue(null);
  useSessionStore.setState({
    currentSessionId: 'session-123456789',
    sessions: [{
      id: 'session-123456789', name: 'Session', cliSessionId: 'cli-session-123', model: 'secret-model',
      workerStatus: 'running', workerId: 'worker-123', alwaysThinkingEnabled: false, effort: '', history: [],
    }],
  });
  useUIStore.setState({ toastQueue: [] });
  useNativeControlStore.setState({ sessions: {}, switching: {}, terminalViews: {} });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  useWorkerStore.setState({
    delayedRestarts: {},
    currentWorker: {
      id: 'worker-123', sessionId: 'session-123456789', status: 'running',
      nativeStatus: { type: 'active', activeFlags: ['waitingOnApproval'] },
    },
    restart: vi.fn(async () => {}),
    interrupt: vi.fn(async () => {}),
    takeover: vi.fn(async () => ({})),
    killCurrent: vi.fn(async () => {}),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, 'clipboard');
  Reflect.deleteProperty(document, 'execCommand');
});

// ── Usage-label fit: measured widths, priority hide/restore ────────────────

describe('TopBar usage fit (measured widths)', () => {
  interface FitLayout {
    bar: number;
    fixed: number;
    name: number;
    actions: number;
    context: number;
    quota: number;
  }

  let flushCallbacks: Array<() => void> = [];

  class FakeResizeObserver {
    constructor(callback: ResizeObserverCallback) {
      flushCallbacks.push(() => callback([], this as unknown as ResizeObserver));
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  function rect(width: number): DOMRect {
    return {
      width, height: 24, x: 0, y: 0, left: 0, top: 0, right: width, bottom: 24,
      toJSON: () => ({}),
    } as DOMRect;
  }

  /** Drive the real measurement path with a controllable layout model. */
  function installLayout(layout: FitLayout) {
    const bar = screen.getByTestId('topbar');
    const left = bar.firstElementChild as HTMLElement;
    const name = screen.getByTestId('topbar-session-name');
    const actions = screen.getByTestId('topbar-actions');
    const context = screen.queryByTestId('topbar-context-usage');
    const quota = screen.queryByTestId('topbar-quota-usage');
    const visibleWidth = (element: Element | null, width: number) =>
      element && element.getAttribute('data-usage-hidden') !== 'true' ? width : 0;

    bar.getBoundingClientRect = () => rect(layout.bar);
    name.getBoundingClientRect = () => rect(Math.min(layout.name, 200));
    Object.defineProperty(name, 'scrollWidth', { configurable: true, value: layout.name });
    left.getBoundingClientRect = () => rect(layout.fixed + Math.min(layout.name, 200));
    actions.getBoundingClientRect = () => rect(
      layout.actions + visibleWidth(context, layout.context) + visibleWidth(quota, layout.quota),
    );
    if (context) context.getBoundingClientRect = () => rect(layout.context);
    if (quota) quota.getBoundingClientRect = () => rect(layout.quota);
  }

  function flushFit() {
    act(() => { for (const callback of flushCallbacks) callback(); });
  }

  function setupCodexSession() {
    useSessionStore.setState({
      currentSessionId: 'session-codex',
      sessions: [{
        id: 'session-codex', name: 'Codex session', adapter: 'codex', cliSessionId: 'cli-codex',
        workerStatus: 'running', workerId: 'worker-codex',
        alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({
      delayedRestarts: {},
      currentWorker: {
        id: 'worker-codex', sessionId: 'session-codex', status: 'running',
        nativeUsage: { last: { totalTokens: 12000 }, modelContextWindow: 200000 },
      },
    });
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: 'session-codex', adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true, cacheMode: 'persisted', profileKey: 'profile-a',
        windows: {
          first: { kind: 'five_hour', usage: { usedPercent: 20 } },
          secondary: { kind: 'weekly', usage: { usedPercent: 45 } },
        },
      },
    });
  }

  beforeEach(() => {
    flushCallbacks = [];
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  });

  it('hides context first, then quota, and restores them in reverse order as width returns', async () => {
    setupCodexSession();
    render(<TopBar />);
    await screen.findByTestId('topbar-quota-usage');
    const context = screen.getByTestId('topbar-context-usage');
    const quota = screen.getByTestId('topbar-quota-usage');
    const base = { fixed: 420, name: 200, actions: 160, context: 90, quota: 170 };

    installLayout({ ...base, bar: 1100 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('0');
    expect(context.getAttribute('data-usage-hidden')).toBe('false');
    expect(quota.getAttribute('data-usage-hidden')).toBe('false');

    installLayout({ ...base, bar: 1000 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('1');
    expect(context.getAttribute('data-usage-hidden')).toBe('true');
    expect(context.getAttribute('aria-hidden')).toBe('true');
    expect(quota.getAttribute('data-usage-hidden')).toBe('false');
    // Repeated measurements at the same width stay put (no flip-flop).
    flushFit();
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('1');
    expect(context.getAttribute('data-usage-hidden')).toBe('true');
    expect(quota.getAttribute('data-usage-hidden')).toBe('false');

    installLayout({ ...base, bar: 900 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('2');
    expect(context.getAttribute('data-usage-hidden')).toBe('true');
    expect(quota.getAttribute('data-usage-hidden')).toBe('true');

    // Width comes back: quota first, then context.
    installLayout({ ...base, bar: 1000 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('1');
    expect(context.getAttribute('data-usage-hidden')).toBe('true');
    expect(quota.getAttribute('data-usage-hidden')).toBe('false');

    installLayout({ ...base, bar: 1100 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('0');
    expect(context.getAttribute('data-usage-hidden')).toBe('false');
    expect(quota.getAttribute('data-usage-hidden')).toBe('false');
  });

  it('reacts to the long Session name and Sidebar-driven width changes, not a viewport breakpoint', async () => {
    setupCodexSession();
    render(<TopBar />);
    await screen.findByTestId('topbar-quota-usage');
    const base = { bar: 1000, fixed: 420, actions: 160, context: 90, quota: 170 };

    installLayout({ ...base, name: 200 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('1');
    expect(screen.getByTestId('topbar-context-usage').getAttribute('data-usage-hidden')).toBe('true');

    // Same bar width, short name: everything fits again.
    installLayout({ ...base, name: 100 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('0');

    // Sidebar expands (bar narrows) with the long name: both labels go.
    installLayout({ ...base, name: 200, bar: 900 });
    flushFit();
    expect(screen.getByTestId('topbar').getAttribute('data-usage-fit')).toBe('2');
  });
});

// ── Session-name copy ──────────────────────────────────────────────────────

describe('TopBar Session-name copy', () => {
  const longName = 'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789';

  function setupNamedSession(name = longName) {
    useSessionStore.setState({
      currentSessionId: 'session-long',
      sessions: [{
        id: 'session-long', name, cliSessionId: 'cli-long',
        workerStatus: 'running', workerId: 'worker-long',
        alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({
      delayedRestarts: {},
      currentWorker: { id: 'worker-long', sessionId: 'session-long', status: 'running' },
    });
  }

  function stubClipboard(writeText: (text: string) => Promise<void>) {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn(writeText) },
    });
  }

  it('copies the full untruncated name (never the id) from a keyboard-accessible button', async () => {
    setupNamedSession();
    stubClipboard(async () => {});
    render(<TopBar />);

    const nameButton = screen.getByTestId('topbar-session-name');
    expect(nameButton.tagName).toBe('BUTTON');
    expect(nameButton.getAttribute('type')).toBe('button');
    expect(nameButton.getAttribute('aria-label')).toBe(`Copy session name: ${longName}`);
    expect(nameButton.textContent).toBe(longName);

    fireEvent.click(nameButton);
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(longName);
      expect(useUIStore.getState().toastQueue.some(
        (toast) => toast.message === `Copied session name: ${longName}`,
      )).toBe(true);
    });
    // Copying is inert: no Session switch, no rename, no view change.
    expect(useSessionStore.getState().currentSessionId).toBe('session-long');
    expect(useSessionStore.getState().sessions[0]?.name).toBe(longName);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('tui');
  });

  it('reports a copy failure instead of a false success', async () => {
    setupNamedSession();
    stubClipboard(async () => { throw new Error('denied'); });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn(() => false),
    });
    render(<TopBar />);

    fireEvent.click(screen.getByTestId('topbar-session-name'));
    await waitFor(() => {
      expect(useUIStore.getState().toastQueue.some(
        (toast) => toast.type === 'error' && toast.message === 'Copy session name failed',
      )).toBe(true);
    });
    expect(useUIStore.getState().toastQueue.some(
      (toast) => toast.message.startsWith('Copied session name:'),
    )).toBe(false);
  });

  it('follows the current Session name across a switch', () => {
    setupNamedSession('First session name');
    render(<TopBar />);
    expect(screen.getByTestId('topbar-session-name').textContent).toBe('First session name');

    act(() => useSessionStore.setState((state) => ({
      currentSessionId: 'session-second',
      sessions: [...state.sessions, {
        id: 'session-second', name: 'Second session name', workerStatus: 'running',
        workerId: 'worker-second', alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    })));

    const nameButton = screen.getByTestId('topbar-session-name');
    expect(nameButton.textContent).toBe('Second session name');
    expect(nameButton.getAttribute('aria-label')).toBe('Copy session name: Second session name');
  });
});

// ── Appearance: hide only the chat view switcher ───────────────────────────

describe('TopBar chat view switcher visibility', () => {
  beforeEach(() => {
    useSessionStore.setState((state) => ({
      sessions: state.sessions.map((s) => ({ ...s, adapter: 'cbc', nativeControl: false })),
    }));
  });

  it('Appearance can hide only the Chat view switcher; Terminal TUI / Upgrades stays', () => {
    useAppSettingsStore.setState({ showChatViewSwitcher: false });
    render(<TopBar />);

    expect(screen.queryByRole('group', { name: 'Chat view' })).toBeNull();
    for (const name of ['Switch to TUI view', 'Switch to Bubble view', 'Switch to Raw view']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    const upgrades = screen.getByRole('button', { name: 'Terminal TUI' });
    expect(upgrades.textContent).toContain('Upgrades');
    expect(upgrades.getAttribute('data-terminal-status')).toBe('upgrade');
    expect(upgrades.hasAttribute('disabled')).toBe(false);
  });

  it('hides the mobile cycle button too and keeps worker controls', () => {
    mockMatchMedia(true);
    useAppSettingsStore.setState({ showChatViewSwitcher: false });
    render(<TopBar />);

    for (const name of ['Switch to TUI view', 'Switch to Bubble view', 'Switch to Raw view']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
  });

  it('keeps the switcher by default', () => {
    render(<TopBar />);
    expect(screen.getByRole('group', { name: 'Chat view' })).toBeTruthy();
  });
});
