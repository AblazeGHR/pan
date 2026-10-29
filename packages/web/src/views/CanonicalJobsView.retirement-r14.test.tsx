// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './CanonicalJobsView';
import { useAutomationAuth } from '@/stores/automationStore';
import type { JsonObject } from '@/types/canonicalJobs';

let writes: { url: string; body: JsonObject }[];
let lost: boolean;
let available: boolean;
const ref = (id: string) => ({ id, revision: 3, digest: 'a'.repeat(64) });
const response = (body: unknown) => new Response(JSON.stringify(body));
beforeEach(() => {
  writes = []; lost = false; available = true;
  useAutomationAuth.setState({ token: 'owned', expiresAt: Date.now() + 60000 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    if (init.method === 'POST') {
      writes.push({ url, body: JSON.parse(String(init.body)) as JsonObject });
      if (lost) throw new TypeError('owned lost response');
      return response({ operationId: 'op', status: 'completed', objectRefs: [], links: {} });
    }
    if (url === '/api/jobs/actions') return response({ items: ['product_assign_v2', 'product_process_logs_v1'].map((id) => ({ id, system: true, state: 'published', enabled: available, defaultCompletion: id.includes('assign') ? 'durable-queue-accepted' : 'process-exit' })), references: Object.fromEntries(['product_assign_v2', 'product_process_logs_v1'].map((id) => [id, ref(id)])) });
    if (url.includes('operations')) return response({ operationId: 'op', status: 'completed', objectRefs: [], links: {} });
    return response({ items: [], nextCursor: null });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); useAutomationAuth.getState().clear(); });
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const change = (name: string, value: string) => fireEvent.change(screen.getByLabelText(name), { target: { value } });
function host(kind: 'assign' | 'shell') {
  render(<MemoryRouter><CanonicalJobsView /></MemoryRouter>);
  click('JobTemplates'); click(`New ${kind} Template`);
  change('Template name', '  Complete 中文  ');
}
async function ready() { await waitFor(() => expect((screen.getByRole('button', { name: 'Save passive Template' }) as HTMLButtonElement).disabled).toBe(false)); }

it('simple assign production caller sends complete passive product Template and raw fields without source impersonation controls', async () => {
  host('assign'); change('Session ID', 'ses_owned'); change('Text', '  中文\nraw  '); await ready();
  expect(writes).toEqual([]); click('Save passive Template');
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ url: '/api/job-templates', body: {
    expectedRevision: 0, form: { kind: 'assign', fields: { sessionId: 'ses_owned', text: '  中文\nraw  ' } },
    definition: { name: '  Complete 中文  ', jobDefinition: { name: '  Complete 中文  ', steps: [{ stepId: 'effect', actionRef: ref('product_assign_v2'), arguments: {}, completion: { contract: 'durable-queue-accepted' } }] }, composition: { creators: [], triggers: [] } },
  } });
  expect(writes[0]!.body.requestKey).toEqual(expect.any(String));
  expect(screen.queryByLabelText('source')).toBeNull();
  await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
});

it('shell caller passes raw command/cwd to host normalization, never guesses browser shell or executes at save', async () => {
  host('shell'); change('Command', 'echo "raw text" > result'); change('Working directory', '  owned directory  '); await ready();
  click('Save passive Template'); await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.body.form).toEqual({ kind: 'shell', fields: { command: 'echo "raw text" > result', cwd: '  owned directory  ', hideWindow: true } });
  expect(writes[0]!.url).toBe('/api/job-templates');
});

it('unknown creation queries the original operation and disables duplicate form submission', async () => {
  host('assign'); change('Session ID', 'ses_owned'); change('Text', 'raw'); await ready();
  lost = true; click('Save passive Template'); await screen.findByText('请求结果未确认，请按原身份查询。');
  expect((screen.getByRole('button', { name: 'Save passive Template' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole('form'));
  expect(writes).toHaveLength(1);
  click('查询原操作'); await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
  expect(writes).toHaveLength(1);
});

it('missing or disabled fixed publication blocks form writes without a fallback', async () => {
  available = false; host('shell'); await screen.findByText(/Required product Action is unavailable/);
  expect((screen.getByRole('button', { name: 'Save passive Template' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole('form')); expect(writes).toEqual([]);
});
