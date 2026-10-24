// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cancelJob, createJob, patchJob, deleteJob, fetchLegacyJob, retryLegacyJob } from './api';
import { useAutomationAuth } from '@/stores/automationStore';

afterEach(() => { vi.unstubAllGlobals(); useAutomationAuth.getState().clear(); });

it('retains JSON content type and current Bearer on legacy create/edit after user login', async () => {
  useAutomationAuth.setState({ token: 'owned-test-token', expiresAt: Date.now() + 60000 });
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ ok: true, job: { jobId: 'job_old' } }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  await createJob({ kind: 'session-message', target: { sessionId: 's' }, text: 'later', schedule: { type: 'interval', intervalSeconds: 3600 } });
  await patchJob('job_old', { name: 'changed' });
  for (const [, init] of fetch.mock.calls) {
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer owned-test-token');
    expect(JSON.parse(init.body)).toBeTruthy();
  }
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/jobs', '/api/jobs/job_old']);
});

it('uses original endpoints for legacy get/cancel/retry/delete without Action auth', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ jobId: 'job_old', ok: true }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  await fetchLegacyJob('job_old');
  await cancelJob('job_old');
  await retryLegacyJob('job_old');
  await deleteJob('job_old');
  expect(fetch.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET'])).toEqual([
    ['/api/background-jobs/job_old', 'GET'],
    ['/api/background-jobs/job_old/cancel', 'POST'],
    ['/api/background-jobs/job_old/retry', 'POST'],
    ['/api/jobs/job_old', 'DELETE'],
  ]);
});

it.each([fetchLegacyJob, retryLegacyJob, deleteJob])('propagates old API errors and does not fall back to new execution', async (operation) => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, error: { code: 'job_busy', message: 'retain unknown tree' } }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  await expect(operation('job_old')).rejects.toThrow('retain unknown tree');
  expect(fetch).toHaveBeenCalledTimes(1);
});
