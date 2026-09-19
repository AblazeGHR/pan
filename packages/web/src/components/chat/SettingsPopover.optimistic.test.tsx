// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SettingsPopover } from './SettingsPopover';
import { useAdapterStore } from '@/stores/adapterStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { fetchSession, fetchSessions } from '@/services/api';
import type { AdapterConfig, Session } from '@/types';

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<typeof import('@/services/api')>('@/services/api');
  return { ...actual, fetchSession: vi.fn(), fetchSessions: vi.fn() };
});

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
  beforeEach(() => vi.clearAllMocks());

  afterEach(() => {
    cleanup();
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
