// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LegacyActionsView } from './ActionsView';
import { actionRequest, ApiRequestError, canonicalRequest } from '@/services/api';
import { resetActionCachesForTests } from '@/stores/actionRegistryStore';
import type { ActionRegistry } from '@/types/actions';

vi.mock('@/services/api', async (original) => ({
  ...(await original<object>()),
  actionRequest: vi.fn(),
  canonicalRequest: vi.fn(),
}));

const definition = {
  name: 'Two commands',
  description: 'Advanced serial definition',
  system: false,
  effectClass: 'write_local',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  execution: {
    operation: 'process.command',
    config: {
      legacyOutputMerge: 'concat/v1',
      cwd: '/owned',
      commands: [
        { argv: ['tool', 'first'], env: { KEEP: 'first' } },
        { argv: ['tool', 'second'], timeout: 19 },
      ],
    },
  },
  retry: { idempotent: false },
  defaultCompletion: 'process-exit',
};

let registry: ActionRegistry;

function receipt(status = 'completed', object?: Record<string, unknown>) {
  return {
    operationId: 'op_canonical',
    requestKey: 'server-key',
    status,
    objectRefs: [],
    links: {},
    result: object ? { object } : {},
  };
}

beforeEach(() => {
  sessionStorage.clear();
  resetActionCachesForTests();
  vi.clearAllMocks();
  registry = {
    revision: 4,
    groups: [],
    actions: [
      {
        id: 'old_alias',
        name: 'Old alias',
        description: 'Retained source',
        enabled: true,
        effect: 'write_local',
        output_format: 'text',
        input_schema: { type: 'object', properties: {}, additionalProperties: false },
        execution: { kind: 'command', executable: '/old', args: [], cwd: '/owned' },
      },
    ],
    canonicalActions: {
      old_alias: {
        object: {
          ...definition,
          id: 'action_original',
          revision: 7,
          stateRevision: 3,
          state: 'published',
          enabled: true,
        },
        definition,
        reason: 'single command projection unavailable',
      },
    },
  };
  vi.mocked(actionRequest).mockImplementation(async () => structuredClone(registry));
  vi.mocked(canonicalRequest).mockImplementation(async (path, method) => {
    if (path === 'actions' && (!method || method === 'GET')) {
      return {
        items: [{ id: 'template_action', execution: { operation: 'process.command' } }],
        references: {},
        nextCursor: null,
      } as never;
    }
    if (path === 'jobs/actions/template_action/versions') {
      return { items: [{ revision: 1, definition: { ...definition, name: 'Template source' } }] } as never;
    }
    return receipt() as never;
  });
});

afterEach(cleanup);

async function openMapped() {
  render(<LegacyActionsView />);
  await screen.findByText('Old alias');
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Old alias' }));
}

it('duplicates a mapped advanced Action with the complete canonical definition', async () => {
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Duplicate canonical Action' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create canonical copy' }));
  await waitFor(() => expect(canonicalRequest).toHaveBeenCalled());
  const [path, method, body] = vi.mocked(canonicalRequest).mock.calls.find(
    ([value, verb]) => value === 'jobs/actions' && verb === 'POST',
  )!;
  expect(path).toBe('jobs/actions');
  expect(method).toBe('POST');
  expect(body).toMatchObject({
    definition: { ...definition, name: 'Two commands copy' },
    expectedRevision: 0,
  });
  expect((body as { requestKey: string }).requestKey).toBeTruthy();
  expect(vi.mocked(actionRequest).mock.calls.filter(([, verb]) => verb === 'POST')).toHaveLength(0);
});

it('publishes or disables the mapped canonical head with observed CAS', async () => {
  registry.canonicalActions!.old_alias!.object.state = 'draft';
  registry.canonicalActions!.old_alias!.object.enabled = false;
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Publish canonical Action' }));
  await waitFor(() => expect(canonicalRequest).toHaveBeenCalled());
  const [path, method, body] = vi.mocked(canonicalRequest).mock.calls.find(
    ([value]) => String(value).endsWith('/operations'),
  )!;
  expect(path).toBe('jobs/actions/action_original/operations');
  expect(method).toBe('POST');
  expect(body).toMatchObject({
    operation: 'publish',
    expectedRevision: 7,
    expectedStateRevision: 3,
  });
  expect(vi.mocked(actionRequest).mock.calls.filter(([, verb]) => verb === 'POST')).toHaveLength(0);
});

