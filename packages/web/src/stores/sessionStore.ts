import { create } from 'zustand';
import type {
  Session,
  Message,
  ApiSessionHistoryResponse,
  ApiGenericResponse,
  SettingsBody,
} from '@/types';
import {
  fetchSessions,
  fetchSessionHistory,
  createSession,
  deleteSession,
  batchDeleteSessions,
  renameSession,
  branchSession,
  reimportSession,
} from '@/services/api';
import { isMockMode } from '@/demo/mockBackend';
import { useUIStore } from '@/stores/uiStore';
import { inheritMessageIdentity } from '@/utils/messageIdentity';
import {
  canonicalHistory,
  createWindow,
  durableOffsetOf,
  isDurableRow,
  isLocalMarker,
  liveProjectionKeys,
  markDurableRow,
  markLocalMarker,
  mergeWindowPage,
  taskScopeKey,
  windowFromSession,
  windowRows,
  type HistoryPage,
  type LoadedWindow,
} from '@/stores/messageOrdering';

interface SessionStore {
  // State
  sessions: Session[];
  sessionsLoading: boolean;
  currentSessionId: string | null;
  currentMessages: Message[];
  hasMoreMessages: boolean;
  historyLoading: boolean;
  /** 首次进入 session 时拉取 fresh history 的进行中标志。summary=1 快照不带
   *  每条 history，selectSession 后 currentMessages 短暂为空——此时应显示转圈
   *  loading，而非「无消息」空态。与 historyLoading（loadOlderMessages 用）
   *  区分开：selectSession 故意不置 historyLoading=true（见下方注释）。 */
  initialLoading: boolean;
  historyLoadEnd: number;
  multiSelectMode: boolean;
  selectedIds: Set<string>;
  inputDrafts: Record<string, string>;
  inputDraftRevisions: Record<string, number>;
  rendering: boolean;

  // ── Internal staleness guards ──
  // Incremented on every loadSessions() start so an older in-flight response
  // can never overwrite a newer refresh.
  _loadSeq: number;
  // sessionId → monotonic sequence of that session's last WS-driven update.
  // loadSessions() snapshots this map at request time and compares per session,
  // so it only skips reverting workerStatus/workerId for sessions that WS
  // events actually freshened *while its own HTTP request was in flight*.
  _sessionWsTouchedSeq: Record<string, number>;
  _historyRefreshSeq: Record<string, number>;
  _historyPageSeq: Record<string, number>;
  /** Monotonic per-session selection request sequence; protects A→B→A. */
  _selectionSeq: Record<string, number>;
  _sessionLocalTouchedSeq: Record<string, number>;
  _sessionSettingsTouchedSeq: Record<string, number>;
  _sessionEventPatches: Record<string, Partial<Session>>;
  _deliveredQueueIds: Record<string, Set<string>>;
  _pendingQueueIds: Record<string, Set<string>>;
  liveStreamBuffers: Record<string, LiveStreamBuffer>;
  terminalWatermarks: Record<string, TerminalWatermark>;
  /** Loaded durable window + runtime-row coverage boundary, per Session. */
  sessionTranscripts: Record<string, SessionTranscript>;
  /** Latest optimistic settings mutation per Session. */
  sessionSettingMutations: Record<string, SessionSettingMutationInternal>;
  /** Authoritative server process epoch for runtime watermarks. */
  serverEpoch: string | null;
  /** After a resync boundary, unscoped stream frames are replay echoes until
   * the first task-scoped frame for that Session arrives. */
  unscopedReplayPending: Record<string, boolean>;
  /** Start offset of each Session's loaded canonical history window. */
  historyWindowStarts: Record<string, number>;

  // Actions
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  refreshCurrentSessionHistory: () => Promise<void>;
  loadOlderMessages: (limit?: number) => Promise<void>;
  /** Load pages until the stable fromEnd target is present in currentMessages. */
  ensureMessageLoaded: (fromEnd: number, total: number) => Promise<Message | null>;
  createNewSession: (
    name: string,
    workdir?: string | null,
    adapter?: string,
    sessionTemplate?: string,
    settings?: {
      model?: string;
      permissionMode?: string;
      alwaysThinkingEnabled?: boolean;
      effort?: string;
      outputMode?: string;
      workspaceIds?: string[];
    },
  ) => Promise<void>;
  removeSession: (id: string) => Promise<void>;
  removeSessions: (ids: string[], cascadeIds?: string[]) => Promise<void>;
  batchRemoveSessions: () => Promise<void>;
  rename: (id: string, name: string) => Promise<void>;
  branch: (id: string, name: string) => Promise<void>;
  reimport: (id: string) => Promise<void>;
  setInputDraft: (id: string, draft: string) => void;
  acceptServerEpoch: (epoch: string | null | undefined) => void;
  beginUnscopedReplay: (sessionIds: string[]) => void;
  addMessage: (msg: Message) => void;
  appendMessages: (msgs: Message[]) => void;
  /** Append a user-side control/message to its target Session projection. */
  appendLocalMessage: (sessionId: string, msg: Message) => void;
  getLiveStreamMessages: (sessionId: string) => Message[];
  /** Read the current task watermark before building a stream projection. */
  canApplyLiveStream: (sessionId: string, meta: LiveStreamMeta) => boolean;
  applyLiveStream: (sessionId: string, messages: Message[], meta: LiveStreamMeta) => boolean;
  reconcileWorkerResult: (
    sessionId: string,
    event: {
      status?: string;
      cancelled?: boolean;
      result?: string;
      terminalCoverage?: TerminalCoverage;
      historyEpoch?: string;
      historyRevision?: number;
    },
    meta: LiveStreamMeta,
  ) => boolean;
  applyWorkerStatus: (
    sessionId: string,
    status: string | null,
    meta: LiveStreamMeta,
    terminal?: boolean,
  ) => boolean;
  /** Merge one history page into a Session's loaded durable window. */
  applyHistoryPage: (sessionId: string, page: HistoryPage) => boolean;
  /** Converge runtime rows onto canonical history after a terminal coverage. */
  recoverSessionHistory: (sessionId: string) => Promise<void>;
  clearLiveStream: (sessionId: string) => void;
  /** Show a durably queued user message before local CLI hand-off. */
  appendQueuedMessage: (sessionId: string, item: { id: string; text: string; parts?: Message['parts'] }) => void;
  /** Update only the still-pending projection for an in-place queue edit. */
  updateQueuedMessage: (sessionId: string, item: { id: string; text: string; parts?: Message['parts'] }) => void;
  removeQueuedMessage: (sessionId: string, queueItemId: string) => void;
  /** Append user messages after the server confirms local CLI hand-off. */
  appendDeliveredMessages: (sessionId: string, msgs: Message[]) => void;
  /** Apply settings locally first, then reconcile or roll back the exact mutation. */
  patchSessionSettings: (
    sessionId: string,
    patch: SessionSettingPatch,
    persist: (id: string, settings: SettingsBody) => Promise<Session | ApiGenericResponse>,
  ) => Promise<SessionSettingsMutationResult>;
  updateSession: (id: string, data: Partial<Session>, preserveOnSnapshot?: boolean) => void;
  /** 就地更新某 session 卡片：追加结果文本到 history + lastResult + historyTotal，
   *  不等 300ms 防抖全量兜底即可让「最后消息 summary」立即最新（镜像 vanilla
   *  `_applyWorkerUpdate` 的就地更新路径）。 */
  applyResultToSession: (
    id: string,
    e: { status?: string; cancelled?: boolean; result?: string },
  ) => void;
  toggleMultiSelect: (id?: string) => void;
  toggleSelection: (id: string) => void;
  exitMultiSelect: () => void;
  setRendering: (v: boolean) => void;
}

/** Monotonic sequence stamped onto `_sessionWsTouchedSeq` by updateSession().
 *  Kept outside the store because only the relative order *per session* matters
 *  (see loadSessions): a value recorded during a fetch is strictly greater than
 *  the one captured when that fetch was issued. */
let wsTouchSeq = 0;
let localTouchSeq = 0;
let settingsTouchSeq = 0;
let localMessageSeq = 0;

// Local user rows are transient projection state, not a new persisted wire
// field.  Keep the count at which a row was created outside Zustand so an
// older history response can be distinguished from the canonical response
// that eventually contains it without adding state that every test/reset must
// know how to clear.
const localMessageOrigins = new WeakMap<Message, number>();

function queueIds(message: Message): string[] {
  return Array.isArray(message.queueItemIds)
    ? message.queueItemIds.filter((id): id is string => typeof id === 'string' && id.length > 0)
    : [];
}

function canonicalQueueId(id: string): string {
  return id.startsWith('queue:') ? id.slice('queue:'.length) : id;
}

function queueIdMatches(a: string, b: string): boolean {
  return a === b || canonicalQueueId(a) === canonicalQueueId(b);
}

function queueSetMatches(ids: Set<string>, candidate: string): boolean {
  return [...ids].some((id) => queueIdMatches(id, candidate));
}

function messageShapeKey(message: Message): string {
  return `${message.role}\u0000${message.content}\u0000${JSON.stringify(message.parts ?? null)}`;
}

function explicitMessageIdentity(message: Message): string[] {
  return [
    ...(message.messageId ? [`message:${message.messageId}`] : []),
    ...(message.blockId ? [`block:${message.blockId}`] : []),
    ...queueIds(message).map((id) => `queue:${canonicalQueueId(id)}`),
    ...(message.nativeItemId ? [`native:${message.nativeItemId}`] : []),
  ];
}

function hasExplicitIdentityOverlap(a: Message, b: Message): boolean {
  const bIds = new Set(explicitMessageIdentity(b));
  return explicitMessageIdentity(a).some((id) => bIds.has(id));
}

function isLocallyOwnedUserMessage(message: Message): boolean {
  return message.role === 'user'
    && (queueIds(message).length > 0
      || message.nativeItemId?.startsWith('local:user:') === true);
}

function rememberLocalMessageOrigin(message: Message, historyTotal: number): void {
  if (isLocallyOwnedUserMessage(message) && !localMessageOrigins.has(message)) {
    localMessageOrigins.set(message, Math.max(0, historyTotal));
  }
}

function copyLocalMessageOrigin(next: Message, previous: Message): void {
  const origin = localMessageOrigins.get(previous);
  if (origin !== undefined) localMessageOrigins.set(next, origin);
}

function withLocalUserIdentity(sessionId: string, message: Message): Message {
  if (message.role !== 'user') return message;
  if (queueIds(message).length > 0 || message.nativeItemId?.startsWith('local:user:')) {
    return message;
  }
  localMessageSeq += 1;
  return {
    ...message,
    nativeItemId: `local:user:${sessionId}:${localMessageSeq}`,
  };
}

const SESSION_SETTING_KEYS = [
  'model',
  'permissionMode',
  'alwaysThinkingEnabled',
  'effort',
  'outputMode',
  'modelContextWindow',
  'modelAutoCompactTokenLimit',
] as const satisfies readonly (keyof Session)[];

type SessionSettingKey = (typeof SESSION_SETTING_KEYS)[number];
export type SessionSettingPatch = Partial<Pick<Session, SessionSettingKey>>;

export interface SessionSettingMutationState {
  sequence: number;
  pending: boolean;
  patch?: SessionSettingPatch;
  error?: string | null;
}

interface SessionSettingMutationInternal extends SessionSettingMutationState {
  rollback: SessionSettingPatch;
  authoritative: SessionSettingPatch;
}

export interface SessionSettingsMutationResult {
  response: Session | ApiGenericResponse;
  applied: boolean;
  stale: boolean;
}

export interface LiveStreamBuffer {
  workerId?: string;
  generation?: number;
  taskSeq?: number;
  taskId?: string;
  replayed?: boolean;
  turnId?: string;
  itemId?: string;
  streamText?: string;
  /** Identity of the task this buffer belongs to; scopes id-less block slots. */
  taskKey?: string;
  /** Absolute history boundary captured when this task entered running state. */
  historyStartOffset?: number;
  revision: number;
  messages: Message[];
  /** Projection indexes let delta updates avoid scanning canonical history. */
  projectionIndexes?: Record<string, number>;
  /**
   * The exact row object this buffer last wrote at each projection index. A
   * cached index is only reusable while the array still holds that object;
   * a prepend/refresh/filter that shifts rows invalidates it instead of letting
   * a stale index overwrite an unrelated message.
   */
  projectionRefs?: Record<string, Message>;
}

/**
 * Per-Session transcript metadata: the loaded durable window (keyed by absolute
 * storage offset) plus the runtime rows that are not covered by it yet.
 *
 * `currentMessages` stays the rendered authority so unrelated structural
 * updates (a prepend by a merge, a display filter) are never silently dropped;
 * this record supplies the ordering/coverage facts that identity alone cannot:
 * offsets for pagination, and the boundary where runtime rows begin.
 */
export interface SessionTranscript {
  window: LoadedWindow;
  /** Unconverged display rows after the durable window, in insertion order. */
  runtime: Message[];
  /** Absolute offset where the runtime region starts. */
  anchorOffset: number;
  serverEpoch: string | null;
}

// Stable keys for runtime rows so a re-delivered live buffer replaces its own
// previous run instead of appending a second copy.
const runtimeKeys = new WeakMap<Message, string>();
/** Canonical end offset proven to precede a local worker.result marker. */
const terminalMarkerOffsets = new WeakMap<Message, { epoch: string | null; end: number }>();

function bindRuntimeKey(message: Message, key: string): Message {
  runtimeKeys.set(message, key);
  return message;
}

function runtimeKeyOf(message: Message): string | null {
  return runtimeKeys.get(message) ?? null;
}

/** Terminal coverage carried by `worker.result` (server.py `_publish_terminal_events`). */
export interface TerminalCoverage {
  historyEpoch?: string;
  historyRevision?: number;
  messageIds?: string[];
}

export interface LiveStreamMeta {
  serverEpoch?: string;
  workerId?: string;
  generation?: number;
  taskSeq?: number;
  taskId?: string;
  /** True only for the server's reconnect replay cache, not live traffic. */
  replayed?: boolean;
  turnId?: string;
  itemId?: string;
  streamText?: string;
}

interface TerminalWatermark extends LiveStreamMeta {
  status: string;
  revision: number;
  /** Task identity of the terminal event, for idempotent replays. */
  taskKey?: string;
  /** Result body recorded at terminal time (replay detection without a cursor). */
  result?: string;
  /** Durable coverage boundary carried by `worker.result`. */
  historyEpoch?: string;
  historyRevision?: number;
}

/**
 * Page size for interactive history requests. Kept in one place so the loaded
 * window, the lazy-load trigger and terminal recovery agree on the window.
 */
function historyPageSize(): number {
  return 50;
}

function sameWorkerGeneration(a: LiveStreamMeta, b: LiveStreamMeta): boolean {
  return Boolean(a.workerId && b.workerId && a.workerId === b.workerId
    && (a.generation === undefined || b.generation === undefined || a.generation === b.generation));
}

