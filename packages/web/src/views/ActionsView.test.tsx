// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ActionsView from './ActionsView';
import { actionRequest } from '@/services/api';
import { resetActionCachesForTests, useActionRegistryStore } from '@/stores/actionRegistryStore';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import type { Session } from '@/types';

vi.mock('@/services/api', () => ({ actionRequest: vi.fn() }));
const invoke = vi.mocked(actionRequest);

const source: Action = {
  id: 'list_sessions',
  name: 'List Sessions',
  description: 'List isolated Sessions',
  enabled: true,
  effect: 'read',
  output_format: 'json',
  input_schema: { type: 'object', properties: {}, required: [], additionalProperties: false },
  execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
};

let registry: ActionRegistry;
let nextId = 1;

function makeSession(id: string, name: string, bindings: Session['pluginBindings'] = []): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    pluginBindings: bindings,
    pluginSelectionRevision: 4,
  } as Session;
}

function makeBuiltGroup(overrides: Partial<ActionGroup> = {}): ActionGroup {
  return {
    id: 'tools',
    name: 'Tools',
    action_ids: ['list_sessions'],
    build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: ['list_sessions'] },
    blockers: [],
    sessions: [],
    ...overrides,
  };
}

beforeEach(() => {
  registry = { revision: 1, actions: [structuredClone(source)], groups: [] };
  nextId = 1;
  invoke.mockReset();
  resetActionCachesForTests();
  invoke.mockImplementation(async (path, method = 'GET', body) => {
    if (path === 'actions' && method === 'GET') return structuredClone(registry);
    if (path === 'mcp/servers') {
      return {
        servers: [
          {
            name: 'action-group-tools',
            displayName: 'Tools',
            supportedModes: ['standalone', 'pan_native', 'gateway'],
            defaultMode: 'pan_native',
          },
        ],
      };
    }
    if (path === 'sessions' && method === 'GET') {
      return {
        sessions: [
          makeSession('s1', 'First', [{ plugin_id: 'other', enabled: true, mode: 'standalone' }]),
          makeSession('s2', 'Second'),
        ],
      };
    }
    if (path?.startsWith('sessions/') && method === 'GET') {
      const id = decodeURIComponent(path.split('/')[1] ?? '');
      return makeSession(id, id === 's1' ? 'First' : 'Second', [
        { plugin_id: 'other', enabled: true, mode: 'standalone' },
      ]);
    }
    if (path?.startsWith('sessions/') && method === 'PATCH') {
      const id = decodeURIComponent(path.split('/')[1] ?? '');
      const request = body as { pluginBindings: NonNullable<Session['pluginBindings']> };
      return {
        ...makeSession(id, id === 's1' ? 'First' : 'Second', request.pluginBindings),
        pluginApplyStatus: 'applied',
      } as Session;
    }
    if (method === 'POST' && path === 'actions/actions') {
      const request = body as { item: Action };
      const item = { ...structuredClone(request.item), id: `action_copy_${nextId++}` };
      registry.actions.push(item);
      registry.revision += 1;
      return { ...structuredClone(registry), item: structuredClone(item) };
    }
    if (method === 'PUT' && path?.startsWith('actions/actions/')) {
      const request = body as { item: Action };
      registry.actions = registry.actions.map((action) =>
        action.id === request.item.id ? structuredClone(request.item) : action,
      );
      registry.revision += 1;
      return structuredClone(registry);
    }
    if (method === 'DELETE' && path?.startsWith('actions/actions/')) {
      const id = decodeURIComponent(path.split('/')[2]?.split('?')[0] ?? '');
      registry.actions = registry.actions.filter((action) => action.id !== id);
      registry.revision += 1;
      return structuredClone(registry);
    }
    if (method === 'POST' && path?.endsWith('/build')) {
      const id = decodeURIComponent(path.split('/')[2] ?? '');
      registry.groups = registry.groups.map((group) =>
        group.id === id
          ? {
              ...group,
              build: {
                plugin_id: `action-group-${id}`,
                version: 'v2',
                action_ids: [...group.action_ids],
              },
              blockers: [],
            }
          : group,
      );
      registry.revision += 1;
      return structuredClone(registry);
    }
    if (method === 'POST' && path?.endsWith('/unbuild')) {
      const id = decodeURIComponent(path.split('/')[2] ?? '');
      registry.groups = registry.groups.map((group) =>
        group.id === id ? { ...group, build: undefined, sessions: [], blockers: [] } : group,
      );
      registry.revision += 1;
      return structuredClone(registry);
    }
    throw new Error(`Unhandled request ${method} ${path}`);
  });
});

afterEach(() => {
  cleanup();
});

async function renderList() {
  render(<ActionsView />);
  await screen.findByRole('button', { name: 'Edit List Sessions' });
}

