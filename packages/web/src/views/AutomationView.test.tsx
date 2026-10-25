// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
async function openNew() {
  const view = render(<TestRouter><AutomationView /></TestRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'New Trigger' }));
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'Idle pause' } });
  fireEvent.click(screen.getByLabelText('workers.all_idle · Available'));
  fireEvent.change(screen.getByLabelText('Action reference'), { target: { value: 'pause' } });
  return view;
}
it('creates a disabled serial draft with user Bearer, OR Events and typed parameters, preserving draft across tabs', async () => {
  useAutomationAuth.setState({ token: 'private-token', expiresAt: Date.now() + 60000 });
  await openNew();
  fireEvent.click(screen.getByLabelText('plugin.ready · Unavailable'));
  fireEvent.change(screen.getByLabelText('Parameter name'), { target: { value: 'message' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add parameter' }));
  fireEvent.change(screen.getByLabelText('Literal value (JSON)'), { target: { value: '"kept"' } });
  fireEvent.click(screen.getByRole('button', { name: 'Runs' }));
  fireEvent.click(screen.getByRole('button', { name: 'Triggers' }));
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('Idle pause');
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  expect(writes[0]!.body).toMatchObject({ expectedRevision: 0, item: { enabled: false, executionMode: 'serial', eventTypes: ['workers.all_idle', 'plugin.ready'], arguments: { message: { from: 'literal', value: 'kept' } } } });
  expect(writes[0]!.init.headers).toHaveProperty('Authorization', 'Bearer private-token');
});
it('requires an explicit Action/parameter review before enable and clears auth on 401 without losing draft', async () => {
  await openNew();
  await act(() => automationLogin('local-secret'));
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Review and enable' }));
  expect(writes.filter((w) => w.url.endsWith('/triggers'))).toHaveLength(0);
  expect(screen.getByRole('region', { name: 'Enable review' }).textContent).toContain('Pause targets');
  rejectStatus = 401;
  fireEvent.click(screen.getByRole('button', { name: 'Confirm enable' }));
  await screen.findByText('User login required. Your draft is preserved.');
  expect(useAutomationAuth.getState().token).toBeNull();
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('Idle pause');
  expect(writes.at(-1)?.init.headers).toHaveProperty('Authorization', 'Bearer private-token');
});
it('preserves a conflicting draft and offers explicit comparison/rebase, keeping metadata out of writes', async () => {
  snapshot.triggers[saved.id] = structuredClone(saved);
  render(<TestRouter><AutomationView /></TestRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Existing' }));
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'My local name' } });
  snapshot.triggers[saved.id]!.revision = 2;
  rejectStatus = 409;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText(/Revision conflict/);
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('My local name');
  expect((writes[0]!.body.item as object)).not.toHaveProperty('createdBy');
  fireEvent.click(screen.getByRole('button', { name: 'Keep draft against latest revision' }));
  rejectStatus = 0;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  expect(writes.at(-1)?.body.expectedRevision).toBe(2);
});
it('does not let a delayed save overwrite a different editor', async () => {
  const view = await openNew();
  let release!: (r: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  // A host unmount can still occur independently of the guarded controls.
  view.unmount();
  render(<TestRouter><AutomationView /></TestRouter>);
  await screen.findByRole('button', { name: 'New Trigger' });
  fireEvent.click(screen.getByRole('button', { name: 'New Trigger' }));
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'Second draft' } });
  await act(async () => { release(response({ item: saved })); });
  await waitFor(() => expect(useAutomationStore.getState().loading).toBe(false));
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('Second draft');
});
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
it('preserves Event field parameters and nested OR/NOT conditions in the request', async () => {
  await openNew();
  fireEvent.click(screen.getByLabelText('Check State after Event'));
  fireEvent.change(screen.getByLabelText('Condition type'), { target: { value: 'or' } });
  fireEvent.change(screen.getAllByLabelText('Condition type')[1]!, { target: { value: 'not' } });
  fireEvent.change(screen.getByLabelText('Parameter name'), { target: { value: 'message' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add parameter' }));
  fireEvent.change(screen.getByLabelText('Parameter source'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText('Event path'), { target: { value: 'payload.message' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  expect(writes[0]!.body.item).toMatchObject({ condition: { or: [{ not: { field: 'workers.runningCount', op: 'eq', value: 0 } }] }, arguments: { message: { from: 'event', path: 'payload.message' } } });
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
it('guards dirty New/Edit/Close and route departure, cancellation keeps the draft, saving clears dirty', async () => {
  snapshot.triggers[saved.id] = structuredClone(saved);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await openNew();
  fireEvent.click(screen.getByRole('button', { name: 'New Trigger' }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('Idle pause');
  fireEvent.click(screen.getByRole('button', { name: 'Edit Existing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Close editor' }));
  expect(confirm).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole('link', { name: 'Manage Actions' }));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(4));
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('Idle pause');
  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  fireEvent.click(screen.getByRole('button', { name: 'Close editor' }));
  expect(confirm).toHaveBeenCalledTimes(4);
  expect(screen.queryByLabelText('Trigger name')).toBeNull();
  confirm.mockRestore();
});
it('guards accepted switching, but blocks departure and duplicate submits while saving', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  await openNew();
  fireEvent.click(screen.getByRole('button', { name: 'New Trigger' }));
  expect((screen.getByLabelText('Trigger name') as HTMLInputElement).value).toBe('');
  expect(confirm).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'Pending' } });
  fireEvent.click(screen.getByLabelText('workers.all_idle · Available'));
  fireEvent.change(screen.getByLabelText('Action reference'), { target: { value: 'pause' } });
  let release!: (r: Response) => void;
  const count = writes.length;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
  const form = screen.getByRole('button', { name: 'Save disabled draft' }).closest('form')!;
  fireEvent.submit(form); fireEvent.submit(form);
  expect((screen.getByRole('button', { name: 'New Trigger' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Close editor' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('link', { name: 'Manage Actions' }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(screen.queryByText('Actions destination')).toBeNull();
  await act(async () => { release(response({ item: saved })); });
  await screen.findByText('Trigger saved.');
  // Only the first fetch was delayed; a duplicate would reach the regular mock.
  expect(writes).toHaveLength(count);
  fireEvent.click(screen.getByRole('button', { name: 'New Trigger' }));
  expect(confirm).toHaveBeenCalledTimes(1);
});
it('posts the displayed surviving literal after deletion and preserves invalid text during conflict rebase', async () => {
  snapshot.triggers[saved.id] = { ...structuredClone(saved), condition: { and: [{ field: 'workers.runningCount', op: 'eq', value: 11 }, { field: 'workers.runningCount', op: 'eq', value: 22 }] } };
  render(<TestRouter><AutomationView /></TestRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Existing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove condition 1' }));
  expect((screen.getByLabelText('Compare value (JSON)') as HTMLInputElement).value).toBe('22');
  snapshot.triggers[saved.id]!.revision = 2; rejectStatus = 409;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText(/Revision conflict/);
  expect(writes.at(-1)?.body.item).toMatchObject({ condition: { and: [{ value: 22 }] } });
  fireEvent.change(screen.getByLabelText('Compare value (JSON)'), { target: { value: '22e' } });
  fireEvent.click(screen.getByRole('button', { name: 'Keep draft against latest revision' }));
  const input = screen.getByLabelText('Compare value (JSON)') as HTMLInputElement;
  expect(input.value).toBe('22e'); expect(input.checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: '22.0' } });
  rejectStatus = 0;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  expect(writes.at(-1)?.body).toMatchObject({ expectedRevision: 2, item: { condition: { and: [{ value: 22 }] } } });
  expect((screen.getByLabelText('Compare value (JSON)') as HTMLInputElement).value).toBe('22');
});
it('loading a conflicting server configuration replaces local literal text and resets dirty', async () => {
  snapshot.triggers[saved.id] = { ...structuredClone(saved), condition: { field: 'workers.runningCount', op: 'eq', value: 11 } };
  render(<TestRouter><AutomationView /></TestRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Existing' }));
  fireEvent.change(screen.getByLabelText('Compare value (JSON)'), { target: { value: '12' } });
  snapshot.triggers[saved.id] = { ...snapshot.triggers[saved.id]!, revision: 2, condition: { field: 'workers.runningCount', op: 'eq', value: 33 } }; rejectStatus = 409;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText(/Revision conflict/);
  fireEvent.change(screen.getByLabelText('Compare value (JSON)'), { target: { value: '12e' } });
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft and load server' }));
  const input = screen.getByLabelText('Compare value (JSON)') as HTMLInputElement;
  expect(input.value).toBe('33'); expect(input.checkValidity()).toBe(true);
  rejectStatus = 0;
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await screen.findByText('Trigger saved.');
  expect(writes.at(-1)?.body.item).toMatchObject({ condition: { value: 33 } });
});
