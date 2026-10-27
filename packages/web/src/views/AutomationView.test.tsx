// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { useState, type ReactNode } from 'react';
function TestRouter({ children }: { children: ReactNode }) {
  const [router] = useState(() => createMemoryRouter([{ path: '/', element: children }, { path: '/actions', element: <div>Actions destination</div> }], { initialEntries: ['/?tab=trigger'] }));
  return <RouterProvider router={router} />;
}
import AutomationView from './AutomationView';
import { automationLogin, automationRequest, createAutomationAction, useAutomationAuth, useAutomationStore } from '@/stores/automationStore';
import type { AutomationSnapshot, Trigger } from '@/types/automation';
import { resetActionCachesForTests, useActionRegistryStore } from '@/stores/actionRegistryStore';
import { actionRequest } from '@/services/api';
import { useSessionOperationTemplateStore } from '@/stores/sessionOperationTemplateStore';
vi.mock('@/services/api', () => ({ actionRequest: vi.fn() }));

let snapshot: AutomationSnapshot;
const action = { id: 'pause', name: 'Pause targets', description: 'Pause', enabled: true, effect: 'write_local', output_format: 'json', input_schema: { type: 'object', properties: { message: { type: 'string' } }, additionalProperties: false }, execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'explicit', ids: ['s1'] } } };
const saved: Trigger = { id: 'trg_one', name: 'Existing', eventTypes: ['workers.all_idle'], actionId: 'pause', arguments: {}, executionMode: 'serial', enabled: false, revision: 1, createdBy: { type: 'agent', id: 'ta' }, createdAt: 'today', updatedAt: 'today' };
let writes: { url: string; init: RequestInit; body: Record<string, unknown> }[];
let rejectStatus = 0;
const fetcher = vi.fn();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(async () => {
  snapshot = { revision: 0, triggers: {}, runs: {}, definitions: [{ type: 'workers.all_idle', available: true, schema: {}, source: {}, schemaVersion: 1 }, { type: 'plugin.ready', available: false, schema: {}, source: {}, schemaVersion: 1 }], dependencies: {}, diagnostics: [], limits: {} };
  writes = []; rejectStatus = 0;
  useAutomationAuth.getState().clear();
  useAutomationStore.setState({ data: null, updatedAt: 0, error: '' });
  fetcher.mockReset();
  resetActionCachesForTests();
  useSessionOperationTemplateStore.setState({ data: null, error: '' });
  vi.mocked(actionRequest).mockImplementation(async (path) => {
    const result = await fetcher(`/api/${path}`);
    return result.json();
  });
  fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
    if (!init.method || init.method === 'GET') {
      if (url === '/api/automation') return response(snapshot);
      if (url === '/api/actions') return response({ revision: 3, actions: [action], groups: [] });
      if (url === '/api/actions/session-operation-template') return response({ item: { ...action, id: undefined, enabled: false, execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'query', query: { field: 'name', op: 'eq', value: 'Choose a Session name' } } } }, queryFields: {}, queryPredicateSchema: {}, limits: { depth: 8, nodes: 64, values: 128, textLength: 512, candidates: 2048, results: 256 } });
      return response({ sessions: [{ id: 's1', name: 'First Session' }] });
    }
    const body = JSON.parse(init.body as string || '{}') as Record<string, unknown>;
    writes.push({ url, init, body });
    if (url.endsWith('/login')) return response({ token: 'private-token', expiresIn: 43200 });
    if (rejectStatus) return response({ detail: 'rejected' }, rejectStatus);
    if (url.endsWith('/triggers')) {
      const item = { ...saved, ...body.item as object, revision: Number(body.expectedRevision) + 1 };
      snapshot.triggers[item.id] = item;
      return response({ item });
    }
    if (url === '/api/actions/actions') return response({ revision: 4, actions: [action], groups: [], item: { id: 'new_pause' } });
    return response({ ok: true });
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
// Store/auth/Action-registry invariants retained; native workspace UI coverage lives in AutomationWorkspace.test.tsx.
it('deduplicates refresh and rejects an old read after mutation invalidation', async () => {
  let release!: (r: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
  const first = useAutomationStore.getState().refresh();
  expect(useAutomationStore.getState().refresh()).toBe(first);
  const stale = structuredClone(snapshot); stale.revision = 1; snapshot.revision = 2;
  await useAutomationStore.getState().invalidate();
  release(response(stale)); await first;
  expect(useAutomationStore.getState().data?.automation.revision).toBe(2);
});
it('sends Bearer for cancel and never retries rejected effects', async () => {
  await automationLogin('secret'); rejectStatus = 409;
  await expect(automationRequest('automation/runs/r1/cancel', 'POST', { expectedRevision: 3 })).rejects.toMatchObject({ status: 409 });
  expect(writes.filter((w) => w.url.endsWith('/cancel'))).toHaveLength(1);
});
it('creates the Event target template as a normal Action using the registry revision', async () => {
  render(<TestRouter><AutomationView /></TestRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Pause Everything template' }));
  fireEvent.change(await screen.findByLabelText('Target source'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText(/Target Event path/), { target: { value: 'payload.targets' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create Session Operation Action' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.url).toBe('/api/actions/actions');
  expect(writes[0]!.body).toMatchObject({ expectedRevision: 3, item: { execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'event', path: 'payload.targets' } } } });
  expect(writes[0]!.body).toMatchObject({ item: { enabled: false } });
});
it('a late unauthorized response cannot clear a newer login', async () => {
  await automationLogin('first');
  let release!: (response: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
  const request = automationRequest('automation/triggers', 'POST', {});
  useAutomationAuth.setState({ token: 'new-token', expiresAt: Date.now() + 100000 });
  release(response({}, 401));
  await expect(request).rejects.toMatchObject({ status: 401 });
  expect(useAutomationAuth.getState().token).toBe('new-token');
});
it('shares Action loads, adopts template mutation without extra reads, and fences late registry replies', async () => {
  await Promise.all([useAutomationStore.getState().refresh(), useActionRegistryStore.getState().ensureRegistry()]);
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/actions')).toHaveLength(1);
  const old = structuredClone(useActionRegistryStore.getState().registry);
  let release!: (value: typeof old) => void;
  vi.mocked(actionRequest).mockImplementationOnce(() => new Promise<typeof old>((resolve) => { release = resolve; }));
  const read = useActionRegistryStore.getState().ensureRegistry({ force: true });
  await createAutomationAction({ name: 'Template pause' });
  expect(useActionRegistryStore.getState().registry.revision).toBe(4);
  release(old); await read;
  expect(useActionRegistryStore.getState().registry.revision).toBe(4);
  expect(useActionRegistryStore.getState().refreshing).toBe(false);
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/actions')).toHaveLength(1);
  await useAutomationStore.getState().invalidate();
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/actions')).toHaveLength(1);
});