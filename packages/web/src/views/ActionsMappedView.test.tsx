// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LegacyActionsView } from './ActionsView';
import { actionRequest, canonicalRequest } from '@/services/api';
import { resetActionCachesForTests, useActionRegistryStore } from '@/stores/actionRegistryStore';
import type { ActionRegistry } from '@/types/actions';

vi.mock('@/services/api', async (original) => ({ ...await original<object>(), actionRequest: vi.fn(), canonicalRequest: vi.fn() }));
let registry: ActionRegistry;
const definition = { name: 'Two commands', system: false, inputSchema: { type: 'object' },
  execution: { operation: 'process.command', config: { legacyOutputMerge: 'concat/v1', cwd: '/owned',
    commands: [{ argv: ['tool', 'first'], env: { KEEP: 'first' } }, { argv: ['tool', 'second'], timeout: 19 }] } } };
beforeEach(() => {
  sessionStorage.clear();
  resetActionCachesForTests();
  vi.clearAllMocks();
  registry = { revision: 4, groups: [], actions: [{ id: 'old_alias', name: 'Old alias', description: 'Retained source',
    enabled: true, effect: 'write_local', output_format: 'text', input_schema: { type: 'object', properties: {}, additionalProperties: false },
    execution: { kind: 'command', executable: '/old', args: [], cwd: '/owned' } }],
    canonicalActions: { old_alias: { object: { id: 'action_original', revision: 7, name: 'Two commands' }, definition, reason: 'single command projection unavailable' } } };
  vi.mocked(actionRequest).mockImplementation(async () => structuredClone(registry));
  vi.mocked(canonicalRequest).mockResolvedValue({ operationId: 'op_original', requestKey: 'saved', status: 'completed', objectRefs: [], links: {} });
});
afterEach(cleanup);

it('opens the full mapped definition through the old Edit entry and keeps its observed CAS on refresh', async () => {
  render(<LegacyActionsView />);
  await screen.findByText('Old alias');
  fireEvent.click(screen.getByRole('button', { name: 'Edit Old alias' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑为新草稿版本' }));
  const text = screen.getByRole('textbox', { name: 'Action definition' });
  const edited = { ...definition, name: 'Edited full series' };
  expect(JSON.parse((text as HTMLTextAreaElement).value)).toEqual(definition);
  fireEvent.change(text, { target: { value: JSON.stringify(edited) } });
  registry.canonicalActions!.old_alias!.object.revision = 8;
  useActionRegistryStore.setState({ registry: structuredClone(registry) });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿版本' }));
  await waitFor(() => expect(canonicalRequest).toHaveBeenCalled());
  const [path, method, body] = vi.mocked(canonicalRequest).mock.calls[0]!;
  expect(path).toBe('jobs/actions/action_original');
  expect(method).toBe('PUT');
  expect(body).toMatchObject({ expectedRevision: 7, definition: edited });
  expect(vi.mocked(actionRequest).mock.calls.every(([, verb]) => !verb || verb === 'GET')).toBe(true);
});

it('retains the existing discard confirmation for unsaved multi-command text', async () => {
  render(<LegacyActionsView />);
  await screen.findByText('Old alias');
  fireEvent.click(screen.getByRole('button', { name: 'Edit Old alias' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑为新草稿版本' }));
  fireEvent.click(screen.getByRole('button', { name: '返回旧目录' }));
  expect((await screen.findByRole('dialog')).textContent).toContain('Discard unsaved changes?');
  expect(canonicalRequest).not.toHaveBeenCalled();
});
