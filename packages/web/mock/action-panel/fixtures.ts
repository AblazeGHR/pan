import type { Action, ActionGroup, ActionRegistry, InputSchema } from '@/types/actions';
import type { McpServerInfo, PluginBinding, PluginMode } from '@/types';

/**
 * Local fixtures for the Action Panel redesign mock.
 *
 * These records mirror the shape returned by GET /api/actions. They are long
 * enough to exercise truncation, wrapping, search, empty results and list
 * scrolling. Nothing here is read from or written to a real Pan server.
 */

const schema = (
  properties: InputSchema['properties'] = {},
  required: string[] = [],
): InputSchema => ({
  type: 'object',
  properties,
  required,
  additionalProperties: false,
});

export const fixtureActions: Action[] = [
  {
    id: 'action_7f2c91a4d83b6e05a1c47d92',
    name: 'List Sessions',
    description: 'List isolated Sessions with adapter, status and workspace metadata.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema(
      {
        adapter: { type: 'string', description: 'Filter by adapter id, for example codex or claude.' },
        include_archived: { type: 'boolean', description: 'Include archived Sessions in the result.' },
        limit: { type: 'integer', description: 'Maximum number of Sessions to return (1–200).', default: 50 },
      },
      ['limit'],
    ),
    execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
  },
  {
    id: 'action_2b8e40cd7a91f36e5d0c8b74',
    name: 'Session Transcript Search',
    description:
      'Search message text across one Session transcript and return highlighted matches with stable message anchors.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema(
      {
        session_id: { type: 'string', description: 'Session to search.' },
        query: { type: 'string', description: 'Plain-text query; case-insensitive.' },
        max_matches: { type: 'integer', default: 20 },
      },
      ['session_id', 'query'],
    ),
    execution: { kind: 'api', method: 'GET', path: '/api/sessions/{session_id}/search' },
  },
  {
    id: 'action_5c1d9e73b602a84f7c3e10d8',
    name: 'Echo',
    description: 'Echo an argument value back to the caller for connectivity checks.',
    enabled: true,
    effect: 'write_local',
    output_format: 'text',
    input_schema: schema({ value: { type: 'string' } }, ['value']),
    execution: {
      kind: 'command',
      executable: 'node',
      args: ['scripts/echo.mjs', '{value}'],
      cwd: 'D:/project/Pan',
      timeout_sec: 15,
    },
  },
  {
    id: 'action_9a6f0b25c7e3481d92b5a60e',
    name: 'Workspace Directory Scan',
    description: 'Enumerate Session working directories and flag missing or unreadable paths before cleanup.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema({ roots: { type: 'array', description: 'Optional explicit roots.' } }),
    execution: {
      kind: 'command',
      executable: 'python',
      args: ['scripts/scan_workspaces.py', '--json'],
      cwd: 'D:/project/Pan',
      timeout_sec: 30,
    },
  },
  {
    id: 'action_3e7a58d1f940c26b8e5d71a3',
    name: 'Model Catalog',
    description: 'Return registered adapters, model capabilities and the current fast/strong routing defaults.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema({ adapter: { type: 'string' } }),
    execution: { kind: 'api', method: 'GET', path: '/api/models' },
  },
  {
    id: 'action_8d4b2f60a1c95e37d80b6f42',
    name: 'Browser Session Snapshot',
    description: 'Capture open browser Session tabs, viewport size and console errors as one JSON snapshot.',
    enabled: false,
    effect: 'read',
    output_format: 'json',
    input_schema: schema({ session_id: { type: 'string' }, include_console: { type: 'boolean' } }, ['session_id']),
    execution: { kind: 'api', method: 'GET', path: '/api/sessions?adapter=browser' },
  },
  {
    id: 'action_6f0c84e2d5b17a39c4e8b205',
    name: 'Bulk Session Export',
    description:
      'Write a portable archive of selected Sessions, including transcripts, settings and workspace references.',
    enabled: true,
    effect: 'write_local',
    output_format: 'json',
    input_schema: schema(
      {
        session_ids: { type: 'array', description: 'Sessions to include in the archive.' },
        output_dir: { type: 'string', description: 'Absolute directory that receives the archive.' },
        include_artifacts: { type: 'boolean', default: true },
      },
      ['session_ids', 'output_dir'],
    ),
    execution: {
      kind: 'command',
      executable: 'python',
      args: ['scripts/export_sessions.py', '--out', '{output_dir}', '--ids', '{session_ids}'],
      cwd: 'D:/project/Pan',
      timeout_sec: 30,
    },
  },
  {
    id: 'action_1c9e63b7f408d25a90b6e34c',
    name: 'Native CLI Capability Probe',
    description: 'Detect resume, rewind and native-control support for an installed CLI adapter without starting a Session.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema({ adapter: { type: 'string' }, verbose: { type: 'boolean' } }, ['adapter']),
    execution: {
      kind: 'command',
      executable: 'python',
      args: ['scripts/probe_native.py', '--adapter', '{adapter}'],
      cwd: 'D:/project/Pan',
      timeout_sec: 20,
    },
  },
  {
    id: 'action_4a7d10f9c2e65b38a1d07e96',
    name: 'MCP Server Health',
    description: 'Check configured MCP servers for handshake latency, exposed tool count and the last error.',
    enabled: true,
    effect: 'read',
    output_format: 'json',
    input_schema: schema({ server: { type: 'string' } }),
    execution: { kind: 'api', method: 'GET', path: '/api/mcp/servers' },
  },
  {
    id: 'action_0d8f52b6a3c74e19b5f6d28a',
    name: 'Release Gate Check',
    description: 'Evaluate release readiness for a build candidate across lint, typecheck and targeted regression results.',
    enabled: true,
    effect: 'high_risk',
    output_format: 'json',
    input_schema: schema({ candidate: { type: 'string' }, strict: { type: 'boolean', default: true } }, ['candidate']),
    execution: { kind: 'api', method: 'GET', path: '/api/release/status' },
  },
];

