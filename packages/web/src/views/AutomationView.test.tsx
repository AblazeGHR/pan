// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AutomationView from './AutomationView';
import { automationLogin, automationRequest, useAutomationAuth, useAutomationStore } from '@/stores/automationStore';
import type { AutomationSnapshot, Trigger } from '@/types/automation';

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
  fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
    if (!init.method || init.method === 'GET') {
      if (url === '/api/automation') return response(snapshot);
      if (url === '/api/actions') return response({ revision: 3, actions: [action], groups: [] });
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
    return response({ ok: true });
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function openNew() {
  render(<MemoryRouter><AutomationView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'New Trigger' }));
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'Idle pause' } });
  fireEvent.click(screen.getByLabelText('workers.all_idle · Available'));
  fireEvent.change(screen.getByLabelText('Action reference'), { target: { value: 'pause' } });
}
it('creates a disabled serial draft without auth, OR Events and typed parameters, preserving draft across tabs', async () => {
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
  expect(writes[0]!.init.headers).not.toHaveProperty('Authorization');
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
  render(<MemoryRouter><AutomationView /></MemoryRouter>);
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
  await openNew();
  let release!: (r: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
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
  render(<MemoryRouter><AutomationView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Pause Everything template' }));
  fireEvent.change(screen.getByLabelText('Target source'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText(/Target Event path/), { target: { value: 'payload.targets' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create Session Operation Action' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.url).toBe('/api/actions/actions');
  expect(writes[0]!.body).toMatchObject({ expectedRevision: 3, item: { execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'event', path: 'payload.targets' } } } });
  expect(screen.getByText(/Query selection is not implemented/)).toBeTruthy();
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
