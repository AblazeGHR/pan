export type StepBinding = { from: 'literal'; value: unknown } | { from: 'input' | 'event' | 'system'; path: string } | { from: 'step'; stepId: string; path: string };
export type ChainStep = { stepId: string; actionId: string; arguments: Record<string, StepBinding> };
export type ScheduleRule = { kind: 'once' | 'interval' | 'cron'; at?: string; intervalSec?: number; interval_sec?: number; anchor?: string; cron?: string; timezone?: string };
export type SchedulerConfig = { name: string; schedule: ScheduleRule; enabled: boolean; misfirePolicy: 'fire_now' | 'skip'; graceSec: number };
export type SourceReference = { triggerId: string; name: string; jobId?: string; enabled: boolean };
export type SchedulerSource = SchedulerConfig & { id: string; revision: number; owner: string; nextFireAt: string | null; publishCursor?: unknown; references?: SourceReference[]; referencesVersion?: string; lastOutcome?: string };
// Canonical source definitions retain the entire provider config, including
// multiple rules, decimal durations, anchors, explicit zones/DST and driveLimit.
import type { CanonicalObject, JsonObject } from './canonicalJobs';
export interface EventCreator extends CanonicalObject {
  revision: number;
  providerRef: JsonObject;
  configSchemaRef: JsonObject;
  config: JsonObject;
  eventTypes: string[];
  requiredCapabilities: string[];
}
export type CreatorReferences = { references: { triggerId: string; name: string; jobId: string; jobName: string; revision: number; stateRevision: number; enabled: boolean }[]; referenceDigest: string };
export type SchedulerProvider = Pick<EventCreator, 'providerRef' | 'configSchemaRef' | 'eventTypes' | 'requiredCapabilities'> & { configSchema: unknown };
export type TimeLink = { linkId: string; mode: 'existing'; sourceId: string; sourceRevision: number } | { linkId: string; mode: 'new'; config: SchedulerConfig };
export type JobConfig = {
  name: string; description: string; enabled: boolean; actionId: string; arguments: Record<string, unknown>; steps?: ChainStep[];
  trigger: { kind: string; eventTypes?: string[]; query?: unknown }; schedule: unknown[];
  overlap: { mode: 'forbid' | 'queue' | 'parallel'; parallelLimit: number };
  recovery: { enabled: boolean; monitoring: boolean; maxAttempts: number; backoffSec: number[]; stableResetSec: number };
  completion: { kind: 'ordinary' | 'monitoring'; signalType?: string };
  notification: { on: string[]; target: 'job'; sessionIds?: string[] }; maxRuns?: number | null; misfirePolicy?: string;
};
export type ActionJob = { jobId: string; name: string; configRevision: number; config: JobConfig; enabled: boolean; status: string; paused?: boolean; lastStatus?: string; runCount?: number; integrity?: { status: string; approvalRequired: boolean; issues: unknown[] } };
export type JobEdit = { key: string; jobId?: string; status: string; error?: string };
export const newId = (prefix: string) => `${prefix}_${crypto.randomUUID?.().replaceAll('-', '') ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')}`;
export const defaultSource = (): SchedulerConfig => ({ name: '', enabled: false, schedule: { kind: 'interval', intervalSec: 3600, timezone: 'Asia/Shanghai' }, misfirePolicy: 'fire_now', graceSec: 300 });
export function normalizeChainSteps(steps: { stepId: string; actionId: string; arguments: Record<string, unknown> }[]): ChainStep[] {
  return steps.map((step) => ({ ...step, arguments: Object.fromEntries(Object.entries(step.arguments).map(([name, raw]) => [name, raw && typeof raw === 'object' && 'from' in raw ? raw as StepBinding : { from: 'literal', value: raw }])) }));
}
export const defaultJob = (): JobConfig => ({ name: '', description: '', enabled: false, actionId: '', arguments: {}, steps: [], trigger: { kind: 'manual' }, schedule: [], overlap: { mode: 'forbid', parallelLimit: 3 }, recovery: { enabled: false, monitoring: false, maxAttempts: 3, backoffSec: [5, 15, 60], stableResetSec: 300 }, completion: { kind: 'ordinary' }, notification: { on: ['completed', 'failed', 'cancelled', 'uncertain'], target: 'job' } });
export function scheduleSummary(source: SchedulerConfig) {
  const s = source.schedule;
  return `${s.kind === 'interval' ? `Every ${s.intervalSec ?? s.interval_sec} seconds` : s.kind === 'cron' ? `Cron ${s.cron}` : `Once ${s.at}`} 路 ${s.timezone || 'Host local timezone'}`;
}