function isOlderMeta(incoming: LiveStreamMeta, known: LiveStreamMeta): boolean {
  if (incoming.serverEpoch && known.serverEpoch && incoming.serverEpoch !== known.serverEpoch) {
    return true;
  }
  if (incoming.generation !== undefined && known.generation !== undefined) {
    if (incoming.generation < known.generation) return true;
    if (incoming.generation > known.generation) return false;
  }
  if (incoming.workerId && known.workerId && incoming.workerId !== known.workerId) {
    return incoming.generation === undefined || known.generation === undefined
      || incoming.generation <= known.generation;
  }
  if (incoming.taskSeq !== undefined && known.taskSeq !== undefined) {
    if (incoming.taskSeq < known.taskSeq) return true;
    if (incoming.taskSeq > known.taskSeq) return false;
    // taskSeq is the primary cursor, but a mismatched taskId at the same
    // cursor is still a foreign/replayed task.  Do not let its native alias
    // or stream row enter the current task window.
    if (incoming.taskId && known.taskId && incoming.taskId !== known.taskId) {
      return true;
    }
  }
  // Once a task-scoped frame has been accepted, an unscoped frame from the
  // same worker generation cannot prove that it belongs to a newer turn.
  // This is the small gap between a status/task boundary and adapters that
  // still emit legacy frames without taskSeq.
  if (incoming.taskSeq === undefined && known.taskSeq !== undefined
      && sameWorkerGeneration(incoming, known)) {
    return true;
  }
  if (incoming.taskId && known.taskId && incoming.taskId !== known.taskId
      && incoming.taskSeq === undefined && known.taskSeq === undefined
      && sameWorkerGeneration(incoming, known)) {
    return true;
  }
  // stream_text is cumulative.  A delayed shorter prefix (or an exact replay)
  // is stale even when its transport envelope has no monotonic cursor.
  const sameStreamTarget = Boolean(
    (incoming.itemId && known.itemId && incoming.itemId === known.itemId)
      || (incoming.turnId && known.turnId && incoming.turnId === known.turnId)
      || (!incoming.itemId && !known.itemId
        && !incoming.turnId && !known.turnId
        && sameWorkerGeneration(incoming, known)
        && (incoming.taskSeq === known.taskSeq)),
  );
  if (sameStreamTarget && incoming.streamText !== undefined
      && known.streamText !== undefined
      && known.streamText.startsWith(incoming.streamText)
      && incoming.streamText.length <= known.streamText.length) {
    return true;
  }
  return false;
}

function isBlockedByTerminal(
  incoming: LiveStreamMeta,
  terminal: TerminalWatermark | undefined,
  status: string,
): boolean {
  if (!terminal) return false;
  if (isOlderMeta(incoming, terminal)) return true;
  if (status === 'idle' && sameWorkerGeneration(incoming, terminal)) return false;
  if (status === 'running'
      && sameWorkerGeneration(incoming, terminal)
      && incoming.taskSeq === undefined) {
    // Running events without taskSeq cannot prove that they belong to a new
    // turn. The backend includes taskSeq on new lifecycle events; rejecting
    // this ambiguous shape is safer than resurrecting a completed turn.
    return true;
  }
  if (status === 'stream'
      && sameWorkerGeneration(incoming, terminal)
      && terminal.taskSeq !== undefined
      && incoming.taskSeq === undefined) {
    return true;
  }
  if (incoming.taskSeq !== undefined && terminal.taskSeq !== undefined
      && incoming.taskSeq <= terminal.taskSeq
      && (incoming.generation === undefined || terminal.generation === undefined
        || incoming.generation === terminal.generation)) {
    return true;
  }
  return false;
}

function valueEqual(a: unknown, b: unknown): boolean {
  return Array.isArray(a) || Array.isArray(b)
    ? JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
    : a === b;
}

function pickSessionSettings(session: Partial<Session>): SessionSettingPatch {
  const result: SessionSettingPatch = {};
  for (const key of SESSION_SETTING_KEYS) {
    if (Object.prototype.hasOwnProperty.call(session, key)) {
      result[key] = session[key] as never;
    }
  }
  return result;
}

function mergeSessionSettingPatch(
  session: Session,
  patch: SessionSettingPatch | undefined,
): Session {
  return patch && Object.keys(patch).length > 0 ? { ...session, ...patch } : session;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Failed to save settings');
}

/** Compare only fields returned by summary=1; preserve the existing object
 * reference when the server did not change any sidebar-visible value. */
function sameSessionSnapshot(previous: Session, next: Session, server: Session): boolean {
  return Object.keys(server).every((key) =>
    valueEqual(previous[key as keyof Session], next[key as keyof Session]),
  );
}

const SUMMARY_PROJECTION_FIELDS = [
  'summaryRevision', 'lastUserPreview', 'lastAssistantPreview',
  'lastDisplayPreview', 'lastMessage', 'historyTotal', 'updatedAt',
  'workerStatus', 'workerId', 'workerGeneration', 'workerTaskId', 'workerTaskSeq',
] as const;

/** A delayed HTTP/WS snapshot must not roll a newer summary projection back. */
function preserveNewerSummary(current: Session, incoming: Session): Session {
  const currentRevision = current.summaryRevision;
  const incomingRevision = incoming.summaryRevision;
  if (typeof currentRevision !== 'number'
      || currentRevision <= (typeof incomingRevision === 'number' ? incomingRevision : -1)) {
    return incoming;
  }
  const preserved = { ...incoming, summaryRevision: currentRevision };
  for (const field of SUMMARY_PROJECTION_FIELDS) {
    if (field in current) Object.assign(preserved, { [field]: current[field] });
  }
  return preserved;
}

// ── Transcript helpers ──────────────────────────────────────────────────────

/**
 * Lazily build the transcript record for a Session from its loaded history.
 * Seeded state (tests, fixtures, a Session list snapshot) has no transcript, so
 * the durable window is derived once from `history` + `historyStart/total`.
 */
function ensureTranscript(
  transcripts: Record<string, SessionTranscript> | undefined,
  session: Session,
): SessionTranscript {
  const existing = transcripts?.[session.id];
  if (existing && !transcriptIsStale(existing, session)) return existing;
  return {
    window: windowFromSession(session),
    runtime: [],
    anchorOffset: session.historyTotal ?? (session.history?.length ?? 0),
    serverEpoch: null,
  };
}

/**
 * A transcript is stale when the Session's canonical projection cannot be a
 * continuation of the window it was built from: the epoch changed, or the row
 * count shrank (history replaced rather than appended). Real flows only grow,
 * so this never fires in production; it is what keeps a transcript from
 * outliving the Session snapshot it was derived from.
 */
function transcriptIsStale(transcript: SessionTranscript, session: Session): boolean {
  if (session.historyEpoch && transcript.window.epoch
      && session.historyEpoch !== transcript.window.epoch) return true;
  if (typeof session.historyTotal === 'number'
      && session.historyTotal < transcript.window.total) return true;
  return false;
}

/**
 * Compatibility between an unconverged runtime row and the canonical row that
 * occupies the next expected storage offset.
 *
 * Provider rows carry no block id (the real CBC/Codex text shape), so the only
 * available signal is the role plus the authoritative body. A different role
 * means the canonical row is a *different* message the client never saw (an
 * agent-injected user row, a tool row) and must be inserted before the runtime
 * row instead of replacing it. Within one role the canonical body is
 * authoritative (it may be the finalized, longer or shorter version of the same
 * block). Deliberately never used to match rows *across* tasks.
 */
function normalizedToolContent(content: string): string | undefined {
  const open = content.indexOf('(');
  if (open <= 0 || !content.endsWith(')')) return undefined;
  try {
    const input = JSON.parse(content.slice(open + 1, -1)) as unknown;
    const sortObjectKeys = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(sortObjectKeys);
      if (value !== null && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, child]) => [key, sortObjectKeys(child)]));
      }
      return value;
    };
    return `${content.slice(0, open)}(${JSON.stringify(sortObjectKeys(input))})`;
  } catch {
    // Some adapters truncate very large tool inputs for display. Preserve the
    // exact-content path for those rows instead of guessing their identity.
    return undefined;
  }
}

function runtimeRowCompatible(runtimeRow: Message, durableRow: Message): boolean {
  if (runtimeRow.role !== durableRow.role) return false;
  if (runtimeRow.content === durableRow.content) return true;
  // Tool content is a display projection of structured JSON input. Providers
  // serialize the same object with different whitespace/Unicode escaping, so
  // exact string comparison can break alignment here and make every later
  // row in the task appear a second time after history recovery.
  if (runtimeRow.role === 'tool') {
    const runtimeTool = normalizedToolContent(runtimeRow.content);
    const durableTool = normalizedToolContent(durableRow.content);
    if (runtimeTool !== undefined && runtimeTool === durableTool) return true;
  }
  // A role-only match is unsafe: two adjacent assistant/thinking/tool rows
  // from different tasks can have different bodies at the same offset.  The
  // final result/history convergence path supplies the authoritative body; a
  // non-identical runtime row may only consume it when the provider/canonical
  // identity explicitly agrees.
  return explicitIdentityOf(runtimeRow).some((identity) =>
    explicitIdentityOf(durableRow).includes(identity));
}

/**
 * Project the display from the durable window plus the runtime region.
 *
 * The runtime region is aligned against the canonical rows that start at
 * `anchorOffset`: a runtime row consumes the next canonical row when they are
 * role-compatible, and any incompatible canonical row the server appended in
 * between is emitted as its own row *before* it. Runtime rows with no canonical
 * counterpart yet (the in-flight tail) are emitted after every canonical row
 * the server already knows, and canonical rows past the aligned prefix follow.
 * Display markers keep their exact runtime position, which is what anchors a
 * DONE row inside its own task.
 */
function alignedLiveBufferStart(
  transcript: SessionTranscript,
  buffer: LiveStreamBuffer | undefined,
): number | undefined {
  if (!buffer?.taskKey || buffer.messages.length === 0) return undefined;
  const taskPrefix = `slot:${buffer.taskKey}:`;
  const taskRows = transcript.runtime.filter((row) =>
    runtimeKeyOf(row)?.startsWith(taskPrefix),
  );
  if (taskRows.length !== buffer.messages.length) return undefined;
  if (!taskRows.every((row, index) =>
    row.role === buffer.messages[index]!.role
      && row.content === buffer.messages[index]!.content,
  )) return undefined;

  const taskRuntimeIndexes = transcript.runtime.flatMap((row, index) =>
    runtimeKeyOf(row)?.startsWith(taskPrefix) ? [index] : [],
  );
  const taskRuntimeStart = taskRuntimeIndexes[0]!;
  const beforeTask = transcript.runtime.slice(0, taskRuntimeStart);
  // A local/queued user row immediately before the live turn belongs in the
  // same canonical run. Earlier unconverged task rows make a wider re-anchor
  // ambiguous, so retain the ordinary projection in that case.
  if (beforeTask.some((row) => !isLocalMarker(row) && row.role !== 'user')) {
    return undefined;
  }
  const runtimePrefix = beforeTask.filter((row) => !isLocalMarker(row));
  const expected = [...runtimePrefix, ...buffer.messages];
  const prefixLength = runtimePrefix.length;
  const taskStart = buffer.historyStartOffset ?? transcript.anchorOffset;
  const minimumStart = Math.max(
    transcript.window.start ?? 0,
    taskStart - prefixLength,
  );
  const maximumStart = (transcript.window.end ?? transcript.window.total) - expected.length;
  const matches: number[] = [];
  for (let start = minimumStart; start <= maximumStart; start += 1) {
    let matchesRun = true;
    for (let index = 0; index < expected.length; index += 1) {
      const durable = transcript.window.rows.get(start + index);
      if (!durable || !runtimeRowCompatible(expected[index]!, durable)) {
        matchesRun = false;
        break;
      }
      // A same-text row from an earlier turn must not be mistaken for this
      // active task. Its persisted response cannot precede the task boundary.
      if (index >= prefixLength && start + index < taskStart) {
        matchesRun = false;
        break;
      }
    }
    if (matchesRun) matches.push(start);
  }
  return matches.length === 1 ? matches[0] : undefined;
}

/**
 * A completed task can stream its native items in a different order from the
 * final history page. Match only the runtime run ending at that task's result
 * marker, starting at the next unconsumed absolute history offset. Every
 * runtime row needs a distinct canonical counterpart before this run can be
 * replaced; an unseen user row is a task boundary, not a match to skip past.
 */
function coveredTerminalRunEnd(
  window: LoadedWindow,
  startOffset: number,
  rows: Message[],
): number | undefined {
  if (rows.length === 0) return undefined;
  const used = new Set<number>();
  const limit = window.end ?? window.total;
  let end = startOffset;
  for (const row of rows) {
    let matched = -1;
    for (let offset = startOffset; offset < limit; offset += 1) {
      const durable = window.rows.get(offset);
      if (!durable) break;
      if (!used.has(offset) && runtimeRowCompatible(row, durable)) {
        matched = offset;
        break;
      }
    }
    if (matched < 0) return undefined;
    used.add(matched);
    end = Math.max(end, matched + 1);
  }
  for (let offset = startOffset; offset < end; offset += 1) {
    const durable = window.rows.get(offset);
    if (!durable || (durable.role === 'user' && !used.has(offset))) return undefined;
  }
  return end;
}