it('does not silently advance canonical CAS when the registry refreshes', async () => {
  await openMapped();
  registry.canonicalActions!.old_alias!.object.revision = 8;
  registry.canonicalActions!.old_alias!.object.stateRevision = 4;
  fireEvent.click(screen.getByRole('button', { name: 'Disable canonical Action' }));
  await waitFor(() => expect(canonicalRequest).toHaveBeenCalled());
  expect(
    vi.mocked(canonicalRequest).mock.calls.find(([value]) => String(value).endsWith('/operations'))?.[2],
  ).toMatchObject({
    operation: 'disable',
    expectedRevision: 7,
    expectedStateRevision: 3,
  });
});

it('keeps an uncertain lifecycle request query-only and never resends it', async () => {
  let failed = false;
  vi.mocked(canonicalRequest).mockImplementation(async (path) => {
    if (String(path).endsWith('/operations') && !failed) {
      failed = true;
      throw new TypeError('Network disconnected');
    }
    if (String(path).startsWith('operations?')) return receipt() as never;
    return { items: [], references: {}, nextCursor: null } as never;
  });
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Disable canonical Action' }));
  await screen.findByText(/Network disconnected/);
  expect(
    (screen.getByRole('button', { name: 'Disable canonical Action' }) as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Query original lifecycle operation' }));
  await screen.findByText(/Operation status:completed/);
  const writes = vi
    .mocked(canonicalRequest)
    .mock.calls.filter(([, method]) => method === 'POST' || method === 'DELETE');
  expect(writes).toHaveLength(1);
  expect(
    vi.mocked(canonicalRequest).mock.calls.some(([path]) =>
      String(path).startsWith('operations?operation=disable'),),
  ).toBe(true);
});

it('shows permission denial without retrying and preserves the draft reason', async () => {
  vi.mocked(canonicalRequest).mockImplementation(async (path) => {
    if (String(path).endsWith('/operations')) throw new ApiRequestError(403, 'Object capability denied');
    return { items: [], references: {}, nextCursor: null } as never;
  });
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Disable canonical Action' }));
  expect((await screen.findByRole('alert')).textContent).toContain('Permission denied');
  expect(
    (screen.getByRole('button', { name: 'Disable canonical Action' }) as HTMLButtonElement).disabled,
  ).toBe(false);
  expect(screen.getByText(/single command projection unavailable/)).toBeTruthy();
  expect(vi.mocked(canonicalRequest).mock.calls.filter(([, method]) => method === 'POST')).toHaveLength(1);
});

it('blocks deletion of a mapped Action that is still used by a Group', async () => {
  registry.groups = [
    {
      id: 'tools',
      name: 'Tools',
      action_ids: ['old_alias'],
      blockers: [],
      sessions: [],
    },
  ];
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Delete canonical Action' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(vi.mocked(actionRequest).mock.calls.filter(([, method]) => method === 'DELETE')).toHaveLength(0);
});

it('deletes an unreferenced mapped Action through the compatibility adapter with registry CAS', async () => {
  await openMapped();
  fireEvent.click(screen.getByRole('button', { name: 'Delete canonical Action' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete Action' }));
  await waitFor(() =>
    expect(
      vi.mocked(actionRequest).mock.calls.some(
        ([path, method]) =>
          method === 'DELETE' &&
          String(path).startsWith('actions/actions/old_alias?expectedRevision=4'),
      ),
    ).toBe(true),
  );
  expect(vi.mocked(canonicalRequest).mock.calls.filter(([, method]) => method === 'DELETE')).toHaveLength(0);
});

it('creates a new advanced canonical Action from the old entry without a legacy write', async () => {
  render(<LegacyActionsView />);
  await screen.findByText('Old alias');
  fireEvent.click(screen.getByRole('button', { name: 'New advanced Action' }));
  fireEvent.click(screen.getByRole('button', { name: 'New definition' }));
  const text = await screen.findByRole('textbox', { name: 'Action definition' });
  fireEvent.change(text, { target: { value: JSON.stringify(definition) } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft version' }));
  await waitFor(() => expect(canonicalRequest).toHaveBeenCalled());
  const write = vi.mocked(canonicalRequest).mock.calls.find(
    ([path, method]) => path === 'jobs/actions' && method === 'POST',
  )!;
  expect(write[2]).toMatchObject({ definition, expectedRevision: 0 });
  expect(vi.mocked(actionRequest).mock.calls.filter(([, verb]) => verb === 'POST')).toHaveLength(0);
});
