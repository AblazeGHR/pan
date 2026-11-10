// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { useAutomationAuth } from '@/stores/automationStore';
import { fetchJobs } from './legacyJobs';

afterEach(() => { vi.unstubAllGlobals(); useAutomationAuth.getState().clear(); });
it('loads the Jobs projection after a cookie session is restored without a Bearer token', async () => {
  useAutomationAuth.setState({ token: null, authenticated: true, expiresAt: Date.now() + 60_000 });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: [{ jobId: 'owned' }] }) });
  vi.stubGlobal('fetch', fetchMock);
  expect(await fetchJobs()).toEqual([{ jobId: 'owned' }]);
  expect(fetchMock).toHaveBeenCalledWith('/api/jobs/legacy-list', expect.objectContaining({ credentials: 'same-origin' }));
});
it('does not load private Jobs while unauthenticated', async () => {
  useAutomationAuth.getState().clear();
  const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
  expect(await fetchJobs()).toEqual([]); expect(fetchMock).not.toHaveBeenCalled();
});
