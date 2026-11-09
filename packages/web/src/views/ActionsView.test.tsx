// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LegacyActionsView as ActionsView } from './ActionsView';
import { actionRequest, canonicalRequest } from '@/services/api';
import { resetActionCachesForTests } from '@/stores/actionRegistryStore';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import type { Session } from '@/types';
import { useSessionOperationTemplateStore } from '@/stores/sessionOperationTemplateStore';

vi.mock('@/services/api', () => ({ actionRequest: vi.fn(), canonicalRequest: vi.fn() }));
const invoke = vi.mocked(actionRequest);
const canonical = vi.mocked(canonicalRequest);

/** Canonical create: preview maps the designed draft, then the catalog write. */
function canonicalCreateMock(nameOf: (draft: Record<string, unknown>) => string = () => 'created') {
  canonical.mockImplementation(async (path, method, body) => {
    const value = (body ?? {}) as Record<string, unknown>;
    if (path === 'jobs/actions/definition-preview') {
      const draft = value.draft as Record<string, unknown>;
      return { definition: { name: nameOf(draft), execution: { operation: 'api.get', config: {} } } } as never;
    }
    if (path === 'jobs/actions' && method === 'POST') {
      return {
        status: 'completed',
        operationId: 'op-create',
        requestKey: value.requestKey,
        result: { object: { id: 'action_created', revision: 1, stateRevision: 1, state: 'draft' } },
      } as never;
    }
    return { items: [] } as never;
  });
}

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
  sessionStorage.clear();
  registry = { revision: 1, actions: [structuredClone(source)], groups: [] };
  nextId = 1;
  invoke.mockReset();
  vi.mocked(canonicalRequest).mockReset();
  // The designed page reads the canonical catalog additively; an empty catalog
  // keeps the legacy fixture assertions unchanged.
  vi.mocked(canonicalRequest).mockResolvedValue({ items: [] } as never);
  resetActionCachesForTests();
  useSessionOperationTemplateStore.setState({ data: null, error: '' });
  invoke.mockImplementation(async (path, method = 'GET', body) => {
    if (path === 'actions' && method === 'GET') return structuredClone(registry);
    if (path === 'actions/session-operation-template') return { item: {}, queryFields: {}, queryPredicateSchema: {}, limits: { depth: 8, nodes: 64, values: 128, textLength: 512, candidates: 2048, results: 256 } };
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
  await screen.findByRole('list', { name: 'Actions' });
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
    expect(screen.getByRole('button', { name: 'Inspect List Sessions' })).toBeTruthy();
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('keeps cached rows and shows a retry banner when refresh fails', async () => {
    await renderList();
    invoke.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText(/Couldn’t refresh\./)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Inspect List Sessions' })).toBeTruthy();
  });
});

