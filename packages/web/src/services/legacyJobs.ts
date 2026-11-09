/**
 * Legacy-shaped Job client over the canonical authority.
 *
 * The designed Jobs page speaks the old shape; every call below hits the
 * projection routes in `packages/jobs/final_api.py`, which build ordinary
 * canonical Jobs and run them through create_run/admission. No legacy writer,
 * no second engine.
 */
import { useAutomationAuth } from '@/stores/automationStore';
import type { Job, JobCreateInput, JobKindMeta, JobPatchInput, JobRunRecord } from '@/types/jobs';

class LegacyJobError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const auth = useAutomationAuth.getState();
  if (auth.token && auth.expiresAt <= Date.now()) auth.clear();
  const token = useAutomationAuth.getState().token;
  const response = await fetch(`/api/${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = (await response.json().catch(() => ({}))) as {
    ok?: boolean;
    job?: Job;
    jobs?: Job[];
    items?: unknown[];
    kind?: string;
    kinds?: JobKindMeta[];
    error?: { code?: string; message?: string };
    detail?: unknown;
  };
  if (!response.ok) {
    if (response.status === 401) useAutomationAuth.getState().clear();
    const message =
      typeof data.detail === 'string'
        ? data.detail
        : data.error?.message || `Request failed (${response.status})`;
    throw new LegacyJobError(response.status, message, data.error?.code);
  }
  if (data.ok === false) throw new LegacyJobError(400, data.error?.message || 'Request failed', data.error?.code);
  return data as T;
}

export async function fetchJobKinds(): Promise<JobKindMeta[]> {
  const data = await call<{ kinds?: JobKindMeta[] }>('jobs/kinds');
  return data.kinds || [];
}

export async function fetchJobs(): Promise<{ jobs: Job[] }> {
  const data = await call<{ jobs?: Job[] }>('jobs/legacy-list');
  return { jobs: data.jobs || [] };
}

export async function fetchJob(jobId: string): Promise<Job> {
  const data = await call<{ job?: Job }>(`jobs/legacy/${encodeURIComponent(jobId)}`);
  if (!data.job) throw new Error('job missing in response');
  return data.job;
}

export async function createJob(input: JobCreateInput): Promise<Job> {
  const data = await call<{ job?: Job }>('jobs/legacy-create', 'POST', {
    ...input,
    requestKey: (input as { requestKey?: string }).requestKey || crypto.randomUUID(),
  });
  if (!data.job) throw new Error('job missing in response');
  return data.job;
}

export async function patchJob(jobId: string, patch: JobPatchInput): Promise<Job> {
  const data = await call<{ job?: Job }>(`jobs/legacy/${encodeURIComponent(jobId)}`, 'PATCH', patch);
  if (!data.job) throw new Error('job missing in response');
  return data.job;
}

export async function deleteJob(jobId: string): Promise<void> {
  await call(`jobs/${encodeURIComponent(jobId)}`, 'DELETE');
}

export async function runJobNow(jobId: string): Promise<Job> {
  const data = await call<{ job?: Job }>(`jobs/${encodeURIComponent(jobId)}/run-now`, 'POST', {});
  if (!data.job) throw new Error('job missing in response');
  return data.job;
}

export async function pinJob(jobId: string, pinned: boolean): Promise<Job> {
  const data = await call<{ job?: Job }>(`jobs/legacy/${encodeURIComponent(jobId)}/pin`, 'PUT', { pinned });
  if (!data.job) throw new Error('job missing in response');
  return data.job;
}

export async function fetchJobRuns(jobId: string, limit = 50): Promise<JobRunRecord[]> {
  const data = await call<{ items?: Array<Record<string, unknown>> }>(
    `jobs/${encodeURIComponent(jobId)}/runs?limit=${limit}`,
  );
  // Canonical Run records use camelCase; the designed drawer reads the old
  // snake_case fields. Keep both so Run history and the attempt log share one
  // identity (`run_id` -> canonical runId).
  return (data.items || []).map((row) => ({
    ...(row as unknown as JobRunRecord),
    run_id: typeof row.runId === 'string' ? row.runId : (row.run_id as string | undefined),
    status: String(row.state ?? row.status ?? 'unknown'),
    fire_at: (row.createdAt as string | undefined) ?? (row.fire_at as string | undefined) ?? null,
    actual_at: (row.startedAt as string | undefined) ?? (row.actual_at as string | undefined) ?? null,
    exit_code: typeof row.exitCode === 'number' ? row.exitCode : (row.exit_code as number | null | undefined) ?? null,
  }));
}

export interface RunLogReference {
  attemptId: string;
  status: string;
  href?: string | null;
  reason?: string;
}

export async function fetchRunLogReferences(runId: string): Promise<RunLogReference[]> {
  const data = await call<{ items?: RunLogReference[] }>(
    `job-runs/${encodeURIComponent(runId)}/logs?limit=100`,
  );
  return data.items || [];
}

export async function fetchRunLogText(runId: string, attemptId: string): Promise<string> {
  const data = await call<{ text?: string | null }>(
    `job-runs/${encodeURIComponent(runId)}/attempts/${encodeURIComponent(attemptId)}/log`,
  );
  return typeof data.text === 'string' ? data.text : '';
}

export async function cancelJob(jobId: string): Promise<void> {
  await call(`jobs/${encodeURIComponent(jobId)}/cancel`, 'POST', {});
}

export { LegacyJobError };
