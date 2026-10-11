import { beforeEach, describe, expect, it, vi } from 'vitest';
import { nativeRequest, type NativeControlState } from '@/services/nativeControl';
import { useNativeControlStore as store } from './nativeControlStore';
import { useSessionStore } from './sessionStore';
vi.mock('@/services/nativeControl', () => ({ nativeRequest: vi.fn() }));
const request = vi.mocked(nativeRequest);
const pan: NativeControlState = { workerId: 'w', mode: 'pan', generation: 0, error: null };
const native: NativeControlState = { ...pan, mode: 'native', generation: 1 };
beforeEach(() => { request.mockReset(); store.setState({ sessions: {}, switching: {}, terminalViews: {} }); });
describe('native ownership receipts', () => {
  it('accepts a fresh runtime generation with reset control counters on the same Worker id', async () => {
    store.setState({ sessions: { restarted: { ...native, generation: 8, workerGeneration: 2 } } });
    request.mockResolvedValueOnce({ ...pan, workerGeneration: 3 });
    await store.getState().refresh('restarted');
    expect(store.getState().sessions.restarted).toMatchObject({ mode: 'pan', generation: 0, workerGeneration: 3 });
    request.mockResolvedValueOnce({ ...native, workerGeneration: 3 });
    await store.getState().switchMode('restarted', 'native');
    expect(store.getState().sessions.restarted).toMatchObject({ mode: 'native', generation: 1, workerGeneration: 3 });
  });
  it('does not publish unchanged poll receipts or replace loaded Session history', async () => {
    const state = { ...pan, enabled: true };
    store.setState({ sessions: { stable: state } });
    const session = { id: 'stable', name: 'stable', nativeControl: true, history: [], alwaysThinkingEnabled: false, effort: '' };
    useSessionStore.setState({ sessions: [session] });
    const nativeChanged = vi.fn();
    const sessionChanged = vi.fn();
    const unsubscribeNative = store.subscribe(nativeChanged);
    const unsubscribeSession = useSessionStore.subscribe(sessionChanged);
    try {
      request.mockImplementation(async () => ({ ...state }));
      for (let index = 0; index < 60; index += 1) await store.getState().refresh('stable');
      expect(nativeChanged).not.toHaveBeenCalled();
      expect(sessionChanged).not.toHaveBeenCalled();
      expect(useSessionStore.getState().sessions[0]).toBe(session);
      request.mockResolvedValueOnce({ ...state, pendingEnabled: false });
      await store.getState().refresh('stable');
      expect(nativeChanged).toHaveBeenCalledOnce();
      request.mockResolvedValueOnce({ ...state, enabled: false });
      await store.getState().refresh('stable');
      expect(sessionChanged).toHaveBeenCalledOnce();
      expect(useSessionStore.getState().sessions[0]?.nativeControl).toBe(false);
    } finally { unsubscribeNative(); unsubscribeSession(); }
  });
  it('repairs a stale Session projection even when the native receipt is unchanged', async () => {
    const state = { ...pan, enabled: true };
    store.setState({ sessions: { projection: state } });
    useSessionStore.setState({ sessions: [{ id: 'projection', name: 'projection', history: [], nativeControl: false, alwaysThinkingEnabled: false, effort: '' }] });
    request.mockResolvedValueOnce({ ...state });
    await store.getState().refresh('projection');
    expect(store.getState().sessions.projection).toBe(state);
    expect(useSessionStore.getState().sessions[0]?.nativeControl).toBe(true);
  });
  it('never lets an older refresh overwrite a completed handoff', async () => {
    let release!: (value: NativeControlState) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const refresh = store.getState().refresh('s');
    request.mockResolvedValueOnce(native);
    await store.getState().switchMode('s', 'native');
    release(pan); await refresh;
    expect(store.getState().sessions.s).toEqual(native);
  });
  it('lost receipt keeps ownership unknown and serializes retries', async () => {
    request.mockRejectedValueOnce(new Error('lost'));
    request.mockRejectedValueOnce(new Error('offline'));
    await expect(store.getState().switchMode('s', 'native')).rejects.toThrow('lost');
    expect(store.getState().sessions.s?.mode).toBe('switching');
    request.mockResolvedValueOnce(native);
    await store.getState().switchMode('s', 'native');
    expect(store.getState().sessions.s?.mode).toBe('native');
  });
  it('a failed native entry reconciles confirmed Pan ownership rather than trapping the view', async () => {
    request.mockRejectedValueOnce(new Error('native-switch-unconfirmed'));
    request.mockResolvedValueOnce({ ...pan, enabled: true });
    await expect(store.getState().switchMode('s', 'native')).rejects.toThrow();
    expect(store.getState().sessions.s).toMatchObject({ mode: 'pan', enabled: true });
  });
  it('allows history presentation without pretending to recover unknown ownership', async () => {
    request.mockRejectedValueOnce(new Error('lost'));
    request.mockRejectedValueOnce(new Error('offline'));
    await expect(store.getState().switchMode('s', 'native')).rejects.toThrow();
    store.getState().showTerminal('s', false);
    expect(store.getState().terminalViews.s).toBe(false);
    expect(store.getState().sessions.s?.mode).toBe('switching');
  });
  it('late native acknowledgement cannot override a subsequent history-view choice', async () => {
    let release!: (value: NativeControlState) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const pending = store.getState().switchMode('s', 'native');
    store.getState().showTerminal('s', false);
    release(native); await pending;
    expect(store.getState().terminalViews.s).toBe(false);
    expect(store.getState().sessions.s?.mode).toBe('native');
  });
  it('an accepted upgrade remains Pan while waiting for idle, rather than claiming native control', async () => {
    request.mockResolvedValueOnce({ enabled: false, pendingEnabled: true });
    await store.getState().configureSession('s', true);
    expect(request).toHaveBeenCalledWith('s', '/configuration', { enabled: true });
    expect(store.getState().sessions.s).toMatchObject({ mode: 'pan', enabled: false, pendingEnabled: true });
    expect(store.getState().switching.s).toBe(false);
  });
  it('pending downgrade does not manufacture Pan ownership before the worker is replaced', async () => {
    store.setState({ sessions: { s: native } });
    request.mockResolvedValueOnce({ enabled: true, pendingEnabled: false });
    await store.getState().configureSession('s', false);
    expect(store.getState().sessions.s).toMatchObject({ mode: 'native', generation: 1, pendingEnabled: false });
  });
});