const binding = (plugin_id: string, mode: PluginMode, enabled = true): PluginBinding => ({
  plugin_id,
  enabled,
  mode,
});

export const fixtureGroups: ActionGroup[] = [
  {
    id: 'group_browser_tools',
    name: 'Browser Tools',
    action_ids: [
      'action_7f2c91a4d83b6e05a1c47d92',
      'action_5c1d9e73b602a84f7c3e10d8',
      'action_4a7d10f9c2e65b38a1d07e96',
    ],
    build: {
      plugin_id: 'action-group-group-browser-tools',
      version: '7f1277b480b349b89552c2727b50af7d',
      action_ids: [
        'action_7f2c91a4d83b6e05a1c47d92',
        'action_5c1d9e73b602a84f7c3e10d8',
        'action_4a7d10f9c2e65b38a1d07e96',
      ],
    },
    blockers: [],
    sessions: [
      {
        id: 'ses_2c7f41ab9e30',
        name: 'Release audit — action groups',
        enabled: true,
        mode: 'pan_native',
        plugin_id: 'action-group-group-browser-tools',
        loadedVersion: '7f1277b480b349b89552c2727b50af7d',
      },
      {
        id: 'ses_8a1d90c47b52',
        name: 'Probe gateway',
        enabled: false,
        mode: 'standalone',
        plugin_id: 'action-group-group-browser-tools',
        loadedVersion: null,
      },
    ],
  },
  {
    id: 'group_probe_tools',
    name: 'Probe Tools',
    action_ids: ['action_1c9e63b7f408d25a90b6e34c', 'action_7f2c91a4d83b6e05a1c47d92'],
    build: {
      plugin_id: 'action-group-group-probe-tools',
      version: '4d7a90b2c61e83f5a09d27e4c8b1536a',
      action_ids: ['action_1c9e63b7f408d25a90b6e34c', 'action_7f2c91a4d83b6e05a1c47d92'],
    },
    blockers: [],
    sessions: [
      {
        id: 'ses_8a1d90c47b52',
        name: 'Probe gateway',
        enabled: true,
        mode: 'gateway',
        plugin_id: 'action-group-group-probe-tools',
        loadedVersion: null,
      },
    ],
  },
  {
    id: 'group_session_ops',
    name: 'Session Ops',
    action_ids: [
      'action_2b8e40cd7a91f36e5d0c8b74',
      'action_6f0c84e2d5b17a39c4e8b205',
      'action_8d4b2f60a1c95e37d80b6f42',
    ],
    blockers: [],
    sessions: [],
  },
  {
    id: 'group_release_readiness',
    name: 'Release Readiness',
    action_ids: [
      'action_0d8f52b6a3c74e19b5f6d28a',
      'action_3e7a58d1f940c26b8e5d71a3',
      'action_4a7d10f9c2e65b38a1d07e96',
    ],
    blockers: [],
    sessions: [],
  },
  {
    id: 'group_workspace_utilities',
    name: 'Workspace Utilities',
    action_ids: ['action_9a6f0b25c7e3481d92b5a60e'],
    blockers: [],
    sessions: [],
  },
  {
    id: 'group_native_qa',
    name: 'Native QA Toolkit',
    action_ids: [
      'action_1c9e63b7f408d25a90b6e34c',
      'action_5c1d9e73b602a84f7c3e10d8',
      'action_7f2c91a4d83b6e05a1c47d92',
      'action_2b8e40cd7a91f36e5d0c8b74',
      'action_4a7d10f9c2e65b38a1d07e96',
    ],
    build: {
      plugin_id: 'action-group-group-native-qa',
      version: 'b93e6c40d7a128f5e4b09c62a8d3174f',
      action_ids: [
        'action_1c9e63b7f408d25a90b6e34c',
        'action_5c1d9e73b602a84f7c3e10d8',
        'action_7f2c91a4d83b6e05a1c47d92',
        'action_2b8e40cd7a91f36e5d0c8b74',
        'action_4a7d10f9c2e65b38a1d07e96',
      ],
    },
    blockers: [
      { id: 'ses_4d9a02ebc781', name: 'Native control smoke' },
      { id: 'ses_7e3c58d0a194', name: 'Workspace cleanup' },
    ],
    sessions: [
      {
        id: 'ses_4d9a02ebc781',
        name: 'Native control smoke',
        enabled: true,
        mode: 'pan_native',
        plugin_id: 'action-group-group-native-qa',
        loadedVersion: 'b93e6c40d7a128f5e4b09c62a8d3174f',
      },
      {
        id: 'ses_7e3c58d0a194',
        name: 'Workspace cleanup',
        enabled: true,
        mode: 'standalone',
        plugin_id: 'action-group-group-native-qa',
        loadedVersion: 'b93e6c40d7a128f5e4b09c62a8d3174f',
      },
      {
        id: 'ses_b125e9073c46',
        name: 'Docs migration',
        enabled: true,
        mode: 'gateway',
        plugin_id: 'action-group-group-native-qa',
        loadedVersion: null,
      },
    ],
  },
];