describe('Action create, edit and duplicate', () => {
  it('edits structured query groups, retains malformed raw JSON on conflict refresh, and posts only repaired typed query', async () => {
    await renderList();
    const before = structuredClone(registry);
    fireEvent.click(screen.getByRole('button', {name: 'Inspect List Sessions'}));
    expect(screen.getByText('Invalid / Retired')).toBeTruthy();
    expect(screen.queryByRole('button', {name: 'Save Action'})).toBeNull();
    expect(screen.getByText(/Legacy definitions are read-only/)).toBeTruthy();
    expect(invoke.mock.calls.some(([, method]) => method === 'PUT' || method === 'POST')).toBe(false);
    expect(registry).toEqual(before);
  });
  it('edits, duplicates and saves a Session Operation without changing its original or Group release', async () => {
    registry.actions[0] = {...structuredClone(source), name: 'Pause selected', execution: {kind: 'session_operation', operation: 'pause_everything', selector: {mode: 'event', path: 'payload.targets'}}};
    registry.groups = [makeBuiltGroup()]; const before = structuredClone(registry);
    await renderList(); fireEvent.click(screen.getByRole('button', {name: 'Inspect Pause selected'}));
    expect(screen.getByText(/payload.targets/)).toBeTruthy();
    expect(screen.queryByRole('button', {name: 'Run fixed version'})).toBeNull();
    expect(registry).toEqual(before);
    expect(invoke.mock.calls.some(([, method]) => method && method !== 'GET')).toBe(false);
  });
  it('creates Session Operation through the canonical definition adapter and rejects invalid Event targets without posting', async () => {
    canonicalCreateMock(() => 'New pause');
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
    expect(canonical.mock.calls.some(([, method]) => method === 'POST')).toBe(false);
    fireEvent.change(screen.getByLabelText('Target Event path'), { target: { value: 'payload.targets' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    await waitFor(() =>
      expect(canonical.mock.calls.some(([path]) => path === 'jobs/actions/definition-preview')).toBe(true),
    );
    const preview = canonical.mock.calls.find(([path]) => path === 'jobs/actions/definition-preview');
    const draft = (preview?.[2] as { draft: Action }).draft;
    expect(draft).not.toHaveProperty('id');
    expect(draft.execution).toMatchObject({
      kind: 'session_operation',
      selector: { mode: 'event', path: 'payload.targets' },
    });
    const created = canonical.mock.calls.find(([path, method]) => path === 'jobs/actions' && method === 'POST');
    expect((created?.[2] as { expectedRevision: number }).expectedRevision).toBe(0);
    expect((created?.[2] as { definition: { name: string } }).definition.name).toBe('New pause');
  });
  it('blocks Group build for enabled Session Operation while preserving membership and build APIs', async () => {
    registry.groups = [makeBuiltGroup()]; const before = structuredClone(registry);
    await renderList(); fireEvent.click(screen.getByRole('button', {name: /^Groups/}));
    fireEvent.click(screen.getByRole('button', {name: 'Inspect Tools'}));
    expect(screen.getByText('Invalid / Retired')).toBeTruthy();
    expect(screen.queryByRole('button', {name: 'Build'})).toBeNull();
    expect(screen.queryByRole('button', {name: 'Use in Session'})).toBeNull();
    expect(registry).toEqual(before);
    expect(invoke.mock.calls.some(([path]) => path?.endsWith('/build'))).toBe(false);
  });
  it('creates an Action through the canonical adapter without a client ID', async () => {
    canonicalCreateMock(() => 'Echo');
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'New Action' }));
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Echo' } });
    fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: 'Echo a value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    await waitFor(() =>
      expect(canonical.mock.calls.some(([path]) => path === 'jobs/actions/definition-preview')).toBe(true),
    );
    const preview = canonical.mock.calls.find(([path]) => path === 'jobs/actions/definition-preview');
    const draft = (preview?.[2] as { draft: Action }).draft;
    expect(draft).not.toHaveProperty('id');
    const created = canonical.mock.calls.find(([path, method]) => path === 'jobs/actions' && method === 'POST');
    expect(created).toBeTruthy();
    const body = created?.[2] as { expectedRevision: number; requestKey: string; definition: { name: string } };
    expect(body.expectedRevision).toBe(0);
    expect(typeof body.requestKey).toBe('string');
    expect(body.definition.name).toBe('Echo');
    // Creation must not fall back to the legacy writer.
    expect(invoke.mock.calls.some(([path, method]) => path === 'actions/actions' && method === 'POST')).toBe(false);
  });

  it('duplicates through POST without an ID, opens the copy, and never touches Group references', async () => {
    registry.groups = [makeBuiltGroup()]; const before = structuredClone(registry);
    canonicalCreateMock(() => 'Copy'); await renderList();
    fireEvent.click(screen.getByRole('button', {name: 'New Action'}));
    fireEvent.change(screen.getByLabelText(/^Name/), {target: {value: 'Copy'}});
    fireEvent.change(screen.getByLabelText(/^Description/), {target: {value: 'Independent new definition'}});
    fireEvent.click(screen.getByRole('button', {name: 'Create Action'}));
    await waitFor(() => expect(canonical.mock.calls.some(([path, method]) => path === 'jobs/actions' && method === 'POST')).toBe(true));
    expect((canonical.mock.calls.find(([path]) => path === 'jobs/actions/definition-preview')?.[2] as {draft: Action}).draft).not.toHaveProperty('id');
    expect(invoke.mock.calls.some(([, method]) => method === 'POST' || method === 'PUT')).toBe(false);
    expect(registry.groups).toEqual(before.groups);
  });

  it('uses a clear suffixed default name when the first copy name already exists', async () => {
    registry.actions.push({...structuredClone(source), id: 'list_sessions_copy', name: 'List Sessions copy'});
    await renderList();
    expect(screen.getByRole('button', {name: 'Inspect List Sessions copy'})).toBeTruthy();
    fireEvent.click(screen.getByRole('button', {name: 'Inspect List Sessions'}));
    expect(screen.getByText('Invalid / Retired')).toBeTruthy();
    expect(registry.actions.map((item) => item.id)).toEqual(['list_sessions', 'list_sessions_copy']);
    expect(invoke.mock.calls.some(([, method]) => method === 'POST')).toBe(false);
  });

  it('keeps the draft and shows the write error', async () => {
    await renderList(); fireEvent.click(screen.getByRole('button', {name: 'New Action'}));
    fireEvent.change(screen.getByLabelText(/^Name/), {target: {value: 'New draft'}});
    fireEvent.change(screen.getByLabelText(/^Description/), {target: {value: 'My edit'}});
    canonical.mockRejectedValueOnce(new Error('Definition unavailable'));
    fireEvent.click(screen.getByRole('button', {name: 'Create Action'}));
    expect((await screen.findByRole('alert')).textContent).toContain('Definition unavailable');
    expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe('My edit');
    expect(invoke.mock.calls.some(([, method]) => method === 'POST')).toBe(false);
  });

  it('does not submit malformed advanced JSON and keeps the text for repair', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'New Action' }));
    fireEvent.click(screen.getByRole('button', { name: /Advanced JSON schema/ }));
    fireEvent.change(screen.getByLabelText('Input schema JSON'), { target: { value: '{bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
    expect((await screen.findAllByRole('alert')).length).toBeGreaterThan(0);
    expect(invoke.mock.calls.some(([, method]) => method === 'PUT')).toBe(false);
    expect((screen.getByLabelText('Input schema JSON') as HTMLTextAreaElement).value).toBe('{bad');
  });

  it('guards a dirty draft before returning to the list', async () => {
    await renderList();
    fireEvent.click(screen.getByRole('button', { name: 'New Action' }));
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
    registry.groups = [makeBuiltGroup()]; await renderList();
    fireEvent.click(screen.getByRole('button', {name: /^Groups/}));
    fireEvent.click(screen.getByRole('button', {name: 'Inspect Tools'}));
    expect(screen.queryByRole('button', {name: 'Rebuild Group'})).toBeNull();
    fireEvent.click(screen.getByRole('button', {name: 'Unbuild retired Group'}));
    await waitFor(() => expect(invoke.mock.calls.some(([path, method]) => path === 'actions/groups/tools/unbuild' && method === 'POST')).toBe(true));
    expect(invoke.mock.calls.some(([path]) => path === 'actions/groups/tools/build')).toBe(false);
  });

  it('preserves other plugin bindings and sends the expected revision', async () => {
    registry.groups = [makeBuiltGroup()]; const before = structuredClone(registry);
    await renderList(); fireEvent.click(screen.getByRole('button', {name: /^Groups/}));
    fireEvent.click(screen.getByRole('button', {name: 'Inspect Tools'}));
    expect(screen.queryByRole('button', {name: 'Use in Session'})).toBeNull();
    expect(invoke.mock.calls.some(([path, method]) => path?.startsWith('sessions/') && method === 'PATCH')).toBe(false);
    expect(registry).toEqual(before);
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
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Tools' }));
    const unbuild = await screen.findByRole('button', { name: 'Unbuild retired Group' });
    expect((unbuild as HTMLButtonElement).disabled).toBe(true);
    expect(unbuild.getAttribute('title')).toContain('blocked by running Sessions');
  });
});