describe('Action Panel cache and lists', () => {
  it('loads only the registry first and renders the compact list', async () => {
    await renderList();
    expect(invoke.mock.calls.map(([path]) => path)).toEqual(['actions']);
    expect(screen.getByRole('button', { name: /^Actions/ }).textContent).toContain('1');
    expect(screen.getByRole('button', { name: 'New Action' })).toBeTruthy();
    expect(screen.getByLabelText('Search Actions')).toBeTruthy();
  });

  it('filters and clears without another request', async () => {
    await renderList();
    fireEvent.change(screen.getByLabelText('Search Actions'), { target: { value: 'missing' } });
    expect(screen.getByText('No Actions match this view')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByRole('button', { name: 'Edit List Sessions' })).toBeTruthy();
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('keeps cached rows and shows a retry banner when refresh fails', async () => {
    await renderList();
    invoke.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText(/Couldn’t refresh\./)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit List Sessions' })).toBeTruthy();
  });
});

describe('Action create, edit and duplicate', () => {
  it('edits, duplicates and saves a Session Operation without changing its original or Group release', async () => {
    const pause: Action = { ...structuredClone(source), id: 'pause', name: 'Pause selected', effect: 'write_local', execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'explicit', ids: ['s1', 'missing'] } } };
    registry.actions.push(pause);
    registry.groups = [makeBuiltGroup()];
    const groupBefore = structuredClone(registry.groups);
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Pause selected' }));
    expect(screen.getByRole('heading', { name: 'Pause selected' })).toBeTruthy();
    expect((screen.getByLabelText('Effect') as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getAllByText(/Not in current Session list/).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('Target source'), { target: { value: 'event' } });
    fireEvent.change(screen.getByLabelText('Target Event path'), { target: { value: 'payload.targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await screen.findByRole('button', { name: 'Edit Pause selected' });
    expect(registry.actions.find((a) => a.id === 'pause')?.execution).toEqual({ kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'event', path: 'payload.targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'More actions for Pause selected' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Duplicate Action/ }));
    await screen.findByRole('heading', { name: 'Pause selected copy' });
    fireEvent.change(screen.getByLabelText('Target source'), { target: { value: 'explicit' } });
    fireEvent.change(screen.getByLabelText('Session ID to add'), { target: { value: 's2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Session ID' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await screen.findByRole('button', { name: 'Edit Pause selected copy' });
    expect(registry.actions.find((a) => a.name === 'Pause selected copy')?.execution).toMatchObject({ selector: { mode: 'explicit', ids: ['s2'] } });
    expect(registry.actions.find((a) => a.id === 'pause')?.execution).toMatchObject({ selector: { mode: 'event', path: 'payload.targets' } });
    expect(registry.groups).toEqual(groupBefore);
    expect(invoke.mock.calls.filter(([path, method]) => path === 'actions' && (!method || method === 'GET'))).toHaveLength(1);
  });
  it('creates Session Operation in the new editor and rejects invalid Event targets without posting', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'New Action' }));
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'New pause' } });
    fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: 'Pause targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'Session Operation' }));
    expect((screen.getByLabelText('Effect') as HTMLSelectElement).value).toBe('write_local');
    fireEvent.change(screen.getByLabelText('Target source'), { target: { value: 'event' } });
    fireEvent.change(screen.getByLabelText('Target Event path'), { target: { value: 'payload..targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    expect(screen.getByRole('alert').textContent).toContain('valid target Event');
    expect(invoke.mock.calls.some(([, method]) => method === 'POST')).toBe(false);
    fireEvent.change(screen.getByLabelText('Target Event path'), { target: { value: 'payload.targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    await screen.findByRole('button', { name: 'Edit New pause' });
    expect(registry.actions.find((a) => a.name === 'New pause')?.execution).toMatchObject({ kind: 'session_operation', selector: { path: 'payload.targets' } });
  });
  it('blocks Group build for enabled Session Operation while preserving membership and build APIs', async () => {
    registry.actions[0] = { ...structuredClone(source), effect: 'write_local', execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'explicit', ids: ['s1'] } } };
    registry.groups = [makeBuiltGroup({ build: undefined })];
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: /^Groups/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tools' }));
    expect((screen.getByRole('button', { name: 'Build Group' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText(/Session Operation is automation only/).length).toBeGreaterThan(0);
    expect(invoke.mock.calls.some(([path]) => path?.endsWith('/build'))).toBe(false);
  });
  it('creates an Action without a client ID and returns to the list', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'New Action' }));
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Echo' } });
    fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: 'Echo a value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    await screen.findByRole('button', { name: 'Edit Echo' });
    const post = invoke.mock.calls.find(([path, method]) => path === 'actions/actions' && method === 'POST');
    expect(post).toBeTruthy();
    const body = post?.[2] as { item: Action; expectedRevision: number };
    expect(body.item).not.toHaveProperty('id');
    expect(body.expectedRevision).toBe(1);
    expect(registry.actions.some((action) => action.name === 'Echo')).toBe(true);
  });

  it('duplicates through POST without an ID, opens the copy, and never touches Group references', async () => {
    registry.groups = [makeBuiltGroup()];
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: /^Groups/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Actions/ }));
    fireEvent.click(screen.getByRole('button', { name: 'More actions for List Sessions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Duplicate Action/ }));

    await screen.findByRole('heading', { name: 'List Sessions copy' });
    const post = invoke.mock.calls.find(([path, method]) => path === 'actions/actions' && method === 'POST');
    const body = post?.[2] as { item: Action; expectedRevision: number };
    expect(body.item).not.toHaveProperty('id');
    expect(body.item.name).toBe('List Sessions copy');
    expect(body.expectedRevision).toBe(1);
    expect(registry.groups[0]!.action_ids).toEqual(['list_sessions']);
    expect(useActionRegistryStore.getState().registry.actions.filter((action) => action.id === 'list_sessions')).toHaveLength(1);
    expect(screen.getByText('action_copy_1')).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Copy renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await screen.findByRole('button', { name: 'Edit Copy renamed' });
    expect(invoke.mock.calls.filter(([path, method]) => path === 'actions/actions' && method === 'POST')).toHaveLength(1);
    expect(invoke.mock.calls.some(([path, method]) => path === 'actions/actions/action_copy_1' && method === 'PUT')).toBe(true);
  });

  it('uses a clear suffixed default name when the first copy name already exists', async () => {
    registry.actions.push({ ...structuredClone(source), id: 'list_sessions_copy', name: 'List Sessions copy' });
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'More actions for List Sessions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Duplicate Action/ }));
    await screen.findByRole('heading', { name: 'List Sessions copy 2' });
    const post = invoke.mock.calls.find(([path, method]) => path === 'actions/actions' && method === 'POST');
    expect((post?.[2] as { item: Action }).item.name).toBe('List Sessions copy 2');
  });

  it('keeps the draft and shows the write error', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'Edit List Sessions' }));
    fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: 'My edit' } });
    invoke.mockRejectedValueOnce(new Error('Action registry changed; reload before saving'));
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect((await screen.findByRole('alert')).textContent).toContain('registry changed');
    expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe('My edit');
    expect((screen.getByRole('button', { name: 'Save Action' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('does not submit malformed advanced JSON and keeps the text for repair', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'Edit List Sessions' }));
    fireEvent.click(screen.getByRole('button', { name: /Advanced JSON schema/ }));
    fireEvent.change(screen.getByLabelText('Input schema JSON'), { target: { value: '{bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(invoke.mock.calls.some(([, method]) => method === 'PUT')).toBe(false);
    expect((screen.getByLabelText('Input schema JSON') as HTMLTextAreaElement).value).toBe('{bad');
  });

  it('guards a dirty draft before returning to the list', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'Edit List Sessions' }));
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Changed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('dialog')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByDisplayValue('Changed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    await screen.findByRole('button', { name: 'New Action' });
  });
});