function projectTranscript(
  transcript: SessionTranscript,
  liveBuffer?: LiveStreamBuffer,
): Message[] {
  const { window, runtime } = transcript;
  // Session summary metadata may advance past the canonical rows while a
  // background task is streaming. If this task's complete runtime block run
  // now exists in history, start projection at that run so its live copies
  // converge onto the canonical objects instead of being appended after them.
  const anchorOffset = alignedLiveBufferStart(transcript, liveBuffer)
    ?? transcript.anchorOffset;
  const ordered = [...window.rows.entries()].sort((a, b) => a[0] - b[0]);
  // A canonical row can move ahead of a still-streaming tool row. Keep an
  // explicit identity lookup so the matching runtime row can be discarded
  // even after the ordinary in-order alignment has stopped.
  const canonicalIds = new Map<string, number | null>();
  if (runtime.length > 0) {
    const runtimeIds = new Set(runtime.flatMap((row) =>
      explicitIdentityOf(row).map((id) => `${row.role}\0${id}`)));
    for (const [offset, row] of ordered) {
      for (const id of explicitIdentityOf(row)) {
        const key = `${row.role}\0${id}`;
        if (!runtimeIds.has(key)) continue;
        canonicalIds.set(key, canonicalIds.has(key) ? null : offset);
      }
    }
  }
  // Older Pan history rows have no provider/Steer identity. For just the
  // current live task and locally owned users, an exact, unique body in their
  // bounded canonical region can bridge those rows after an order mismatch.
  // Never use this fallback across tasks or when either side is ambiguous.
  const taskPrefix = liveBuffer?.taskKey ? `slot:${liveBuffer.taskKey}:` : null;
  const legacyEligible = (row: Message): boolean => (
    (row.role === 'user' && row.nativeItemId?.startsWith('local:user:') === true
      && localMessageOrigins.has(row))
    || ((row.role === 'assistant' || row.role === 'thinking')
      && Boolean(row.nativeItemId && taskPrefix
        && runtimeKeyOf(row)?.startsWith(taskPrefix)))
  );
  const legacyKey = (row: Message): string => `${row.role}\0${row.content}`;
  const legacyRuntimeCounts = new Map<string, number>();
  for (const row of runtime) {
    if (!legacyEligible(row)) continue;
    const key = legacyKey(row);
    legacyRuntimeCounts.set(key, (legacyRuntimeCounts.get(key) ?? 0) + 1);
  }
  const legacyCanonicalOffsets = new Map<string, number[]>();
  if (legacyRuntimeCounts.size > 0) {
    for (const [offset, row] of ordered) {
      if (row.nativeItemId || !['user', 'assistant', 'thinking'].includes(row.role)) continue;
      const key = legacyKey(row);
      if (!legacyRuntimeCounts.has(key)) continue;
      const offsets = legacyCanonicalOffsets.get(key) ?? [];
      offsets.push(offset);
      legacyCanonicalOffsets.set(key, offsets);
    }
  }
  const display: Message[] = [];
  for (const [offset, message] of ordered) {
    if (offset < anchorOffset) display.push(message);
  }
  const emitted = new Set<number>();
  let next = anchorOffset;
  let aligned = true;
  for (let index = 0; index < runtime.length; index += 1) {
    const row = runtime[index]!;
    // Result markers bound a single completed turn. Once its whole runtime
    // run is durable, show canonical order and keep the marker after that run.
    // This also advances the offset before the next turn, so identical text in
    // two separate tasks cannot consume one history row twice.
    if (aligned && (index === 0 || isLocalMarker(runtime[index - 1]!))
        && !isLocalMarker(row)) {
      const markerIndex = runtime.findIndex((candidate, candidateIndex) =>
        candidateIndex > index && isLocalMarker(candidate)
          && candidate.nativeItemId?.startsWith('worker.result:') === true,
      );
      if (markerIndex >= 0) {
        const run = runtime.slice(index, markerIndex);
        if (run.every((candidate) => !isLocalMarker(candidate))) {
          const coveredEnd = coveredTerminalRunEnd(window, next, run);
          if (coveredEnd !== undefined) {
            for (let offset = next; offset < coveredEnd; offset += 1) {
              display.push(window.rows.get(offset)!);
              emitted.add(offset);
            }
            const marker = runtime[markerIndex]!;
            terminalMarkerOffsets.set(marker, { epoch: window.epoch, end: coveredEnd });
            display.push(marker);
            next = coveredEnd;
            index = markerIndex;
            continue;
          }
        }
      }
    }
    if (isLocalMarker(row)) {
      const coverage = terminalMarkerOffsets.get(row);
      if (aligned && coverage?.epoch === window.epoch && coverage.end >= next) {
        let fullyLoaded = true;
        for (let offset = next; offset < coverage.end; offset += 1) {
          if (!window.rows.has(offset)) { fullyLoaded = false; break; }
        }
        if (fullyLoaded) {
          for (let offset = next; offset < coverage.end; offset += 1) {
            display.push(window.rows.get(offset)!);
            emitted.add(offset);
          }
          next = coverage.end;
        }
      }
      display.push(row);
      continue;
    }
    const canonicalOffset = explicitIdentityOf(row)
      .map((id) => canonicalIds.get(`${row.role}\0${id}`))
      .find((offset): offset is number => offset !== undefined && offset !== null
        && window.rows.get(offset)?.role === row.role);
    const legacyMinimum = row.role === 'user'
      ? Math.max(0, (localMessageOrigins.get(row) ?? 0) - 1)
      : liveBuffer?.historyStartOffset;
    const legacyOffsets = legacyEligible(row)
      && legacyRuntimeCounts.get(legacyKey(row)) === 1
      && legacyMinimum !== undefined
      ? (legacyCanonicalOffsets.get(legacyKey(row)) ?? []).filter((offset) =>
          offset >= legacyMinimum
          && (!row.messageId || !window.rows.get(offset)?.messageId
            || window.rows.get(offset)?.messageId?.startsWith('legacy:')))
      : [];
    const legacyOffset = legacyOffsets.length === 1 ? legacyOffsets[0] : undefined;
    if ((canonicalOffset !== undefined && (!aligned || canonicalOffset < next))
        || (canonicalOffset === undefined && legacyOffset !== undefined
          && (!aligned || legacyOffset < next))) {
      // The canonical row is already in the prefix, or will be emitted in the
      // remaining canonical tail. It represents this exact runtime item even
      // when another item changed their relative order.
      continue;
    }
    if (aligned) {
      let matched = -1;
      for (;;) {
        const durable = window.rows.get(next);
        if (!durable) break;
        if (runtimeRowCompatible(row, durable)) {
          matched = next;
          break;
        }
        // A canonical row the client never streamed (agent/user/tool injection)
        // belongs before this runtime row.
        display.push(durable);
        emitted.add(next);
        next += 1;
      }
      if (matched >= 0) {
        display.push(window.rows.get(matched)!);
        emitted.add(matched);
        next = matched + 1;
        continue;
      }
      aligned = false;
    }
    display.push(row);
  }
  for (const [offset, message] of ordered) {
    if (offset >= anchorOffset && !emitted.has(offset)) display.push(message);
  }
  return display;
}

function withTranscript(
  state: SessionStore,
  sessionId: string,
  transcript: SessionTranscript,
): { sessionTranscripts: Record<string, SessionTranscript> } {
  return {
    sessionTranscripts: { ...state.sessionTranscripts, [sessionId]: transcript },
  };
}

/** Keep `Session.history` as the canonical (marker-free) projection. */
function mirrorHistory(
  sessions: Session[],
  sessionId: string,
  display: Message[],
): Session[] {
  const canonical = canonicalHistory(display);
  return sessions.map((session) => session.id === sessionId
    ? { ...session, history: canonical }
    : session);
}

function sessionOf(state: SessionStore, sessionId: string): Session | undefined {
  return state.sessions.find((session) => session.id === sessionId);
}

function explicitIdentityOf(message: Message): string[] {
  return [
    ...(message.messageId ? [`message:${message.messageId}`] : []),
    ...(message.blockId ? [`block:${message.blockId}`] : []),
    ...(message.nativeItemId ? [`native:${message.nativeItemId}`] : []),
  ];
}

interface LiveProjection {
  display: Message[];
  indexes: Record<string, number>;
  refs: Record<string, Message>;
  appended: number;
}

/**
 * Project one task's full live row list onto the rendered transcript.
 *
 * Rows are matched, in order of preference, by (1) a cached index that is still
 * occupied by the exact row we last wrote there, (2) the previous buffer's own
 * row object (survives an unrelated prepend), (3) explicit provider identity.
 * A body-text match is never used. Anything unmatched is appended, so a row is
 * never written over an unrelated message and two tasks can emit the same text.
 */
function projectLiveRows(
  current: Message[],
  previousBuffer: LiveStreamBuffer | undefined,
  taskKey: string,
  rows: Message[],
  durableTailStart: number,
): LiveProjection {
  let display = current.slice();
  const indexes: Record<string, number> = {};
  const refs: Record<string, Message> = {};
  const usedTargetIndexes = new Set<number>();
  const targetIndexes: number[] = [];
  const targetRefs: Message[] = [];
  let appended = 0;
  for (const [slot, live] of rows.entries()) {
    const keys = liveProjectionKeys(live, { taskKey, slot });
    const previousRow = previousBuffer?.messages[slot];
    const previousKeys = previousRow
      ? liveProjectionKeys(previousRow, {
          taskKey: previousBuffer?.taskKey ?? taskKey,
          slot,
        })
      : [];
    let targetIndex = -1;
    const sameTask = previousBuffer?.taskKey === taskKey;
    const explicit = explicitIdentityOf(live);
    // The previous buffer's keys are only meaningful inside the same task: a
    // new task must never reuse the old task's slot, or its first block would
    // overwrite the previous answer.
    if (targetIndex < 0) for (const key of sameTask ? [...keys, ...previousKeys] : keys) {
      const cached = previousBuffer?.projectionIndexes?.[key];
      if (cached === undefined || cached < 0 || cached >= display.length) continue;
      const row = display[cached]!;
      const sameIdentity = explicitIdentityOf(row).some((identity) =>
        explicitIdentityOf(live).includes(identity));
      if (row.role !== live.role && !sameIdentity) continue;
      const durableOffset = durableOffsetOf(row);
      const canonicalizedPreviousRow = sameTask
        && previousRow !== undefined
        && durableOffset !== undefined
        && durableOffset >= durableTailStart
        && row.role === previousRow.role
        && row.content === previousRow.content;
      if (previousBuffer?.projectionRefs?.[key] !== row && !canonicalizedPreviousRow) continue;
      if (usedTargetIndexes.has(cached)) continue;
      targetIndex = cached;
      break;
    }
    if (targetIndex < 0 && previousRow && sameTask) {
      // The previous row object is still in the array (an older page was
      // prepended, so its index moved). Locate it by object identity.
      const found = display.indexOf(previousRow);
      if (found >= 0 && display[found]!.role === live.role
          && !usedTargetIndexes.has(found)) targetIndex = found;
    }
    if (targetIndex < 0 && sameTask) {
      // A history projection may have moved or cloned the row while leaving
      // its task-local ownership key intact. The cached index then points at
      // the newly inserted durable row, and object identity no longer finds
      // the live row. Search only for this task's key before appending.
      const keyed = display.flatMap((candidate, index) =>
        !usedTargetIndexes.has(index)
          && candidate.role === live.role
          && keys.includes(runtimeKeyOf(candidate) ?? '')
          ? [index]
          : [],
      );
      if (keyed.length === 1) targetIndex = keyed[0]!;
    }
    if (targetIndex < 0 && sameTask && explicit.length === 0) {
      // Id-less adapters can introduce earlier blocks in a final envelope and
      // move the already-streamed assistant to a later slot. Reuse its prior
      // task-local slot only when there is exactly one role/content-prefix
      // candidate in the previous buffer.
      const priorSlots = previousBuffer!.messages.flatMap((candidate, slot) =>
        explicitIdentityOf(candidate).length === 0
          && candidate.role === live.role
          && (candidate.content.startsWith(live.content)
            || live.content.startsWith(candidate.content))
          ? [slot]
          : [],
      );
      if (priorSlots.length === 1) {
        const priorSlot = priorSlots[0]!;
        const prior = previousBuffer!.messages[priorSlot]!;
        const priorKeys = liveProjectionKeys(prior, { taskKey, slot: priorSlot });
        for (const key of priorKeys) {
          const cached = previousBuffer!.projectionIndexes?.[key];
          if (cached === undefined || cached < 0 || cached >= display.length) continue;
          const row = display[cached]!;
          if (row.role !== prior.role || usedTargetIndexes.has(cached)) continue;
          const refMatches = previousBuffer!.projectionRefs?.[key] === row;
          const offset = durableOffsetOf(row);
          const durableMatches = offset !== undefined
            && offset >= durableTailStart
            && row.role === prior.role
            && row.content === prior.content;
          if (refMatches || durableMatches) {
            targetIndex = cached;
            break;
          }
        }
      }
    }
    if (targetIndex < 0 && previousRow && sameTask) {
      // A history refresh can replace the exact live row at its canonical
      // offset with a new Message object that has messageId instead of the
      // provider's nativeItemId. Rebind only when the durable row is adjacent
      // to this task's runtime boundary and exactly equals the prior row for
      // this task slot. The boundary may sit before or after the canonical
      // write depending on which Session summary the browser last saw. This is
      // a scoped identity bridge, not a transcript body-text dedupe.
      const canonicalMatches = display.flatMap((candidate, index) => {
        const offset = durableOffsetOf(candidate);
        return !usedTargetIndexes.has(index)
          && offset !== undefined
          && offset >= durableTailStart
          && candidate.role === previousRow.role
          && candidate.content === previousRow.content
          ? [index]
          : [];
      });
      if (canonicalMatches.length === 1) targetIndex = canonicalMatches[0]!;
    }
    if (targetIndex < 0 && sameTask) {
      if (explicit.length > 0) {
        targetIndex = display.findIndex((candidate, index) =>
          !usedTargetIndexes.has(index)
          && explicitIdentityOf(candidate).some((id) => explicit.includes(id)),
        );
      }
    }
    if (targetIndex >= 0) {
      // Only the changed block needs a new object. Retain all other row refs
      // so React.memo can skip their Markdown/tool renderers during deltas.
      if (live !== previousRow || !sameTask) {
        const target = display[targetIndex]!;
        const durableOffset = durableOffsetOf(target);
        // When a terminal/canonical assistant was persisted before the last
        // cumulative delta reached this browser, that delta may be only a
        // prefix of the already complete row. Keep the authoritative body
        // while attaching the live identity so subsequent deltas still update
        // this same display row.
        const keepDurableBody = durableOffset !== undefined
          && durableOffset >= durableTailStart
          && target.role === live.role
          && target.content.startsWith(live.content);
        const merged = {
          ...target,
          ...live,
          ...(keepDurableBody ? { content: target.content } : {}),
        };
        inheritMessageIdentity(merged, target);
        if (durableOffset !== undefined) markDurableRow(merged, durableOffset);
        display[targetIndex] = merged;
      }
      usedTargetIndexes.add(targetIndex);
    } else {
      targetIndex = display.length;
      display.push(live);
      appended += 1;
      usedTargetIndexes.add(targetIndex);
    }
    targetIndexes[slot] = targetIndex;
    targetRefs[slot] = display[targetIndex]!;
    for (const key of keys) {
      indexes[key] = targetIndex;
      refs[key] = display[targetIndex]!;
    }
    // A row has one runtime ownership key.  The slot key is stable for the
    // task and is deliberately primary; binding the later fallback identity
    // here made the displayed clone look untracked during terminal rebuild.
    bindRuntimeKey(display[targetIndex]!, keys[0]!);
  }

  // When a provider item changes the live order (for example text delta first,
  // tool completion second), the identity matches above identify the right
  // existing DOM rows but do not by themselves move those rows. Rebuild only a
  // contiguous run belonging entirely to this task; canonical rows and rows
  // from another task remain untouched. This keeps the path safe for the
  // normal append-only/id-less provider shape while making native-item reorder
  // explicit and stable across every subsequent delta.
  const targetSet = new Set(targetIndexes);
  const orderedTargets = [...targetSet].sort((a, b) => a - b);
  const canReorder = previousBuffer?.taskKey === taskKey
    && targetIndexes.length === rows.length
    && targetSet.size === rows.length
    && orderedTargets.length > 0
    && orderedTargets.at(-1)! - orderedTargets[0]! + 1 === rows.length
    // A history refresh may have rebound live rows to canonical rows. Once a
    // row has a durable offset, the server's absolute history order wins over
    // any later-arriving stream snapshot for that same task.
    && targetRefs.every((row) => !isDurableRow(row))
    && targetIndexes.some((index, slot) => index !== orderedTargets[0]! + slot);
  if (canReorder) {
    const first = orderedTargets[0]!;
    const taskRows = targetRefs.slice();
    const withoutTask = display.filter((_row, index) => !targetSet.has(index));
    const beforeCount = display
      .slice(0, first)
      .filter((_row, index) => !targetSet.has(index))
      .length;
    display = [
      ...withoutTask.slice(0, beforeCount),
      ...taskRows,
      ...withoutTask.slice(beforeCount),
    ];
    for (const [slot, live] of rows.entries()) {
      const row = targetRefs[slot]!;
      const index = display.indexOf(row);
      for (const key of liveProjectionKeys(live, { taskKey, slot })) {
        indexes[key] = index;
        refs[key] = row;
      }
    }
  }
  return { display, indexes, refs, appended };
}

