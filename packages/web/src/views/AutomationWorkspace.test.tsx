// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { useState } from 'react';
import { transferableAbortController } from 'node:util';
import AutomationView from './AutomationView';
import { useAutomationAuth, useAutomationStore } from '@/stores/automationStore';
import { resetActionCachesForTests } from '@/stores/actionRegistryStore';
import { actionRequest } from '@/services/api';
import type { Action } from '@/types/actions';
import type { AutomationSnapshot, Trigger } from '@/types/automation';
import { defaultJob, type ActionJob, type SchedulerSource } from '@/types/automationJobs';
import { newId } from '@/types/automationJobs';

vi.mock('@/services/api', () => ({ actionRequest: vi.fn() }));
const action: Action = { id: 'pause', name: 'Pause selected Sessions', description: 'Pause', enabled: true, effect: 'write_local', output_format: 'json', input_schema: { type: 'object', properties: { message: { type: 'string' } }, additionalProperties: false }, execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'explicit', ids: ['s1'] } } };
let jobs: ActionJob[], sources: SchedulerSource[], snapshot: AutomationSnapshot;
let writes: { url: string; body: Record<string, unknown>; headers: HeadersInit | undefined }[];
let fail = 0;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
function Host() {
  const [router] = useState(() => createMemoryRouter([{ path: '/', element: <AutomationView /> }, { path: '/actions', element: <div>Actions destination</div> }]));
  return <RouterProvider router={router} />;
}
beforeEach(() => {
  // React Router uses Node's Request in jsdom. Keep its cancellation signal in
  // the same realm; this remains a real abortable controller, not a no-op shim.
  vi.stubGlobal('AbortController',function () { return transferableAbortController(); });
  jobs = [{ jobId: 'job_review', name: 'Morning review', configRevision: 3, enabled: true, status: 'scheduled', runCount: 4, config: { ...defaultJob(), name: 'Morning review', enabled: true, actionId: 'pause', steps: [{ stepId: 'stable_first', actionId: 'pause', arguments: {} }] } }];
  const owned: Trigger = { id: 'trg_owned', name: 'Morning review · Shared timer', enabled: true, revision: 1, createdAt: '', updatedAt: '', createdBy: { type: 'user' }, eventTypes: ['scheduler.time.v1'], eventFilter: { 'payload.scheduleId': 'schedule_shared' }, actionId: 'pause', arguments: {}, executionMode: 'serial', ownerJobId: 'job_review', ownerLinkId: 'stable_link', targetJobId: 'job_review' };
  sources = [{ id: 'schedule_shared', name: 'Shared timer', revision: 7, owner: 'scheduler-event-v1', enabled: true, schedule: { kind: 'interval', intervalSec: 3600, timezone: 'Asia/Shanghai' }, misfirePolicy: 'fire_now', graceSec: 300, nextFireAt: '2026-10-26T09:00:00', references: [{ triggerId: owned.id, name: owned.name, jobId: 'job_review', enabled: true }] }];
  snapshot = { revision: 1, triggers: { [owned.id]: owned }, runs: {}, definitions: [{ type: 'scheduler.time.v1', schema: { type: 'object' }, schemaVersion: 1, source: { type: 'core', id: 'scheduler' }, available: true }], dependencies: { [owned.id]: { available: true, unavailable: [] } }, diagnostics: [], limits: {} };
  snapshot.triggers.direct = { ...owned, id: 'direct', name: 'Independent watch', ownerJobId: undefined, ownerLinkId: undefined, targetJobId: undefined };
  writes = []; fail = 0;
  resetActionCachesForTests();useAutomationStore.setState({ data: null, updatedAt: 0, error: '' });
  useAutomationAuth.setState({ token: 'owner-token', expiresAt: Date.now() + 60000 });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
    if (!init.method || init.method === 'GET') {
      if (url === '/api/actions') return response({ revision: 1, actions: [action], groups: [] });
      if (url === '/api/automation') return response(snapshot);
      if (url === '/api/jobs/action-jobs') return response({ jobs });
      if (url === '/api/scheduler/event-sources') return response({ sources, payloadSchema: { type: 'object', required: ['scheduleId'] } });
      if (url === '/api/automation/job-edits') return response({ operations: [] });
      return response({ sessions: [] });
    }
    const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>;
    writes.push({ url, body, headers: init.headers });
    if (fail) { const status = fail; fail = 0; return response({ detail: 'injected failure' }, status); }
    if (url === '/api/automation/jobs/compose') {
      const config = body.config as ActionJob['config'];
      const job = { jobId: String(body.jobId ?? 'job_created'), name: config.name, configRevision: 4, config, enabled: config.enabled, status: 'pending' };
      jobs = [...jobs.filter((j) => j.jobId !== job.jobId), job];
      return response({ job });
    }
    if (url.startsWith('/api/scheduler/event-sources/')) {
      const id = url.split('/').at(-1)!.split('?')[0];
      if (init.method === 'DELETE') sources = sources.filter((s) => s.id !== id);
      else sources = sources.map((s) => s.id === id ? { ...s, ...body.config as object, revision: s.revision + 1 } : s);
      return response({ source: sources.find((s) => s.id === id) });
    }
    return response({ ok: true });
  });
  vi.stubGlobal('fetch', fetcher);
  vi.mocked(actionRequest).mockImplementation(async (path) => { const r = await fetcher(`/api/${path}`); return r.json(); });
});
afterEach(() => { cleanup();vi.unstubAllGlobals();vi.restoreAllMocks(); });

