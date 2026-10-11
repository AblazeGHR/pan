// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SettingsPopover } from './SettingsPopover';
import { TopBar } from '@/components/layout/TopBar';
import { useAdapterStore } from '@/stores/adapterStore';
import { useNativeControlStore } from '@/stores/nativeControlStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { fetchSession, fetchSessions } from '@/services/api';
import type { AdapterConfig, Session } from '@/types';

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<typeof import('@/services/api')>('@/services/api');
  return { ...actual, fetchSession: vi.fn(), fetchSessions: vi.fn() };
});

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

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: Error) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const config: AdapterConfig = {
  models: ['model-a', 'model-b', 'model-c'],
  defaultModel: 'model-a',
  effortValues: ['low', 'high'],
  modelEfforts: { 'model-a': ['low'], 'model-b': ['high'], 'model-c': ['low', 'high'] },
  permissionModes: [{ value: 'default', label: 'Default' }],
  defaultPermissionMode: 'default',
  supportedSettings: ['model', 'permissionMode', 'thinking', 'effort'],
};

function session(id = 's1', model = 'model-a', effort = 'low'): Session {
  return {
    id,
    name: id,
    adapter: 'cbc',
    model,
    permissionMode: 'default',
    alwaysThinkingEnabled: true,
    effort,
    history: [],
  };
}

function setup(initial = session()) {
  const applySettings = vi.fn();
  const loadSessions = vi.fn().mockResolvedValue(undefined);
  const showToast = vi.fn();
  const current = { ...initial };
  useSessionStore.setState({
    sessions: [current],
    currentSessionId: current.id,
    loadSessions,
    sessionSettingMutations: {},
    _sessionSettingsTouchedSeq: {},
  });
  useAdapterStore.setState({
    currentAdapter: 'cbc',
    adapterConfigs: { cbc: config },
    applySettings,
  });
  useUIStore.setState({ showToast });
  useWorkerStore.setState({ currentWorker: null });
  vi.mocked(fetchSession).mockResolvedValue(current);
  const anchor = document.createElement('button');
  document.body.append(anchor);
  const rendered = render(
    <SettingsPopover open onClose={vi.fn()} anchorRef={{ current: anchor }} />,
  );
  return { ...rendered, applySettings, loadSessions, showToast };
}

async function chooseModel(model: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /model-a|model-b|model-c/ }));
  });
  await act(async () => {
    fireEvent.click(await screen.findByRole('option', { name: model }));
  });
}