/**
 * Merge one page into a Session's transcript and return the resulting state
 * slice. `base` lets a caller pass the window as it was *before* a Session
 * snapshot was overwritten (loadSessions must compare against the pre-refresh
 * window, not against the history it just merged in).
 */
function applyHistoryPageToState(
  s: SessionStore,
  sessionId: string,
  page: HistoryPage,
  base?: SessionTranscript,
): Partial<SessionStore> {
  const session = sessionOf(s, sessionId);
  if (!session) return s;
  const transcript = base ?? ensureTranscript(s.sessionTranscripts, session);
  const merged = mergeWindowPage(transcript.window, page);
  if (!merged.accepted) {
    // A page that neither adds an offset nor advances the revision is not an
    // error — a focus refresh of an unchanged window is exactly this — but the
    // in-flight loading flags must still settle.
    return s.currentSessionId === sessionId
      ? { historyLoading: false, initialLoading: false }
      : s;
  }
  const isCurrentSession = s.currentSessionId === sessionId;
  let next: SessionTranscript = {
    ...transcript,
    window: merged.window,
    serverEpoch: s.serverEpoch,
  };
  if (merged.replacedEpoch) {
    // A new epoch invalidates every runtime row of the old identity scope.
    next = { ...next, runtime: [], anchorOffset: merged.window.total };
  } else {
    // Track rendered rows that neither carry a canonical offset nor belong to
    // the runtime region, so a rebuild cannot drop them. The leading run that
    // already mirrors the window (same order, role and body) is this
    // projection's own copy of those canonical rows and is not adopted —
    // otherwise it would be emitted a second time next to its window row.
    const windowList = windowRows(next.window);
    const tracked = new Set(next.runtime);
    const trackedKeys = new Set(next.runtime.map(runtimeKeyOf).filter((key) => key !== null));
    // `currentMessages` is global UI state. It is safe as a compatibility
    // source only while this target is selected; a background Session's page
    // must derive from that Session's own transcript or it will adopt A's
    // runtime rows while applying B's history page.
    const targetDisplay = isCurrentSession
      ? s.currentMessages
      : projectTranscript(transcript, s.liveStreamBuffers[sessionId]);
    let mirrored = 0;
    while (
      mirrored < targetDisplay.length
      && mirrored < windowList.length
      && targetDisplay[mirrored]!.role === windowList[mirrored]!.role
      && targetDisplay[mirrored]!.content === windowList[mirrored]!.content
    ) {
      mirrored += 1;
    }
    const adopted = targetDisplay
      .slice(mirrored)
      .filter((row) => {
        const key = runtimeKeyOf(row);
        return !isDurableRow(row) && !tracked.has(row)
          && (key === null || !trackedKeys.has(key));
      });
    if (adopted.length > 0) next = { ...next, runtime: [...next.runtime, ...adopted] };
  }
  // Rebuild the rendered transcript only when the page actually contributed
  // canonical rows (or replaced the epoch). A focus refresh that re-delivers
  // the window we already have must not reconstruct the display: rows that
  // exist only in the rendered projection (an optimistic user row, an
  // agent-injected message) would be dropped by a rebuild.
  const projected = projectTranscript(next, s.liveStreamBuffers[sessionId]);
  // Keep the previous array reference when the projection is unchanged: callers
  // (and the agent-injection retry) compare array identity to decide whether a
  // refresh brought new content.
  const unchanged = isCurrentSession
    && projected.length === s.currentMessages.length
    && projected.every((row, index) => {
      const other = s.currentMessages[index]!;
      return row.role === other.role
        && row.content === other.content
        && (row.messageId ?? null) === (other.messageId ?? null)
        && (row.nativeItemId ?? null) === (other.nativeItemId ?? null);
    });
  const display = unchanged ? s.currentMessages : projected;
  const lastRow = windowRows(merged.window)[merged.window.rows.size - 1];
  return {
    sessions: s.sessions.map((candidate) => candidate.id === sessionId
      ? {
          ...candidate,
          history: canonicalHistory(display),
          historyTotal: merged.window.total,
          historyStart: merged.window.start ?? 0,
          historyTruncated: (merged.window.start ?? 0) > 0,
          historyEpoch: merged.window.epoch ?? candidate.historyEpoch,
          historyRevision: merged.window.revision,
          ...(lastRow ? { lastMessage: String(lastRow.content).slice(0, 200) } : {}),
        }
      : candidate),
    historyWindowStarts: {
      ...s.historyWindowStarts,
      [sessionId]: merged.window.start ?? 0,
    },
    ...(isCurrentSession
      ? {
          historyLoadEnd: merged.window.start ?? 0,
          hasMoreMessages: (merged.window.start ?? 0) > 0,
          historyLoading: false,
          initialLoading: false,
          ...(!unchanged ? { currentMessages: display } : {}),
        }
      : {}),
    ...withTranscript(s, sessionId, next),
  };
}

/**
 * The canonical rows of a Session: the transcript's loaded window when one
 * exists, otherwise the Session's projected history (seeded state, fixtures).
 */
function durableRowsOf(state: SessionStore, sessionId: string): Message[] {
  const transcript = state.sessionTranscripts[sessionId];
  if (transcript) return [...transcript.window.rows.values()];
  const session = sessionOf(state, sessionId);
  return (session?.history ?? []).filter((row) => !isLocalMarker(row));
}

