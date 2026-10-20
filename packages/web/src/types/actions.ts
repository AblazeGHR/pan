import type { PluginBinding } from './index';

export type InputSchema = {
  type: 'object'; properties: Record<string, { type: string; description?: string; default?: unknown }>;
  required?: string[]; additionalProperties: false;
};
export type Action = {
  id: string; name: string; description: string; enabled: boolean;
  effect: 'read' | 'write_local' | 'write_external' | 'high_risk'; output_format: 'json' | 'text';
  input_schema: InputSchema;
  execution: { kind: 'api'; method: 'GET'; path: string } |
    { kind: 'command'; executable: string; args: string[]; cwd: string; timeout_sec?: number };
};
export type ActionGroup = {
  id: string; name: string; action_ids: string[];
  build?: { plugin_id: string; version: string; action_ids: string[] };
  blockers: { id: string; name: string }[];
  sessions: ({ id: string; name: string; loadedVersion: string | null } & PluginBinding)[];
};
export type ActionRegistry = { revision: number; actions: Action[]; groups: ActionGroup[] };
