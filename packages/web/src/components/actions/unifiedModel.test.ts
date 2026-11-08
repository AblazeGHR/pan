// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  bindableGroup,
  buildUnifiedActions,
  buildUnifiedGroups,
  groupBuildBlockers,
  pluginIdFor,
  runningBlockers,
  withVersionIndex,
} from './unifiedModel';
import type { Action, ActionGroup, ActionRegistry } from '@/types/actions';
import type { JsonObject } from '@/types/canonicalJobs';
import type { Session } from '@/types';

const ref = (id: string, revision: number) => ({ id, revision, digest: String(revision).repeat(64) });

const canonicalAction = (id: string, name: string, extra: JsonObject = {}): JsonObject => ({
  id,
  name,
  description: `${name} description`,
  state: 'published',
  enabled: true,
  revision: 2,
  stateRevision: 3,
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  execution: { operation: 'process.command', config: {} },
  effectClass: 'read',
  ...extra,
});

const legacyAction = (id: string, extra: Partial<Action> = {}): Action => ({
  id,
  name: `Legacy ${id}`,
  description: 'legacy',
  enabled: true,
  effect: 'read',
  output_format: 'json',
  input_schema: { type: 'object', properties: {}, additionalProperties: false },
  execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
  ...extra,
});

const emptyRegistry = (patch: Partial<ActionRegistry> = {}): ActionRegistry => ({
  revision: 1,
  actions: [],
  groups: [],
  ...patch,
});

const legacyGroup = (id: string, actionIds: string[], build?: boolean): ActionGroup => ({
  id,
  name: `Group ${id}`,
  action_ids: actionIds,
  build: build
    ? { plugin_id: pluginIdFor(id), version: 'release-1', action_ids: actionIds }
    : undefined,
  blockers: [],
  sessions: [],
});

const session = (id: string, patch: Partial<Session> = {}): Session =>
  ({
    id,
    name: `Session ${id}`,
    workerStatus: 'idle',
    pluginBindings: [],
    ...patch,
  }) as Session;