describe('Group release and Session binding', () => {
  it('builds and unbuilds a Group through the existing mutation paths', async () => {
    registry.groups = [makeBuiltGroup()];
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: /^Groups/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tools' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Rebuild Group' }));
    await waitFor(() =>
      expect(invoke.mock.calls.some(([path, method]) => path === 'actions/groups/tools/build' && method === 'POST')).toBe(true),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Unbuild Group' }));
    await waitFor(() =>
      expect(invoke.mock.calls.some(([path, method]) => path === 'actions/groups/tools/unbuild' && method === 'POST')).toBe(true),
    );
  });

  it('preserves other plugin bindings and sends the expected revision', async () => {
    registry.groups = [makeBuiltGroup()];
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: /^Groups/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tools' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use in Session' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(await screen.findByText('First', { exact: true }));
    await screen.findByText('Plugin binding');
    fireEvent.click(screen.getByText('Discover / call', { exact: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply to Session' }));
    await waitFor(() =>
      expect(invoke.mock.calls.some(([path, method]) => path === 'sessions/s1' && method === 'PATCH')).toBe(true),
    );
    const patch = invoke.mock.calls.find(([path, method]) => path === 'sessions/s1' && method === 'PATCH');
    expect(patch?.[2]).toMatchObject({
      expectedPluginRevision: 4,
      pluginBindings: [
        { plugin_id: 'other', enabled: true, mode: 'standalone' },
        { plugin_id: 'action-group-tools', enabled: true, mode: 'gateway' },
      ],
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(dialog).toBeTruthy();
  });

  it('disables unbuild while a running Session blocks the Group', async () => {
    registry.groups = [
      makeBuiltGroup({
        blockers: [{ id: 's1', name: 'First' }],
        sessions: [
          {
            id: 's1',
            name: 'First',
            enabled: true,
            mode: 'pan_native',
            plugin_id: 'action-group-tools',
            loadedVersion: 'v1',
          },
        ],
      }),
    ];
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: /^Groups/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tools' }));
    const unbuild = await screen.findByRole('button', { name: 'Unbuild Group' });
    expect((unbuild as HTMLButtonElement).disabled).toBe(true);
    expect(unbuild.getAttribute('title')).toContain('blocked by running Sessions');
  });
});