export const initialRegistry: ActionRegistry = {
  revision: 42,
  actions: fixtureActions,
  groups: fixtureGroups,
};

export type MockSession = {
  id: string;
  name: string;
  adapter: 'codex' | 'claude' | 'kimi';
  status: 'running' | 'idle';
  workdir: string;
  pluginBindings: PluginBinding[];
  pluginSelectionRevision: number;
  pluginApplyStatus?: 'applied' | 'next_start' | 'pending' | 'failed';
};

export const fixtureSessions: MockSession[] = [
  {
    id: 'ses_2c7f41ab9e30',
    name: 'Release audit — action groups',
    adapter: 'codex',
    status: 'idle',
    workdir: 'D:/project/Pan/.validation/action-review/integration',
    pluginBindings: [binding('action-group-group-browser-tools', 'pan_native'), binding('action-group-group-native-qa', 'pan_native')],
    pluginSelectionRevision: 18,
    pluginApplyStatus: 'applied',
  },
  {
    id: 'ses_8a1d90c47b52',
    name: 'Probe gateway',
    adapter: 'claude',
    status: 'idle',
    workdir: 'D:/project/Pan/.validation/action-groups/live-5d56add2bb',
    pluginBindings: [binding('action-group-group-probe-tools', 'gateway'), binding('action-group-group-browser-tools', 'standalone', false)],
    pluginSelectionRevision: 7,
    pluginApplyStatus: 'applied',
  },
  {
    id: 'ses_4d9a02ebc781',
    name: 'Native control smoke',
    adapter: 'codex',
    status: 'running',
    workdir: 'D:/project/Pan/.validation/codex-native-control',
    pluginBindings: [binding('action-group-group-native-qa', 'pan_native')],
    pluginSelectionRevision: 11,
    pluginApplyStatus: 'next_start',
  },
  {
    id: 'ses_7e3c58d0a194',
    name: 'Workspace cleanup',
    adapter: 'kimi',
    status: 'running',
    workdir: 'D:/project/Pan/data/workdirs/workspace-cleanup',
    pluginBindings: [],
    pluginSelectionRevision: 3,
    pluginApplyStatus: 'applied',
  },
  {
    id: 'ses_b125e9073c46',
    name: 'Docs migration',
    adapter: 'claude',
    status: 'idle',
    workdir: 'D:/project/Pan/docs',
    pluginBindings: [binding('action-group-group-native-qa', 'gateway')],
    pluginSelectionRevision: 9,
    pluginApplyStatus: 'applied',
  },
];

