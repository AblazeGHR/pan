import { afterEach, expect, it, vi } from 'vitest';
import { useAutomationAuth } from '@/stores/automationStore';
import { lockerRequest } from './lockerModel';
afterEach(() => {
  useAutomationAuth.getState().clear();
  vi.unstubAllGlobals();
});
it('uses the shared memory Bearer for GET and clears it after 401', async () => {
  useAutomationAuth.setState({ token: 'owned-token', expiresAt: Date.now() + 60000 });
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ lockers: [] }) })
    .mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: { code: 'unauthorized' } }),
    });
  vi.stubGlobal('fetch', fetch);
  await lockerRequest('/api/user-lockers');
  expect(fetch.mock.calls[0]?.[1].headers.Authorization).toBe('Bearer owned-token');
  await expect(lockerRequest('/api/user-lockers')).rejects.toThrow('unauthorized');
  expect(useAutomationAuth.getState().token).toBeNull();
});

it('discards a late old-user response and never clears a successor token', async () => {
  useAutomationAuth.setState({ token: 'old', expiresAt: Date.now() + 60000 });
  let release: (value: unknown) => void = () => {};
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: false, status: 401, json: () => promise })),
  );
  const request = lockerRequest('/api/user-lockers');
  useAutomationAuth.setState({ token: 'successor', expiresAt: Date.now() + 60000 });
  release({ detail: { code: 'unauthorized' } });
  await expect(request).rejects.toThrow('Authorization changed');
  expect(useAutomationAuth.getState().token).toBe('successor');
});
