// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { fetchSessionHistory } from './api';
import {
  beginForegroundRequest,
  foregroundActivity,
  subscribeForegroundActivity,
} from './foregroundActivity';
afterEach(() => vi.unstubAllGlobals());

it('tracks overlapping foreground work and idempotent completion', () => {
  const counts: number[] = [];
  const unsubscribe = subscribeForegroundActivity(() => counts.push(foregroundActivity().requests));
  const a = beginForegroundRequest(),
    b = beginForegroundRequest();
  a();
  a();
  b();
  unsubscribe();
  expect(counts).toEqual([1, 2, 1, 0]);
});

it('retains foreground priority through body parsing and releases it on failure', async () => {
  let reject!: (error: Error) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: () =>
        new Promise((_r, j) => {
          reject = j;
        }),
    })),
  );
  const result = fetchSessionHistory('a', 100);
  await Promise.resolve();
  await Promise.resolve();
  expect(foregroundActivity().requests).toBe(1);
  reject(new Error('body failed'));
  await expect(result).rejects.toThrow('body failed');
  expect(foregroundActivity().requests).toBe(0);
});

it('bounds decompressed speculative bodies while normal history remains unrestricted', async () => {
  const body = JSON.stringify({
    history: [{ role: 'user', content: 'x'.repeat(600_000) }],
    start: 0,
    total: 1,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body)),
  );
  await expect(fetchSessionHistory('a', 100, 50, undefined, false, true)).rejects.toThrow(
    'byte budget',
  );
  expect(foregroundActivity().requests).toBe(0);
  expect((await fetchSessionHistory('a', 100)).history[0]?.content.length).toBe(600_000);
  expect(foregroundActivity().requests).toBe(0);
});