export const fixtureServers: McpServerInfo[] = [
  {
    name: 'action-group-group-browser-tools',
    displayName: 'Browser Tools',
    command: 'python -m packages.actions.provider',
    cwd: 'D:/project/Pan',
    supportedModes: ['standalone', 'pan_native', 'gateway'],
    defaultMode: 'pan_native',
    sourceKind: 'pan_api',
  },
  {
    name: 'action-group-group-probe-tools',
    displayName: 'Probe Tools',
    command: 'python -m packages.actions.provider',
    cwd: 'D:/project/Pan',
    supportedModes: ['standalone', 'pan_native', 'gateway'],
    defaultMode: 'gateway',
    sourceKind: 'pan_api',
  },
  {
    name: 'action-group-group-native-qa',
    displayName: 'Native QA Toolkit',
    command: 'python -m packages.actions.provider',
    cwd: 'D:/project/Pan',
    supportedModes: ['standalone', 'pan_native', 'gateway'],
    defaultMode: 'pan_native',
    sourceKind: 'pan_api',
  },
];

export const MODE_LABELS: Record<PluginMode, string> = {
  standalone: 'Standalone MCP',
  pan_native: 'Pan native',
  gateway: 'Discover / call',
};

export const MODE_DESCRIPTIONS: Record<PluginMode, string> = {
  standalone: 'Run the group as its own stdio MCP server process.',
  pan_native: 'Expose the group through Pan’s native MCP surface. Recommended for Pan-managed Sessions.',
  gateway: 'Let the Session discover the group and call its tools on demand.',
};

/** Explain, in the same terms as the backend, why Build is unavailable. */
export function buildBlockers(
  actionIds: readonly string[],
  actions: readonly Action[],
): { kind: 'empty' | 'disabled' | 'high_risk'; message: string } | null {
  const selected = actionIds
    .map((id) => actions.find((action) => action.id === id))
    .filter((action): action is Action => Boolean(action));
  if (!selected.length) return { kind: 'empty', message: 'Add at least one Action before building.' };
  const disabled = selected.filter((action) => !action.enabled);
  if (disabled.length) {
    return {
      kind: 'disabled',
      message: `Enable ${disabled.map((action) => action.name).join(', ')} before building.`,
    };
  }
  const highRisk = selected.filter((action) => action.effect === 'high_risk');
  if (highRisk.length) {
    return {
      kind: 'high_risk',
      message: `${highRisk.map((action) => action.name).join(', ')} ${
        highRisk.length === 1 ? 'is' : 'are'
      } high risk. Build requires an approval policy and is unavailable.`,
    };
  }
  return null;
}

/** Deterministic-looking new IDs for the mock; real IDs are assigned by the server. */
export function mockActionId(): string {
  return 'action_' + Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 10) +
    Math.random().toString(16).slice(2, 10);
}

export function mockGroupId(): string {
  return 'group_' + Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 10) +
    Math.random().toString(16).slice(2, 10);
}

export function mockVersion(): string {
  return Array.from({ length: 4 }, () => Math.random().toString(16).slice(2, 10)).join('').slice(0, 32);
}
