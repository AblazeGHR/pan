// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ActionsView from './ActionsView';
import { actionRequest } from '@/services/api';
import type { ActionRegistry, InputSchema } from '@/types/actions';

vi.mock('@/services/api', () => ({ actionRequest: vi.fn() }));
const invoke = vi.mocked(actionRequest);
let registry: ActionRegistry;
const source = { id: 'list_sessions', name: 'List sessions', description: 'List Sessions', enabled: true,
  effect: 'read' as const, output_format: 'json' as const,
  input_schema: { type: 'object' as const, properties: {}, required: [], additionalProperties: false as const },
  execution: { kind: 'api' as const, method: 'GET' as const, path: '/api/sessions' } };

beforeEach(() => {
  registry = { revision: 1, actions: [structuredClone(source)], groups: [] };
  invoke.mockReset();
  invoke.mockImplementation(async (path, method = 'GET', body) => {
    if (path === 'actions' && method === 'GET') return structuredClone(registry);
    if (path === 'mcp/servers') return { servers: [{ name: 'action-group-tools', displayName: 'Tools', supportedModes: ['standalone', 'pan_native', 'gateway'] }] };
    if (path === 'sessions') return { sessions: [{ id: 's1', name: 'First' }, { id: 's2', name: 'Second' }] };
    if (path?.startsWith('sessions/')) return { id: path.split('/')[1], name: path, pluginBindings: [], pluginSelectionRevision: 0 };
    const request = body as { item: typeof source; expectedRevision: number };
    if (method === 'POST' && path === 'actions/actions') {
      const item = { ...request.item, id: 'action_generated' }; registry.actions.push(item); registry.revision++;
      return { ...structuredClone(registry), item };
    }
    if (method === 'PUT' && path?.startsWith('actions/actions/')) registry.actions = [...registry.actions.filter((a) => a.id !== request.item.id), request.item];
    registry.revision++;
    return structuredClone(registry);
  });
});
afterEach(cleanup);

describe('core Actions Panel', () => {
  it('edits API drafts whose valid object schema omits properties', async () => {
    delete (registry.actions[0]!.input_schema as Partial<InputSchema>).properties;
    render(<ActionsView />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit List sessions' }));
    expect(screen.getByLabelText('Description')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('New input field'), { target: { value: 'filter' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }));
    expect(screen.getByLabelText('filter type')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('actions/actions/list_sessions', 'PUT', expect.objectContaining({
      item: expect.objectContaining({ input_schema: expect.objectContaining({ properties: { filter: { type: 'string' } } }) }) })));
  });

  it('creates an Action with typed input fields and fixed command argv', async () => {
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    fireEvent.click(screen.getByRole('tab', { name: 'New Action' }));
    expect(screen.queryByLabelText('ID', { exact: true })).toBeNull();
    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'Echo' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Echo a value' } });
    fireEvent.change(screen.getByLabelText('New input field'), { target: { value: 'value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }));
    fireEvent.change(screen.getByLabelText('value type'), { target: { value: 'integer' } });
    fireEvent.change(screen.getByLabelText('Execution'), { target: { value: 'command' } });
    fireEvent.change(screen.getByLabelText('Executable'), { target: { value: 'C:/python.exe' } });
    fireEvent.change(screen.getByLabelText('Working directory'), { target: { value: 'D:/work' } });
    fireEvent.change(screen.getByLabelText('Arguments (one per line)'), { target: { value: 'echo.py\n{value}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('actions/actions', 'POST', expect.objectContaining({ expectedRevision: 1,
      item: expect.objectContaining({ input_schema: expect.objectContaining({ properties: { value: { type: 'integer' } }, required: ['value'] }),
        execution: expect.objectContaining({ args: ['echo.py', '{value}'] }) }) })));
  });

  it('edits shared references through Group selection and displays build metadata', async () => {
    registry.groups = [{ id: 'tools', name: 'Tools', action_ids: ['list_sessions'],
      build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: ['list_sessions'] },
      blockers: [{ id: 'busy_id', name: 'Busy MA' }], sessions: [{ id: 'busy_id', name: 'Busy MA', enabled: true, mode: 'gateway', plugin_id: 'action-group-tools', loadedVersion: 'v0' }] }];
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    fireEvent.click(screen.getByRole('tab', { name: 'Groups' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tools' }));
    expect(screen.getByText('tools')).toBeTruthy();
    expect(screen.queryByLabelText('Group ID')).toBeNull();
    expect(screen.getByText('Version: v1')).toBeTruthy();
    expect(screen.getByText('Exposed Actions: list_sessions')).toBeTruthy();
    expect(screen.getByText(/Unbuild blocked by running Sessions: Busy MA \(busy_id\)/)).toBeTruthy();
    expect(screen.getByText(/Loaded version: v0/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'New Group' }));
    expect(screen.queryByLabelText('Group ID')).toBeNull();
    expect(screen.getAllByRole('checkbox').some((input) => (input as HTMLInputElement).checked)).toBe(false);
  });

  it('keeps user edits and shows write errors', async () => {
    render(<ActionsView />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit List sessions' }));
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'My edit' } });
    invoke.mockRejectedValueOnce(new Error('Action registry changed; reload before saving'));
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect((await screen.findByRole('alert')).textContent).toContain('registry changed');
    expect((screen.getByLabelText('Description') as HTMLInputElement).value).toBe('My edit');
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save Action' }) as HTMLButtonElement).disabled).toBe(false));
  });

  it('ignores stale Session loads and uses shared plugin bindings with English modes', async () => {
    registry.groups = [{ id: 'tools', name: 'Tools', action_ids: ['list_sessions'], build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: ['list_sessions'] }, blockers: [], sessions: [] }];
    let resolveFirst: (value: unknown) => void = () => {};
    const base = invoke.getMockImplementation()!;
    invoke.mockImplementation((path, ...args) => path === 'sessions/s1'
      ? new Promise((resolve) => { resolveFirst = resolve; }) : base(path, ...args));
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    fireEvent.click(screen.getByRole('tab', { name: 'Groups' }));
    fireEvent.click(screen.getByRole('button', { name: 'Use in Session' }));
    fireEvent.change(screen.getByLabelText('Session', { exact: true }), { target: { value: 's1' } });
    fireEvent.change(screen.getByLabelText('Session', { exact: true }), { target: { value: 's2' } });
    await screen.findByLabelText('action-group-tools mode');
    await act(async () => resolveFirst({ id: 's1', name: 'First', pluginBindings: [{ plugin_id: 'action-group-tools', enabled: true, mode: 'pan_native' }], pluginSelectionRevision: 8 }));
    expect((screen.getByRole('checkbox', { name: /Tools/ }) as HTMLInputElement).checked).toBe(false);
    fireEvent.change(screen.getByLabelText('action-group-tools mode'), { target: { value: 'gateway' } });
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('sessions/s2', 'PATCH', expect.objectContaining({ expectedPluginRevision: 0,
      pluginBindings: [{ plugin_id: 'action-group-tools', enabled: false, mode: 'gateway' }] })));
  });
});


