// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './CanonicalJobsView';
import { useAutomationAuth } from '@/stores/automationStore';

const template = {
  id: 'template_owned',
  name: 'Owned template',
  description: 'Complete advanced template',
  state: 'published',
  stateRevision: 2,
  revision: 5,
  jobDefinition: {
    name: 'Owned job',
    steps: [
      {
        stepId: 'effect',
        actionRef: { id: 'action_original', revision: 2, digest: 'digest' },
        arguments: {},
        completion: { contract: 'process-exit' },
      },
    ],
    retention: { policyRef: 'default' },
  },
  composition: { creators: [], triggers: [] },
};

let writes: { url: string; method: string; body: Record<string, unknown> }[];

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

beforeEach(() => {
  writes = [];
  useAutomationAuth.setState({ token: 'test-user', expiresAt: Date.now() + 60000 });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET';
      if (method !== 'GET') {
        writes.push({ url, method, body: JSON.parse(String(init.body)) });
        return response({
          operationId: 'op_one',
          requestKey: 'key',
          status: 'completed',
          objectRefs: [],
          links: {},
        });
      }
      if (url === '/api/job-maintenance/records') return response({ items: [], message: '' });
      if (url.startsWith('/api/job-templates?')) return response({ items: [template], nextCursor: null });
      return response(template);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useAutomationAuth.getState().clear();
});

it('duplicates a complete canonical Template with its full advanced definition', async () => {
  render(
    <MemoryRouter>
      <CanonicalJobsView />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'JobTemplates' }));
  await screen.findByRole('article', { name: 'JobTemplates Owned template' });
  fireEvent.click(screen.getByRole('button', { name: 'duplicate Owned template' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({
    url: '/api/job-templates',
    method: 'POST',
    body: {
      expectedRevision: 0,
      definition: {
        name: 'Owned template copy',
        description: template.description,
        jobDefinition: template.jobDefinition,
        composition: template.composition,
      },
    },
  });
  expect(writes[0]!.body.requestKey).toBeTruthy();
  expect(JSON.stringify(writes[0]!.body.definition)).not.toContain('stateRevision');
});

it('publishes a draft canonical Template with the observed revision and state revision', async () => {
  template.state = 'draft';
  try {
    render(
      <MemoryRouter>
        <CanonicalJobsView />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'JobTemplates' }));
    await screen.findByRole('article', { name: 'JobTemplates Owned template' });
    fireEvent.click(screen.getByRole('button', { name: 'Publish Owned template' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toMatchObject({
      url: '/api/job-templates/template_owned/operations',
      method: 'POST',
      body: {
        operation: 'publish',
        expectedRevision: 5,
        expectedStateRevision: 2,
        parameters: {},
      },
    });
    expect(writes[0]!.body.requestKey).toBeTruthy();
  } finally {
    template.state = 'published';
  }
});
