/**
 * One logical entry per Action and per Group.
 *
 * The canonical catalog (`/api/jobs/actions`, `/api/action-groups`) owns
 * definitions, fixed versions and releases. The legacy registry
 * (`/api/actions`) is the compatibility projection: it carries alias
 * adoption, preserved records, legacy-only Groups, release pins and Session
 * bindings. Neither view gets to hide the other's records, so both are merged
 * here instead of being shown as two separate screens.
 */
import type {
  Action,
  ActionGroup,
  ActionRegistry,
  CanonicalLegacyAction,
} from '@/types/actions';
import type { JsonObject } from '@/types/canonicalJobs';
import type { PluginBinding, PluginMode, Session } from '@/types';

export const MODES: PluginMode[] = ['standalone', 'pan_native', 'gateway'];

export const obj = (value: unknown): JsonObject =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : {};

export const rows = (value: unknown): JsonObject[] =>
  Array.isArray(value) ? (value as JsonObject[]) : [];

export const text = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : value === undefined || value === null ? fallback : String(value);

export const count = (value: unknown): number => (typeof value === 'number' ? value : Number(value) || 0);

/** `action-group-<id>` is the stable plugin identity used by Session bindings. */
export function pluginIdFor(groupId: string): string {
  return `action-group-${groupId.replaceAll('_', '-')}`;
}

/** Preserve, blocked and adoption state of one logical Action. */
export type ActionAdoption = 'canonical' | 'adopted' | 'legacy' | 'preserved';

export interface UnifiedActionRow {
  key: string;
  name: string;
  description: string;
  system: boolean;
  adoption: ActionAdoption;
  canonicalId: string;
  legacyId: string;
  versionCount: number;
  latestRevision: number | null;
  state: string;
  enabled: boolean;
  effectClass: string;
  executionOperation: string;
  /** Why the legacy editor refuses this record (preserved / mapped). */
  preservedReason: string;
  /** Groups (canonical or legacy ids) that reference this logical Action. */
  usedByGroupIds: string[];
  canonical?: JsonObject;
  legacy?: Action;
  mapped?: CanonicalLegacyAction & { legacyId: string };
}

export type GroupAdoption = 'canonical' | 'adopted' | 'legacy';

export interface UnifiedGroupRow {
  key: string;
  id: string;
  name: string;
  adoption: GroupAdoption;
  pluginId: string;
  revision: number | null;
  /** Latest committed immutable release version, empty when unbuilt. */
  release: string;
  /** Fixed Action versions of the draft, as committed by the canonical writer. */
  tools: Array<{ alias: string; actionRef: JsonObject }>;
  /** Reasons a build cannot be requested from the current catalog snapshot. */
  buildBlockers: string[];
  /** Legacy-only Groups cannot accept canonical writes until they are adopted. */
  canonicalWritable: boolean;
  canonical?: JsonObject;
  legacy?: ActionGroup;
}

const actionRefOf = (tool: JsonObject): JsonObject => obj(tool.actionRef);

function toolList(value: unknown): Array<{ alias: string; actionRef: JsonObject }> {
  return rows(value)
    .map((tool) => ({ alias: text(tool.alias), actionRef: actionRefOf(tool) }))
    .filter((tool) => Boolean(tool.alias));
}

/**
 * Merge the canonical Action catalog with the legacy registry projection.
 *
 * A legacy alias never becomes a second logical Action: when the alias is
 * adopted, the canonical id is the row identity and the alias is kept as
 * provenance. Legacy-only records stay visible with their own writer state.
 */
