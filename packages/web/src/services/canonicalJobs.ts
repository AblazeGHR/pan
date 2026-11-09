import { ApiRequestError, canonicalRequest } from './api';
import type {
  CanonicalKind,
  CanonicalObject,
  CanonicalPage,
  CanonicalReceipt,
  CanonicalRun,
  JsonObject,
  RunOperation,
} from '@/types/canonicalJobs';
import jobSchema from '../../../jobs/schemas/final/job_definition.schema.json';
import triggerSchema from '../../../jobs/schemas/final/trigger_definition.schema.json';
import creatorSchema from '../../../jobs/schemas/final/event_creator_definition.schema.json';
import templateSchema from '../../../jobs/schemas/final/template_definition.schema.json';

export const definitionSchemas = {
  jobs: jobSchema,
  triggers: triggerSchema,
  'event-creators': creatorSchema,
  'job-templates': templateSchema,
};
export const runOperations: RunOperation[] = [
  'retry',
  'run-again',
  'clone-and-run',
  'cancel',
  'pause',
  'resume',
];
export const revisionOf = (item: CanonicalObject) => item.configRevision ?? item.revision!;
export function definitionOf(kind: CanonicalKind, item: CanonicalObject): JsonObject {
  return Object.fromEntries(
    Object.entries(item).filter(([key]) => key in definitionSchemas[kind].properties),
  );
}
export const canonicalErrorMessages: Record<string, string> = {
  invalid_schema: 'Invalid definition or arguments. Check the fields.',
  invalid_condition: 'Invalid condition expression.',
  unsupported_operation: 'This operation is unavailable for this object.',
  unauthenticated: 'Authorization expired. Sign in again.',
  authorization_denied: 'Permission denied. Your draft is preserved.',
  not_found: 'Object missing or inaccessible.',
  revision_conflict: 'Revision conflict. Refresh, compare and reopen the editor. Your draft is preserved.',
  idempotency_conflict: 'Request identity conflict. Query the original operation.',
  reference_conflict: 'Reference missing or revision mismatch.',
  pending_publication: 'Reference is not published.',
  reconciliation_required: 'The effect is uncertain. Verify manually; do not retry automatically.',
  not_retryable: 'This Run cannot be retried.',
  cancellation_window_closed: 'Run Already finished; cancellation is unavailable.',
  capability_unsupported: 'This capability is unsupported by the platform or executor.',
  unsupported_schema_version: 'This schema version is unsupported by the service.',
  capacity_exceeded: 'Capacity reached. Refresh later.',
  provider_unavailable: 'Executor or source is unavailable.',
  storage_unavailable: 'Storage is unavailable.',
};
export function canonicalError(error: unknown): string {
  if (error instanceof ApiRequestError)
    return `${canonicalErrorMessages[error.code ?? ''] ?? (error.status === 422 ? 'This capability is unsupported by the service.' : error.status === 403 ? canonicalErrorMessages.authorization_denied : error.message)} [${error.code ?? error.status}]`;
  return error instanceof Error ? error.message : 'Request failed.';
}
const part = encodeURIComponent;
const pageQuery = (cursor?: string | null) =>
  `?limit=100${cursor ? `&cursor=${part(cursor)}` : ''}`;