describe('unified directory model', () => {
  it('keeps one logical row per Action and records alias adoption', () => {
    const rows = buildUnifiedActions({
      catalog: [canonicalAction('echo', 'Echo')],
      registry: emptyRegistry({
        actions: [legacyAction('echo')],
        canonicalActions: {
          echo: { object: canonicalAction('echo', 'Echo'), definition: { name: 'Echo' }, reason: 'multi-command' },
        },
      }),
      groups: [],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.adoption).toBe('adopted');
    expect(rows[0]!.legacyId).toBe('echo');
    expect(rows[0]!.canonicalId).toBe('echo');
    expect(rows[0]!.mapped?.reason).toBe('multi-command');
  });

  it('merges a legacy alias into its canonical head instead of listing the Action twice', () => {
    const rows = buildUnifiedActions({
      catalog: [canonicalAction('canon', 'Canonical Head')],
      registry: emptyRegistry({ actions: [legacyAction('old', { name: 'Legacy alias' })] }),
      groups: [],
      aliases: { old: 'canon' },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.canonicalId).toBe('canon');
    expect(rows[0]!.legacyId).toBe('old');
    expect(rows[0]!.adoption).toBe('adopted');
  });

  it('shows preserved, legacy and canonical rows side by side with their state', () => {
    const rows = buildUnifiedActions({
      catalog: [canonicalAction('canon', 'Canonical')],
      registry: emptyRegistry({
        actions: [legacyAction('old', { system: false }), legacyAction('pres', { system: false })],
        preservedActions: [{ id: 'pres', reason: 'unrepresentable shell' }],
      }),
      groups: [],
    });
    const byId = new Map(rows.map((row) => [row.canonicalId || row.legacyId, row]));
    expect(rows).toHaveLength(3);
    expect(byId.get('canon')!.adoption).toBe('canonical');
    expect(byId.get('old')!.adoption).toBe('legacy');
    expect(byId.get('pres')!.adoption).toBe('preserved');
    expect(byId.get('pres')!.preservedReason).toBe('unrepresentable shell');
  });

  it('counts Group references from both the canonical draft and the legacy alias list', () => {
    const rows = buildUnifiedActions({
      catalog: [canonicalAction('echo', 'Echo'), canonicalAction('path', 'Path')],
      registry: emptyRegistry({ actions: [legacyAction('old')] }),
      groups: [
        { id: 'g1', name: 'G1', tools: [{ alias: 'e', actionRef: ref('echo', 2) }] },
        { id: 'g2', name: 'G2', tools: [{ alias: 'p', actionRef: ref('path', 2) }] },
      ],
    });
    expect(rows.find((row) => row.canonicalId === 'echo')?.usedByGroupIds).toEqual(['g1']);
    expect(rows.find((row) => row.canonicalId === 'path')?.usedByGroupIds).toEqual(['g2']);
  });

  it('reads the per-Action version index without inventing versions', () => {
    const rows = withVersionIndex(
      buildUnifiedActions({ catalog: [canonicalAction('echo', 'Echo')], registry: emptyRegistry(), groups: [] }),
      { versions: { echo: { count: 3, latest: ref('echo', 7) } } },
    );
    expect(rows[0]!.versionCount).toBe(3);
    expect(rows[0]!.latestRevision).toBe(7);
  });

  it('keeps legacy-only Groups visible and marks them unwritable in the canonical catalog', () => {
    const rows = buildUnifiedGroups({
      canonical: [{ id: 'canon', name: 'Canonical', revision: 4, release: 'rel-9', tools: [{ alias: 'a', actionRef: ref('echo', 2) }] }],
      registry: emptyRegistry({ groups: [legacyGroup('canon', ['echo'], true), legacyGroup('only', ['echo'], true)] }),
      catalog: [],
    });
    const byId = new Map(rows.map((row) => [row.id, row]));
    expect(byId.get('canon')!.adoption).toBe('adopted');
    expect(byId.get('canon')!.release).toBe('rel-9');
    expect(byId.get('canon')!.canonicalWritable).toBe(true);
    expect(byId.get('only')!.adoption).toBe('legacy');
    expect(byId.get('only')!.canonicalWritable).toBe(false);
    expect(byId.get('only')!.release).toBe('release-1');
  });

  it('blocks a build only for reasons the catalog snapshot can prove', () => {
    expect(groupBuildBlockers([], [])).toEqual(['At least one fixed Action tool is required.']);
    // One reason per tool: a tool without an Action never also reports a pin gap.
    expect(groupBuildBlockers([{ alias: 'a', actionRef: {} }], [])).toEqual([
      'Tool a has no fixed Action.',
    ]);
    expect(
      groupBuildBlockers([{ alias: 'a', actionRef: { id: 'echo', revision: 0, digest: '' } }], []),
    ).toEqual(['Tool a does not pin an Action revision and digest.']);
    expect(
      groupBuildBlockers(
        [
          { alias: 'a', actionRef: ref('missing', 1) },
          { alias: 'a', actionRef: ref('gone', 1) },
        ],
        [],
      ),
    ).toEqual([
      'Action missing is not in the canonical catalog.',
      'Duplicate tool alias a.',
      'Action gone is not in the canonical catalog.',
    ]);
    expect(
      groupBuildBlockers(
        [{ alias: 'a', actionRef: ref('retired', 1) }],
        buildUnifiedActions({
          catalog: [canonicalAction('retired', 'R', { state: 'retired' })],
          registry: emptyRegistry(),
          groups: [],
        }),
      ),
    ).toEqual(['Action retired is retired.']);
  });

  it('reports only enabled bindings on running Workers as blockers', () => {
    const plugin = pluginIdFor('g1');
    const sessions = [
      session('running', {
        workerStatus: 'running',
        pluginBindings: [{ plugin_id: plugin, enabled: true, mode: 'standalone' }],
      }),
      session('idle', {
        workerStatus: 'idle',
        pluginBindings: [{ plugin_id: plugin, enabled: true, mode: 'pan_native' }],
      }),
      session('disabled', {
        workerStatus: 'running',
        pluginBindings: [{ plugin_id: plugin, enabled: false, mode: 'gateway' }],
      }),
    ];
    expect(runningBlockers(plugin, sessions).map((item) => item.id)).toEqual(['running']);
  });

  it('adapts a built Group to the binding dialog only when a release exists', () => {
    const rows = buildUnifiedGroups({
      canonical: [{ id: 'g1', name: 'G1', revision: 2, release: 'rel-1', tools: [{ alias: 'a', actionRef: ref('echo', 2) }] }],
      registry: emptyRegistry(),
      catalog: [],
    });
    expect(bindableGroup(rows[0]!, [])?.build).toEqual({
      plugin_id: pluginIdFor('g1'),
      version: 'rel-1',
      action_ids: ['a'],
    });
    const unbuilt = buildUnifiedGroups({
      canonical: [{ id: 'g2', name: 'G2', revision: 1, tools: [{ alias: 'a', actionRef: ref('echo', 2) }] }],
      registry: emptyRegistry(),
      catalog: [],
    });
    expect(bindableGroup(unbuilt[0]!, [])).toBeNull();
  });
});
