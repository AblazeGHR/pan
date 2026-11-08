// @vitest-environment jsdom
/**
 * Unified directory: Groups live in the same navigation as Actions, keep their
 * fixed versions/release visible, and bind to a Session in one of three MCP
 * modes with the observed CAS revision. Legacy-only records stay reachable
 * through the legacy writer surface instead of being hidden.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import UnifiedActionsView from './UnifiedActionsView';
import { canonicalRequest, actionRequest } from '@/services/api';
import { useAutomationAuth } from '@/stores/automationStore';
import {
  useActionCatalogStore,
  useActionRegistryStore,
  useActionSessionDetailStore,
  useActionSessionsStore,
} from '@/stores/actionRegistryStore';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  canonicalRequest: vi.fn(),
  actionRequest: vi.fn(),
}));

const request = vi.mocked(canonicalRequest);
const legacyRequest = vi.mocked(actionRequest);
const ref = (revision: number) => ({ id: 'echo', revision, digest: String(revision).repeat(64) });
const releaseVersion = 'rel-9f8e7d6c5b4a';

const actionCatalog = {
  items: [
    {
      id: 'echo',
      name: 'Echo',
      description: 'echo',
      state: 'published',
      enabled: true,
      revision: 2,
      stateRevision: 2,
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      execution: { operation: 'process.command', config: {} },
      effectClass: 'read',
    },
  ],
  versions: { echo: { count: 2, latest: ref(2) } },
  references: { echo: ref(2) },
};

const groups = {
  items: [
    {
      id: 'release_group',
      name: 'Release Group',
      revision: 5,
      release: releaseVersion,
      tools: [{ alias: 'echo_tool', actionRef: ref(2) }],
    },
    { id: 'draft_group', name: 'Draft Group', revision: 1, tools: [{ alias: 'echo_tool', actionRef: ref(2) }] },
  ],
};

function canonicalMock(overrides: Record<string, unknown> = {}) {
  request.mockImplementation(async (path) => {
    if (path in overrides) {
      const value = overrides[path as string];
      if (value instanceof Error) throw value;
      return value as never;
    }
    if (path === 'jobs/actions') return actionCatalog as never;
    if (path === 'action-groups') return groups as never;
    if (path === 'jobs/actions/echo/versions')
      return { items: [{ revision: 2, ref: ref(2), state: 'published', enabled: true, definition: {} }] } as never;
    throw new Error(`Unhandled ${path}`);
  });
}

beforeEach(() => {
  sessionStorage.clear();
  useAutomationAuth.setState({ token: 'owned', authenticated: true, expiresAt: Date.now() + 600000 });
  useActionRegistryStore.getState().reset();
  useActionSessionsStore.getState().reset();
  useActionCatalogStore.getState().reset();
  useActionSessionDetailStore.setState({ entries: {} });
  request.mockReset();
  legacyRequest.mockReset();
  canonicalMock();
  legacyRequest.mockImplementation(async (path, method) => {
    if (path === 'actions') return { revision: 1, actions: [], groups: [] } as never;
    if (path === 'sessions') return { sessions: [] } as never;
    if (path === 'mcp/servers') return { servers: [], loaded: true } as never;
    throw new Error(`Unhandled legacy ${path} ${method}`);
  });
});

afterEach(cleanup);

async function openGroups() {
  render(<UnifiedActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: /^Groups/ }));
  return screen.findByRole('button', { name: 'Open Group Release Group' });
}

it('keeps Groups in the same directory with fixed versions, release and build controls', async () => {
  request.mockImplementation(async (path) => {
    if (path === 'jobs/actions') return actionCatalog as never;
    if (path === 'action-groups') return groups as never;
    if (path === `action-groups/releases/${releaseVersion}`)
      return {
        version: releaseVersion,
        digest: 'd'.repeat(64),
        tools: [{ alias: 'echo_tool', actionRef: ref(2) }],
      } as never;
    if (path === 'jobs/actions/echo/versions') return { items: [] } as never;
    throw new Error(`Unhandled ${path}`);
  });
  await openGroups();
  expect(screen.getByRole('button', { name: 'Open Group Draft Group' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open Group Release Group' }));
  expect(await screen.findByRole('button', { name: /在 Session 中使用/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: /重新 Build/ })).toBeTruthy();
  expect(screen.getByText(releaseVersion)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '查看当前 release' }));
  await waitFor(() => expect(screen.getByText('d'.repeat(64))).toBeTruthy());
  expect(request.mock.calls.some(([p]) => p === `action-groups/releases/${releaseVersion}`)).toBe(true);
});

it('binds a built Group to one Session in one of the three MCP modes with the observed CAS revision', async () => {
  legacyRequest.mockImplementation(async (path, method) => {
    if (path === 'actions') return { revision: 1, actions: [], groups: [] } as never;
    if (path === 'sessions')
      return {
        sessions: [
          {
            id: 's1',
            name: 'Session One',
            workerStatus: 'idle',
            pluginBindings: [],
            pluginSelectionRevision: 4,
          },
        ],
      } as never;
    if (path === 'mcp/servers')
      return {
        servers: [
          {
            id: 'action-group-release-group',
            name: 'action-group-release-group',
            supportedModes: ['standalone', 'pan_native', 'gateway'],
            defaultMode: 'pan_native',
          },
        ],
        loaded: true,
      } as never;
    if (path === 'sessions/s1')
      return {
        id: 's1',
        name: 'Session One',
        workerStatus: 'idle',
        pluginBindings: [],
        pluginSelectionRevision: method === 'PATCH' ? 5 : 4,
      } as never;
    throw new Error(`Unhandled legacy ${path}`);
  });
  await openGroups();
  fireEvent.click(screen.getByRole('button', { name: 'Open Group Release Group' }));
  fireEvent.click(await screen.findByRole('button', { name: /在 Session 中使用/ }));
  fireEvent.click(await screen.findByRole('radio', { name: /Session One/ }));
  fireEvent.click(await screen.findByRole('radio', { name: /Discover \/ call/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply to Session' }));
  await waitFor(() =>
    expect(legacyRequest.mock.calls.some(([p, method]) => p === 'sessions/s1' && method === 'PATCH')).toBe(
      true,
    ),
  );
  expect(
    legacyRequest.mock.calls.find(([p, method]) => p === 'sessions/s1' && method === 'PATCH')![2],
  ).toMatchObject({
    pluginBindings: [{ plugin_id: 'action-group-release-group', enabled: true, mode: 'gateway' }],
    expectedPluginRevision: 4,
  });
});

it('keeps the observed Group revision on build and only queries after a lost response', async () => {
  request.mockImplementation(async (path) => {
    if (path === 'jobs/actions') return actionCatalog as never;
    if (path === 'action-groups') return groups as never;
    if (path === 'jobs/actions/echo/versions') return { items: [] } as never;
    if (path === 'action-groups/release_group/build') throw new TypeError('lost response');
    if (path.startsWith('operations?'))
      return {
        status: 'unknown',
        operationId: 'op-group',
        requestKey: 'key',
        result: { status: 'uncertain' },
      } as never;
    throw new Error(`Unhandled ${path}`);
  });
  await openGroups();
  fireEvent.click(screen.getByRole('button', { name: 'Open Group Release Group' }));
  fireEvent.click(await screen.findByRole('button', { name: /重新 Build/ }));
  await screen.findByText(/lost response/);
  const body = request.mock.calls.find(([p]) => p === 'action-groups/release_group/build')![2] as {
    expectedRevision: number;
    requestKey: string;
  };
  expect(body.expectedRevision).toBe(5);
  fireEvent.click(screen.getByRole('button', { name: '查询原 Group 操作' }));
  await waitFor(() =>
    expect(request.mock.calls.some(([p]) => p.includes(encodeURIComponent(body.requestKey)))).toBe(true),
  );
  expect(request.mock.calls.filter(([p]) => p === 'action-groups/release_group/build')).toHaveLength(1);
});

it('shows a legacy-only Group through the legacy editor instead of inventing canonical state', async () => {
  request.mockImplementation(async (path) => {
    if (path === 'jobs/actions') return { items: [], versions: {}, references: {} } as never;
    if (path === 'action-groups') return { items: [] } as never;
    throw new Error(`Unhandled ${path}`);
  });
  legacyRequest.mockImplementation(async (path) => {
    if (path === 'actions')
      return {
        revision: 3,
        actions: [],
        groups: [{ id: 'old_group', name: 'Old Group', action_ids: ['echo'], blockers: [], sessions: [] }],
      } as never;
    if (path === 'sessions') return { sessions: [] } as never;
    if (path === 'mcp/servers') return { servers: [], loaded: true } as never;
    throw new Error(`Unhandled legacy ${path}`);
  });
  render(<UnifiedActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: /^Groups/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open Group Old Group' }));
  expect(await screen.findByText('Group identity')).toBeTruthy();
  expect(screen.getByText('旧记录')).toBeTruthy();
});

it('shows preserved and adopted state per row and blocks the preserved editor with its reason', async () => {
  const legacyAction = (id: string, name: string) => ({
    id,
    name,
    description: 'legacy',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
    execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
  });
  legacyRequest.mockImplementation(async (path) => {
    if (path === 'actions')
      return {
        revision: 4,
        actions: [legacyAction('echo', 'Echo'), legacyAction('preserved_one', 'Preserved One')],
        groups: [],
        canonicalActions: {
          echo: { object: { id: 'echo', name: 'Echo' }, definition: { name: 'Echo' }, reason: 'multi-command' },
        },
        preservedActions: [{ id: 'preserved_one', reason: 'unrepresentable shell execution' }],
      } as never;
    if (path === 'sessions') return { sessions: [] } as never;
    if (path === 'mcp/servers') return { servers: [], loaded: true } as never;
    throw new Error(`Unhandled legacy ${path}`);
  });
  render(<UnifiedActionsView />);
  expect(await screen.findByText('已接管旧定义')).toBeTruthy();
  expect(screen.getByText('已保留 · 写入阻断')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open Action Preserved One' }));
  expect(await screen.findByText(/unrepresentable shell execution/)).toBeTruthy();
});

it('creates a canonical Action draft from the same directory without publishing it', async () => {
  const posted: Array<Record<string, unknown>> = [];
  request.mockImplementation(async (path, method, body) => {
    if (path === 'actions' || path === 'jobs/actions') {
      if (method === 'POST') {
        posted.push(body as Record<string, unknown>);
        return { status: 'completed', operationId: 'op-create', requestKey: 'k' } as never;
      }
      return actionCatalog as never;
    }
    if (path === 'jobs/actions/echo/versions')
      return {
        items: [
          {
            revision: 2,
            ref: ref(2),
            state: 'published',
            enabled: true,
            definition: {
              name: 'Echo',
              inputSchema: { type: 'object', properties: {}, additionalProperties: false },
              execution: { operation: 'process.command', config: { argv: ['fixed'] } },
            },
          },
        ],
      } as never;
    if (path === 'action-groups') return groups as never;
    throw new Error(`Unhandled ${path} ${method}`);
  });
  render(<UnifiedActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: /新建 Action/ }));
  fireEvent.click(await screen.findByRole('button', { name: '新建统一 Action' }));
  const textarea = await screen.findByLabelText('Action definition');
  const definition = JSON.parse((textarea as HTMLTextAreaElement).value);
  fireEvent.change(textarea, {
    target: { value: JSON.stringify({ ...definition, name: 'Browser Action Draft' }, null, 2) },
  });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿版本' }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]!).toMatchObject({ expectedRevision: 0 });
  expect((posted[0]!.definition as { name: string }).name).toBe('Browser Action Draft');
  expect(typeof posted[0]!.requestKey).toBe('string');
  expect(posted[0]!).not.toHaveProperty('publish');
});

it('merges an adopted alias only through the explicit binding projection', async () => {
  request.mockImplementation(async (rawPath) => {
    const path = String(rawPath).split('?')[0];
    if (path === 'jobs/actions')
      return {
        items: [
          {
            id: 'canon_adopted',
            name: 'Adopted Canonical',
            description: 'adopted head',
            state: 'published',
            enabled: true,
            revision: 1,
            stateRevision: 1,
            inputSchema: { type: 'object', properties: {}, additionalProperties: false },
            execution: { operation: 'process.command', config: {} },
            effectClass: 'read',
          },
        ],
        versions: {},
        references: {},
      } as never;
    if (path === 'action-groups/legacy-action-bindings')
      return {
        items: [
          { legacyId: 'old_alias', actionId: 'canon_adopted', sourceRoot: 'C:/owned/source', deleted: false },
          { legacyId: 'gone_alias', actionId: 'canon_adopted', sourceRoot: 'C:/owned/source', deleted: true },
        ],
        nextCursor: null,
      } as never;
    if (path === 'action-groups') return { items: [] } as never;
    if (path === 'jobs/actions/canon_adopted/versions') return { items: [] } as never;
    throw new Error(`Unhandled ${path}`);
  });
  legacyRequest.mockImplementation(async (path) => {
    if (path === 'actions')
      return {
        revision: 1,
        actions: [
          {
            id: 'old_alias',
            name: 'Legacy alias name',
            description: 'legacy',
            enabled: true,
            effect: 'read',
            output_format: 'json',
            input_schema: { type: 'object', properties: {}, additionalProperties: false },
            execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
          },
          {
            id: 'gone_alias',
            name: 'Gone alias',
            description: 'legacy',
            enabled: true,
            effect: 'read',
            output_format: 'json',
            input_schema: { type: 'object', properties: {}, additionalProperties: false },
            execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
          },
        ],
        groups: [],
      } as never;
    if (path === 'sessions') return { sessions: [] } as never;
    if (path === 'mcp/servers') return { servers: [], loaded: true } as never;
    throw new Error(`Unhandled legacy ${path}`);
  });
  render(<UnifiedActionsView />);
  expect(await screen.findByText('已接管旧定义')).toBeTruthy();
  // The live binding is merged into the canonical row; the deleted one keeps
  // its legacy writer state instead of inheriting the canonical head.
  expect(screen.getByRole('button', { name: 'Open Action Adopted Canonical' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Open Action Gone alias' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Open Action Legacy alias name' })).toBeNull();
  // No inferred pairing: the singular alias endpoint is not consulted.
  assertNoSingularAliasCalls();
});

function assertNoSingularAliasCalls() {
  const calls = request.mock.calls.map(([path]) => String(path));
  expect(calls.some((path) => path.startsWith('jobs/actions/aliases/'))).toBe(false);
}

it('lists a Group release history and opens one immutable release', async () => {
  const first = {
    version: 'rel-first-0001',
    groupId: 'release_group',
    name: 'Release Group',
    revision: 4,
    digest: 'a'.repeat(64),
    tools: [{ alias: 'echo_tool', actionRef: ref(1) }],
  };
  const second = {
    version: releaseVersion,
    groupId: 'release_group',
    name: 'Release Group',
    revision: 5,
    digest: 'b'.repeat(64),
    tools: [{ alias: 'echo_tool', actionRef: ref(2) }],
  };
  request.mockImplementation(async (rawPath) => {
    const path = String(rawPath).split('?')[0];
    if (path === 'jobs/actions') return actionCatalog as never;
    if (path === 'action-groups') return groups as never;
    if (path === 'action-groups/legacy-action-bindings') return { items: [], nextCursor: null } as never;
    if (path === 'action-groups/release_group/releases')
      return { items: [second, first], nextCursor: null } as never;
    if (path === 'action-groups/releases/rel-first-0001') return first as never;
    if (path === 'jobs/actions/echo/versions') return { items: [] } as never;
    throw new Error(`Unhandled ${path}`);
  });
  render(<UnifiedActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: /^Groups/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open Group Release Group' }));
  const history = await screen.findByRole('region', { name: 'Release history' });
  expect(history.textContent).toContain('rel-first-0001');
  expect(history.textContent).toContain(releaseVersion);
  fireEvent.click(within(history).getByRole('button', { name: '查看 rel-first-0001' }));
  await waitFor(() =>
    expect(request.mock.calls.some(([p]) => p === 'action-groups/releases/rel-first-0001')).toBe(true),
  );
});
