// @vitest-environment jsdom
// Caller contract evidence only: fixture HTTP replies do not execute a provider.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './CanonicalJobsView';
import { useAutomationAuth } from '@/stores/automationStore';

const text = { type: 'string', minLength: 1 };
// Schema/required/completion shapes from FinalService._publish_product_actions.
const actions = [
  { id: 'product_assign_v2', name: 'assign', inputSchema: {
    type: 'object', properties: { sessionId: text, text, source: { enum: ['MA'] }, sourceSessionId: text, taskId: text },
    required: ['sessionId', 'text'], additionalProperties: false,
  }, defaultCompletion: 'durable-queue-accepted' },
  { id: 'product_process_logs_v1', name: 'process', inputSchema: {
    type: 'object', properties: { argv: { type: 'array', items: text, minItems: 1 }, cwd: text, hideWindow: { type: 'boolean' } },
    required: ['argv', 'cwd'], additionalProperties: false,
  }, defaultCompletion: 'process-exit' },
].map((action) => ({ ...action, state: 'published', enabled: true, revision: 1,
  completionContracts: [{ id: action.defaultCompletion }] }));
const references = Object.fromEntries(actions.map((a) => [a.id, { id: a.id, revision: 1, digest: `fixture-${a.id}` }]));
let writes: { url: string; body: Record<string, any> }[];
let rejected: string;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  writes = [];
  rejected = '';
  useAutomationAuth.setState({ token: 'owned-r13-fixture', expiresAt: Date.now() + 60000 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    if (init.method && init.method !== 'GET') {
      writes.push({ url, body: JSON.parse(String(init.body)) });
      if (rejected) return response({ error: { code: rejected } }, rejected === 'revision_conflict' ? 409 : 422);
      return response({ operationId: 'owned-operation', requestKey: writes.at(-1)!.body.requestKey, status: 'completed', objectRefs: [], links: {} });
    }
    if (url === '/api/jobs/actions') return response({ items: actions, references, nextCursor: null });
    if (url.includes('job-templates')) return response({ items: [{ id: 'passive', name: 'No Creator', state: 'published', revision: 2,
      stateRevision: 1, jobDefinition: { name: 'assign', steps: [] }, composition: { creators: [], triggers: [] } }], nextCursor: null });
    return response({ items: [], nextCursor: null });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); useAutomationAuth.getState().clear(); });
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const change = (name: string, value: string) => fireEvent.change(screen.getByLabelText(name), { target: { value } });
function host() { render(<MemoryRouter><CanonicalJobsView /></MemoryRouter>); }
async function choose(id: string) {
  host(); click('创建 Jobs'); change('名称', '  原文 Job  ');
  await screen.findByRole('option', { name: /assign · Revision/ });
  change('Action for step 1', id);
}
function parameter(name: string, value: string) {
  change('Add parameter for step 1', name);
  change(`${name} value`, value);
}

it('existing assign caller writes exact literal fields and immutable Action reference to a passive canonical Job', async () => {
  await choose('product_assign_v2');
  change('sessionId value', 'ses_owned'); change('text value', '  原文\ntext  ');
  parameter('source', 'MA'); parameter('sourceSessionId', 'ses_caller'); parameter('taskId', 'task_owned');
  expect(writes).toEqual([]);
  click('保存定义');
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.url).toBe('/api/jobs');
  expect(writes[0]!.body).toMatchObject({ expectedRevision: 0, definition: { name: '  原文 Job  ', steps: [{
    stepId: 'step1', actionRef: references.product_assign_v2,
    completion: { contract: 'durable-queue-accepted' }, arguments: {
      sessionId: { from: 'literal', value: 'ses_owned' }, text: { from: 'literal', value: '  原文\ntext  ' },
      source: { from: 'literal', value: 'MA' }, sourceSessionId: { from: 'literal', value: 'ses_caller' }, taskId: { from: 'literal', value: 'task_owned' },
    },
  }] } });
  expect(writes[0]!.body.requestKey).toEqual(expect.any(String));
  expect(writes[0]!.body.definition).not.toHaveProperty('enabled');
  expect(writes[0]!.body.definition).not.toHaveProperty('action');
});

it('existing process caller preserves argv boundaries/cwd/hideWindow without guessing command-string shell syntax', async () => {
  await choose('product_process_logs_v1');
  const argv = ['owned executable', 'a b', '"quoted"', '$literal', '中文'];
  change('argv value (JSON)', JSON.stringify(argv)); change('cwd value', '  owned directory  ');
  parameter('hideWindow', 'true');
  expect(writes).toEqual([]);
  click('保存定义'); await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]!.url).toBe('/api/jobs');
  expect(writes[0]!.body.definition.steps[0]).toEqual({ stepId: 'step1', actionRef: references.product_process_logs_v1,
    completion: { contract: 'process-exit' }, arguments: { argv: { from: 'literal', value: argv },
      cwd: { from: 'literal', value: '  owned directory  ' }, hideWindow: { from: 'literal', value: true } } });
  expect(writes[0]!.body.definition.steps[0].arguments).not.toHaveProperty('command');
});

it('missing Action selection rejects locally without a legacy fallback write', async () => {
  host(); click('创建 Jobs'); change('名称', 'Incomplete');
  click('Add step'); click('保存定义');
  expect(await screen.findAllByText(/Choose an Action for each step/)).toHaveLength(2);
  expect(writes).toEqual([]);
});

it('server rejection preserves the structured draft without retrying a legacy endpoint', async () => {
  await choose('product_assign_v2'); change('sessionId value', 'ses_owned'); change('text value', 'raw');
  rejected = 'revision_conflict'; click('保存定义');
  await screen.findByText(/\[revision_conflict\]/);
  expect((screen.getByLabelText('text value') as HTMLInputElement).value).toBe('raw');
  expect(writes).toHaveLength(1); expect(writes[0]!.url).toBe('/api/jobs');
});

it('no-Creator Template exposes structured unsupported and keeps activation false without a fallback or Run request', async () => {
  host(); click('JobTemplates'); await screen.findByRole('button', { name: 'Instantiate No Creator' });
  click('Instantiate No Creator');
  expect((screen.getByLabelText('Activate future time events') as HTMLInputElement).checked).toBe(false);
  expect(writes).toEqual([]);
  rejected = 'capability_unsupported'; click('Create from time Template');
  await screen.findByText(/\[capability_unsupported\]/);
  expect(writes).toHaveLength(1);
  expect(writes[0]!.url).toBe('/api/job-templates/passive/instantiate');
  expect(writes[0]!.body).toMatchObject({ activate: false, expectedRevision: 2 });
  expect(screen.getByRole('region', { name: 'Instantiate time Template' })).toBeTruthy();
});