export function buildUnifiedActions({
  catalog,
  registry,
  groups,
  aliases = {},
}: {
  catalog: JsonObject[];
  registry: ActionRegistry;
  groups: JsonObject[];
  /** Legacy alias -> canonical Action id, resolved through the alias endpoint. */
  aliases?: Record<string, string>;
}): UnifiedActionRow[] {
  const rowsById = new Map<string, UnifiedActionRow>();
  // Only an explicit, still-present canonical head may adopt a legacy alias.
  const canonicalIds = new Set(catalog.map((item) => text(item.id)).filter(Boolean));
  const upsert = (row: UnifiedActionRow) => {
    if (!rowsById.has(row.key)) rowsById.set(row.key, row);
    return rowsById.get(row.key)!;
  };

  for (const item of catalog) {
    const id = text(item.id);
    if (!id) continue;
    upsert({
      key: `canonical:${id}`,
      name: text(item.name, id),
      description: text(item.description),
      system: item.system === true,
      adoption: 'canonical',
      canonicalId: id,
      legacyId: '',
      versionCount: 1,
      latestRevision: null,
      state: text(item.state, 'draft'),
      enabled: item.enabled === true,
      effectClass: text(item.effectClass),
      executionOperation: text(obj(item.execution).operation),
      preservedReason: '',
      usedByGroupIds: [],
      canonical: item,
    });
  }

  const preserved = new Map((registry.preservedActions ?? []).map((item) => [item.id, item.reason]));

  for (const action of registry.actions) {
    const mapped = registry.canonicalActions?.[action.id];
    const bound = aliases[action.id];
    const canonicalId = mapped
      ? text(mapped.object.id, action.id)
      : bound && canonicalIds.has(bound)
        ? bound
        : action.id;
    const existing = rowsById.get(`canonical:${canonicalId}`);
    const reason = preserved.get(action.id) ?? '';
    if (existing) {
      existing.legacyId = action.id;
      existing.legacy = action;
      existing.adoption = reason ? 'preserved' : 'adopted';
      existing.preservedReason = reason;
      existing.mapped = mapped ? { ...mapped, legacyId: action.id } : undefined;
      existing.name = existing.name || action.name;
      existing.description = existing.description || action.description;
      continue;
    }
    upsert({
      key: `legacy:${action.id}`,
      name: action.name || action.id,
      description: action.description ?? '',
      system: action.system === true,
      adoption: reason ? 'preserved' : mapped ? 'adopted' : 'legacy',
      canonicalId: mapped ? canonicalId : '',
      legacyId: action.id,
      versionCount: mapped ? 1 : 0,
      latestRevision: null,
      state: mapped ? (action.enabled ? 'published' : 'draft') : 'Retired',
      enabled: Boolean(mapped) && action.enabled === true,
      effectClass: action.effect ?? '',
      executionOperation: text(obj(action.execution).kind),
      preservedReason: reason,
      usedByGroupIds: [],
      legacy: action,
      mapped: mapped ? { ...mapped, legacyId: action.id } : undefined,
    });
  }

  for (const group of groups) {
    for (const tool of toolList(group.tools)) {
      const id = text(tool.actionRef.id);
      const row = rowsById.get(`canonical:${id}`);
      if (row) row.usedByGroupIds.push(text(group.id));
    }
  }
  for (const group of registry.groups) {
    for (const alias of group.action_ids) {
      const row =
        rowsById.get(`canonical:${alias}`) ??
        [...rowsById.values()].find((item) => item.legacyId === alias);
      if (row && !row.usedByGroupIds.includes(group.id)) row.usedByGroupIds.push(group.id);
    }
  }

  return [...rowsById.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** Version counts come from the per-Action version index on the catalog page. */
export function withVersionIndex(
  list: UnifiedActionRow[],
  page: JsonObject,
): UnifiedActionRow[] {
  const index = obj(page.versions);
  for (const row of list) {
    if (!row.canonicalId) continue;
    const entry = obj(index[row.canonicalId]);
    row.versionCount = Math.max(1, count(entry.count) || 1);
    const latest = obj(entry.latest);
    row.latestRevision = latest.revision === undefined ? row.latestRevision : count(latest.revision);
  }
  return list;
}

/**
 * A build needs every fixed tool to resolve to an available published Action
 * version. The catalog page only knows the logical head, so this reports what
 * the snapshot can prove and leaves server-side checks to the build response.
 */
export function groupBuildBlockers(
  tools: Array<{ alias: string; actionRef: JsonObject }>,
  catalog: UnifiedActionRow[],
): string[] {
  if (!tools.length) return ['At least one fixed Action tool is required.'];
  const blockers: string[] = [];
  const aliases = new Set<string>();
  const actions = new Set<string>();
  for (const tool of tools) {
    if (aliases.has(tool.alias)) blockers.push(`Duplicate tool alias ${tool.alias}.`);
    aliases.add(tool.alias);
    const id = text(tool.actionRef.id);
    if (!id) {
      blockers.push(`Tool ${tool.alias} has no fixed Action.`);
      continue;
    }
    if (actions.has(id)) blockers.push(`Action ${id} is fixed twice in one Group.`);
    actions.add(id);
    if (!tool.actionRef.revision || !tool.actionRef.digest) {
      blockers.push(`Tool ${tool.alias} does not pin an Action revision and digest.`);
      continue;
    }
    const row = catalog.find((item) => item.canonicalId === id);
    if (!row) blockers.push(`Action ${id} is not in the canonical catalog.`);
    else if (row.state === 'retired') blockers.push(`Action ${id} is retired.`);
  }
  return blockers;
}

/**
 * Merge canonical Groups with legacy-only Groups. Legacy writer status is
 * reported, never silently promoted: an unadopted legacy Group can only be
 * previewed for reference upgrades until its writer is adopted.
 */
export function buildUnifiedGroups({
  canonical,
  registry,
  catalog,
}: {
  canonical: JsonObject[];
  registry: ActionRegistry;
  catalog: UnifiedActionRow[];
}): UnifiedGroupRow[] {
  const result = new Map<string, UnifiedGroupRow>();
  for (const group of canonical) {
    const id = text(group.id);
    if (!id) continue;
    const tools = toolList(group.tools);
    result.set(id, {
      key: `canonical:${id}`,
      id,
      name: text(group.name, id),
      adoption: 'canonical',
      pluginId: pluginIdFor(id),
      revision: group.revision === undefined ? null : count(group.revision),
      release: text(group.release),
      tools,
      buildBlockers: groupBuildBlockers(tools, catalog),
      canonicalWritable: true,
      canonical: group,
    });
  }
  for (const group of registry.groups) {
    const existing = result.get(group.id);
    if (existing) {
      existing.adoption = 'adopted';
      existing.legacy = group;
      existing.release = existing.release || text(group.build?.version);
      continue;
    }
    result.set(group.id, {
      key: `legacy:${group.id}`,
      id: group.id,
      name: group.name || group.id,
      adoption: 'legacy',
      pluginId: pluginIdFor(group.id),
      revision: null,
      release: text(group.build?.version),
      tools: group.action_ids.map((alias) => ({ alias, actionRef: {} })),
      buildBlockers: [],
      canonicalWritable: false,
      legacy: group,
    });
  }
  return [...result.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** Session bindings of one Group plugin, from the authoritative Session list. */
export function boundSessions(pluginId: string, sessions: Session[]): Session[] {
  return sessions.filter((session) =>
    (session.pluginBindings ?? []).some((binding) => binding.plugin_id === pluginId),
  );
}

export function bindingOf(session: Session | null | undefined, pluginId: string): PluginBinding | null {
  return (session?.pluginBindings ?? []).find((binding) => binding.plugin_id === pluginId) ?? null;
}

/**
 * A running Worker keeps its own frozen release, so a changed binding is only
 * a conflict for build/unbuild decisions, not for saving the draft.
 */
export function runningBlockers(pluginId: string, sessions: Session[]): Array<{ id: string; name: string }> {
  return boundSessions(pluginId, sessions)
    .filter((session) => {
      const binding = bindingOf(session, pluginId);
      return binding?.enabled === true && session.workerStatus === 'running';
    })
    .map((session) => ({ id: session.id, name: session.name }));
}

/**
 * Adapt any built Group to the binding dialog's existing contract. The dialog
 * only needs the plugin identity, the frozen release version and the Sessions
 * that already reference it.
 */
export function bindableGroup(row: UnifiedGroupRow, sessions: Session[]): ActionGroup | null {
  if (!row.release) return null;
  const projected = row.legacy;
  const aliases = row.tools.map((tool) => tool.alias);
  return {
    id: row.id,
    name: row.name,
    action_ids: aliases.length ? aliases : (projected?.action_ids ?? []),
    build: {
      plugin_id: row.pluginId,
      version: row.release,
      action_ids: aliases.length ? aliases : (projected?.build?.action_ids ?? []),
    },
    blockers: runningBlockers(row.pluginId, sessions),
    sessions: (projected?.sessions ?? []).filter((session) => session.plugin_id === row.pluginId),
  };
}

export const ADOPTION_LABELS: Record<ActionAdoption | GroupAdoption, string> = {
  canonical: 'Catalog',
  adopted: 'Canonical definition',
  legacy: 'Retired',
  preserved: 'Preserved',
};

export function adoptionTone(
  adoption: ActionAdoption | GroupAdoption,
): 'accent' | 'success' | 'warning' | 'muted' {
  if (adoption === 'canonical') return 'accent';
  if (adoption === 'adopted') return 'success';
  if (adoption === 'preserved') return 'warning';
  return 'muted';
}

/**
 * Reverse projection of a canonical Action definition into the designed
 * quick-create form. Only shapes the form can express are converted; anything
 * else returns null so the caller keeps the full definition in the explicit
 * advanced area instead of dropping fields.
 */
export function legacyDraftFromDefinition(
  definition: JsonObject,
  options: { enabled: boolean },
): Action | null {
  const execution = obj(definition.execution);
  const operation = text(execution.operation);
  const config = obj(execution.config);
  const schema = obj(definition.inputSchema);
  const base = {
    id: '',
    name: text(definition.name),
    description: text(definition.description),
    enabled: options.enabled,
    effect: (text(definition.effectClass, 'read') as Action['effect']),
    output_format: 'json' as const,
    input_schema: {
      type: 'object' as const,
      properties: (schema.properties ?? {}) as Action['input_schema']['properties'],
      ...(Array.isArray(schema.required) ? { required: schema.required as string[] } : {}),
      additionalProperties: false as const,
    },
  };
  const keys = (value: JsonObject) => Object.keys(value).sort().join(',');
  if (operation === 'api.get' && keys(config) === 'method,path' && config.method === 'GET') {
    return { ...base, execution: { kind: 'api', method: 'GET', path: text(config.path) } };
  }
  if (operation === 'process.command' && ['args,cwd,executable,timeout_sec', 'args,cwd,executable'].includes(keys(config))) {
    const args = Array.isArray(config.args) ? config.args : [];
    if (!args.every((item) => typeof item === 'string')) return null;
    return {
      ...base,
      // An argv placeholder would be shell-expanded differently here; keep it
      // in the advanced area instead of silently rewriting the command.
      execution: {
        kind: 'command',
        executable: text(config.executable),
        args: args as string[],
        cwd: text(config.cwd),
        timeout_sec: typeof config.timeout_sec === 'number' ? config.timeout_sec : undefined,
      },
    };
  }
  if (operation === 'session.pause-everything' && keys(config) === 'operation,selector'
      && config.operation === 'pause_everything') {
    const selector = obj(config.selector);
    if (!['explicit', 'event', 'query'].includes(text(selector.mode))) return null;
    return {
      ...base,
      effect: 'write_local',
      execution: { kind: 'session_operation', operation: 'pause_everything', selector: selector as never },
    };
  }
  if (operation === 'template.instantiate' && keys(config) === 'legacyTemplate,templateRef') {
    const legacyTemplate = obj(config.legacyTemplate);
    if (text(legacyTemplate.kind) !== 'job_template') return null;
    return {
      ...base,
      effect: 'write_local',
      input_schema: { type: 'object', properties: {}, additionalProperties: false },
      execution: {
        kind: 'job_template',
        templateId: text(legacyTemplate.templateId),
        templateRevision: Number(legacyTemplate.templateRevision) || 1,
      },
    };
  }
  return null;
}
