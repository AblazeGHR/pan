import { wsClient } from '@/services/ws';
// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './CanonicalJobsView';
import { useAutomationAuth } from '@/stores/automationStore';
import { runOperations } from '@/services/canonicalJobs';
import type { CanonicalKind } from '@/types/canonicalJobs';
let writes: { url: string; method: string; body: Record<string, unknown> }[];
let failure = 0;
let networkFailure = false;
let accepted = false;
let runState = 'failed';
const item = {
  id: 'canonical/one',
  name: 'Example',
  state: 'draft',
  stateRevision: 3,
  configRevision: 7,
  revision: 5,
  retention: { policyRef: 'default' },
  steps: [],
  schemaVersion: 2,
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const receipt = (status = 'completed', requestKey = String(writes.at(-1)?.body.requestKey ?? 'server-key')) => ({
  operationId: 'op/one',
  requestKey,
  status,
  objectRefs: [],
  links: {},
});
beforeEach(() => {
  writes = [];
  failure = 0;
  networkFailure = false;
  accepted = false;
  runState = 'failed';
  sessionStorage.clear();
  useAutomationAuth.setState({ token: 'test-user', expiresAt: Date.now() + 60000 });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET';
      if (method !== 'GET') {
        writes.push({
          url,
          method,
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
        });
        if (networkFailure) throw new TypeError('Network disconnected');
        if (failure)
          return response(
            {
              error: {
                code:
                  failure === 422
                    ? 'capability_unsupported'
                    : failure === 409
                      ? 'revision_conflict'
                      : 'authorization_denied',
              },
            },
            failure,
          );
        return response(receipt(accepted ? 'accepted' : 'completed'));
      }
      if (url === '/api/job-maintenance/records') return response({ items: [], message: '' });
      if (failure) return response({ error: { code: 'authorization_denied' } }, failure);
      if (url.includes('operations')) return response(receipt());
      if (url === '/api/jobs/actions')
        return response({ items: [], references: {}, nextCursor: null });
      if (url.includes('/runs?'))
        return response({
          items: [
            {
              runId: 'run/one',
              jobId: item.id,
              state: runState,
              stateRevision: 4,
              configRevision: 1,
            },
          ],
          nextCursor: null,
        });
      if (url.includes('limit=')) return response({ items: [item], nextCursor: null });
      return response(item);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useAutomationAuth.getState().clear();
});
function host() {
  return render(
    <MemoryRouter>
      <CanonicalJobsView />
    </MemoryRouter>,
  );
}
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
it('blocks repeat Template instantiation after a lost receipt and queries the original operation', async () => {
  item.state = 'published';
  try {
    host();
    click('JobTemplates');
    await screen.findByRole('button', { name: 'Instantiate Example' });
    click('Instantiate Example');
    expect(
      (screen.getByLabelText('Enable Job and configured subscriptions') as HTMLInputElement)
        .checked,
    ).toBe(false);
    networkFailure = true;
    click('Create from Template');
    await screen.findByText('The result is unconfirmed. Query the original identity.');
    expect(writes).toHaveLength(1);
    expect(writes[0]!.body).toMatchObject({ activate: false });
    expect(
      (screen.getByRole('button', { name: 'Create from Template' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    networkFailure = false;
    click('Query original operation');
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Instantiate Template' })).toBeNull(),
    );
    expect(writes).toHaveLength(1);
  } finally {
    item.state = 'draft';
  }
});
const names: Record<CanonicalKind, string> = {
  jobs: 'Jobs',
  triggers: 'Triggers',
  'event-creators': 'EventCreators',
  'job-templates': 'JobTemplates',
};
it.each(Object.entries(names))(
  'lists, creates, edits and retires %s with canonical CAS envelopes',
  async (kind, name) => {
    host();
    if (kind !== 'jobs') click(name);
    await screen.findByRole('article', { name: `${name} Example` });
    click(`Create ${name}`);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Created' } });
    fireEvent.change(screen.getByLabelText('Definition JSON (name is entered separately)'), {
      target: { value: '{"description":"new"}' },
    });
    click('Save definition');
    await waitFor(() => expect(writes.length).toBe(1));
    await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
    expect(writes[0]).toMatchObject({
      url: `/api/${kind}`,
      method: 'POST',
      body: { definition: { name: 'Created', description: 'new' }, expectedRevision: 0 },
    });
    click('Edit Example');
    const definition = JSON.parse(
      (screen.getByLabelText('Definition JSON (name is entered separately)') as HTMLTextAreaElement).value,
    );
    expect(definition).not.toHaveProperty('stateRevision');
    expect(definition).not.toHaveProperty('id');
    expect(definition).not.toHaveProperty('schemaVersion');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Edited' } });
    click('Save definition');
    await waitFor(() => expect(writes.length).toBe(2));
    await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
    expect(writes[1]).toMatchObject({
      url: `/api/${kind}/canonical%2Fone`,
      method: 'PUT',
      body: { expectedRevision: 7, definition: { name: 'Edited' } },
    });
    click('retire Example');
    click('Confirm retirement');
    await waitFor(() => expect(writes.length).toBe(3));
    expect(writes[2]).toMatchObject({
      method: 'DELETE',
      body: { expectedRevision: 7, expectedStateRevision: 3 },
    });
    expect(new Set(writes.map((w) => w.body.requestKey)).size).toBe(3);
  },
);
it.each(runOperations)(
  'performs %s using Run state revision and current Job config',
  async (operation) => {
    runState = operation === 'resume' ? 'paused_at_boundary' : operation === 'cancel' || operation === 'pause' ? 'running' : 'failed';
    host();
    await screen.findByText('Example');
    click('Runs Example');
    await screen.findByText(/run\/one/);
    click(operation);
    if (operation === 'retry')
      fireEvent.change(screen.getByLabelText('Step ID (optional; defaults to the failure boundary)'), {
        target: { value: 'failed-step' },
      });
    click('Confirm operation');
    await waitFor(() => expect(writes.length).toBe(1));
    expect(writes[0]).toMatchObject({
      url: '/api/job-runs/run%2Fone/operations',
      method: 'POST',
      body: { operation, expectedStateRevision: 4 },
    });
    if (operation === 'run-again' || operation === 'clone-and-run')
      expect(writes[0]!.body.expectedRevision).toBe(7);
    else expect(writes[0]!.body).not.toHaveProperty('expectedRevision');
    if (operation === 'retry') expect(writes[0]!.body.stepId).toBe('failed-step');
    await screen.findByText('Operation status:completed');
  },
);
it('disables operations when the Run state cannot admit them', async () => {
  runState = 'failed'; host(); await screen.findByText('Example'); click('Runs Example'); await screen.findByText(/run\/one/);
  for (const name of ['pause', 'resume', 'cancel']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'retry' }) as HTMLButtonElement).disabled).toBe(false);
  expect(writes).toHaveLength(0);
});
it.each([422, 403])('shows readable %s rejection for run operations', async (status) => {
  runState = 'running';
  host();
  await screen.findByText('Example');
  click('Runs Example');
  await screen.findByText(/run\/one/);
  click('pause');
  failure = status;
  click('Confirm operation');
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain(status === 422 ? 'unsupported' : 'Permission denied');
  expect(screen.getByRole('form', { name: 'Run operation' })).toBeTruthy();
});
it('keeps an edit draft on conflict and close does not submit', async () => {
  host();
  await screen.findByText('Example');
  click('Edit Example');
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My draft' } });
  failure = 409;
  click('Save definition');
  expect((await screen.findByRole('alert')).textContent).toContain('Revision conflict');
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('My draft');
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  click('Close editor');
  expect(screen.getByRole('form')).toBeTruthy();
  confirm.mockReturnValue(true);
  click('Close editor');
  expect(screen.queryByRole('form')).toBeNull();
  expect(writes.length).toBe(1);
});
it('does not resend an unconfirmed request and queries its original identity', async () => {
  host();
  await screen.findByText('Example');
  click('Edit Example');
  networkFailure = true;
  click('Save definition');
  await screen.findByText('Network disconnected');
  expect((screen.getByRole('button', { name: 'Save definition' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  click('Query original operation');
  await screen.findByText('Operation status:completed');
  const calls = vi.mocked(fetch).mock.calls;
  expect(
    calls.some(([url]) =>
      String(url).includes(`operations?operation=update&requestKey=${writes[0]!.body.requestKey}`),
    ),
  ).toBe(true);
  expect(writes.length).toBe(1);
});
it('shows accepted as pending effect and queries by operationId', async () => {
  host();
  await screen.findByText('Example');
  click('retire Example');
  accepted = true;
  click('Confirm retirement');
  await screen.findByText(/effect is not confirmed/);
  expect((screen.getByRole('button', { name: 'Confirm retirement' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  click('Query original operation');
  await screen.findByText('Operation status:completed');
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/operations/op%2Fone')).toBe(
    true,
  );
});
it('renders loading and list errors with a retry', async () => {
  let resolve!: (value: Response) => void;
  const original = vi.mocked(fetch).getMockImplementation()!;
  let delayed = false;
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (!delayed && String(url).startsWith('/api/jobs?')) {
      delayed = true;
      return new Promise<Response>((done) => {
        resolve = done;
      });
    }
    return original(url, init);
  });
  host();
  await screen.findByText('Loading...');
  resolve(response({ error: { code: 'authorization_denied' } }, 403));
  expect((await screen.findByRole('alert')).textContent).toContain('Permission denied');
  click('Refresh list');
  await screen.findByText('Example');
});
it('keeps canonical navigation and performs no legacy transport requests', async () => {
  host();
  await screen.findByText('Example');
  expect(screen.getByRole('link', { name: 'Automation' }).getAttribute('href')).toBe('/automation');
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(
        ([url]) =>
          url === '/api/job-maintenance/records' ||
          /^\/api\/(?:jobs|triggers|event-creators|job-templates|job-runs|operations)(?:\/|\?|$)/.test(
            String(url),
          ),
      ),
  ).toBe(true);
});
it('clears canonical data on logout and blocks unauthenticated reads', async () => {
  host();
  await screen.findByText('Example');
  click('Log out');
  await screen.findByText('Sign in to load Jobs.');
  expect(screen.queryByText('Example')).toBeNull();
});

it('allows an omitted name only for new Jobs and sends the convenience input unchanged', async () => {
  host();
  await screen.findByRole('heading', { name: 'Jobs', level: 2 });
  click('Create Jobs');
  expect((screen.getByLabelText('Name') as HTMLInputElement).required).toBe(false);
  fireEvent.change(screen.getByLabelText('Definition JSON (name is entered separately)'), {
    target: { value: '{"description":"unnamed"}' },
  });
  click('Save definition');
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.body.definition).toEqual({ description: 'unnamed' });
  expect(writes[0]!.body).toMatchObject({ expectedRevision: 0 });
});

it('refreshes canonical data from committed change signals and unsubscribes on unmount', async () => {
  let changed: (() => void) | undefined;
  const unsubscribe = vi.fn();
  vi.spyOn(wsClient, 'on').mockImplementation((type, handler) => {
    if (type === 'canonical.changed') changed = () => handler({ type });
    return unsubscribe;
  });
  const view = host();
  await screen.findByText('Example');
  expect(changed).toBeDefined();
  const before = vi.mocked(fetch).mock.calls.length;
  changed!();
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(before));
  expect(writes).toHaveLength(0);
  view.unmount();
  expect(unsubscribe).toHaveBeenCalled();
});
