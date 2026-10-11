import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createNativeTerminalPoller } from './nativeTerminalPolling';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

it('drains full chunks without the 80ms wait and keeps reads serialized', async () => {
  let release!: (bytes: number) => void;
  const read = vi.fn<() => Promise<number | null>>()
    .mockImplementationOnce(() => new Promise(resolve => { release = resolve; }))
    .mockResolvedValueOnce(65536).mockResolvedValue(1);
  const poller = createNativeTerminalPoller(read, () => true);
  poller.wake(); await vi.advanceTimersByTimeAsync(0);
  poller.wake(); poller.wake(); await vi.advanceTimersByTimeAsync(100);
  expect(read).toHaveBeenCalledTimes(1);
  release(65536); await vi.advanceTimersByTimeAsync(5);
  expect(read).toHaveBeenCalledTimes(3);
  poller.stop();
});

it('backs off genuinely idle terminals but wakes immediately for confirmed input', async () => {
  const read = vi.fn().mockResolvedValue(0);
  const poller = createNativeTerminalPoller(read, () => true);
  poller.wake(); await vi.advanceTimersByTimeAsync(10000);
  expect(read.mock.calls.length).toBeLessThan(45);
  const before = read.mock.calls.length;
  poller.wake(); await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(before + 1);
  poller.stop();
});

it('keeps recent output responsive rather than backing off between chunks', async () => {
  const read = vi.fn().mockResolvedValueOnce(32).mockResolvedValue(0);
  const poller = createNativeTerminalPoller(read, () => true);
  poller.wake(); await vi.advanceTimersByTimeAsync(960);
  expect(read).toHaveBeenCalledTimes(13);
  poller.stop();
});

it('does not read hidden pages and resumes from the same reader on visibility', async () => {
  let visible = true;
  const read = vi.fn().mockResolvedValue(0);
  const poller = createNativeTerminalPoller(read, () => visible);
  poller.wake(); await vi.advanceTimersByTimeAsync(0);
  visible = false; poller.pause(); await vi.advanceTimersByTimeAsync(10000);
  expect(read).toHaveBeenCalledOnce();
  visible = true; poller.wake(); await vi.advanceTimersByTimeAsync(0);
  expect(read).toHaveBeenCalledTimes(2);
  poller.stop();
});

it('does not restart after worker replacement or unmount during a read', async () => {
  const read = vi.fn().mockResolvedValue(null);
  const poller = createNativeTerminalPoller(read, () => true);
  poller.wake(); await vi.advanceTimersByTimeAsync(0);
  poller.wake(); await vi.advanceTimersByTimeAsync(1000);
  expect(read).toHaveBeenCalledOnce();
  let release!: (bytes: number) => void;
  const pending = vi.fn(() => new Promise<number>(resolve => { release = resolve; }));
  const second = createNativeTerminalPoller(pending, () => true);
  second.wake(); await vi.advanceTimersByTimeAsync(0);
  second.stop(); release(65536); await vi.advanceTimersByTimeAsync(1000);
  expect(pending).toHaveBeenCalledOnce();
});