async function newJob() {
  render(<Host />);fireEvent.click(await screen.findByRole('button', { name: 'New Job' }));
  expect(screen.queryByRole('region', { name: 'Time trigger 1' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Job name'), { target: { value: 'New workflow' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  fireEvent.change(screen.getByLabelText('Action for step 1'), { target: { value: 'pause' } });
}

it('navigates exactly three accessible tabs by keyboard and follows Job/Trigger/Scheduler links', async () => {
  render(<Host />);await screen.findByRole('article', { name: 'Job Morning review' });
  const tabs = screen.getAllByRole('tab');expect(tabs.map((t) => t.textContent)).toEqual(['Job', 'Trigger', 'Event Register']);
  fireEvent.keyDown(tabs[0]!, { key: 'ArrowRight' });expect(screen.getByRole('tab', { name: 'Trigger' }).getAttribute('aria-selected')).toBe('true');
  expect(await screen.findByText('Managed by Job: Morning review')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Edit Morning review · Shared timer' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit owning Job' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open Scheduler' }));
  expect(screen.getByRole('tab', { name: 'Event Register' }).getAttribute('aria-selected')).toBe('true');
  expect(await screen.findByRole('article', { name: 'Scheduler Shared timer' })).toBeTruthy();
});

it('adds, reorders and removes steps without changing stable IDs and rejects forward output references', async () => {
  await newJob();fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  fireEvent.change(screen.getByLabelText('Action for step 2'), { target: { value: 'pause' } });
  fireEvent.change(screen.getByLabelText('Add parameter to step 2'), { target: { value: 'message' } });
  fireEvent.change(screen.getByLabelText('message source'), { target: { value: 'step' } });
  const prior = (screen.getByLabelText('Output step') as HTMLSelectElement).value;
  fireEvent.click(screen.getByRole('button', { name: 'Move step 2 up' }));
  expect(screen.getByLabelText('Chain validation').textContent).toContain('preceding step');
  expect((screen.getByRole('button', { name: 'Save draft' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Move step 1 down' }));
  expect((screen.getByLabelText('Output step') as HTMLSelectElement).value).toBe(prior);
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(writes.some((w) => w.url.endsWith('/compose'))).toBe(true));
  const config = writes.find((w) => w.url.endsWith('/compose'))!.body.config as ActionJob['config'];
  expect(config.steps![0]!.stepId).toBe(prior);
  expect(config.steps![1]!.arguments.message).toMatchObject({ from: 'step', stepId: prior });
});

it('keeps literal JSON typed and maps Job input or Event fields', async () => {
  await newJob();fireEvent.change(screen.getByLabelText('Add parameter to step 1'), { target: { value: 'message' } });
  fireEvent.change(screen.getByLabelText('message value (JSON)'), { target: { value: '42' } });
  expect(screen.getByLabelText('Chain validation').textContent).toContain('must be string');
  fireEvent.change(screen.getByLabelText('message source'), { target: { value: 'input' } });
  fireEvent.change(screen.getByLabelText('message path'), { target: { value: 'text' } });
  fireEvent.change(screen.getByLabelText('message source'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText('message path'), { target: { value: 'payload.scheduleId' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 1' }));
  expect(screen.getByLabelText('Chain validation').textContent).toContain('at least one');
});

it('selects an existing Scheduler by search and submits its revision with the Job', async () => {
  await newJob();fireEvent.click(screen.getByRole('button', { name: 'Add time trigger' }));
  fireEvent.change(screen.getByLabelText('Search Schedulers'), { target: { value: 'Shanghai' } });
  fireEvent.change(screen.getByLabelText('Scheduler Event'), { target: { value: 'schedule_shared' } });
  expect(screen.getByRole('region', { name: 'Time trigger 1' }).textContent).toContain('Revision 7');
  fireEvent.click(screen.getByRole('button', { name: 'Save and enable' }));
  await waitFor(() => expect(writes.length).toBe(1));
  expect(writes[0]!.body).toMatchObject({ config: { enabled: true }, schedulers: [{ mode: 'existing', sourceId: 'schedule_shared', sourceRevision: 7 }] });
  expect(writes[0]!.headers).toHaveProperty('Authorization', 'Bearer owner-token');
});

it('creates a new Scheduler through one recoverable operation and retries with the same key', async () => {
  await newJob();fireEvent.click(screen.getByRole('button', { name: 'Add time trigger' }));
  fireEvent.click(within(screen.getByRole('region', { name: 'Time trigger 1' })).getByRole('button', { name: 'New Scheduler' }));
  fireEvent.change(screen.getByLabelText('Scheduler name'), { target: { value: 'Independent timer' } });
  fail = 503;fireEvent.click(screen.getByRole('button', { name: 'Save and enable' }));
  await screen.findByRole('button', { name: 'Retry setup' });
  expect(writes.filter((w) => w.url.includes('event-sources'))).toHaveLength(0);
  const first = structuredClone(writes[0]!.body);
  expect(first).toMatchObject({ schedulers: [{ mode: 'new', config: { name: 'Independent timer', enabled: true } }] });
  fireEvent.click(screen.getByRole('button', { name: 'Retry setup' }));
  await waitFor(() => expect(writes).toHaveLength(2));expect(writes[1]!.body).toEqual(first);
});

it('shows source impact, requires review, and preserves a conflicting draft', async () => {
  render(<Host />);fireEvent.click(screen.getByRole('tab', { name: 'Event Register' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Shared timer' }));
  expect(screen.getByRole('region', { name: 'Scheduler impact' }).textContent).toContain('Morning review');
  expect((screen.getByRole('button', { name: 'Save Scheduler' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Delete Scheduler' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Scheduler name'), { target: { value: 'Local source edit' } });
  fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));
  fail = 409;fireEvent.click(screen.getByRole('button', { name: 'Save Scheduler' }));
  await screen.findByText(/Revision conflict/);
  expect((screen.getByLabelText('Scheduler name') as HTMLInputElement).value).toBe('Local source edit');
  expect(writes[0]!.body.expectedRevision).toBe(7);
});

it('disables a source without touching Job lifecycle and deletes only unreferenced disabled sources', async () => {
  sources[0]!.references = [];render(<Host />);
  fireEvent.click(screen.getByRole('tab', { name: 'Event Register' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Shared timer' }));
  fireEvent.click(screen.getByLabelText('Scheduler enabled'));fireEvent.click(screen.getByRole('button', { name: 'Save Scheduler' }));
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Scheduler editor' })).toBeNull());
  expect(writes[0]!.body).toMatchObject({ config: { enabled: false } });
  expect(writes[0]!.url).toBe('/api/scheduler/event-sources/schedule_shared');
  fireEvent.click(screen.getByRole('button', { name: 'Edit Shared timer' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete Scheduler' }));
  await waitFor(() => expect(writes).toHaveLength(2));expect(writes[1]!.url).toContain('expectedRevision=8');
});

it('generates stable random IDs on remote HTTP contexts without randomUUID', () => {
  const getRandomValues = crypto.getRandomValues.bind(crypto);
  vi.stubGlobal('crypto', { getRandomValues });
  const ids = Array.from({ length: 10 }, () => newId('step'));
  expect(new Set(ids).size).toBe(10);expect(ids.every((id) => /^step_[a-f0-9]{32}$/.test(id))).toBe(true);
});

it('keeps existing timing controls on pre-composition Jobs while allowing chain edits', async () => {
  jobs[0]!.config.trigger = { kind: 'immediate' };
  snapshot.triggers = {};
  render(<Host />);fireEvent.click(await screen.findByRole('button', { name: 'Edit Morning review' }));
  expect((screen.getByRole('button', { name: 'Add time trigger' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('link', { name: 'Open Canonical Job controls' }).getAttribute('href')).toBe('/automation/canonical');
  fireEvent.click(screen.getByRole('button', { name: 'Save and disable' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.body).toMatchObject({ config: { trigger: { kind: 'immediate' }, schedule: [] }, schedulers: [] });
});

it('preserves existing chain literals, trusted mappings and parallel policy in the Trigger editor', async () => {
  Object.assign(snapshot.triggers.direct!, { enabled: false, arguments: { message: { from: 'system', path: 'system.sessionId' } }, executionMode: 'parallel', maxConcurrency: 2, retry: { enabled: true, maxAttempts: 3 }, steps: [{ stepId: 'existing_step', actionId: 'pause', arguments: { message: 'literal from the existing API' } }] });
  render(<Host />);fireEvent.click(screen.getByRole('tab', { name: 'Trigger' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Independent watch' }));
  expect((screen.getByLabelText('message value (JSON)') as HTMLInputElement).value).toBe('"literal from the existing API"');
  expect((screen.getByLabelText('Trusted system path') as HTMLInputElement).value).toBe('system.sessionId');
  fireEvent.click(screen.getByRole('button', { name: 'Save disabled draft' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.body).toMatchObject({ item: { executionMode: 'parallel', maxConcurrency: 2, retry: { enabled: true, maxAttempts: 3 }, arguments: { message: { from: 'system', path: 'system.sessionId' } }, steps: [{ stepId: 'existing_step', arguments: { message: { from: 'literal', value: 'literal from the existing API' } } }] } });
});

it('explicitly rebases a source conflict and requires impact review again', async () => {
  render(<Host />);fireEvent.click(screen.getByRole('tab', { name: 'Event Register' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Shared timer' }));
  fireEvent.change(screen.getByLabelText('Scheduler name'), { target: { value: 'Kept local source' } });
  fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));
  sources = sources.map((s) => ({ ...s, revision: 8, name: 'Server source' }));
  fail = 409;fireEvent.click(screen.getByRole('button', { name: 'Save Scheduler' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Keep draft against latest source revision' }));
  expect((screen.getByLabelText('Scheduler name') as HTMLInputElement).value).toBe('Kept local source');
  expect((screen.getByRole('button', { name: 'Save Scheduler' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('I reviewed the affected subscribers.'));
  fireEvent.click(screen.getByRole('button', { name: 'Save Scheduler' }));
  await waitFor(() => expect(writes).toHaveLength(2));expect(writes[1]!.body.expectedRevision).toBe(8);
});

it('uses one navigation guard for simultaneous drafts on different subpages', async () => {
  await newJob();
  fireEvent.click(screen.getByRole('tab', { name: 'Trigger' }));
  fireEvent.click(screen.getByRole('button', { name: 'New Trigger' }));
  fireEvent.change(screen.getByLabelText('Trigger name'), { target: { value: 'Another draft' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Event Register' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit Shared timer' }));
  fireEvent.change(screen.getByLabelText('Scheduler name'), { target: { value: 'Source draft' } });
  vi.mocked(window.confirm).mockReturnValue(false);
  fireEvent.click(screen.getByRole('link', { name: 'Manage Actions' }));
  await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('heading', { name: 'Automation' })).toBeTruthy();
  const unload = new Event('beforeunload', { cancelable: true });window.dispatchEvent(unload);expect(unload.defaultPrevented).toBe(true);
});
