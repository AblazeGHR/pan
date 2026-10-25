export type CanonicalKind = 'jobs' | 'triggers' | 'event-creators' | 'job-templates';
export type JsonObject = { [key: string]: unknown };
export interface CanonicalObject extends JsonObject {
  id: string;
  name: string;
  state: string;
  stateRevision: number;
  configRevision?: number;
  revision?: number;
}
export interface CanonicalRun extends JsonObject {
  runId: string;
  jobId: string;
  state: string;
  stateRevision: number;
  configRevision: number;
}
export interface CanonicalPage<T> {
  items: T[];
  nextCursor: string | null;
}
export type RunOperation = 'retry' | 'run-again' | 'clone-and-run' | 'cancel' | 'pause' | 'resume';
export interface CanonicalReceipt {
  operationId: string;
  requestKey: string;
  status: 'accepted' | 'pending' | 'unknown' | 'completed' | 'rejected' | 'cancelled';
  objectRefs: JsonObject[];
  links: JsonObject;
  runId?: string;
  error?: { code: string; message: string };
  result?: JsonObject;
}
