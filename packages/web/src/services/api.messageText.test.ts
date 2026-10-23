// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { fetchSessionMessageText } from './api';

afterEach(() => vi.unstubAllGlobals());

it('reads only the clicked canonical message without truncating Markdown', async () => {
  const body = '# raw\n```ts\nlet x = 1;\n```\n' + 'full '.repeat(1000);
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true,
    sessionId: 'ses_a', message: { messageId: 'pan:abc', content: body } })));
  vi.stubGlobal('fetch', fetch);
  expect(await fetchSessionMessageText('ses_a', 'pan:abc')).toBe(body);
  expect(fetch.mock.calls[0]![0]).toBe('/api/sessions/ses_a/messages/pan%3Aabc');
});

it.each([
  { ok: false, error: { code: 'message_not_found' } },
  { ok: true, sessionId: 'ses_b', message: { messageId: 'pan:abc', content: 'other session' } },
  { ok: true, sessionId: 'ses_a', message: { messageId: 'pan:other', content: 'other message' } },
])('rejects missing or mismatched original bodies %#', async data => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));
  await expect(fetchSessionMessageText('ses_a', 'pan:abc')).rejects.toThrow('Message body is unavailable');
});