describe('SettingsPopover optimistic session settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockMatchMedia(false);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it('shows the model and linked effort change before a deferred PATCH settles', async () => {
    const patch = deferred<Session>();
    const { applySettings } = setup();
    applySettings.mockReturnValue(patch.promise);
    await act(async () => {});

    await chooseModel('model-b');

    expect(screen.getByRole('button', { name: /model-b/ })).toBeTruthy();
    expect(useSessionStore.getState().sessions[0]?.model).toBe('model-b');
    expect(useSessionStore.getState().sessions[0]?.effort).toBe('');
    expect(applySettings).toHaveBeenCalledWith('s1', { model: 'model-b', effort: '' });
    expect(useSessionStore.getState().sessionSettingMutations.s1?.pending).toBe(true);

    await act(async () => patch.resolve({ ...session(), model: 'model-b', effort: '' }));
  });

  it('converges to the complete server response and runs snapshot reconciliation in background', async () => {
    const patch = deferred<Session>();
    const { applySettings, loadSessions } = setup();
    applySettings.mockReturnValue(patch.promise);
    await act(async () => {});
    await chooseModel('model-c');

    await act(async () =>
      patch.resolve({ ...session(), model: 'model-c', effort: 'high' }),
    );

    expect(useSessionStore.getState().sessions[0]).toMatchObject({ model: 'model-c', effort: 'high' });
    expect(loadSessions).toHaveBeenCalled();
    expect(screen.queryByTestId('settings-pending')).toBeNull();
  });

  it('rolls back the complete optimistic patch and reports network failure', async () => {
    const patch = deferred<Session>();
    const { applySettings, showToast } = setup();
    applySettings.mockReturnValue(patch.promise);
    await act(async () => {});
    await chooseModel('model-b');

    await act(async () => patch.reject(new Error('network down')));

    expect(useSessionStore.getState().sessions[0]).toMatchObject({ model: 'model-a', effort: 'low' });
    expect(screen.getByRole('button', { name: /model-a/ })).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith('network down', 'error');
    expect(useSessionStore.getState().sessionSettingMutations.s1?.error).toBe('network down');
  });

  it('ignores an older A response while B is optimistic, then rolls B back to A on B failure', async () => {
    const first = deferred<Session>();
    const second = deferred<Session>();
    const { applySettings } = setup();
    applySettings.mockImplementation((_id: string, patch: { model?: string }) =>
      patch.model === 'model-b' ? first.promise : second.promise,
    );
    await act(async () => {});
    await chooseModel('model-b');
    await chooseModel('model-c');

    await act(async () => first.resolve({ ...session(), model: 'model-b', effort: 'high' }));
    expect(useSessionStore.getState().sessions[0]?.model).toBe('model-c');

    await act(async () => second.reject(new Error('B rejected')));
    expect(useSessionStore.getState().sessions[0]).toMatchObject({ model: 'model-b', effort: 'high' });
  });

  it('keeps settings mutations isolated when the selected Session changes', async () => {
    const first = deferred<Session>();
    const second = deferred<Session>();
    const s1 = session('s1');
    const s2 = session('s2', 'model-c', 'high');
    useSessionStore.setState({
      sessions: [s1, s2],
      currentSessionId: 's1',
      sessionSettingMutations: {},
      _sessionSettingsTouchedSeq: {},
    });
    const persist = vi.fn((_id: string, patch: { model?: string }) =>
      patch.model === 'model-b' ? first.promise : second.promise,
    );

    const s1Result = useSessionStore.getState().patchSessionSettings('s1', { model: 'model-b' }, persist);
    useSessionStore.setState({ currentSessionId: 's2' });
    const s2Result = useSessionStore.getState().patchSessionSettings('s2', { model: 'model-a' }, persist);
    await act(async () => first.resolve({ ...s1, model: 'model-b' }));
    await act(async () => second.resolve({ ...s2, model: 'model-a' }));
    await Promise.all([s1Result, s2Result]);

    expect(useSessionStore.getState().sessions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', model: 'model-b' }),
        expect.objectContaining({ id: 's2', model: 'model-a' }),
      ]),
    );
  });

  it('does not let a session.updated payload overwrite a pending optimistic value', async () => {
    const patch = deferred<Session>();
    const current = session();
    const persist = vi.fn().mockReturnValue(patch.promise);
    useSessionStore.setState({
      sessions: [current],
      currentSessionId: current.id,
      sessionSettingMutations: {},
      _sessionSettingsTouchedSeq: {},
    });
    const request = useSessionStore.getState().patchSessionSettings('s1', { model: 'model-b', effort: '' }, persist);
    useSessionStore.getState().updateSession('s1', { model: 'model-a', effort: 'low' }, true);
    expect(useSessionStore.getState().sessions[0]).toMatchObject({ model: 'model-b', effort: '' });
    await act(async () => patch.resolve({ ...current, model: 'model-b', effort: '' }));
    await request;
  });

  it('keeps a newer successful setting when an older loadSessions snapshot returns later', async () => {
    const patch = deferred<Session>();
    const snapshot = deferred<Session[]>();
    const current = session();
    const persist = vi.fn().mockReturnValue(patch.promise);
    vi.mocked(fetchSessions).mockReturnValue(snapshot.promise);
    useSessionStore.setState({
      sessions: [current],
      currentSessionId: current.id,
      _loadSeq: 0,
      _sessionSettingsTouchedSeq: {},
      sessionSettingMutations: {},
    });

    const request = useSessionStore.getState().patchSessionSettings(
      's1',
      { model: 'model-b', effort: '' },
      persist,
    );
    const refresh = useSessionStore.getState().loadSessions();
    await act(async () => patch.resolve({ ...current, model: 'model-b', effort: '' }));
    snapshot.resolve([{ ...current, model: 'model-a', effort: 'low' }]);
    await Promise.all([request, refresh]);

    expect(useSessionStore.getState().sessions[0]).toMatchObject({ model: 'model-b', effort: '' });
  });
});