/** Append rows that are not already represented, keeping the tail deduped. */
function appendCanonicalRows(history: Message[], rows: Message[]): Message[] {
  if (rows.length === 0) return history;
  const result = history.slice();
  const identities = new Set(result.flatMap(explicitIdentityOf));
  for (const row of rows) {
    const ids = explicitIdentityOf(row);
    if (ids.length > 0 && ids.some((id) => identities.has(id))) continue;
    const last = result[result.length - 1];
    // Mirrors the backend rule for the result row: only an identical trailing
    // assistant row is deduped (packages/core/worker.py `_persist_terminal_state`).
    if (!(last && last.role === row.role && last.content === row.content)) result.push(row);
    ids.forEach((id) => identities.add(id));
  }
  return result;
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessions: [],
  sessionsLoading: false,
  currentSessionId: null,
  currentMessages: [],
  hasMoreMessages: false,
  historyLoading: false,
  initialLoading: false,
  historyLoadEnd: 0,
  multiSelectMode: false,
  selectedIds: new Set(),
  inputDrafts: {},
  inputDraftRevisions: {},
  rendering: false,
  _loadSeq: 0,
  _sessionWsTouchedSeq: {},
  _historyRefreshSeq: {},
  _historyPageSeq: {},
  _selectionSeq: {},
  _sessionLocalTouchedSeq: {},
  _sessionSettingsTouchedSeq: {},
  _sessionEventPatches: {},
  _deliveredQueueIds: {},
  _pendingQueueIds: {},
  liveStreamBuffers: {},
  terminalWatermarks: {},
  sessionTranscripts: {},
  sessionSettingMutations: {},
  serverEpoch: null,
  unscopedReplayPending: {},
  historyWindowStarts: {},

  loadSessions: async () => {
    // Reserve this refresh's sequence + snapshot the per-session WS touch
    // counters so a stale in-flight response can neither overwrite a newer
    // refresh nor revert sessions that were locally freshened while THIS
    // request was in flight.
    const loadSeq = get()._loadSeq + 1;
    const touchedAtStart = get()._sessionWsTouchedSeq;
    const localTouchedAtStart = get()._sessionLocalTouchedSeq ?? {};
    const settingsTouchedAtStart = get()._sessionSettingsTouchedSeq ?? {};
    const eventPatchesAtStart = get()._sessionEventPatches ?? {};
    set({ _loadSeq: loadSeq, sessionsLoading: true });
    try {
      // summary=1: lean list (no per-session history download). Card preview
      // comes from `lastMessage`, count from `historyTotal`; the selected
      // session's messages are loaded by selectSession via fetchSessionHistory.
      const sessions = await fetchSessions(true);
      if (get()._loadSeq !== loadSeq) return; // superseded by a newer refresh
      const { currentSessionId } = get();

      set((s) => {
        // Merge the snapshot with locally-fresher workerStatus/workerId.
        //
        // The comparison is strictly per session: the local value wins only
        // when this session's own touch counter advanced *while this fetch was
        // in flight* (that WS write is older than nothing — the response cannot
        // contain it). Comparing against a global "last touch anywhere" counter
        // was wrong: it both shielded the most recently touched session from
        // the authoritative snapshot indefinitely (so a terminal event that was
        // never delivered could not be corrected by any refresh, including the
        // reconnect/focus recovery), and made one session's fate depend on an
        // unrelated session's traffic.
        //
        // Two narrow cases keep local state even without an in-flight touch:
        //  - an explicit local null is the destroy/crash transition the summary
        //    must not resurrect while it lags behind the event;
        //  - the backend holds `w.status = "done"` only transiently between the
        //    worker.result broadcast and its reset to "idle", so a snapshot
        //    carrying it must not regress a status WS events already settled.
        // Anything else means the snapshot is authoritative for this session —
        // including the settled status of a completion event we never received.
        const merged = sessions.map((sess) => {
          const sid = sess.id;
          const cur = s.sessions.find((x) => x.id === sid);
          if (!cur) return sess;
          const touchedBefore = Object.prototype.hasOwnProperty.call(
            touchedAtStart,
            sid,
          );
          const touchedDuringFetch =
            (s._sessionWsTouchedSeq[sid] ?? 0) > (touchedAtStart[sid] ?? 0);
          const locallyTouchedDuringFetch =
            (s._sessionLocalTouchedSeq?.[sid] ?? 0) > (localTouchedAtStart[sid] ?? 0);
          const settingsTouchedDuringFetch =
            (s._sessionSettingsTouchedSeq?.[sid] ?? 0) > (settingsTouchedAtStart[sid] ?? 0);
          const snapshotIsTransientDone = sess.workerStatus === 'done';
          const preserveLocalWorker =
            touchedDuringFetch ||
            (touchedBefore && (cur.workerStatus === null || snapshotIsTransientDone));
          let next = sess;
          // summary=1 intentionally omits history.  Do not let that empty
          // projection erase a loaded/local history that is needed when the
          // user leaves this Session and returns (especially a just-completed
          // Steer or queue hand-off).  The next select/refresh still fetches
          // canonical history and reconciles this projection by identity.
          const summaryHasHistory = Array.isArray(sess.history)
            && sess.history.length > 0;
          const hasLocalUserProjection = Boolean(
            cur && (cur.history || []).some(isLocallyOwnedUserMessage),
          );
          if (cur && !summaryHasHistory && (cur.history || []).length > 0) {
            next = {
              ...next,
              history: cur.history,
              historyTruncated: cur.historyTruncated,
              historyTotal: Math.max(
                sess.historyTotal ?? 0,
                cur.historyTotal ?? 0,
                cur.history.length,
              ),
              ...(hasLocalUserProjection && cur.lastMessage
                ? { lastMessage: cur.lastMessage }
                : {}),
            };
          }
          // The selected chat is rendered from `currentMessages` plus its
          // transcript, not from the session-list response. `fetchSessions(true)`
          // is a summary read and must not replace that projection with a
          // compatibility payload containing only a tail window. Keep the
          // selected window metadata too: copying a stale epoch/revision from
          // the summary would make the next history response look like an epoch
          // replacement and drop the already-rendered prefix.
          if (sid === currentSessionId) {
            const localHistory = (cur.history || []).length > 0
              ? cur.history
              : canonicalHistory(s.currentMessages);
            const localTranscript = s.sessionTranscripts?.[sid];
            if (localHistory.length > 0) {
              const localTotal = localTranscript?.window.total
                ?? cur.historyTotal
                ?? localHistory.length;
              next = {
                ...next,
                history: localHistory,
                historyTruncated: cur.historyTruncated,
                historyTotal: Math.max(
                  sess.historyTotal ?? 0,
                  cur.historyTotal ?? 0,
                  localTotal,
                ),
                ...(typeof cur.historyStart === 'number'
                  ? { historyStart: cur.historyStart }
                  : localTranscript?.window.start !== null
                    && localTranscript?.window.start !== undefined
                    ? { historyStart: localTranscript.window.start }
                    : {}),
                ...(cur.historyEpoch !== undefined
                  ? { historyEpoch: cur.historyEpoch }
                  : localTranscript?.window.epoch
                    ? { historyEpoch: localTranscript.window.epoch }
                    : {}),
                ...(typeof cur.historyRevision === 'number'
                  ? { historyRevision: cur.historyRevision }
                  : localTranscript
                    ? { historyRevision: localTranscript.window.revision }
                    : {}),
              };
            }
          }
          if (preserveLocalWorker) {
            next = {
              ...next,
              // WS state is newer than this snapshot.  Preserve explicit null:
              // it is the destroy/crash transition, not a missing value.
              workerStatus: cur.workerStatus,
              workerId: cur.workerId,
            };
          }
          // summary=1 omits workerId (server.py `_session_summary`) — carry the
          // last-known value forward so the toolbar / worker actions keep
          // resolving the worker after a plain list refresh. Only when the
          // server still reports a live worker (workerStatus present): once the
          // worker is killed/crashed the summary flips workerStatus to null,
          // and a dead workerId must not keep the action buttons alive.
          const carryWorkerId = preserveLocalWorker
            ? cur.workerId
            : (cur.workerId && sess.workerStatus ? cur.workerId : sess.workerId);
          // summary=1 omits the per-session settings — keep the current
          // session's known values (loaded on demand via the settings popover)
          // across refreshes so the pills / effort select don't flip to
          // defaults.
          if (sid === currentSessionId && cur.model) {
            next = {
              ...next,
              model: sess.model ?? cur.model,
              permissionMode: sess.permissionMode ?? cur.permissionMode,
              alwaysThinkingEnabled:
                sess.alwaysThinkingEnabled ?? cur.alwaysThinkingEnabled,
              effort: sess.effort || cur.effort || '',
              workdir: sess.workdir ?? cur.workdir,
              workerId: carryWorkerId,
            };
          } else if (carryWorkerId && next.workerId !== carryWorkerId) {
            next = { ...next, workerId: carryWorkerId };
          }
          // Result/delivery events can update a card before this request
          // returns. Preserve those monotonic fields for this response; a
          // later refresh still performs the final server reconciliation.
          if (locallyTouchedDuringFetch) {
            if ((cur.historyTotal ?? 0) > (next.historyTotal ?? 0)) {
              next = { ...next, historyTotal: cur.historyTotal, lastMessage: cur.lastMessage };
            } else if (cur.lastMessage && cur.lastMessage !== next.lastMessage) {
              next = { ...next, lastMessage: cur.lastMessage };
            }
            if (cur.lastResult) next = { ...next, lastResult: cur.lastResult };
          }
          // A session event is safe to apply immediately, but its debounced
          // snapshot may have been queued before the write reached the
          // backend (or may be an intentionally synthetic/stale snapshot from
          // another client). Consume the event patch once at reconciliation so
          // that this response cannot visibly roll back the newer payload.
          const eventPatch = s._sessionEventPatches?.[sid] ?? eventPatchesAtStart[sid];
          if (eventPatch && Object.keys(eventPatch).length > 0) {
            next = { ...next, ...eventPatch };
          }
          const mutation = s.sessionSettingMutations?.[sid];
          if (settingsTouchedDuringFetch || mutation?.pending) {
            // This HTTP snapshot was issued before the latest local settings
            // mutation settled, or the mutation is still pending.  Keep the
            // newer local fields and let a later refresh perform authority
            // reconciliation.
            next = mergeSessionSettingPatch(next, mutation?.patch);
            if (settingsTouchedDuringFetch && !mutation?.pending) {
              next = mergeSessionSettingPatch(next, mutation?.authoritative);
            }
          }
          next = preserveNewerSummary(cur, next);
          return sameSessionSnapshot(cur, next, sess) ? cur : next;
        });
        const consumedEventIds = new Set(sessions.map((sess) => sess.id));
        return {
          sessions: merged,
          _sessionEventPatches: Object.fromEntries(
            Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => !consumedEventIds.has(sid)),
          ),
        };
      });

      // 服务端 order 驱动：custom 排序模式下，把服务端返回的 session 顺序
      // 同步进 customOrder（另一客户端重排 / 本地 customOrder 过期时仍以服务端
      // 为准）。mock demo 无后端，顺序归 localStorage 管，跳过。
      if (!isMockMode() && useUIStore.getState().sortBy === 'custom') {
        useUIStore.getState().setCustomOrder(sessions.map((s) => s.id));
      }

      // Do not rebuild the selected transcript from a session-list response.
      // The list request is summary=1; its history is intentionally absent and
      // any compatibility/partial tail is not an authoritative history page.
      // Current history is refreshed only by the guarded history loaders
      // (selectSession/refreshCurrentSessionHistory/loadOlderMessages). This is
      // the isolation boundary for summaryBackfillCompleted: a sidebar metadata
      // refresh cannot replace the chat window.
      const selectedAfterRefresh = get().currentSessionId;
      if (selectedAfterRefresh
          && !get().sessions.some((session) => session.id === selectedAfterRefresh)) {
        // Preserve the existing deletion semantics: a session that is no
        // longer in the authoritative list cannot remain selected. This is
        // separate from the normal selected-session projection path above.
        set({
          currentSessionId: null,
          currentMessages: [],
          hasMoreMessages: false,
          initialLoading: false,
        });
      }
    } catch {
      console.warn('[sessionStore] loadSessions failed');
    } finally {
      // 只有最新的刷新请求拥有 sessionsLoading 标志：被更新请求取代的旧响应
      // 在 finally 里不能清掉新请求的转圈状态（否则刷新瞬间 sidebar 闪烁）。
      if (get()._loadSeq === loadSeq) set({ sessionsLoading: false });
    }
  },

  selectSession: async (id: string) => {
    // Drafts are persisted by InputRow's onChange → setInputDraft; nothing to
    // save here. (Previously read a non-existent `#chatInput` DOM node.)

    const session = get().sessions.find((s) => s.id === id);
    if (!session) return;

    const loaded = (session.history || []).length;
    const needsOlder = !!session.historyTruncated;
    const selectionSeq = (get()._selectionSeq[id] ?? 0) + 1;
    const liveRows = get().liveStreamBuffers[id]?.messages ?? [];

    // Single set — one render for session switch. The window is reused when this
    // Session already has a transcript: `Session.history` mirrors the rendered
    // projection (it includes runtime rows), so re-deriving the window from it
    // would mis-tag in-flight blocks as canonical history. Runtime rows come
    // from the transcript when it exists (it already carries the deltas that
    // arrived while this Session was not selected — A→B→A) and from the live
    // buffer on a first entry.
    set((s) => {
      const existing = s.sessionTranscripts[id];
      const transcript: SessionTranscript = existing
        ? { ...existing, serverEpoch: s.serverEpoch }
        : {
            window: windowFromSession(session),
            runtime: liveRows.slice(),
            anchorOffset: session.historyTotal ?? loaded,
            serverEpoch: s.serverEpoch,
          };
      const display = projectTranscript(transcript, s.liveStreamBuffers[id]);
      const start = transcript.window.start ?? 0;
      return {
        currentSessionId: id,
        _selectionSeq: { ...s._selectionSeq, [id]: selectionSeq },
        currentMessages: display,
        hasMoreMessages: needsOlder,
        historyLoading: false,
        historyLoadEnd: start,
        historyWindowStarts: { ...s.historyWindowStarts, [id]: start },
        initialLoading: loaded === 0,
        sessionTranscripts: { ...s.sessionTranscripts, [id]: transcript },
      };
    });

    // 进入 session 后立即拉服务端最新历史替换快照。React 的快照只靠防抖
    // loadSessions 刷新，可能滞后或（在 _loadSeq 超驰/事件丢失时）过期——
    // 事件刷新快照可能先于 history 持久化，不能覆盖正在流式显示的回复。
    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        id,
        0,
        historyPageSize(),
      );
      if (
        get().currentSessionId !== id ||
        get()._selectionSeq[id] !== selectionSeq
      ) {
        // 用户已切走，丢弃过期结果。若当前已无选中 session（如该 session 在
        // 请求期间被删除），不会有后续 selectSession 重置 initialLoading——
        // 这里兜底清掉，避免 chat 面板一直停在转圈。
        if (!get().currentSessionId) set({ initialLoading: false });
        return;
      }
      get().applyHistoryPage(id, data as HistoryPage);
    } catch {
      // 网络失败：保留快照（上方已 set），不阻塞切换。同样清掉
      // initialLoading——否则「空快照 + 拉取失败」会让 chat 面板一直转圈。
      if (
        (get().currentSessionId === id && get()._selectionSeq[id] === selectionSeq) ||
        !get().currentSessionId
      ) {
        set({ initialLoading: false });
      }
      console.warn('[sessionStore] selectSession fresh-history fetch failed', id);
    }
  },

  refreshCurrentSessionHistory: async () => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const requestSeq = (get()._historyRefreshSeq[sid] ?? 0) + 1;
    set((s) => ({
      _historyRefreshSeq: { ...s._historyRefreshSeq, [sid]: requestSeq },
    }));
    try {
      const data = await fetchSessionHistory(sid, 0, historyPageSize());
      const current = get();
      if (
        current.currentSessionId !== sid ||
        current._historyRefreshSeq[sid] !== requestSeq
      ) return;
      // A tail refresh merges rows **by absolute offset** into the loaded
      // window: it must never move the oldest loaded offset, and it must never
      // rebuild the transcript from the page alone (that is what dropped live
      // rows and reordered older pages).
      current.applyHistoryPage(sid, data as HistoryPage);
    } catch {
      // A focus recovery is best effort; retain the stream and local snapshot.
    }
  },

  ensureMessageLoaded: async (fromEnd: number, total: number) => {
    const absoluteIndex = total - 1 - fromEnd;
    if (absoluteIndex < 0) return null;
    let previousEnd = Number.POSITIVE_INFINITY;
    for (let attempt = 0; attempt < 400; attempt += 1) {
      const state = get();
      if (!state.currentSessionId) return null;
      if (state.historyLoadEnd <= absoluteIndex || !state.hasMoreMessages) break;
      if (state.historyLoading) {
        await new Promise((resolve) => setTimeout(resolve, 25));
        continue;
      }
      if (state.historyLoadEnd >= previousEnd) break;
      previousEnd = state.historyLoadEnd;
      // A navigation jump knows the absolute span it still needs. Fetch that
      // span in one bounded request instead of replaying 50-message pages;
      // normal scroll pagination keeps its 50-message default below.
      const needed = state.historyLoadEnd - absoluteIndex;
      await state.loadOlderMessages(Math.min(Math.max(needed, 50), 1000));
    }
    const state = get();
    if (!state.currentSessionId || state.historyLoadEnd > absoluteIndex) return null;
    const localIndex = absoluteIndex - state.historyLoadEnd;
    return state.currentMessages[localIndex] ?? null;
  },

  loadOlderMessages: async (limit?: number) => {
    const { currentSessionId, historyLoading, historyLoadEnd } = get();
    if (
      historyLoading ||
      historyLoadEnd <= 0 ||
      !currentSessionId
    )
      return;

    set({ historyLoading: true });
    const sid = currentSessionId;
    const pageSeq = (get()._historyPageSeq[sid] ?? 0) + 1;
    set((s) => ({ _historyPageSeq: { ...s._historyPageSeq, [sid]: pageSeq } }));

    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        sid,
        historyLoadEnd,
        limit ?? historyPageSize(),
      );
      if (get().currentSessionId !== sid || get()._historyPageSeq[sid] !== pageSeq) {
        if (get().currentSessionId === sid) set({ historyLoading: false });
        return;
      }

      const msgs = data.history || [];
      if (msgs.length === 0) {
        set({ historyLoading: false });
        return;
      }
      get().applyHistoryPage(sid, data as HistoryPage);
      if (get()._historyPageSeq[sid] === pageSeq) set({ historyLoading: false });
    } catch {
      if (get().currentSessionId === sid && get()._historyPageSeq[sid] === pageSeq) {
        set({ historyLoading: false });
      }
    }
  },

  createNewSession: async (name, workdir, adapter, sessionTemplate, settings) => {
    const placeholder: Session = {
      id: `__pending_${name}`,
      name: '...',
      adapter: adapter || 'cbc',
      model: settings?.model ?? null,
      permissionMode: settings?.permissionMode ?? null,
      alwaysThinkingEnabled: settings?.alwaysThinkingEnabled ?? false,
      effort: settings?.effort || '',
      workspaceIds: settings?.workspaceIds ? [...settings.workspaceIds] : [],
      history: [],
    };
    set((s) => ({
      sessions: [...s.sessions, placeholder],
      currentSessionId: placeholder.id,
      // 新建会话的聊天面板必须立刻清空旧 session 的消息：currentMessages
      // 不会被下方 set 自动重置，若不在此清空，左侧卡片已切到新 session 但
      // 聊天区仍渲染旧 session 内容，需反复切换才刷新（bug 1）。
      currentMessages: [],
      // 新 session 没有 history 需要拉取——清掉可能残留的 initialLoading，
      // 否则空历史的新会话会一直显示转圈而非空态。
      initialLoading: false,
    }));

    try {
      const session = await createSession(
        name,
        workdir,
        adapter,
        sessionTemplate,
        settings,
      );
      set((s) => {
        // Drop the placeholder first — a concurrent loadSessions() (e.g. from
        // a WS event) may have overwritten `sessions` while the create call was
        // in flight, so the placeholder may no longer be present. Blindly using
        // .map() would then fail to insert the real session and it would not
        // appear until a later reload/refresh.
        const withoutPlaceholder = s.sessions.filter(
          (se) => se.id !== placeholder.id,
        );
        // Only append the real session if a concurrent reload didn't already
        // bring it in (avoids a duplicate row).
        const sessions = withoutPlaceholder.some((se) => se.id === session.id)
          ? withoutPlaceholder
          : [...withoutPlaceholder, session];
        // A concurrent loadSessions() also resets currentSessionId to null
        // when it can't find the client-only placeholder in the server list —
        // treat that as "still the newly created session" so the selection
        // lands on the real session.
        const wasCurrent =
          s.currentSessionId === placeholder.id || s.currentSessionId === null;
        return {
          sessions,
          currentSessionId: wasCurrent ? session.id : s.currentSessionId,
          // 真实 session 就绪后，聊天区同步为新 session 的历史（新建为空）。
          // 否则 currentMessages 仍残留上一 session 的内容（bug 1）。
          // 仅当创建流程仍是当前选中时才覆盖——若用户中途切走，保持其当前
          // session 的消息不变，避免把别的 session 的消息区清空。
          currentMessages: wasCurrent ? session.history || [] : s.currentMessages,
          initialLoading: false,
        };
      });
    } catch (e) {
      set((s) => ({
        sessions: s.sessions.filter((se) => se.id !== placeholder.id),
        initialLoading: false,
      }));
      throw e;
    }
  },

  removeSession: async (id: string) => {
    if (id.startsWith('__pending_')) return;
    // Optimistic removal
    set((s) => ({
      sessions: s.sessions.filter((session) => session.id !== id),
      currentSessionId:
        s.currentSessionId === id ? null : s.currentSessionId,
      currentMessages:
        s.currentSessionId === id ? [] : s.currentMessages,
      _deliveredQueueIds: Object.fromEntries(
        Object.entries(s._deliveredQueueIds ?? {}).filter(([sid]) => sid !== id),
      ),
      _sessionLocalTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionLocalTouchedSeq ?? {}).filter(([sid]) => sid !== id),
      ),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => sid !== id),
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => sid !== id),
      ),
      _sessionEventPatches: Object.fromEntries(
        Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => sid !== id),
      ),
    }));

    try {
      await deleteSession(id);
      if (get().currentSessionId === id) {
        set({ currentSessionId: null, currentMessages: [] });
      }
      await get().loadSessions();
    } catch {
      await get().loadSessions(); // Recover
    }
  },

  removeSessions: async (ids: string[], cascadeIds: string[] = []) => {
    const selected = new Set(ids.filter((id) => !id.startsWith('__pending_')));
    if (selected.size === 0) return;
    set((s) => ({
      sessions: s.sessions.filter((session) => !selected.has(session.id)),
      currentSessionId: s.currentSessionId && selected.has(s.currentSessionId)
        ? null : s.currentSessionId,
      currentMessages: s.currentSessionId && selected.has(s.currentSessionId)
        ? [] : s.currentMessages,
      selectedIds: new Set(),
      multiSelectMode: false,
      _deliveredQueueIds: Object.fromEntries(
        Object.entries(s._deliveredQueueIds ?? {}).filter(([sid]) => !selected.has(sid)),
      ),
      _sessionLocalTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionLocalTouchedSeq ?? {}).filter(([sid]) => !selected.has(sid)),
      ),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => !selected.has(sid)),
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => !selected.has(sid)),
      ),
      _sessionEventPatches: Object.fromEntries(
        Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => !selected.has(sid)),
      ),
    }));
    try {
      await batchDeleteSessions([...selected], cascadeIds.filter((id) => selected.has(id)));
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },

  batchRemoveSessions: async () => {
    const { selectedIds } = get();
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    set((s) => ({
      sessions: s.sessions.filter(
        (session) => !selectedIds.has(session.id),
      ),
      multiSelectMode: false,
      selectedIds: new Set(),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => !selectedIds.has(sid)),
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => !selectedIds.has(sid)),
      ),
    }));

    // Clear current if deleted
    const { currentSessionId } = get();
    if (currentSessionId && selectedIds.has(currentSessionId)) {
      set({ currentSessionId: null, currentMessages: [] });
    }

    try {
      await batchDeleteSessions(ids);
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },

  rename: async (id: string, name: string) => {
    await renameSession(id, name);
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? { ...session, name } : session,
      ),
    }));
  },

  branch: async (id: string, name: string) => {
    await branchSession(id, name);
    await get().loadSessions();
  },

  reimport: async (id: string) => {
    const session = get().sessions.find((s) => s.id === id);
    if (!session?.cliSessionId) return;

    const newSession = await reimportSession(
      id,
      session.adapter || 'cbc',
      session.cliSessionId,
      session.workdir,
    );
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? newSession : session,
      ),
      currentSessionId:
        s.currentSessionId === id ? newSession.id : s.currentSessionId,
      initialLoading: false,
    }));
  },

  setInputDraft: (id: string, draft: string) => {
    set((s) => ({
      inputDrafts: { ...s.inputDrafts, [id]: draft },
      inputDraftRevisions: {
        ...(s.inputDraftRevisions ?? {}),
        [id]: (s.inputDraftRevisions?.[id] ?? 0) + 1,
      },
    }));
  },

  acceptServerEpoch: (epoch) => {
    if (!epoch) return;
    set((s) => {
      if (s.serverEpoch === epoch) return s;
      // A process restart invalidates runtime-only watermarks and transient
      // live buffers. Durable history, queue projections, and drafts remain.
      const runtimeMessages = Object.values(s.liveStreamBuffers).flatMap(
        (buffer) => buffer.messages,
      );
      const staleIds = new Set(runtimeMessages.flatMap(explicitMessageIdentity));
      const runtimeRefs = new Set(runtimeMessages);
      const runtimeShapes = new Set(runtimeMessages.map(messageShapeKey));
      // Durable rows are the Session's canonical window, not its mirrored
      // history — after a terminal reconcile that projection also contains the
      // runtime rows, and treating those as durable is what would keep a
      // retired epoch's tail alive.
      const durableMessages = s.currentSessionId
        ? durableRowsOf(s, s.currentSessionId)
        : [];
      const durableRefs = new Set(durableMessages);
      const durableIds = new Set(durableMessages.flatMap(explicitMessageIdentity));
      const durableShapeCounts = new Map<string, number>();
      for (const message of durableMessages) {
        const key = messageShapeKey(message);
        durableShapeCounts.set(key, (durableShapeCounts.get(key) ?? 0) + 1);
      }
      const retainedDurableShapes = new Map<string, number>();
      // Runtime rows of the retired epoch must not survive as a canonical tail;
      // their owning transcripts are reset to the durable window.
      const sessionTranscripts = Object.fromEntries(
        Object.entries(s.sessionTranscripts).map(([sid, transcript]) => {
          const session = s.sessions.find((candidate) => candidate.id === sid);
          return [
            sid,
            {
              ...transcript,
              runtime: [],
              anchorOffset: session?.historyTotal
                ?? transcript.window.total
                ?? transcript.anchorOffset,
              serverEpoch: epoch,
            } satisfies SessionTranscript,
          ];
        }),
      );
      const display = s.currentMessages.filter((message) => {
        if (isLocalMarker(message)) return true;
        // A row that carries a canonical offset is durable by construction and
        // must never be evicted here.
        if (isDurableRow(message)) return true;
        const ids = explicitMessageIdentity(message);
        if (runtimeRefs.has(message) && !durableRefs.has(message)) return false;
        if (ids.some((id) => staleIds.has(id))
            && !ids.some((id) => durableIds.has(id))) return false;
        const shape = messageShapeKey(message);
        if (!runtimeShapes.has(shape)) return true;
        const durableCount = durableShapeCounts.get(shape) ?? 0;
        const retained = retainedDurableShapes.get(shape) ?? 0;
        if (retained < durableCount) {
          retainedDurableShapes.set(shape, retained + 1);
          return true;
        }
        return false;
      });
      return {
        serverEpoch: epoch,
        liveStreamBuffers: {},
        terminalWatermarks: {},
        _sessionEventPatches: {},
        ...(runtimeMessages.length > 0
          ? {
              currentMessages: display,
              ...(s.currentSessionId
                ? { sessions: mirrorHistory(s.sessions, s.currentSessionId, display) }
                : {}),
            }
          : {}),
        sessionTranscripts,
      };
    });
  },

  beginUnscopedReplay: (sessionIds) => {
    set(() => ({
      // Keep the marker even when the snapshot has no session list.  The gate
      // below additionally requires replayed:true, so a legitimate unscoped
      // live frame is not permanently swallowed while the marker is pending.
      unscopedReplayPending: Object.fromEntries(
        [['*', true], ...[...new Set(sessionIds)].map((sessionId) => [sessionId, true])],
      ),
    }));
  },

  addMessage: (msg: Message) => {
    const touchSeq = (localTouchSeq += 1);
    set((s) => {
      const sid = s.currentSessionId;
      if (msg.role === 'system') markLocalMarker(msg);
      const row = msg.role === 'system' ? bindRuntimeKey(msg, `marker:${localTouchSeq}`) : msg;
      if (!sid) return { currentMessages: [...s.currentMessages, row] };
      const session = sessionOf(s, sid);
      const base = session
        ? ensureTranscript(s.sessionTranscripts, session)
        : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const display = [...s.currentMessages, row];
      return {
        currentMessages: display,
        sessions: mirrorHistory(s.sessions, sid, display),
        ...withTranscript(s, sid, { ...base, runtime: [...base.runtime, row] }),
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sid]: touchSeq,
        },
      };
    });
  },

  appendMessages: (msgs: Message[]) => {
    if (!msgs.length) return;
    const touchSeq = (localTouchSeq += 1);
    set((s) => {
      const sid = s.currentSessionId;
      const rows = msgs.map((msg, index) => {
        if (msg.role !== 'system') return msg;
        markLocalMarker(msg);
        return bindRuntimeKey(msg, `marker:${localTouchSeq}:${index}`);
      });
      if (!sid) return { currentMessages: [...s.currentMessages, ...rows] };
      const session = sessionOf(s, sid);
      const base = session
        ? ensureTranscript(s.sessionTranscripts, session)
        : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const display = [...s.currentMessages, ...rows];
      return {
        currentMessages: display,
        sessions: mirrorHistory(s.sessions, sid, display),
        ...withTranscript(s, sid, { ...base, runtime: [...base.runtime, ...rows] }),
        _sessionLocalTouchedSeq: { ...s._sessionLocalTouchedSeq, [sid]: touchSeq },
      };
    });
  },

  appendLocalMessage: (sessionId, message) => {
    if (!sessionId || message.role !== 'user' || !message.content) return;
    const localMessage = withLocalUserIdentity(sessionId, message);
    const touchSeq = (localTouchSeq += 1);
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = target.history || [];
      const alreadyInHistory = history.some((candidate) =>
        hasExplicitIdentityOverlap(candidate, localMessage),
      );
      const historyTotal = target.historyTotal ?? history.length;
      rememberLocalMessageOrigin(localMessage, historyTotal);
      const nextHistory = alreadyInHistory ? history : [...history, localMessage];
      const sessions = s.sessions.map((session) => session.id === sessionId
        ? {
            ...session,
            history: nextHistory,
            historyTotal: Math.max(
              session.historyTotal ?? history.length,
              nextHistory.length,
              historyTotal + (alreadyInHistory ? 0 : 1),
            ),
            lastMessage: localMessage.content.slice(0, 200),
          }
        : session);
      const alreadyCurrent = s.currentMessages.some((candidate) =>
        hasExplicitIdentityOverlap(candidate, localMessage),
      );
      const appendHere = s.currentSessionId === sessionId && !alreadyCurrent;
      const display = appendHere ? [...s.currentMessages, localMessage] : s.currentMessages;
      const base = ensureTranscript(s.sessionTranscripts, target);
      return {
        sessions,
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq,
        },
        ...(appendHere
          ? {
              currentMessages: display,
              ...withTranscript(s, sessionId, {
                ...base,
                runtime: [...base.runtime, bindRuntimeKey(localMessage, `local:${localTouchSeq}`)],
              }),
            }
          : {}),
      };
    });
  },

  getLiveStreamMessages: (sessionId) =>
    (get().liveStreamBuffers[sessionId]?.messages || []).slice(),

  canApplyLiveStream: (sessionId, meta) => {
    const state = get();
    if (meta.replayed === true && meta.taskSeq === undefined && !meta.taskId
        && (state.unscopedReplayPending[sessionId]
          || (state.unscopedReplayPending['*']
            && state.unscopedReplayPending[sessionId] !== false))) return false;
    const terminal = state.terminalWatermarks[sessionId];
    if (isBlockedByTerminal(meta, terminal, 'stream')) return false;
    const previous = state.liveStreamBuffers[sessionId];
    return !previous || !isOlderMeta(meta, previous);
  },

  applyLiveStream: (sessionId, messages, meta) => {
    if (!messages.length) return false;
    let accepted = false;
    set((s) => {
      if (meta.replayed === true && meta.taskSeq === undefined && !meta.taskId
          && (s.unscopedReplayPending[sessionId]
            || (s.unscopedReplayPending['*']
              && s.unscopedReplayPending[sessionId] !== false))) return s;
      const terminal = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, terminal, 'stream')) return s;
      const previous = s.liveStreamBuffers[sessionId];
      if (previous && isOlderMeta(meta, previous)) return s;
      const revision = Math.max(
        previous?.revision ?? 0,
        terminal?.revision ?? 0,
      ) + 1;
      const taskKey = taskScopeKey(sessionId, meta, previous);
      const session = sessionOf(s, sessionId);
      const base = session
        ? ensureTranscript(s.sessionTranscripts, session)
        : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const buffer: LiveStreamBuffer = {
        ...meta,
        taskKey,
        historyStartOffset: previous?.historyStartOffset ?? base.anchorOffset,
        revision,
        messages: messages.slice(),
      };
      accepted = true;
      const replayPending = (meta.taskSeq !== undefined || Boolean(meta.taskId))
        && (s.unscopedReplayPending[sessionId]
          || s.unscopedReplayPending['*'])
        ? { ...s.unscopedReplayPending, [sessionId]: false }
        : s.unscopedReplayPending;
      // Keep each Session's own display projection in sync while it streams in
      // the background. Rebuilding runtime as `other rows + whole live buffer`
      // moves rows appended during the turn (Steer/queue delivery) in front of
      // stream blocks that had already appeared. Use the target Session's
      // current projection, then retain that exact interleaving in runtime.
      const targetDisplay = s.currentSessionId === sessionId
        ? s.currentMessages
        : projectTranscript(base, previous);
      const projected = projectLiveRows(
        targetDisplay,
        previous,
        taskKey,
        buffer.messages,
        buffer.historyStartOffset ?? base.anchorOffset,
      );
      buffer.projectionIndexes = projected.indexes;
      buffer.projectionRefs = projected.refs;
      const runtime = projected.display.filter((row) => !isDurableRow(row));
      const transcript: SessionTranscript = {
        ...base,
        runtime,
      };
      if (s.currentSessionId === sessionId) {
        return {
          liveStreamBuffers: { ...s.liveStreamBuffers, [sessionId]: buffer },
          unscopedReplayPending: replayPending,
          currentMessages: projected.display,
          sessions: mirrorHistory(s.sessions, sessionId, projected.display),
          ...withTranscript(s, sessionId, transcript),
        };
      }
      return {
        liveStreamBuffers: { ...s.liveStreamBuffers, [sessionId]: buffer },
        unscopedReplayPending: replayPending,
        ...withTranscript(s, sessionId, transcript),
      };
    });
    return accepted;
  },

  reconcileWorkerResult: (sessionId, event, meta) => {
    let accepted = false;
    let needsRecovery = false;
    set((s) => {
      const terminal = s.terminalWatermarks[sessionId];
      const previousBuffer = s.liveStreamBuffers[sessionId];
      const result = typeof event.result === 'string' ? event.result : '';
      const hasResult = result.trim().length > 0;
      const status = event.status === 'error'
        ? 'error'
        : event.status === 'cancelled' || event.cancelled
          ? 'cancelled'
          : 'done';
      const incomingTaskKey = taskScopeKey(sessionId, meta, previousBuffer);
      if (terminal) {
        // Every side effect of a terminal event is idempotent. A replay or a
        // late event for the same task cursor must not add a second assistant
        // row, clear a newer live buffer, or move the DONE anchor.
        if (isOlderMeta(meta, terminal)) return s;
        const sameCursor = terminal.taskSeq !== undefined && meta.taskSeq !== undefined
          && terminal.taskSeq === meta.taskSeq;
        const noCursor = terminal.taskSeq === undefined && meta.taskSeq === undefined
          && terminal.taskId === undefined && meta.taskId === undefined;
        if (sameCursor || terminal.taskKey === incomingTaskKey) return s;
        if (noCursor && terminal.result === result) return s;
      }
      if (previousBuffer && isOlderMeta(meta, previousBuffer)) return s;
      const coverage: TerminalCoverage = event.terminalCoverage ?? {
        ...(typeof event.historyEpoch === 'string' ? { historyEpoch: event.historyEpoch } : {}),
        ...(typeof event.historyRevision === 'number'
          ? { historyRevision: event.historyRevision }
          : {}),
      };
      if (coverage.historyEpoch || typeof coverage.historyRevision === 'number') {
        needsRecovery = true;
      }

      const session = sessionOf(s, sessionId);
      const base = session
        ? ensureTranscript(s.sessionTranscripts, session)
        : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const liveMessages = previousBuffer?.messages ?? [];
      // The protocol-referenced final block is the last assistant block *of this
      // task's own live rows* — never "the last assistant anywhere".
      let finalSlot = -1;
      for (let index = liveMessages.length - 1; index >= 0; index -= 1) {
        if (liveMessages[index]!.role === 'assistant') {
          finalSlot = index;
          break;
        }
      }
      let finalized = liveMessages;
      let appendedResult = false;
      if (hasResult) {
        finalized = liveMessages.map((message, index) => {
          const next = index === finalSlot ? { ...message, content: result } : { ...message };
          inheritMessageIdentity(next, message);
          return next;
        });
        if (finalSlot < 0) {
          const row: Message = {
            role: 'assistant',
            content: result,
            ...(meta.turnId ? { nativeItemId: `turn:${meta.turnId}` } : {}),
          };
          finalized = [...finalized, row];
          appendedResult = true;
        }
      }
      const revision = Math.max(
        previousBuffer?.revision ?? 0,
        terminal?.revision ?? 0,
      ) + 1;

      // Authoritative convergence for a replayed turn: when the terminal
      // event carries a coverage revision that the loaded durable window
      // already satisfies, the canonical history already holds this task's
      // rows.  The live rows are then a replay/reconnect echo of durable
      // content, not new blocks.  The check is structural — the finalized
      // rows must line up, in order, with the window rows that end at the
      // runtime anchor (anchorOffset - K .. anchorOffset - 1) — and guarded
      // by exact role+content equality, so identity is never guessed from
      // body text or an arbitrary ordinal and a legitimate new delta whose
      // body differs (or that extends past the anchor) is never swallowed.
      // Providers without a coverage revision on the terminal event keep the
      // pre-existing behaviour unchanged.
      let convergedReplay = false;
      if (typeof coverage.historyRevision === 'number'
          && base.window.revision >= coverage.historyRevision
          && finalized.length > 0 && !appendedResult) {
        const start = base.anchorOffset - finalized.length;
        if (start >= 0) {
          let allMatch = true;
          for (let index = 0; index < finalized.length; index += 1) {
            const durable = base.window.rows.get(start + index);
            const row = finalized[index]!;
            if (!durable || durable.role !== row.role
                || durable.content !== row.content) {
              allMatch = false;
              break;
            }
          }
          convergedReplay = allMatch;
        }
      }
      if (convergedReplay) {
        // The window is authoritative and already covers the task: recovery
        // would re-fetch the same rows and converge nothing.
        needsRecovery = false;
      }

      // The task's finalized rows keep their runtime order: rows another path
      // appended while the task was running (a Steer or a delivered user row)
      // stay in front of the result, which is the order the backend persists.
      const previousLiveKeys = new Set(liveMessages.map((row, slot) =>
        liveProjectionKeys(row, { taskKey: previousBuffer?.taskKey ?? incomingTaskKey, slot })[0]!,
      ));
      const keptRuntime = base.runtime.filter((row) => {
        const key = runtimeKeyOf(row);
        return key === null || !previousLiveKeys.has(key);
      });
      const finalizedRuntime = convergedReplay ? [] : finalized.map((row, slot) =>
        bindRuntimeKey(row, liveProjectionKeys(row, { taskKey: incomingTaskKey, slot })[0]!),
      );
      let nextTranscript: SessionTranscript = {
        ...base,
        runtime: [...keptRuntime, ...finalizedRuntime],
      };

      // Re-project in place, keeping display order. Rows that exist only in the
      // rendered projection (seeded state) are adopted so a rebuild keeps them.
      // A converged replay must not adopt: the replayed live rows duplicate
      // rows the window already holds, and adopting them would re-append the
      // very duplication the convergence just removed.
      const isCurrent = s.currentSessionId === sessionId;
      if (isCurrent && !convergedReplay) {
        const tracked = new Set(nextTranscript.runtime);
        const trackedKeys = new Set(
          nextTranscript.runtime
            .map((row) => runtimeKeyOf(row))
            .filter((key): key is string => key !== null),
        );
        // The leading run that already mirrors the window (same order, role and
        // body) is this projection's own copy of those canonical rows, exactly
        // as in applyHistoryPage: adopting it would emit it a second time next
        // to its window row.
        const windowList = windowRows(nextTranscript.window);
        let mirrored = 0;
        while (
          mirrored < s.currentMessages.length
          && mirrored < windowList.length
          && s.currentMessages[mirrored]!.role === windowList[mirrored]!.role
          && s.currentMessages[mirrored]!.content === windowList[mirrored]!.content
        ) {
          mirrored += 1;
        }
        const adopted = s.currentMessages.slice(mirrored).filter((row) => {
          if (isDurableRow(row) || tracked.has(row)) return false;
          // A row that already carries a runtime key is this transcript's own
          // previous version of a row (a superseded live buffer object), not an
          // untracked row that needs adopting.
          const key = runtimeKeyOf(row);
          return key === null || !trackedKeys.has(key);
        });
        if (adopted.length > 0) {
          nextTranscript = { ...nextTranscript, runtime: [...adopted, ...nextTranscript.runtime] };
        }
      }
      const display = isCurrent && finalized.length > 0
        ? projectTranscript(nextTranscript, previousBuffer)
        : s.currentMessages;

      const sessions = s.sessions.map((candidate) => {
        if (candidate.id !== sessionId) return candidate;
        // Only the selected Session has a rendered projection to mirror. A
        // background Session keeps its own canonical history and receives just
        // the finalized rows, so a result for Session B can never rewrite B's
        // history from the chat pane of Session A.
        const history = isCurrent
          ? canonicalHistory(display)
          : appendCanonicalRows(candidate.history ?? [], finalized);
        return {
          ...candidate,
          history,
          historyTotal: Math.max(
            candidate.historyTotal ?? history.length,
            history.length,
            (candidate.historyTotal ?? 0) + (appendedResult ? 1 : 0),
          ),
          lastMessage: hasResult ? result.slice(0, 200) : candidate.lastMessage,
          lastResult: {
            status,
            result,
            timestamp: new Date().toISOString(),
            ...(meta.taskSeq !== undefined ? { taskSeq: meta.taskSeq } : {}),
          },
        };
      });

      accepted = true;
      const needsRecoveryFlag = needsRecovery;
      if (needsRecoveryFlag) {
        // Authoritative recovery: the result's `terminalCoverage` says the
        // durable history now covers this task, so converge the runtime rows
        // onto the canonical rows instead of trusting the stream's shape.
        queueMicrotask(() => {
          void get().recoverSessionHistory(sessionId);
        });
      }
      return {
        sessions,
        liveStreamBuffers: Object.fromEntries(
          Object.entries(s.liveStreamBuffers).filter(([id]) => id !== sessionId),
        ),
        terminalWatermarks: {
          ...s.terminalWatermarks,
          [sessionId]: {
            ...meta,
            taskKey: incomingTaskKey,
            status,
            revision,
            result,
            ...(coverage.historyEpoch ? { historyEpoch: coverage.historyEpoch } : {}),
            ...(typeof coverage.historyRevision === 'number'
              ? { historyRevision: coverage.historyRevision }
              : {}),
          },
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: (localTouchSeq += 1),
        },
        ...(s.currentSessionId === sessionId ? { currentMessages: display } : {}),
        ...withTranscript(s, sessionId, nextTranscript),
      };
    });
    return accepted;
  },

  /**
   * Apply an authoritative history page to a Session transcript.
   *
   * Shared by focus refresh, lazy paging, session entry and terminal recovery so
   * there is exactly one place that decides which canonical rows are loaded.
   */
  applyHistoryPage: (sessionId, page: HistoryPage) => {
    let accepted = false;
    set((s) => {
      const session = sessionOf(s, sessionId);
      if (!session) return s;
      const base = ensureTranscript(s.sessionTranscripts, session);
      accepted = mergeWindowPage(base.window, page).accepted;
      return applyHistoryPageToState(s, sessionId, page);
    });
    return accepted;
  },

  /** Recover the canonical tail after a terminal event's coverage boundary. */
  recoverSessionHistory: async (sessionId) => {
    const requestSeq = (get()._historyRefreshSeq[sessionId] ?? 0) + 1;
    set((s) => ({
      _historyRefreshSeq: { ...s._historyRefreshSeq, [sessionId]: requestSeq },
    }));
    try {
      const data = await fetchSessionHistory(sessionId, 0, historyPageSize());
      if (get()._historyRefreshSeq[sessionId] !== requestSeq) return;
      get().applyHistoryPage(sessionId, data as HistoryPage);
    } catch {
      // Recovery is best effort; the runtime rows remain visible and a later
      // refresh (focus/reconnect) converges them.
    }
  },

  applyWorkerStatus: (sessionId, status, meta, terminal = false) => {
    if (!sessionId) return false;
    let accepted = false;
    set((s) => {
      const watermark = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, watermark, status || 'idle')) return s;
      const previous = s.liveStreamBuffers[sessionId];
      const captureHistoryBoundary = (): number => {
        const session = sessionOf(s, sessionId);
        if (!session) return 0;
        const transcript = ensureTranscript(s.sessionTranscripts, session);
        return Math.max(
          session.historyTotal ?? session.history?.length ?? 0,
          transcript.window.total,
          transcript.anchorOffset,
        );
      };
      // A late status must not move the Session projection back to an older
      // task while the current live buffer remains authoritative.  Keep the
      // legacy/taskId-only path compatible by applying this guard only when a
      // taskSeq is present on either side.
      if (previous
          && (meta.taskSeq !== undefined || previous.taskSeq !== undefined)
          && isOlderMeta(meta, previous)) return s;
      if (previous && status === 'running' && meta.taskSeq !== undefined
          && previous.taskSeq !== undefined && meta.taskSeq > previous.taskSeq) {
        // A new task on the same worker starts a fresh transient turn. Keep an
        // empty task-scoped watermark instead of deleting the buffer outright:
        // a late old delta must be rejected during the gap before the first
        // new-task delta arrives.
        const revision = Math.max(
          previous.revision,
          s.terminalWatermarks[sessionId]?.revision ?? 0,
        ) + 1;
        const nextBuffers = { ...s.liveStreamBuffers };
        nextBuffers[sessionId] = {
          workerId: meta.workerId,
          generation: meta.generation,
          taskSeq: meta.taskSeq,
          taskId: meta.taskId,
          taskKey: taskScopeKey(sessionId, meta),
          historyStartOffset: captureHistoryBoundary(),
          revision,
          messages: [],
        };
        accepted = true;
        return {
          liveStreamBuffers: nextBuffers,
          sessions: s.sessions.map((session) => session.id === sessionId
            ? { ...session, workerId: meta.workerId ?? session.workerId, workerStatus: status }
            : session),
          _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [sessionId]: (wsTouchSeq += 1) },
        };
      }
      accepted = true;
      const next = {
        sessions: s.sessions.map((session) => session.id === sessionId
          ? {
              ...session,
              workerId: terminal ? null : meta.workerId ?? session.workerId,
              workerStatus: status,
            }
          : session),
        _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [sessionId]: (wsTouchSeq += 1) },
      };
      if (!terminal && status === 'running' && !previous
          && (meta.taskSeq !== undefined || Boolean(meta.taskId))) {
        const nextBuffers = { ...s.liveStreamBuffers };
        nextBuffers[sessionId] = {
          ...meta,
          taskKey: taskScopeKey(sessionId, meta),
          historyStartOffset: captureHistoryBoundary(),
          revision: Math.max(0, watermark?.revision ?? 0) + 1,
          messages: [],
        };
        return { ...next, liveStreamBuffers: nextBuffers };
      }
      if (!terminal && status === 'running' && previous
          && previous.historyStartOffset === undefined
          && (meta.taskSeq === undefined || previous.taskSeq === meta.taskSeq)) {
        return {
          ...next,
          liveStreamBuffers: {
            ...s.liveStreamBuffers,
            [sessionId]: {
              ...previous,
              historyStartOffset: captureHistoryBoundary(),
            },
          },
        };
      }
      if (terminal) {
        const revision = Math.max(
          previous?.revision ?? 0,
          watermark?.revision ?? 0,
        ) + 1;
        const nextBuffers = { ...s.liveStreamBuffers };
        delete nextBuffers[sessionId];
        return {
          ...next,
          liveStreamBuffers: nextBuffers,
          terminalWatermarks: {
            ...s.terminalWatermarks,
            [sessionId]: { ...meta, status: status || 'terminal', revision },
          },
        };
      }
      return next;
    });
    return accepted;
  },

  clearLiveStream: (sessionId) => {
    set((s) => {
      if (!s.liveStreamBuffers[sessionId]) return s;
      const liveStreamBuffers = { ...s.liveStreamBuffers };
      delete liveStreamBuffers[sessionId];
      return { liveStreamBuffers };
    });
  },

  appendQueuedMessage: (sessionId, item) => {
    if (!item.id || !item.text.trim()) return;
    const touchSeq = (localTouchSeq += 1);
    const localMessage: Message = {
      role: 'user',
      content: item.text,
      ...(item.parts ? { parts: item.parts } : {}),
      queueItemIds: [item.id],
    };
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = target.history || [];
      const historyHasItem = history.some((message) =>
        queueIds(message).some((id) => queueIdMatches(id, item.id)),
      );
      const currentHasItem = s.currentSessionId === sessionId
        && s.currentMessages.some((message) =>
          queueIds(message).some((id) => queueIdMatches(id, item.id)),
        );
      const historyTotal = target.historyTotal ?? history.length;
      rememberLocalMessageOrigin(localMessage, historyTotal);
      const nextHistory = historyHasItem ? history : [...history, localMessage];
      const existingHistoryMessage = history.find((message) =>
        queueIds(message).some((id) => queueIdMatches(id, item.id)),
      );
      const currentMessage = existingHistoryMessage ?? localMessage;
      const base = ensureTranscript(s.sessionTranscripts, target);
      const runtimeHasItem = base.runtime.some(message =>
        queueIds(message).some(id => queueIdMatches(id, item.id)));
      const sessions = s.sessions.map((session) => session.id === sessionId
        ? {
            ...session,
            history: nextHistory,
            historyTotal: Math.max(
              session.historyTotal ?? history.length,
              nextHistory.length,
              historyTotal + (historyHasItem ? 0 : 1),
            ),
            lastMessage: item.text.slice(0, 200),
          }
        : session);
      return {
        sessions,
        ...(!runtimeHasItem && !historyHasItem
          ? withTranscript(s, sessionId, { ...base,
              runtime: [...base.runtime, bindRuntimeKey(currentMessage, `queued:${sessionId}:${item.id}`)],
            })
          : {}),
        _pendingQueueIds: {
          ...s._pendingQueueIds,
          [sessionId]: new Set([
            ...(s._pendingQueueIds[sessionId] ?? []),
            canonicalQueueId(item.id),
          ]),
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq,
        },
        ...(s.currentSessionId === sessionId && !currentHasItem
          ? { currentMessages: [...s.currentMessages, currentMessage] }
          : {}),
      };
    });
  },

  updateQueuedMessage: (sessionId, item) => {
    if (!sessionId || !item.id || !item.text.trim()) return;
    const update = (message: Message, pendingIds: Set<string>): Message => {
      if (!queueIds(message).some((id) => queueSetMatches(pendingIds, id))) return message;
      if (!queueIds(message).some((id) => queueIdMatches(id, item.id))) return message;
      const next: Message = {
        ...message,
        content: item.text,
        ...(item.parts ? { parts: item.parts } : {}),
      };
      if (!item.parts) delete next.parts;
      copyLocalMessageOrigin(next, message);
      inheritMessageIdentity(next, message);
      const key = runtimeKeyOf(message);
      if (key !== null) bindRuntimeKey(next, key);
      return next;
    };
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const pendingIds = s._pendingQueueIds[sessionId] ?? new Set<string>();
      const history = (target.history || []).map((message) => update(message, pendingIds));
      const currentMessages = s.currentSessionId === sessionId
        ? s.currentMessages.map((message) => update(message, pendingIds))
        : s.currentMessages;
      const changed = history.some((message, index) => message !== target.history?.[index])
        || (s.currentSessionId === sessionId
          && currentMessages.some((message, index) => message !== s.currentMessages[index]));
      if (!changed) return s;
      return {
        sessions: s.sessions.map((session) => session.id === sessionId
          ? { ...session, history, lastMessage: item.text.slice(0, 200) }
          : session),
        ...(s.sessionTranscripts[sessionId] ? withTranscript(s, sessionId, {
          ...s.sessionTranscripts[sessionId]!,
          runtime: s.sessionTranscripts[sessionId]!.runtime.map(message => update(message, pendingIds)),
        }) : {}),
        ...(s.currentSessionId === sessionId ? { currentMessages } : {}),
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: (localTouchSeq += 1),
        },
      };
    });
  },

  removeQueuedMessage: (sessionId, queueItemId) => {
    if (!sessionId || !queueItemId) return;
    set((s) => {
      const removePending = (message: Message): Message | null => {
        const pendingIds = s._pendingQueueIds[sessionId] ?? new Set<string>();
        if (!queueIds(message).some((id) => queueSetMatches(pendingIds, id))) return message;
        if (!queueIds(message).some((id) => queueIdMatches(id, queueItemId))) return message;
        return null;
      };
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = (target.history || []).map(removePending).filter(
        (message): message is Message => message !== null,
      );
      const currentMessages = s.currentSessionId === sessionId
        ? s.currentMessages.map(removePending).filter(
          (message): message is Message => message !== null,
        )
        : s.currentMessages;
      const nextPendingIds = new Set(s._pendingQueueIds[sessionId] ?? []);
      for (const pendingId of nextPendingIds) {
        if (queueIdMatches(pendingId, queueItemId)) nextPendingIds.delete(pendingId);
      }
      if (history.length === (target.history || []).length
          && currentMessages.length === s.currentMessages.length
          && nextPendingIds.size === (s._pendingQueueIds[sessionId] ?? new Set()).size) return s;
      return {
        sessions: s.sessions.map((session) => session.id === sessionId
          ? { ...session, history }
          : session),
        ...(s.sessionTranscripts[sessionId] ? withTranscript(s, sessionId, {
          ...s.sessionTranscripts[sessionId]!,
          runtime: s.sessionTranscripts[sessionId]!.runtime.map(removePending).filter(
            (message): message is Message => message !== null),
        }) : {}),
        ...(s.currentSessionId === sessionId ? { currentMessages } : {}),
        _pendingQueueIds: { ...s._pendingQueueIds, [sessionId]: nextPendingIds },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: (localTouchSeq += 1),
        },
      };
    });
  },

  appendDeliveredMessages: (sessionId: string, msgs: Message[]) => {
    if (!msgs.length) return;
    const touchSeq = (localTouchSeq += 1);
    const localMessages = msgs.map((message) => ({
      ...(message.role === 'user' ? withLocalUserIdentity(sessionId, message) : message),
    }));
    set((s) => {
      const pendingIds = s._pendingQueueIds[sessionId] ?? new Set<string>();
      const selected = s.currentSessionId === sessionId;
      const base = sessionOf(s, sessionId)
        ? ensureTranscript(s.sessionTranscripts, sessionOf(s, sessionId)!)
        : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const selectedMessages = selected
        ? [...s.currentMessages]
        : projectTranscript(base, s.liveStreamBuffers[sessionId]);
      const existingIds = new Set(
        selectedMessages.flatMap((message) => explicitMessageIdentity(message)),
      );
      const currentAppend: Message[] = [];
      let currentChanged = false;
      for (const message of localMessages) {
        const ids = explicitMessageIdentity(message);
        const queueMatch = selectedMessages.findIndex((candidate) =>
          queueIds(candidate).some((candidateId) =>
            queueIds(message).some((incomingId) => queueIdMatches(candidateId, incomingId)),
          ),
        );
        if (queueMatch >= 0 && queueIds(selectedMessages[queueMatch]!).some((id) => queueSetMatches(pendingIds, id))) {
          const updated = { ...selectedMessages[queueMatch], ...message };
          inheritMessageIdentity(updated, selectedMessages[queueMatch]!);
          const key = runtimeKeyOf(selectedMessages[queueMatch]!);
          if (key !== null) bindRuntimeKey(updated, key);
          selectedMessages[queueMatch] = updated;
          currentChanged = true;
          continue;
        }
        // A duplicate WS delivery event must not render the same hand-off
        // twice. Distinct queue/native ids are allowed to carry identical
        // text, so sameMessage() is intentionally not used here.
        if (ids.some((id) => existingIds.has(id))) continue;
        currentAppend.push(message);
        ids.forEach((id) => existingIds.add(id));
      }

      const deliveredIds = new Set(s._deliveredQueueIds?.[sessionId] ?? []);
      const previouslyDelivered = new Set(deliveredIds);
      localMessages
        .flatMap((message) => queueIds(message))
        .forEach((id) => deliveredIds.add(canonicalQueueId(id)));
      const sessions = s.sessions.map((session) => {
        if (session.id !== sessionId) return session;
        const history = session.history || [];
        const nextHistory = history.slice();
        const historyIds = new Set(history.flatMap((message) => explicitMessageIdentity(message)));
        const historyAppend: Message[] = [];
        let added = 0;
        for (const message of localMessages) {
          const ids = explicitMessageIdentity(message);
          const queueMatch = nextHistory.findIndex((candidate) =>
            queueIds(candidate).some((candidateId) =>
              queueIds(message).some((incomingId) => queueIdMatches(candidateId, incomingId)),
            ),
          );
          if (queueMatch >= 0 && queueIds(nextHistory[queueMatch]!).some((id) => queueSetMatches(pendingIds, id))) {
            const updated = { ...nextHistory[queueMatch], ...message };
            inheritMessageIdentity(updated, nextHistory[queueMatch]!);
            const key = runtimeKeyOf(nextHistory[queueMatch]!);
            if (key !== null) bindRuntimeKey(updated, key);
            nextHistory[queueMatch] = updated;
            continue;
          }
          if (queueIds(message).some((id) => queueSetMatches(previouslyDelivered, id))) continue;
          if (ids.some((id) => historyIds.has(id))) continue;
          rememberLocalMessageOrigin(
            message,
            (session.historyTotal ?? history.length) + added,
          );
          historyAppend.push(message);
          ids.forEach((id) => historyIds.add(id));
          added += 1;
        }
        // Keep the local projection even for summary=1 cards.  The rows carry
        // transient queue identity and are reconciled against the next
        // canonical history response; leaving the projection empty is what
        // made A→B→A erase an otherwise successful hand-off.
        nextHistory.push(...historyAppend);
        const last = localMessages[localMessages.length - 1];
        return {
          ...session,
          history: nextHistory,
          historyTotal: Math.max(
            (session.historyTotal ?? history.length) + added,
            nextHistory.length,
          ),
          lastMessage: last?.content.slice(0, 200) ?? session.lastMessage,
        };
      });

      const deliveredRuntime = base.runtime.map(row => {
        const incoming = localMessages.find(message => queueIds(message).some(id =>
          queueIds(row).some(existing => queueIdMatches(existing, id))));
        if (!incoming) return row;
        const updated = { ...row, ...incoming };
        inheritMessageIdentity(updated, row);
        const key = runtimeKeyOf(row);
        if (key !== null) bindRuntimeKey(updated, key);
        return updated;
      });
      const runtimeRows = currentAppend.length > 0
        ? currentAppend.map((row, index) =>
            bindRuntimeKey(row, `delivered:${touchSeq}:${index}`),
          )
        : [];
      return {
        sessions,
        _pendingQueueIds: {
          ...s._pendingQueueIds,
          [sessionId]: new Set(
            [...pendingIds].filter((pendingId) => !queueSetMatches(deliveredIds, pendingId)),
          ),
        },
        _deliveredQueueIds: {
          ...(s._deliveredQueueIds ?? {}),
          [sessionId]: deliveredIds,
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq,
        },
        ...(selected && (currentAppend.length || currentChanged)
          ? { currentMessages: [...selectedMessages, ...currentAppend] }
          : {}),
        ...withTranscript(s, sessionId, { ...base, runtime: [...deliveredRuntime, ...runtimeRows] }),
      };
    });
  },

  patchSessionSettings: async (sessionId, patch, persist) => {
    const current = get().sessions.find((session) => session.id === sessionId);
    if (!current) throw new Error('Session not found');
    const previous = get().sessionSettingMutations[sessionId];
    const sequence = (previous?.sequence ?? 0) + 1;
    const rollback = pickSessionSettings(current);
    const authoritative = previous?.authoritative ?? rollback;
    const touchSeq = (settingsTouchSeq += 1);

    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === sessionId ? mergeSessionSettingPatch(session, patch) : session,
      ),
      sessionSettingMutations: {
        ...s.sessionSettingMutations,
        [sessionId]: {
          sequence,
          pending: true,
          patch,
          rollback,
          authoritative,
          error: null,
        },
      },
      _sessionSettingsTouchedSeq: {
        ...s._sessionSettingsTouchedSeq,
        [sessionId]: touchSeq,
      },
    }));

    try {
      const response = await persist(sessionId, patch as SettingsBody);
      const responsePatch =
        response && 'id' in response && response.id === sessionId
          ? pickSessionSettings(response)
          : {};
      const latest = get().sessionSettingMutations[sessionId];
      if (!latest || latest.sequence !== sequence) {
        // An A response may improve the rollback baseline, but it must never
        // replace the newer optimistic B value rendered by the store.
        if (latest?.pending && Object.keys(responsePatch).length > 0) {
          set((s) => ({
            sessionSettingMutations: {
              ...s.sessionSettingMutations,
              [sessionId]: { ...latest, authoritative: responsePatch },
            },
          }));
        }
        return { response, applied: false, stale: true };
      }

      const settled = { ...patch, ...responsePatch };
      const settledTouchSeq = (settingsTouchSeq += 1);
      set((s) => ({
        sessions: s.sessions.map((session) =>
          session.id === sessionId ? mergeSessionSettingPatch(session, settled) : session,
        ),
        sessionSettingMutations: {
          ...s.sessionSettingMutations,
          [sessionId]: {
            ...latest,
            pending: false,
            patch: undefined,
            authoritative: settled,
            error: null,
          },
        },
        _sessionSettingsTouchedSeq: {
          ...s._sessionSettingsTouchedSeq,
          [sessionId]: settledTouchSeq,
        },
      }));
      return { response, applied: true, stale: false };
    } catch (error) {
      const message = errorMessage(error);
      const latest = get().sessionSettingMutations[sessionId];
      if (!latest || latest.sequence !== sequence) {
        // An old failure is obsolete too: it must not toast or roll back B.
        return {
          response: { error: message },
          applied: false,
          stale: true,
        };
      }
      const rollbackTouchSeq = (settingsTouchSeq += 1);
      set((s) => ({
        sessions: s.sessions.map((session) =>
          session.id === sessionId
            ? mergeSessionSettingPatch(session, latest.authoritative)
            : session,
        ),
        sessionSettingMutations: {
          ...s.sessionSettingMutations,
          [sessionId]: {
            ...latest,
            pending: false,
            patch: undefined,
            error: message,
          },
        },
        _sessionSettingsTouchedSeq: {
          ...s._sessionSettingsTouchedSeq,
          [sessionId]: rollbackTouchSeq,
        },
      }));
      throw error;
    }
  },

  updateSession: (id: string, data: Partial<Session>, preserveOnSnapshot = false) => {
    const touchSeq = (wsTouchSeq += 1);
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id
          ? preserveNewerSummary(
              session,
              mergeSessionSettingPatch(
                { ...session, ...data },
                s.sessionSettingMutations[id]?.pending
                  ? s.sessionSettingMutations[id].patch
                  : undefined,
              ),
            )
          : session,
      ),
      _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [id]: touchSeq },
      ...(preserveOnSnapshot
        ? {
            _sessionEventPatches: {
              ...s._sessionEventPatches,
              [id]: { ...(s._sessionEventPatches?.[id] ?? {}), ...data },
            },
          }
        : {}),
    }));
  },

  applyResultToSession: (id, e) => {
    const status = e.status === 'error'
      ? 'error'
      : e.status === 'cancelled' || e.cancelled
        ? 'cancelled'
        : 'done';
    const result = e.result;
    const touchSeq = (localTouchSeq += 1);
    set((s) => {
      const sessions = s.sessions.map((x) => {
        if (x.id !== id) return x;
        const history = (x.history || []).slice();
        let historyTotal = x.historyTotal ?? history.length;
        // 镜像后端 _read_stdout 的去重：结果文本若已是最后一条 assistant 则
        // 不重复追加（防止流式末尾块 + result 文本双份）。
        if (typeof result === 'string' && result.trim()) {
          const last = history[history.length - 1];
          if (!(last && last.role === 'assistant' && last.content === result)) {
            history.push({ role: 'assistant', content: result });
            historyTotal += 1;
          }
        }
        // 防内存膨胀：就地追加可能脱离服务端 last-50，这里封顶；全量兜底
        // loadSessions 会把 history 纠正为服务端最新 tail。
        const bounded = history.length > 500 ? history.slice(-500) : history;
        return {
          ...x,
          history: bounded,
          historyTotal,
          // Card preview is summary-driven (lastMessage); keep it in sync with
          // the in-place append so the sidebar updates immediately.
          lastMessage:
            typeof result === 'string' && result.trim()
              ? result.slice(0, 200)
              : x.lastMessage,
          lastResult: {
            status,
            result: result ?? '',
            timestamp: new Date().toISOString(),
          },
        };
      });
      return {
        sessions,
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [id]: touchSeq,
        },
      };
    });
  },

  toggleMultiSelect: (initId?: string) => {
    set((s) => {
      const isActive = !s.multiSelectMode;
      return {
        multiSelectMode: isActive,
        selectedIds: isActive && initId ? new Set([initId]) : new Set<string>(),
      };
    });
  },

  toggleSelection: (id: string) => {
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return { selectedIds: next };
    });
  },

  exitMultiSelect: () => {
    set({ multiSelectMode: false, selectedIds: new Set() });
  },

  setRendering: (v: boolean) => {
    set({ rendering: v });
  },
}));

// Custom hook: derive currentSession from sessions + currentSessionId.
// We cannot use a getter (killed by Zustand's Object.assign) nor a
// subscribe+setState pattern (triggers React #185 nested-update guard).
// Instead, each consumer calls this hook which uses a pure Zustand selector.
export function useCurrentSession() {
  return useSessionStore((s) =>
    s.currentSessionId
      ? s.sessions.find((session) => session.id === s.currentSessionId) ?? null
      : null,
  );
}

// ── E2E inspection seam ─────────────────────────────────────────────────────
// The read-only browser verification
// (audit/run-e2e-consistency.mjs) must export the *store* order alongside the
// server canonical history and the DOM order. The store is intentionally not a
// global, so it is exposed only when the page is opened with an explicit
// `?panE2E=1` query parameter. Normal app URLs never set it and the bundle
// behaves exactly as before.
if (typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).has('panE2E')) {
  (window as unknown as Record<string, unknown>).__panSessionStore = useSessionStore;
}
