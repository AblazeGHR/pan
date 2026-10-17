// @vitest-environment jsdom
import { Profiler } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TopBar } from './TopBar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import * as api from '@/services/api';
import { useNativeControlStore } from '@/stores/nativeControlStore';

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
});

describe('TopBar compact worker presentation', () => {
  it('recovers a missed restart completion when returning to a running Session', async () => {
    const sid = 'session-123456789';
    const pending = { sessionId: sid, workerId: 'worker-123', generation: 1,
      revision: 1, action: 'restart' as const, status: 'pending' as const, error: null };
    vi.mocked(api.requestDelayedRestart).mockResolvedValue(pending);
    render(<TopBar />);
    await waitFor(() => expect(screen.getByTitle('Delayed restart pending: waiting for task completion').hasAttribute('disabled')).toBe(true));
    act(() => useSessionStore.setState((state) => ({
      sessions: [...state.sessions, { id: 'other', name: 'Other', history: [], alwaysThinkingEnabled: false, effort: '' }], currentSessionId: 'other',
    })));
    vi.mocked(api.requestDelayedRestart).mockResolvedValue({ ...pending, revision: 3, status: 'completed' });
    act(() => useSessionStore.setState({ currentSessionId: sid }));
    await waitFor(() => expect(screen.getByTitle('Delayed restart: after task completion, before queued work').hasAttribute('disabled')).toBe(false));
    expect(api.requestDelayedRestart).toHaveBeenLastCalledWith(sid, 'GET');
  });
  it('offers Terminal TUI for every Codex Session and cancellation never upgrades it', async () => {
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, adapter: 'codex', nativeControl: false })) }));
    vi.spyOn(useNativeControlStore.getState(), 'refresh').mockResolvedValue();
    const configure = vi.spyOn(useNativeControlStore.getState(), 'configureSession').mockResolvedValue({ enabled: false, pendingEnabled: true });
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    expect(confirm).toHaveBeenCalled();
    expect(configure).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(configure).toHaveBeenCalledWith('session-123456789', true));
    expect(switchMode).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.some((toast) => toast.message.includes('waiting'))).toBe(true);
    expect(screen.getByRole('button', { name: 'Switch to TUI view' }).getAttribute('aria-pressed')).toBe('true');
  });
  it('does not offer Terminal TUI for other adapters', () => {
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, adapter: 'kimi' })) }));
    render(<TopBar />);
    expect(screen.queryByRole('button', { name: 'Terminal TUI' })).toBeNull();
  });
  it('offers the Terminal TUI upgrade for CBC Sessions', () => {
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, adapter: 'cbc', nativeControl: false })) }));
    render(<TopBar />);
    expect(screen.getByRole('button', { name: 'Terminal TUI' }).getAttribute('data-terminal-status')).toBe('upgrade');
  });
  it('native entry is distinct and clears all View selections without changing the saved preference', async () => {
    const sid = 'session-123456789';
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, adapter: 'codex', nativeControl: true })) }));
    const refresh = vi.spyOn(useNativeControlStore.getState(), 'refresh').mockResolvedValue();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockImplementation(async (id, mode) => {
      useNativeControlStore.setState({ sessions: { [id]: { workerId: 'worker-123', mode, generation: 1, error: null } } });
    });
    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Terminal TUI' }).getAttribute('aria-pressed')).toBe('true'));
    for (const name of ['Switch to TUI view', 'Switch to Bubble view', 'Switch to Raw view']) {
      expect(screen.getByRole('button', { name }).getAttribute('aria-pressed')).toBe('false');
    }
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('tui');
    for (const title of ['Kill worker', 'Restart worker', 'Interrupt', 'Takeover']) {
      expect(screen.getByTitle(title)).toBeTruthy();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Switch to Bubble view' }));
    await waitFor(() => expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble'));
    expect(switchMode.mock.calls).toEqual([[sid, 'native'], [sid, 'pan']]);
    expect(refresh).toHaveBeenCalledWith(sid);
  });
  it('an unconfirmed handoff cannot trap the selected history view', async () => {
    const sid = 'session-123456789';
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, adapter: 'codex', nativeControl: true })) }));
    useNativeControlStore.setState({ sessions: { [sid]: { workerId: 'worker-123', mode: 'switching', generation: 1, error: 'lost' } } });
    vi.spyOn(useNativeControlStore.getState(), 'refresh').mockResolvedValue();
    vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockRejectedValue(new Error('offline'));
    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Switch to Raw view' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Switch to Raw view' }).getAttribute('aria-pressed')).toBe('true'));
    expect(useNativeControlStore.getState().sessions[sid]?.mode).toBe('switching');
    expect(useNativeControlStore.getState().terminalViews[sid]).toBe(false);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('raw');
  });
  it('native-mode topbar invokes the existing lifecycle actions', async () => {
    const sid = 'session-123456789';
    useNativeControlStore.setState({ sessions: { [sid]: { workerId: 'worker-123', mode: 'native', generation: 1, error: null } } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<TopBar />);
    for (const title of ['Restart worker', 'Interrupt', 'Takeover', 'Kill worker']) fireEvent.click(screen.getByTitle(title));
    await waitFor(() => {
      for (const action of ['restart', 'interrupt', 'takeover', 'killCurrent'] as const)
        expect(useWorkerStore.getState()[action]).toHaveBeenCalledWith(sid);
    });
  });
  it('keeps recovery actions when only the native status confirms the live worker', () => {
    useSessionStore.setState((state) => ({ sessions: state.sessions.map((s) => ({ ...s, workerId: undefined, workerStatus: undefined })) }));
    useWorkerStore.setState({ currentWorker: null });
    useNativeControlStore.setState({ sessions: { 'session-123456789': { workerId: 'worker-123', mode: 'switching', generation: 0, error: null } } });
    render(<TopBar />);
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
  });
  it('keeps the mobile top bar single-line and exposes the full truncated session title', () => {
    const longTitle = 'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789';
    mockMatchMedia(true);
    useSessionStore.setState({
      currentSessionId: 'session-123456789',
      sessions: [{
        id: 'session-123456789', name: longTitle, model: 'secret-model',
        workerStatus: 'running', workerId: 'worker-123',
        alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });

    render(<TopBar rightAction={<button className="message-navigation-mobile-toggle">Navigate</button>} />);

    const topbar = screen.getByTestId('topbar');
    const title = topbar.querySelector('.font-medium.truncate');
    expect(topbar.className).toContain('max-md:flex-nowrap');
    expect(title?.className).toContain('max-md:flex-1');
    expect(title?.getAttribute('title')).toBe(longTitle);
    expect(title?.textContent).toBe(longTitle);
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
    expect(topbar.querySelector('.message-navigation-mobile-toggle')).toBeTruthy();
  });

  it('keeps the name region as the mobile top bar flexible item instead of a fixed cap', () => {
    mockMatchMedia(true);
    useSessionStore.setState((state) => ({
      sessions: state.sessions.map((s) => ({ ...s, name: 'Session' })),
    }));
    render(<TopBar />);
    const title = screen.getByTestId('topbar').querySelector('.font-medium.truncate');
    expect(title?.className).not.toContain('max-w-[120px]');
    expect(title?.className).toContain('max-md:flex-1');
  });

  it('hides Terminal TUI and deferred/interrupt actions on mobile and keeps them on desktop', () => {
    useSessionStore.setState((state) => ({
      sessions: state.sessions.map((s) => ({ ...s, adapter: 'codex', nativeControl: true })),
    }));
    vi.spyOn(useNativeControlStore.getState(), 'refresh').mockResolvedValue();

    mockMatchMedia(true);
    const { unmount } = render(<TopBar />);
    expect(screen.queryByRole('button', { name: 'Terminal TUI' })).toBeNull();
    expect(screen.queryByTitle('Interrupt')).toBeNull();
    expect(screen.queryByTitle(/Delayed restart/)).toBeNull();
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
    unmount();

    mockMatchMedia(false);
    render(<TopBar />);
    expect(screen.getByRole('button', { name: 'Terminal TUI' })).toBeTruthy();
    expect(screen.getByTitle('Interrupt')).toBeTruthy();
    expect(screen.getByTitle('Delayed restart: after task completion, before queued work')).toBeTruthy();
  });

  it('offers TUI, Bubble and Raw chat views, kept synchronized with the persistent app setting', () => {
    render(<TopBar />);
    const tui = screen.getByRole('button', { name: 'Switch to TUI view' });
    const bubble = screen.getByRole('button', { name: 'Switch to Bubble view' });
    const raw = screen.getByRole('button', { name: 'Switch to Raw view' });

    expect(tui.getAttribute('aria-pressed')).toBe('true');
    expect(bubble.getAttribute('aria-pressed')).toBe('false');
    expect(raw.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(bubble);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble');
    expect(bubble.getAttribute('aria-pressed')).toBe('true');
    expect(tui.getAttribute('aria-pressed')).toBe('false');
    expect(raw.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(raw);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('raw');
    expect(raw.getAttribute('aria-pressed')).toBe('true');
    expect(bubble.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(tui);
    expect(useAppSettingsStore.getState().chatViewStyle).toBe('tui');
    expect(tui.getAttribute('aria-pressed')).toBe('true');
  });

  it('hides model/status/worker text while retaining the dot and worker actions', () => {
    render(<TopBar />);

    expect(screen.queryByText('secret-model')).toBeNull();
    expect(screen.queryByText(/waiting for approval|active|running|worker-123|no worker/)).toBeNull();
    expect(screen.getByTitle('running')).toBeTruthy();
    expect(screen.getByTitle('Restart worker')).toBeTruthy();
    expect(screen.getByTitle('Interrupt')).toBeTruthy();
    expect(screen.getByTitle('Takeover')).toBeTruthy();
    expect(screen.getByTitle('Kill worker')).toBeTruthy();
    expect(screen.getByTitle('Copy session ID')).toBeTruthy();
    expect(screen.getByTitle('Copy CLI session ID')).toBeTruthy();
  });

  it('shows persisted five-hour and weekly quota when the worker is offline', async () => {
    useSessionStore.setState({
      currentSessionId: 'session-codex',
      sessions: [{
        id: 'session-codex', name: 'Codex', adapter: 'codex', cliSessionId: 'cli-codex',
        workerStatus: 'offline', workerId: null, alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({ currentWorker: null, currentWorkerId: null, workers: {} });
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

    render(<TopBar />);

    expect(await screen.findByText('quota 5h 20% / 周 45%')).toBeTruthy();
    expect(screen.getByTitle('Codex account rate-limit usage (persisted profile cache)')).toBeTruthy();
    expect(api.fetchSessionUsage).toHaveBeenCalledWith('session-codex');
  });

  it('does not display a quota label for missing or unknown windows', async () => {
    useSessionStore.setState({
      currentSessionId: 'session-codex',
      sessions: [{
        id: 'session-codex', name: 'Codex', adapter: 'codex', cliSessionId: 'cli-codex',
        workerStatus: 'offline', workerId: null, alwaysThinkingEnabled: false, effort: '', history: [],
      }],
    });
    useWorkerStore.setState({ currentWorker: null, currentWorkerId: null, workers: {} });
    vi.spyOn(api, 'fetchSessionUsage').mockResolvedValue({
      sessionId: 'session-codex', adapter: 'codex', input: null, output: null,
      cache: { read: null, write: null, total: null }, total: { tokens: null, credit: null },
      codexQuota: {
        ok: true,
        windows: {
          first: { kind: 'unknown', usage: { usedPercent: 20 } },
          secondary: { kind: 'weekly', usage: {} },
        },
      },
    });

    render(<TopBar />);

    await waitFor(() => expect(api.fetchSessionUsage).toHaveBeenCalledWith('session-codex'));
    expect(screen.queryByTitle('Codex account rate-limit usage (persisted profile cache)')).toBeNull();
    expect(screen.queryByText(/quota/)).toBeNull();
  });
});

describe('shared Terminal TUI auto-enter contract (module-level pending target)', () => {
  function codexSession(id: string, overrides: Record<string, unknown> = {}) {
    return {
      id, name: id, adapter: 'codex', nativeControl: false,
      workerStatus: 'running', workerId: `worker-${id}`,
      alwaysThinkingEnabled: false, effort: '', history: [],
      ...overrides,
    };
  }

  function setupSessions(...sessions: ReturnType<typeof codexSession>[]) {
    useSessionStore.setState({
      currentSessionId: sessions[0]!.id,
      sessions,
    });
    useNativeControlStore.setState({ sessions: {}, switching: {}, terminalViews: {} });
  }

  function mockConfigure() {
    return vi.spyOn(useNativeControlStore.getState(), 'configureSession').mockImplementation(async (id: string) => {
      useNativeControlStore.setState((current) => ({
        sessions: { ...current.sessions, [id]: { workerId: `worker-${id}`, mode: 'pan', generation: 1, error: null, enabled: false, pendingEnabled: true } },
      }));
      return { enabled: false, pendingEnabled: true };
    });
  }

  /** refresh mock: mirrors per-session native-control states the test controls. */
  function mockRefresh(states: Record<string, { enabled: boolean }>) {
    return vi.spyOn(useNativeControlStore.getState(), 'refresh').mockImplementation(async (id: string) => {
      const state = states[id];
      if (!state) return;
      useNativeControlStore.setState((current) => ({
        sessions: { ...current.sessions, [id]: {
          workerId: `worker-${id}`, mode: 'pan',
          generation: 1, error: null, enabled: state.enabled,
          pendingEnabled: null,
        } },
      }));
    });
  }

  beforeEach(async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { resetTerminalTuiEntryForTests } = await import('@/hooks/useTerminalTuiEntry');
    resetTerminalTuiEntryForTests();
  });

  it('never auto-enters session B because of a pending upgrade scheduled on session A', async () => {
    setupSessions(codexSession('A'), codexSession('B'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    mockConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();

    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(useNativeControlStore.getState().sessions.A?.pendingEnabled).toBe(true));

    // The user moves to session B, whose upgrade already completed at the
    // server. The stale pending target on A must not push B into Terminal TUI.
    act(() => {
      useSessionStore.setState({ currentSessionId: 'B' });
      states.B = { enabled: true };
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1300)); });
    expect(switchMode).not.toHaveBeenCalledWith('B', 'native');
    expect(switchMode).not.toHaveBeenCalled();
  });

  it('completes session A\'s deferred switch when the user returns to it at the idle boundary', async () => {
    setupSessions(codexSession('A'), codexSession('B'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    mockConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockImplementation(async (id: string) => {
      states[id] = { enabled: true };
      useNativeControlStore.setState((current) => ({
        sessions: { ...current.sessions, [id]: { workerId: `worker-${id}`, mode: 'native', generation: 2, error: null, enabled: true, pendingEnabled: null } },
      }));
    });

    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(useNativeControlStore.getState().sessions.A?.pendingEnabled).toBe(true));

    act(() => { useSessionStore.setState({ currentSessionId: 'B' }); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1200)); });
    expect(switchMode).not.toHaveBeenCalled();

    // Return to A with the upgrade already acknowledged: the poll observes
    // the idle boundary and completes the deferred native switch.
    states.A = { enabled: true };
    act(() => { useSessionStore.setState({ currentSessionId: 'A' }); });
    await waitFor(() => expect(switchMode).toHaveBeenCalledWith('A', 'native'), { timeout: 4000 });
  });

  it('opening a history view cancels the pending auto-enter entirely', async () => {
    setupSessions(codexSession('A'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    mockConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();

    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(useNativeControlStore.getState().sessions.A?.pendingEnabled).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'Switch to Bubble view' }));
    await waitFor(() => expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble'));
    states.A = { enabled: true };
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2300)); });
    expect(switchMode).not.toHaveBeenCalled();
  });

  it('issues the deferred native switch exactly once across repeated polls', async () => {
    setupSessions(codexSession('A'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    mockConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();

    render(<TopBar />);
    // The entry schedules the upgrade; the poll target is cleared before the
    // switch is issued, so later polls (mock still reporting pendingEnabled)
    // cannot re-issue it.
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(useNativeControlStore.getState().sessions.A?.pendingEnabled).toBe(true));
    states.A = { enabled: true };
    await waitFor(() => expect(switchMode).toHaveBeenCalledTimes(1), { timeout: 4000 });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2300)); });
    expect(switchMode).toHaveBeenCalledTimes(1);
  });

  function deferredConfigure() {
    let resolve!: (value: { enabled: boolean; pendingEnabled: boolean | null }) => void;
    const promise = new Promise<{ enabled: boolean; pendingEnabled: boolean | null }>((res) => {
      resolve = res;
    });
    const spy = vi.spyOn(useNativeControlStore.getState(), 'configureSession').mockReturnValue(promise);
    return { spy, resolveConfigure: resolve };
  }

  it('a late configure response cannot revive the auto-enter after a history-view cancel', async () => {
    setupSessions(codexSession('A'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    const { resolveConfigure } = deferredConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();
    const showToast = vi.fn();
    useUIStore.setState({ showToast });

    render(<TopBar />);
    // Click starts the gated configure; while it is in flight the user opens
    // a history view, which cancels the intent.
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    fireEvent.click(screen.getByRole('button', { name: 'Switch to Bubble view' }));
    await waitFor(() => expect(useAppSettingsStore.getState().chatViewStyle).toBe('bubble'));

    // The late response acknowledges the upgrade, but must stay dead: no
    // deferred target, no scheduling toast, no switch — even once A idles.
    resolveConfigure({ enabled: false, pendingEnabled: true });
    await act(async () => { await Promise.resolve(); });
    expect(showToast).not.toHaveBeenCalledWith(
      'Session upgrade scheduled: waiting for the current task to finish', 'info');
    states.A = { enabled: true };
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2300)); });
    expect(switchMode).not.toHaveBeenCalled();
  });

  it('a configure gated across a session switch stays scoped to session A', async () => {
    setupSessions(codexSession('A'), codexSession('B'));
    const states: Record<string, { enabled: boolean }> = { A: { enabled: false } };
    mockRefresh(states);
    const { resolveConfigure } = deferredConfigure();
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();

    render(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    // The user moves to session B while the configure for A is still gated.
    act(() => {
      useSessionStore.setState({ currentSessionId: 'B' });
      states.B = { enabled: true };
    });
    resolveConfigure({ enabled: false, pendingEnabled: true });
    await act(async () => { await Promise.resolve(); });
    // B is never pushed into Terminal TUI…
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1300)); });
    expect(switchMode).not.toHaveBeenCalled();
    // …and A's normal pending contract survives the switch: returning to A
    // (upgrade acknowledged) completes the deferred native switch.
    states.A = { enabled: true };
    act(() => { useSessionStore.setState({ currentSessionId: 'A' }); });
    await waitFor(() => expect(switchMode).toHaveBeenCalledWith('A', 'native'), { timeout: 4000 });
    expect(switchMode).not.toHaveBeenCalledWith('B', 'native');
  });
});

// FE-1: TopBar must only re-render for the slice it reads. Toasts, interactive
// requests and other sessions' worker updates are irrelevant to it.
describe('TopBar render isolation (fine-grained selectors)', () => {  it('ignores unrelated UI-store and worker-store updates', () => {
    const commits: number[] = [];
    render(
      <Profiler id="topbar" onRender={() => commits.push(1)}>
        <TopBar />
      </Profiler>,
    );
    const afterMount = commits.length;
    expect(afterMount).toBeGreaterThan(0);

    act(() => {
      useUIStore.setState({ toastQueue: [{ id: 't1', message: 'hi', type: 'info' }] });
      useUIStore.setState({
        approvalRequests: [
          { sessionId: 'other', workerId: 'w', requestId: 1, method: 'm', params: {} },
        ],
      });
    });
    act(() => {
      // Another session's worker changes; currentWorker keeps its identity.
      useWorkerStore.setState((s) => ({
        workers: {
          ...s.workers,
          other: { id: 'w-other', sessionId: 'other', status: 'running' },
        },
        workerTouchedSeq: { ...s.workerTouchedSeq, other: 1 },
      }));
    });
    expect(commits.length).toBe(afterMount);
  });
});
