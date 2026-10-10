// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { createSession } from './api';

afterEach(() => vi.unstubAllGlobals());

it.each([true, false, undefined])('sends explicit nativeControl=%s without inventing legacy settings', async (nativeControl) => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'ses_native', adapter: 'codex', nativeControl: nativeControl === true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);
  await createSession('native', undefined, 'codex', undefined, nativeControl === undefined ? undefined : { nativeControl });
  const body = JSON.parse(fetchMock.mock.calls[0]![1].body);
  expect(body.adapter).toBe('codex');
  if (nativeControl === undefined) expect(body).not.toHaveProperty('nativeControl');
  else expect(body.nativeControl).toBe(nativeControl);
});
