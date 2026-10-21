import type { Action, ActionRegistry } from './actions';

export type Condition = { and: Condition[] } | { or: Condition[] } | { not: Condition } |
  { field: string; op: 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'; value: unknown };
export type Argument = { from: 'literal'; value: unknown } | { from: 'event'; path: string };
export const comparison = (): Condition => ({ field: 'workers.runningCount', op: 'eq', value: 0 });
export type TriggerConfig = {
  id?: string; name: string; description?: string; enabled: boolean; eventTypes: string[];
  condition?: Condition | null; actionId: string; arguments: Record<string, Argument>; executionMode: 'serial';
};
export type Trigger = TriggerConfig & { id: string; revision: number; createdBy: { type: string; id?: string }; createdAt: string; updatedAt: string };
export type AutomationRun = {
  id: string; revision: number; triggerId: string; triggerRevision: number; actionId: string;
  event: { id: string; type: string; occurredAt: string; payload: unknown };
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'uncertain' | 'skipped';
  arguments: Record<string, unknown>; state?: unknown; createdAt: string; startedAt?: string; finishedAt?: string;
  action?: Action; actionDigest?: string; error?: string; result?: unknown; targetIds?: string[]; targetResults?: unknown[];
  selection?: { mode: 'query'; matchedAt: string; candidateCount: number; matched: number };
};
export type AutomationSnapshot = {
  revision: number; triggers: Record<string, Trigger>; runs: Record<string, AutomationRun>;
  definitions: { type: string; available: boolean; schema: unknown; source: unknown; schemaVersion: number }[];
  dependencies: Record<string, { available: boolean; unavailable: string[] }>;
  diagnostics: unknown[]; limits: Record<string, number>;
};
export type AutomationData = { automation: AutomationSnapshot; actions: ActionRegistry; sessions: { id: string; name: string }[] };

// Only configuration fields may be sent to the strict management endpoint.
export function triggerConfig(item: TriggerConfig): TriggerConfig {
  return { ...(item.id ? { id: item.id } : {}), name: item.name, description: item.description ?? '',
    enabled: item.enabled, eventTypes: [...item.eventTypes], condition: item.condition ?? null,
    actionId: item.actionId, arguments: item.arguments ?? {}, executionMode: 'serial' };
}
