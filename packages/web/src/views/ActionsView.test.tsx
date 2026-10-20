// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ActionsView from './ActionsView';
import { actionRequest } from '@/services/api';
import type { ActionRegistry } from '@/types/actions';

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
    if (method === 'PUT' && path?.startsWith('actions/actions/')) registry.actions = [...registry.actions.filter((a) => a.id !== request.item.id), request.item];
    registry.revision++;
    return structuredClone(registry);
  });
});
afterEach(cleanup);

describe('core Actions Panel', () => {
  it('creates an Action with typed input fields and fixed command argv', async () => {
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'List sessions · Enabled' });
    fireEvent.change(screen.getByLabelText('ID', { exact: true }), { target: { value: 'echo' } });
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
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('actions/actions/echo', 'PUT', expect.objectContaining({ expectedRevision: 1,
      item: expect.objectContaining({ input_schema: expect.objectContaining({ properties: { value: { type: 'integer' } }, required: ['value'] }),
        execution: expect.objectContaining({ args: ['echo.py', '{value}'] }) }) })));
  });

  it('edits shared references through Group selection and displays build metadata', async () => {
    registry.groups = [{ id: 'tools', name: 'Tools', action_ids: ['list_sessions'],
      build: { plugin_id: 'action-group-tools', version: 'v1', action_ids: ['list_sessions'] },
      blockers: [{ id: 'busy_id', name: 'Busy MA' }], sessions: [{ id: 'busy_id', name: 'Busy MA', enabled: true, mode: 'gateway', plugin_id: 'action-group-tools', loadedVersion: 'v0' }] }];
    render(<ActionsView />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tools · Registered' }));
    expect((screen.getByLabelText('Group ID') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('Version: v1')).toBeTruthy();
    expect(screen.getByText('Exposed Actions: list_sessions')).toBeTruthy();
    expect(screen.getByText(/Unbuild blocked by running Sessions: Busy MA \(busy_id\)/)).toBeTruthy();
    expect(screen.getByText(/Loaded version: v0/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'New Group' }));
    expect((screen.getByLabelText('Group ID') as HTMLInputElement).value).toBe('');
    expect(screen.getAllByRole('checkbox').some((input) => (input as HTMLInputElement).checked)).toBe(false);
  });

  it('keeps user edits and shows write errors', async () => {
    render(<ActionsView />);
    fireEvent.click(await screen.findByRole('button', { name: 'List sessions · Enabled' }));
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'My edit' } });
    invoke.mockRejectedValueOnce(new Error('Action registry changed; reload before saving'));
    fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
    expect((await screen.findByRole('alert')).textContent).toContain('registry changed');
    expect((screen.getByLabelText('Description') as HTMLInputElement).value).toBe('My edit');
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save Action' }) as HTMLButtonElement).disabled).toBe(false));
  });

  it('ignores stale Session loads and uses shared plugin bindings with English modes', async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    const base = invoke.getMockImplementation()!;
    invoke.mockImplementation((path, ...args) => path === 'sessions/s1'
      ? new Promise((resolve) => { resolveFirst = resolve; }) : base(path, ...args));
    render(<ActionsView />);
    await screen.findByRole('button', { name: 'List sessions · Enabled' });
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