export const canonicalJobs = {
  writeFormTemplate: (definition: JsonObject, form: JsonObject, requestKey: string) =>
    canonicalRequest<CanonicalReceipt>('job-templates', 'POST', { definition, form, expectedRevision: 0, requestKey }),
  list: (kind: CanonicalKind, cursor?: string | null, signal?: AbortSignal) =>
    canonicalRequest<CanonicalPage<CanonicalObject>>(
      kind + pageQuery(cursor),
      'GET',
      undefined,
      signal,
    ),
  get: (kind: CanonicalKind, id: string) =>
    canonicalRequest<CanonicalObject>(`${kind}/${part(id)}`),
  write: (
    kind: CanonicalKind,
    item: CanonicalObject | null,
    definition: JsonObject,
    requestKey: string,
    expectedReferences?: string,
    expectedCreatorRevisions?: Record<string, number>,
  ) =>
    canonicalRequest<CanonicalReceipt>(
      item ? `${kind}/${part(item.id)}` : kind,
      item ? 'PUT' : 'POST',
      { definition, expectedRevision: item ? revisionOf(item) : 0, requestKey,
        ...(expectedReferences === undefined ? {} : { expectedReferences }),
        ...(expectedCreatorRevisions === undefined ? {} : { expectedCreatorRevisions }) },
    ),
  retire: (kind: CanonicalKind, item: CanonicalObject, requestKey: string, expectedReferences?: string) =>
    canonicalRequest<CanonicalReceipt>(`${kind}/${part(item.id)}`, 'DELETE', {
      expectedRevision: revisionOf(item),
      expectedStateRevision: item.stateRevision,
      requestKey,
      ...(expectedReferences === undefined ? {} : { expectedReferences }),
    }),
  runs: (jobId: string, cursor?: string | null, signal?: AbortSignal) =>
    canonicalRequest<CanonicalPage<CanonicalRun>>(
      `jobs/${part(jobId)}/runs${pageQuery(cursor)}`,
      'GET',
      undefined,
      signal,
    ),
  operate: (
    run: CanonicalRun,
    operation: RunOperation,
    expectedRevision: number,
    requestKey: string,
    parameters: JsonObject,
    stepId?: string,
  ) =>
    canonicalRequest<CanonicalReceipt>(`job-runs/${part(run.runId)}/operations`, 'POST', {
      operation,
      expectedStateRevision: run.stateRevision,
      requestKey,
      ...(operation === 'run-again' || operation === 'clone-and-run'
        ? { expectedRevision, parameters }
        : {}),
      ...(operation === 'retry' && stepId ? { stepId } : {}),
    }),
  lookup: (operation: string, requestKey: string, operationId?: string) =>
    canonicalRequest<CanonicalReceipt>(
      operationId
        ? `operations/${part(operationId)}`
        : `operations?operation=${part(operation)}&requestKey=${part(requestKey)}`,
    ),
  actions: () => canonicalRequest<CanonicalPage<JsonObject> & { references?: Record<string, JsonObject> }>('actions'),
  actionCopy: (definition: JsonObject, name: string, requestKey: string) =>
    canonicalRequest<CanonicalReceipt>('jobs/actions', 'POST', {
      definition: { ...definition, name },
      expectedRevision: 0,
      requestKey,
    }),
  actionControl: (item: JsonObject, operation: 'publish' | 'enable' | 'disable', requestKey: string) =>
    canonicalRequest<CanonicalReceipt>(
      `jobs/actions/${part(String(item.id))}/operations`,
      'POST',
      {
        operation,
        expectedRevision: Number(item.revision),
        expectedStateRevision: Number(item.stateRevision),
        requestKey,
        parameters: {},
      },
    ),
  creatorReferences: (id: string) => canonicalRequest<import('@/types/automationJobs').CreatorReferences>(`event-creators/${part(id)}/references`),
  creatorProviders: () => canonicalRequest<{ items: import('@/types/automationJobs').SchedulerProvider[] }>('event-creators/providers'),
  resolveTimeInput: (value: string, timezone?: string, signal?: AbortSignal) =>
    canonicalRequest<TimeInputResolution>('event-creators/time-input/resolve', 'POST', {
      value, ...(timezone ? { timezone } : {}),
    }, signal),
  instantiateTimeTemplate: (item: CanonicalObject, requestKey: string, activate = false) =>
    canonicalRequest<CanonicalReceipt>(`job-templates/${part(item.id)}/instantiate`, 'POST', {
      expectedRevision: revisionOf(item), requestKey, activate,
    }),
  controlDefinition: (kind: CanonicalKind, item: CanonicalObject, operation: string, requestKey: string, expectedReferences?: string) =>
    canonicalRequest<CanonicalReceipt>(`${kind}/${part(item.id)}/operations`, 'POST', {
      operation, requestKey, expectedRevision: revisionOf(item), expectedStateRevision: item.stateRevision,
      parameters: expectedReferences === undefined ? {} : { expectedReferences },
    }),
};

export type TimeInputResolution = {
  status: 'resolved' | 'ambiguous' | 'nonexistent' | 'invalid' | 'unavailable';
  at?: string;
  timezone?: string | null;
  zoneSelection?: string;
  reason?: string;
  options?: { at: string; localTime: string }[];
};
