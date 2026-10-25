// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { canonicalRequest } from './api';
import {
  canonicalJobs,
  canonicalError,
  canonicalErrorMessages,
  definitionOf,
} from './canonicalJobs';
import { useAutomationAuth } from '@/stores/automationStore';
afterEach(() => {
  vi.unstubAllGlobals();
  useAutomationAuth.getState().clear();
});
it('sends canonical JSON with the current in-memory Bearer including DELETE', async () => {
  useAutomationAuth.setState({ token: 'canonical-user', expiresAt: Date.now() + 60000 });
  const fetcher = vi.fn().mockImplementation(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetcher);
  await canonicalJobs.retire(
    'triggers',
    { id: 't/1', name: 'T', state: 'draft', stateRevision: 2, revision: 5 },
    'retire-key',
  );
  const [url, init] = fetcher.mock.calls[0]!;
  expect(url).toBe('/api/triggers/t%2F1');
  expect(init.method).toBe('DELETE');
  expect(new Headers(init.headers).get('Authorization')).toBe('Bearer canonical-user');
  expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
  expect(JSON.parse(init.body)).toEqual({
    expectedRevision: 5,
    expectedStateRevision: 2,
    requestKey: 'retire-key',
  });
});
it.each(Object.keys(canonicalErrorMessages))(
  'maps WP01 %s without echoing untrusted error text',
  async (code) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code, message: 'untrusted-secret' } }), {
          status: 422,
        }),
      ),
    );
    try {
      await canonicalRequest('jobs');
      throw new Error('expected rejection');
    } catch (error) {
      expect(canonicalError(error)).toContain(canonicalErrorMessages[code]);
      expect(canonicalError(error)).not.toContain('untrusted-secret');
    }
  },
);
it('expires the shared Bearer and clears only the matching 401 credential', async () => {
  useAutomationAuth.setState({ token: 'expired', expiresAt: 1 });
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }));
  vi.stubGlobal('fetch', fetcher);
  await expect(canonicalRequest('jobs')).rejects.toThrow();
  expect(useAutomationAuth.getState().token).toBeNull();
  expect(new Headers(fetcher.mock.calls[0]![1].headers).get('Authorization')).toBeNull();
});
it('encodes page cursors and operation identities', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetcher);
  await canonicalJobs.list('jobs', 'a/b+c=');
  await canonicalJobs.runs('j/1', 'next/2');
  await canonicalJobs.lookup('clone-and-run', 'key/1');
  await canonicalJobs.actions();
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    '/api/jobs?limit=100&cursor=a%2Fb%2Bc%3D',
    '/api/jobs/j%2F1/runs?limit=100&cursor=next%2F2',
    '/api/operations?operation=clone-and-run&requestKey=key%2F1',
    '/api/jobs/actions',
  ]);
});
it('extracts the complete definition without server state', () => {
  const result = definitionOf('jobs', {
    id: 'j',
    name: 'Job',
    state: 'draft',
    stateRevision: 4,
    configRevision: 2,
    steps: [],
    retention: { policyRef: 'default' },
    recovery: { automatic: false },
  });
  expect(result).toEqual({
    name: 'Job',
    steps: [],
    retention: { policyRef: 'default' },
    recovery: { automatic: false },
  });
});
