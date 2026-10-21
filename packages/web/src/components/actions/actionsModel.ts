import type { Action, ActionGroup, ActionRegistry, InputSchema } from '@/types/actions';
import type { PluginMode, Session } from '@/types';

export type GroupDraft = Pick<ActionGroup, 'id' | 'name' | 'action_ids'>;

export type ActionEditorState = {
  kind: 'action';
  mode: 'new' | 'edit';
  originalId: string | null;
  snapshot: string;
  draft: Action;
  schemaText: string;
  schemaError: string;
};

export type GroupEditorState = {
  kind: 'group';
  mode: 'new' | 'edit';
  originalId: string | null;
  snapshot: string;
  draft: GroupDraft;
};

export type EditorState = ActionEditorState | GroupEditorState;

export const EMPTY_SCHEMA: InputSchema = {
  type: 'object',
  properties: {},
  required: [],
  additionalProperties: false,
};

export function emptyActionDraft(): Action {
  return {
    id: '',
    name: '',
    description: '',
    enabled: false,
    effect: 'read',
    output_format: 'json',
    input_schema: structuredClone(EMPTY_SCHEMA),
    execution: { kind: 'api', method: 'GET', path: '/api/sessions' },
  };
}

export function serializeAction(draft: Action, schemaText: string): string {
  return JSON.stringify({ draft, schemaText });
}

export function serializeGroup(draft: GroupDraft): string {
  return JSON.stringify(draft);
}

export function actionEditorState(action: Action, mode: 'new' | 'edit'): ActionEditorState {
  const draft = structuredClone(action);
  const schemaText = JSON.stringify(draft.input_schema, null, 2);
  return {
    kind: 'action',
    mode,
    originalId: mode === 'edit' ? action.id : null,
    snapshot: serializeAction(draft, schemaText),
    draft,
    schemaText,
    schemaError: '',
  };
}

export function groupEditorState(group: GroupDraft, mode: 'new' | 'edit'): GroupEditorState {
  const draft: GroupDraft = {
    id: mode === 'edit' ? group.id : '',
    name: group.name,
    action_ids: [...group.action_ids],
  };
  return {
    kind: 'group',
    mode,
    originalId: mode === 'edit' ? group.id : null,
    snapshot: serializeGroup(draft),
    draft,
  };
}

export function isEditorDirty(editor: EditorState): boolean {
  return editor.kind === 'action'
    ? serializeAction(editor.draft, editor.schemaText) !== editor.snapshot
    : serializeGroup(editor.draft) !== editor.snapshot;
}

export function validateSchemaText(text: string): { schema?: InputSchema; error: string } {
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { error: 'Schema must be a JSON object.' };
    }
    const schema = parsed as Partial<InputSchema>;
    if (schema.type !== 'object') return { error: 'Schema type must be "object".' };
    if (schema.properties !== undefined) {
      if (!schema.properties || typeof schema.properties !== 'object' || Array.isArray(schema.properties)) {
        return { error: 'Schema properties must be an object.' };
      }
    }
    if (schema.additionalProperties !== false) {
      return { error: 'Schema must set additionalProperties to false.' };
    }
    return {
      schema: {
        ...(parsed as InputSchema),
        properties: (schema.properties ?? {}) as InputSchema['properties'],
      },
      error: '',
    };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}

export const EFFECT_LABELS: Record<Action['effect'], string> = {
  read: 'Read',
  write_local: 'Write local',
  write_external: 'Write external',
  high_risk: 'High risk',
};

export function effectTone(effect: Action['effect']): 'neutral' | 'accent' | 'warning' | 'danger' {
  if (effect === 'high_risk') return 'danger';
  if (effect === 'write_external') return 'warning';
  if (effect === 'write_local') return 'accent';
  return 'neutral';
}

export function executionLabel(action: Action): string {
  return action.execution.kind === 'api' ? 'Internal API' : 'Command';
}

export function actionRoute(action: Action): string {
  return action.execution.kind === 'api'
    ? `${action.execution.method} ${action.execution.path}`
    : action.execution.executable;
}

export function pluginIdFor(groupId: string): string {
  return `action-group-${groupId.replaceAll('_', '-')}`;
}

export function shortVersion(version: string): string {
  return version.length > 10 ? `${version.slice(0, 8)}…` : version;
}

export function usedByGroups(registry: ActionRegistry, actionId: string): ActionGroup[] {
  return registry.groups.filter((group) => group.action_ids.includes(actionId));
}

export function groupComposition(group: ActionGroup, actions: Action[]): string {
  return group.action_ids
    .map((id) => actions.find((action) => action.id === id)?.name)
    .filter((name): name is string => Boolean(name))
    .join(' · ');
}

export function formatTime(value: Date | number): string {
  return new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

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

export function sessionIsRunning(session: Session | null | undefined): boolean {
  return session?.workerStatus === 'running';
}

export function buildGate(
  actionIds: readonly string[],
  actions: readonly Action[],
): { kind: 'empty' | 'missing' | 'disabled' | 'high_risk'; message: string } | null {
  if (!actionIds.length) return { kind: 'empty', message: 'Select at least one Action before building.' };
  const selected = actionIds
    .map((id) => actions.find((action) => action.id === id))
    .filter((action): action is Action => Boolean(action));
  if (selected.length !== actionIds.length) {
    return { kind: 'missing', message: 'One or more selected Actions no longer exist. Remove them and save.' };
  }
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
