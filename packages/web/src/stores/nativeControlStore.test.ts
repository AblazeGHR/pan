import { beforeEach, describe, expect, it, vi } from 'vitest';
import { nativeRequest, type NativeControlState } from '@/services/nativeControl';
import { useNativeControlStore as store } from './nativeControlStore';
vi.mock('@/services/nativeControl', () => ({ nativeRequest: vi.fn() }));
const request = vi.mocked(nativeRequest);
const pan: NativeControlState = { workerId: 'w', mode: 'pan', generation: 0, error: null };
const native: NativeControlState = { ...pan, mode: 'native', generation: 1 };
beforeEach(() => { request.mockReset(); store.setState({ sessions: {}, switching: {} }); });
describe('native ownership receipts', () => {
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
    await expect(store.getState().switchMode('s', 'native')).rejects.toThrow('lost');
    expect(store.getState().sessions.s?.mode).toBe('switching');
    request.mockResolvedValueOnce(native);
    await store.getState().switchMode('s', 'native');
    expect(store.getState().sessions.s?.mode).toBe('native');
  });
  it('an accepted upgrade remains Pan while waiting for idle, rather than claiming native control', async () => {
    request.mockResolvedValueOnce({ enabled: false, pendingEnabled: true });
    await store.getState().configureSession('s', true);
    expect(request).toHaveBeenCalledWith('s', '/configuration', { enabled: true });
    expect(store.getState().sessions.s).toMatchObject({ mode: 'pan', enabled: false, pendingEnabled: true });
    expect(store.getState().switching.s).toBe(false);
  });
});
