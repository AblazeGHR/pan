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
  invalid_schema: '定义或参数格式无效，请检查字段。',
  invalid_condition: '条件表达式无效。',
  unsupported_operation: '当前对象不支持此操作。',
  unauthenticated: '授权已失效，请重新登录。',
  authorization_denied: '没有执行此操作的权限；草稿已保留。',
  not_found: '对象不存在或不可见。',
  revision_conflict: '版本冲突。请刷新比较后重新打开编辑；草稿已保留。',
  idempotency_conflict: '请求身份与原请求不一致，请查询原操作。',
  reference_conflict: '引用不存在或版本不匹配。',
  pending_publication: '引用尚未发布。',
  reconciliation_required: '效果不确定，需要人工核对；不可自动重试。',
  not_retryable: '当前 Run 不满足重试条件。',
  cancellation_window_closed: 'Run 已结束，无法取消。',
  capability_unsupported: '当前平台或执行器不支持此能力（unsupported）。',
  unsupported_schema_version: '当前服务不支持此 schema 版本（unsupported）。',
  capacity_exceeded: '容量已满，请稍后刷新。',
  provider_unavailable: '执行器或来源暂不可用。',
  storage_unavailable: '存储暂不可用。',
};
export function canonicalError(error: unknown): string {
  if (error instanceof ApiRequestError)
    return `${canonicalErrorMessages[error.code ?? ''] ?? (error.status === 422 ? '当前服务不支持此能力（unsupported）。' : error.status === 403 ? canonicalErrorMessages.authorization_denied : error.message)} [${error.code ?? error.status}]`;
  return error instanceof Error ? error.message : '请求失败。';
}
const part = encodeURIComponent;
const pageQuery = (cursor?: string | null) =>
  `?limit=100${cursor ? `&cursor=${part(cursor)}` : ''}`;
export const canonicalJobs = {
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
  creatorReferences: (id: string) => canonicalRequest<import('@/types/automationJobs').CreatorReferences>(`event-creators/${part(id)}/references`),
  creatorProviders: () => canonicalRequest<{ items: import('@/types/automationJobs').SchedulerProvider[] }>('event-creators/providers'),
  controlDefinition: (kind: CanonicalKind, item: CanonicalObject, operation: string, requestKey: string, expectedReferences?: string) =>
    canonicalRequest<CanonicalReceipt>(`${kind}/${part(item.id)}/operations`, 'POST', {
      operation, requestKey, expectedRevision: revisionOf(item), expectedStateRevision: item.stateRevision,
      parameters: expectedReferences === undefined ? {} : { expectedReferences },
    }),
};