describe('Actions navigation and creation states', () => {
  it('keeps a committed creation ID when the follow-up catalog read fails', async () => {
    const base = invoke.getMockImplementation()!;
    let created = false;
    invoke.mockImplementation(async (path, ...args) => {
      if (path === 'mcp/servers' && created) { created = false; throw new Error('Catalog unavailable'); }
      const result = await base(path, ...args);
      if (path === 'actions/actions' && args[0] === 'POST') created = true;
      return result;
    });
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    fireEvent.click(screen.getByRole('tab', { name: 'New Action' }));
    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'Committed' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Keep its ID' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Catalog unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('actions/actions/action_generated', 'PUT', expect.anything()));
    expect(invoke.mock.calls.filter(([path, method]) => path === 'actions/actions' && method === 'POST')).toHaveLength(1);
  });

  it('shows three regions and saves the generated identity on later edits', async () => {
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Actions', 'New Action', 'Groups']);
    fireEvent.click(screen.getByRole('tab', { name: 'New Action' }));
    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'Generated' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Created without ID' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await screen.findByRole('button', { name: 'Edit Generated' });
    const createdBody = invoke.mock.calls.find(([path, method]) => path === 'actions/actions' && method === 'POST')?.[2] as { item: object };
    expect(createdBody.item).not.toHaveProperty('id');
    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('actions/actions/action_generated', 'PUT', expect.objectContaining({ item: expect.objectContaining({ id: 'action_generated', name: 'Renamed' }) })));
  });

  it('does not submit malformed advanced JSON and keeps the text for repair', async () => {
    render(<ActionsView />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit List sessions' }));
    fireEvent.change(screen.getByLabelText('Input schema'), { target: { value: '{bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(invoke.mock.calls.some(([,method]) => method === 'PUT')).toBe(false);
    expect((screen.getByLabelText('Input schema') as HTMLTextAreaElement).value).toBe('{bad');
  });

  it('explains an empty Session list in the Group binding context', async () => {
    registry.groups = [{ id: 'tools', name: 'Tools', action_ids: [], build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: [] }, blockers: [], sessions: [] }];
    const base = invoke.getMockImplementation()!;
    invoke.mockImplementation((path, ...args) => path === 'sessions' ? Promise.resolve({ sessions: [] }) : base(path, ...args));
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'Edit List sessions' });
    expect(screen.queryByLabelText('Session', { exact: true })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Groups' }));
    fireEvent.click(screen.getByRole('button', { name: 'Use in Session' }));
    expect(screen.getByText(/No Sessions available/)).toBeTruthy();
    expect(screen.queryByLabelText('Session', { exact: true })).toBeNull();
  });
});
