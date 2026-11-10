// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CanonicalJobEditor } from './CanonicalJobEditor';
import { canonicalJobs } from '@/services/canonicalJobs';
import { CanonicalExecutionDetails } from './CanonicalExecutionDetails';

const ref = { id: 'action_process', revision: 2, digest: 'a'.repeat(64) };
const action = { ...ref, name: 'Process', state: 'published', enabled: true, inputSchema: { type: 'object', properties: {
  argv: { type: 'array', items: { type: 'string' } }, cwd: { type: 'string' }, env: { type: 'object', additionalProperties: { type: 'string' } }, timeout: { type: 'number' },
}, required: ['argv', 'cwd', 'env', 'timeout'] }, defaultCompletion: 'process-exit', completionContracts: [{ id: 'process-exit' }] };
beforeEach(() => { sessionStorage.clear(); vi.spyOn(canonicalJobs, 'actions').mockResolvedValue({ items: [action], references: { [ref.id]: ref }, nextCursor: null }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('creates a canonical Job with schema typed argv, env, cwd and timeout without JSON input', async () => {
  const write = vi.spyOn(canonicalJobs, 'write').mockResolvedValue({ status: 'completed', requestKey: 'k', operationId: 'op', objectRefs: [], links: {} });
  const saved = vi.fn(); render(<CanonicalJobEditor onSaved={saved} />);
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Typed process' } });
  await screen.findByRole('option', { name: 'Process · Revision 2' });
  fireEvent.change(screen.getByLabelText('Action for step 1'), { target: { value: ref.id } });
  expect(screen.queryByLabelText('timeout value type')).toBeNull();
  expect((screen.getByLabelText('timeout value') as HTMLInputElement).type).toBe('number');
  fireEvent.click(screen.getByRole('button', { name: 'Add argv value item' }));
  fireEvent.change(screen.getByLabelText('argv value item 1'), { target: { value: 'python' } });
  fireEvent.change(screen.getByLabelText('cwd value'), { target: { value: 'owned' } });
  fireEvent.change(screen.getByLabelText('New env value field'), { target: { value: 'MODE' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add env value field' }));
  fireEvent.change(screen.getByLabelText('env value MODE'), { target: { value: 'test' } });
  fireEvent.change(screen.getByLabelText('timeout value'), { target: { value: '19' } });
  expect(screen.getByText('Advanced definition JSON').closest('details')?.open).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Create Action Job' }));
  await waitFor(() => expect(write).toHaveBeenCalledOnce());
  expect(write.mock.calls[0]?.[2]).toMatchObject({ name: 'Typed process', steps: [{ actionRef: ref, arguments: {
    argv: { from: 'literal', value: ['python'] }, cwd: { from: 'literal', value: 'owned' }, env: { from: 'literal', value: { MODE: 'test' } }, timeout: { from: 'literal', value: 19 },
  } }] });
  expect(saved).toHaveBeenCalledOnce();
});
it('shows exact fixed configuration and preserves unprojected fields in full detail', () => {
  const key = `${ref.id}:${ref.revision}:${ref.digest}`;
  render(<CanonicalExecutionDetails detail={{ job: { id: 'j', name: 'Owned', state: 'enabled', stateRevision: 1, configRevision: 2, custom: { keep: true }, steps: [{ stepId: 'a', actionRef: ref, arguments: { argv: { from: 'literal', value: ['python'] }, env: { from: 'literal', value: { MODE: 'test' } }, timeout: { from: 'literal', value: 19 } } }] }, actions: { [key]: { name: 'Process', execution: { operation: 'process.command', config: { cwd: 'owned', hideWindow: true } } } }, missing: [] }} />);
  expect(screen.getByText('19')).toBeTruthy(); expect(screen.getByText('Hidden')).toBeTruthy(); expect(screen.getAllByText(/MODE/).some(node => node.tagName === 'DD')).toBe(true);
  expect(screen.getByText(/"custom"/).textContent).toContain('"keep": true');
});

it('uses the exact historical Action schema and retains its reference while editing', async () => {
  const previous = { id: ref.id, revision: 1, digest: 'b'.repeat(64) };
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{ revision: 1, digest: previous.digest, definition: { name: 'Earlier process', inputSchema: { type: 'object', properties: { oldOnly: { type: 'string' }, oldExtra: { type: 'integer' } }, required: ['oldOnly'] }, completionContracts: [{ id: 'process-exit' }] } }] }), { status: 200 })));
  const write = vi.spyOn(canonicalJobs, 'write').mockResolvedValue({ status: 'completed', requestKey: 'k', operationId: 'op', objectRefs: [], links: {} });
  const item = { id: 'owned', name: 'Original', description: 'kept', state: 'draft', stateRevision: 2, configRevision: 7, retention: { policyRef: 'keep' }, steps: [{ stepId: 'one', actionRef: previous, arguments: { oldOnly: { from: 'literal', value: 'retained' } }, completion: { contract: 'process-exit' } }] };
  render(<CanonicalJobEditor item={item} onSaved={vi.fn()} />);
  await screen.findByRole('option', { name: 'oldExtra' });
  expect(screen.queryByRole('option', { name: 'timeout' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Edited' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Action Job' }));
  await waitFor(() => expect(write).toHaveBeenCalledOnce());
  expect(write.mock.calls[0]?.[1]).toMatchObject({ id: 'owned', configRevision: 7 });
  expect(write.mock.calls[0]?.[2]).toMatchObject({ name: 'Edited', description: 'kept', retention: { policyRef: 'keep' }, steps: [{ actionRef: previous, arguments: { oldOnly: { from: 'literal', value: 'retained' } } }] });
});