describe('SettingsPopover mobile Terminal TUI entry (More)', () => {
  const codexConfig: AdapterConfig = {
    ...config,
    supportedSettings: [...config.supportedSettings, 'modelContextWindow', 'modelAutoCompactTokenLimit'],
  };

  function codexSession(overrides: Partial<Session> = {}): Session {
    return {
      id: 's1', name: 's1', adapter: 'codex', workerStatus: 'running', workerId: 'worker-1',
      nativeControl: false, alwaysThinkingEnabled: false, effort: '', history: [],
      ...overrides,
    } as Session;
  }

  function setupCodex(initial: Session) {
    const showToast = vi.fn();
    useSessionStore.setState({
      sessions: [initial],
      currentSessionId: initial.id,
      loadSessions: vi.fn().mockResolvedValue(undefined),
      sessionSettingMutations: {},
      _sessionSettingsTouchedSeq: {},
    });
    useAdapterStore.setState({
      currentAdapter: 'codex',
      adapterConfigs: { codex: codexConfig },
      applySettings: vi.fn(),
    });
    useUIStore.setState({ showToast });
    useWorkerStore.setState({ currentWorker: null });
    useNativeControlStore.setState({ sessions: {}, switching: {}, terminalViews: {} });
    vi.mocked(fetchSession).mockResolvedValue(initial);
    const anchor = document.createElement('button');
    document.body.append(anchor);
    const rendered = render(
      <SettingsPopover open onClose={vi.fn()} anchorRef={{ current: anchor }} />,
    );
    return { ...rendered, showToast };
  }

  async function expandMore() {
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByTestId('settings-terminal-tui-entry');
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mockMatchMedia(true);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it('hosts the Terminal TUI entry in More on mobile and schedules the upgrade with pending idle', async () => {
    const { showToast } = setupCodex(codexSession());
    const configure = vi.spyOn(useNativeControlStore.getState(), 'configureSession')
      .mockResolvedValue({ enabled: false, pendingEnabled: true });
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockResolvedValue();
    await act(async () => {});
    await expandMore();

    const entry = screen.getByRole('button', { name: 'Terminal TUI' });
    expect(entry.getAttribute('data-terminal-status')).toBe('upgrade');
    fireEvent.click(entry);

    await waitFor(() => expect(configure).toHaveBeenCalledWith('s1', true));
    expect(switchMode).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith(
      'Session upgrade scheduled: waiting for the current task to finish', 'info');

    act(() => {
      useNativeControlStore.setState((state) => ({
        sessions: { ...state.sessions, s1: { workerId: 'worker-1', mode: 'pan', generation: 1, error: null, enabled: false, pendingEnabled: true } },
      }));
    });
    expect(screen.getByRole('button', { name: 'Terminal TUI' }).textContent)
      .toContain('Terminal TUI · pending idle');
    expect(screen.getByRole('button', { name: 'Terminal TUI' }).hasAttribute('disabled')).toBe(true);
  });

  it('does not host the Terminal TUI entry in More on desktop', async () => {
    mockMatchMedia(false);
    setupCodex(codexSession());
    vi.spyOn(useNativeControlStore.getState(), 'refresh').mockResolvedValue();
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await act(async () => {});
    expect(screen.queryByTestId('settings-terminal-tui-entry')).toBeNull();
  });

  it('completes an upgrade scheduled from Settings via the shared TopBar poller at the idle boundary', async () => {
    useSessionStore.setState({
      sessions: [codexSession()],
      currentSessionId: 's1',
      loadSessions: vi.fn().mockResolvedValue(undefined),
      sessionSettingMutations: {},
      _sessionSettingsTouchedSeq: {},
    });
    useAdapterStore.setState({ currentAdapter: 'codex', adapterConfigs: { codex: codexConfig } });
    useUIStore.setState({ showToast: vi.fn() });
    useWorkerStore.setState({ currentWorker: null });
    useNativeControlStore.setState({ sessions: {}, switching: {}, terminalViews: {} });
    vi.mocked(fetchSession).mockResolvedValue(codexSession());
    vi.spyOn(useNativeControlStore.getState(), 'configureSession')
      .mockResolvedValue({ enabled: false, pendingEnabled: true });
    vi.spyOn(useNativeControlStore.getState(), 'refresh').mockImplementation(async (id: string) => {
      const existing = useNativeControlStore.getState().sessions[id];
      if (existing?.enabled === true) return;
      useNativeControlStore.setState((state) => ({
        sessions: { ...state.sessions, [id]: { workerId: 'worker-1', mode: 'pan', generation: 1, error: null, enabled: true } },
      }));
    });
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode').mockImplementation(async (id: string) => {
      useNativeControlStore.setState((state) => ({
        sessions: { ...state.sessions, [id]: { workerId: 'worker-1', mode: 'native', generation: 2, error: null, enabled: true } },
        terminalViews: { [id]: true },
      }));
    });
    const anchor = document.createElement('button');
    document.body.append(anchor);
    render(
      <>
        <TopBar />
        <SettingsPopover open onClose={vi.fn()} anchorRef={{ current: anchor }} />
      </>,
    );
    await expandMore();
    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));

    // The TopBar poller observes the idle boundary (enabled, no pending flag)
    // and performs the deferred native switch without further user input.
    await waitFor(() => expect(switchMode).toHaveBeenCalledWith('s1', 'native'), { timeout: 4000 });
    await waitFor(() => expect(
      screen.getAllByRole('button', { name: 'Terminal TUI' })
        .some((button) => button.getAttribute('aria-pressed') === 'true'),
    ).toBe(true));
  });

  it('offers manual retry from More after an unconfirmed control error', async () => {
    const { showToast } = setupCodex(codexSession({ nativeControl: true }));
    useNativeControlStore.setState((state) => ({
      sessions: { ...state.sessions, s1: { workerId: 'worker-1', mode: 'switching', generation: 1, error: 'Control ownership unconfirmed. Retry the switch.' } },
    }));
    const switchMode = vi.spyOn(useNativeControlStore.getState(), 'switchMode')
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue();
    await act(async () => {});
    await expandMore();

    const entry = screen.getByRole('button', { name: 'Terminal TUI' });
    expect(entry.getAttribute('title')).toBe('Terminal TUI — control error; retry');
    expect(entry.hasAttribute('disabled')).toBe(false);
    fireEvent.click(entry);
    await waitFor(() => expect(switchMode).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('offline', 'error'));

    fireEvent.click(screen.getByRole('button', { name: 'Terminal TUI' }));
    await waitFor(() => expect(switchMode).toHaveBeenCalledTimes(2));
  });
});
