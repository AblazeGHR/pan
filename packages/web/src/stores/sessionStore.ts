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
import { cloneMessageWithIdentity, inheritMessageIdentity } from '@/utils/messageIdentity';

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
  sessionUnread: Record<string, Set<string>>;
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
  /** Monotonic per-session selection request sequence; protects A→B→A. */
  _selectionSeq: Record<string, number>;
  _sessionLocalTouchedSeq: Record<string, number>;
  _sessionSettingsTouchedSeq: Record<string, number>;
  _sessionEventPatches: Record<string, Partial<Session>>;
  _deliveredQueueIds: Record<string, Set<string>>;
  liveStreamBuffers: Record<string, LiveStreamBuffer>;
  terminalWatermarks: Record<string, TerminalWatermark>;
  /** Latest optimistic settings mutation per Session. */
  sessionSettingMutations: Record<string, SessionSettingMutationInternal>;

  // Actions
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  refreshCurrentSessionHistory: () => Promise<void>;
  loadOlderMessages: () => Promise<void>;
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
    },
  ) => Promise<void>;
  removeSession: (id: string) => Promise<void>;
  removeSessions: (ids: string[], cascadeIds?: string[]) => Promise<void>;
  batchRemoveSessions: () => Promise<void>;
  rename: (id: string, name: string) => Promise<void>;
  branch: (id: string, name: string) => Promise<void>;
  reimport: (id: string) => Promise<void>;
  setInputDraft: (id: string, draft: string) => void;
  addMessage: (msg: Message) => void;
  appendMessages: (msgs: Message[]) => void;
  getLiveStreamMessages: (sessionId: string) => Message[];
  applyLiveStream: (sessionId: string, messages: Message[], meta: LiveStreamMeta) => boolean;
  reconcileWorkerResult: (
    sessionId: string,
    event: { status?: string; cancelled?: boolean; result?: string },
    meta: LiveStreamMeta,
  ) => boolean;
  applyWorkerStatus: (
    sessionId: string,
    status: string | null,
    meta: LiveStreamMeta,
    terminal?: boolean,
  ) => boolean;
  clearLiveStream: (sessionId: string) => void;
  /** Show a durably queued user message before local CLI hand-off. */
  appendQueuedMessage: (sessionId: string, item: { id: string; text: string; parts?: Message['parts'] }) => void;
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
  getUnread: () => Set<string>;
  markUnread: (sessionId: string, content: string) => void;
  clearUnread: () => void;
}

// Stable empty Set returned by getUnread() when there are no unread items.
// MUST be a shared reference — returning `new Set()` each call would make
// `useSessionStore((s) => s.getUnread())` an unstable selector, which
// (via useSyncExternalStore) triggers infinite re-renders → React #185.
const EMPTY_UNREAD_SET: Set<string> = new Set();

/** Monotonic sequence stamped onto `_sessionWsTouchedSeq` by updateSession().
 *  Kept outside the store because only the relative order *per session* matters
 *  (see loadSessions): a value recorded during a fetch is strictly greater than
 *  the one captured when that fetch was issued. */
let wsTouchSeq = 0;
let localTouchSeq = 0;
let settingsTouchSeq = 0;

function sameMessage(a: Message, b: Message): boolean {
  return a.role === b.role && a.content === b.content
    && JSON.stringify(a.parts ?? null) === JSON.stringify(b.parts ?? null);
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
  turnId?: string;
  itemId?: string;
  revision: number;
  messages: Message[];
}

export interface LiveStreamMeta {
  workerId?: string;
  generation?: number;
  taskSeq?: number;
  turnId?: string;
  itemId?: string;
}

interface TerminalWatermark extends LiveStreamMeta {
  status: string;
  revision: number;
}

function nonSystemMessages(messages: Message[]): Message[] {
  return messages.filter((message) => message.role !== 'system');
}

function messagePrefixCompatible(local: Message, server: Message): boolean {
  if (local.role !== server.role) return false;
  if (local.nativeItemId && server.nativeItemId && local.nativeItemId !== server.nativeItemId) {
    return false;
  }
  return local.content === server.content
    || (local.role === 'assistant'
      // Only local-longer means the HTTP snapshot is stale. A server final
      // answer that is longer than the local delta must be allowed to replace
      // it rather than being mistaken for a prefix and retained forever.
      && local.content.startsWith(server.content));
}

function mergeServerHistoryWithLive(
  serverHistory: Message[],
  liveMessages: Message[],
): Message[] {
  // Preserve object identity for unchanged history and live rows. TanStack
  // Virtual uses a WeakMap-backed key, so cloning every row on every delta
  // remounts the growing Markdown block and makes the viewport flash.
  const result = [...serverHistory];
  const claimedIndexes = new Set<number>();
  for (const live of liveMessages) {
    const identityIndex = live.nativeItemId
      ? result.findIndex((message, index) =>
          !claimedIndexes.has(index)
          && message.nativeItemId === live.nativeItemId
          && message.role === live.role)
      : -1;
    const contentIndex = result.findIndex((message, index) =>
      !claimedIndexes.has(index)
      && message.role === live.role
      && message.content === live.content,
    );
    const compatibleIndex = identityIndex >= 0
      ? identityIndex
      : live.role === 'assistant'
        ? result.findIndex((message, index) => !claimedIndexes.has(index)
          && message.role === 'assistant'
          && (message.content.startsWith(live.content) || live.content.startsWith(message.content)))
        : contentIndex;
    if (compatibleIndex < 0) {
      result.push(live);
      claimedIndexes.add(result.length - 1);
      continue;
    }
    claimedIndexes.add(compatibleIndex);
    const existing = result[compatibleIndex]!;
    // Prefer the longer/current live assistant, but never replace a server
    // final answer with a shorter stale delta.
    if (live.role !== 'assistant' || live.content.length >= existing.content.length) {
      const merged = { ...existing, ...live };
      inheritMessageIdentity(merged, live);
      result[compatibleIndex] = merged;
    }
  }
  return result;
}

function sameWorkerGeneration(a: LiveStreamMeta, b: LiveStreamMeta): boolean {
  return Boolean(a.workerId && b.workerId && a.workerId === b.workerId
    && (a.generation === undefined || b.generation === undefined || a.generation === b.generation));
}

function isOlderMeta(incoming: LiveStreamMeta, known: LiveStreamMeta): boolean {
  if (incoming.generation !== undefined && known.generation !== undefined) {
    if (incoming.generation < known.generation) return true;
    if (incoming.generation > known.generation) return false;
  }
  if (incoming.workerId && known.workerId && incoming.workerId !== known.workerId) {
    return incoming.generation === undefined || known.generation === undefined
      || incoming.generation <= known.generation;
  }
  if (incoming.taskSeq !== undefined && known.taskSeq !== undefined) {
    return incoming.taskSeq < known.taskSeq;
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
  if (incoming.taskSeq !== undefined && terminal.taskSeq !== undefined
      && incoming.taskSeq <= terminal.taskSeq
      && (incoming.generation === undefined || terminal.generation === undefined
        || incoming.generation === terminal.generation)) {
    return true;
  }
  return false;
}

function mergeFinalMessages(local: Message[], canonical: Message[]): Message[] {
  const result = canonical.map(cloneMessageWithIdentity);
  for (const message of local) {
    if (message.role === 'system') continue;
    if (result.some((candidate) => sameMessage(candidate, message))) continue;
    // Keep a locally queued/agent user turn if the authoritative history was
    // fetched before that hand-off became durable. Never retain an old live
    // assistant here: canonical already contains the final replacement.
    if (message.role === 'user') result.push(cloneMessageWithIdentity(message));
  }
  for (const message of local.filter((candidate) => candidate.role === 'system')) {
    if (result.some((candidate) => sameMessage(candidate, message))) continue;
    result.push(cloneMessageWithIdentity(message));
  }
  return result;
}

/** Keep local synthetic completion rows when a later server snapshot adds a
 * real user turn but does not persist the browser-only [DONE] row. */
function mergeServerHistoryPreservingLocal(local: Message[], server: Message[]): Message[] {
  // A selected session may have a locally queued user message or a live
  // assistant suffix that is not in the lagging server snapshot yet. Keep the
  // whole local view when the server is only a prefix; system rows such as
  // [DONE] are ignored by the prefix check and remain in that local view.
  if (isServerHistoryPrefix(local, server)) return local;
  const result = [...server];
  const localSystem = local.filter((message) => message.role === 'system');
  for (const message of localSystem) {
    if (result.some((candidate) => sameMessage(candidate, message))) continue;
    const ordinal = local.slice(0, local.indexOf(message))
      .filter((candidate) => candidate.role !== 'system').length;
    let seen = 0;
    let insertAt = result.length;
    for (let index = 0; index < result.length; index++) {
      if (result[index]!.role !== 'system' && seen >= ordinal) {
        insertAt = index;
        break;
      }
      if (result[index]!.role !== 'system') seen++;
    }
    result.splice(insertAt, 0, message);
  }
  return result;
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

/** True when the server-reported history is a prefix of the locally-rendered
 *  history (element-wise by role+content). A stale snapshot during streaming
 *  is exactly this — the backend persists each streamed block slightly after
 *  broadcasting it, so its history lags what we already show locally. Blindly
 *  overwriting `currentMessages` with such a prefix would wipe the in-flight
 *  assistant reply. */
function isServerHistoryPrefix(
  localHistory: Message[],
  serverHistory: Message[],
): boolean {
  const local = nonSystemMessages(localHistory);
  const server = nonSystemMessages(serverHistory);
  if (server.length > local.length) return false;
  for (let i = 0; i < server.length; i++) {
    const s = server[i];
    const c = local[i];
    if (!s || !c || !sameMessage(s, c)) {
      if (!s || !c || !messagePrefixCompatible(c, s)) return false;
    }
  }
  return true;
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
  sessionUnread: {},
  rendering: false,
  _loadSeq: 0,
  _sessionWsTouchedSeq: {},
  _historyRefreshSeq: {},
  _selectionSeq: {},
  _sessionLocalTouchedSeq: {},
  _sessionSettingsTouchedSeq: {},
  _sessionEventPatches: {},
  _deliveredQueueIds: {},
  liveStreamBuffers: {},
  terminalWatermarks: {},
  sessionSettingMutations: {},

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
      const { currentSessionId, currentMessages } = get();

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
          if (preserveLocalWorker) {
            next = {
              ...sess,
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

      // Restore current session messages after refresh — but NEVER clobber the
      // live-rendered messages with a stale snapshot. While streaming, the
      // server history is a prefix of what we already show locally (the
      // backend persists each block slightly after broadcasting it), so a
      // blind overwrite would wipe the in-flight assistant reply (bug: needs
      // a manual refresh to reappear).
      if (currentSessionId) {
        const found = sessions.find((s) => s.id === currentSessionId);
        if (found) {
          const serverHistory = mergeServerHistoryWithLive(
            found.history || [],
            get().liveStreamBuffers[currentSessionId]?.messages || [],
          );
          const keepLocal = isServerHistoryPrefix(
            currentMessages,
            serverHistory,
          );
          set({
            currentMessages: keepLocal
              ? currentMessages
              : mergeServerHistoryPreservingLocal(currentMessages, serverHistory),
            hasMoreMessages: !!found.historyTruncated,
            historyLoadEnd: Math.max(
              0,
              (found.historyTotal ?? serverHistory.length) -
                serverHistory.length,
            ),
          });
        } else {
          set({
            currentSessionId: null,
            currentMessages: [],
            hasMoreMessages: false,
            initialLoading: false,
          });
        }
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

    // Single set — one render for session switch.
    // NOTE: do NOT set historyLoading=true here. Previously this blocked the
    // scroll-handler lazy-load race, but it also made the subsequent
    // loadOlderMessages() call a no-op (its own guard sees historyLoading=true
    // and returns), leaving the "Loading older messages..." indicator stuck
    // forever and breaking scroll-up lazy loading. The scroll handler's 150ms
    // debounce plus scrollToBottom() in ChatMessages is enough to prevent
    // spurious loads on session switch.
    set({
      currentSessionId: id,
      _selectionSeq: { ...get()._selectionSeq, [id]: selectionSeq },
      currentMessages: mergeServerHistoryWithLive(
        session.history || [],
        get().liveStreamBuffers[id]?.messages || [],
      ),
      hasMoreMessages: needsOlder,
      historyLoading: false,
      historyLoadEnd: Math.max(
        0,
        (session.historyTotal ?? loaded) - loaded,
      ),
      // summary=1 快照不带每 session 的 history → 快照为空时，在下方 fresh
      // history 拉取期间 chat 面板应显示转圈 loading，而不是「无消息」空态。
      initialLoading: loaded === 0,
    });

    // 进入 session 后立即拉服务端最新历史替换快照。React 的快照只靠防抖
    // loadSessions 刷新，可能滞后或（在 _loadSeq 超驰/事件丢失时）过期——
    // 事件刷新快照可能先于 history 持久化，不能覆盖正在流式显示的回复。
    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        id,
        0,
        50,
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
      const serverHistory = data.history || [];
      const historyWithLive = mergeServerHistoryWithLive(
        serverHistory,
        get().liveStreamBuffers[id]?.messages || [],
      );
      // 流式窗口防护：若服务端历史只是本地已渲染消息的前缀（部分块尚未
      // 落盘），保留本地，避免把正在流式的回复抹掉。
      const keepLocal = isServerHistoryPrefix(
        get().currentMessages,
        historyWithLive,
      );
      // Keep the card preview in sync with the freshly-fetched history tail.
      const lastServerMsg = serverHistory[serverHistory.length - 1];
      const lastMessage = lastServerMsg
        ? String(lastServerMsg.content).slice(0, 200)
        : '';
      set((s) => ({
        sessions: s.sessions.map((x) =>
          x.id === id
            ? {
                ...x,
                history: serverHistory,
                historyTruncated: data.hasMore,
                historyTotal: data.total,
                lastMessage,
              }
            : x,
        ),
        currentMessages: keepLocal
          ? s.currentMessages
          : mergeServerHistoryPreservingLocal(s.currentMessages, historyWithLive),
        hasMoreMessages: data.hasMore,
        historyLoadEnd: data.start,
        initialLoading: false,
      }));
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
      const data = await fetchSessionHistory(sid, 0, 50);
      const current = get();
      if (
        current.currentSessionId !== sid ||
        current._historyRefreshSeq[sid] !== requestSeq
      ) return;
      const serverHistory = data.history || [];
      const historyWithLive = mergeServerHistoryWithLive(
        serverHistory,
        current.liveStreamBuffers[sid]?.messages || [],
      );
      const keepLocal = isServerHistoryPrefix(current.currentMessages, historyWithLive);
      const lastServerMsg = serverHistory[serverHistory.length - 1];
      set((s) => ({
        sessions: s.sessions.map((session) => session.id === sid
          ? {
              ...session,
              history: serverHistory,
              historyTruncated: data.hasMore,
              historyTotal: data.total,
              lastMessage: lastServerMsg ? String(lastServerMsg.content).slice(0, 200) : '',
            }
          : session),
        currentMessages: keepLocal
          ? s.currentMessages
          : mergeServerHistoryPreservingLocal(s.currentMessages, historyWithLive),
        hasMoreMessages: data.hasMore,
        historyLoadEnd: data.start,
        initialLoading: false,
      }));
    } catch {
      // A focus recovery is best effort; retain the stream and local snapshot.
    }
  },

  loadOlderMessages: async () => {
    const { currentSessionId, historyLoading, historyLoadEnd } = get();
    if (
      historyLoading ||
      historyLoadEnd <= 0 ||
      !currentSessionId
    )
      return;

    set({ historyLoading: true });
    const sid = currentSessionId;

    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        sid,
        historyLoadEnd,
        50,
      );
      if (get().currentSessionId !== sid) return;

      const msgs = data.history || data.history || [];
      if (msgs.length === 0) {
        set({ historyLoading: false });
        return;
      }

      set((s) => {
        const merged = [...msgs, ...s.currentMessages];
        // Update session in the list
        const sessions = s.sessions.map((session) => {
          if (session.id === sid) {
            return {
              ...session,
              history: merged,
              historyTruncated: data.start > 0,
            };
          }
          return session;
        });
        return {
          sessions,
          currentMessages: merged,
          hasMoreMessages: data.start > 0,
          historyLoadEnd: data.start,
          historyLoading: false,
        };
      });
    } catch {
      set({ historyLoading: false });
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
    }));
  },

  addMessage: (msg: Message) => {
    set((s) => ({
      currentMessages: [...s.currentMessages, msg],
    }));
  },

  appendMessages: (msgs: Message[]) => {
    set((s) => ({
      currentMessages: [...s.currentMessages, ...msgs],
    }));
  },

  getLiveStreamMessages: (sessionId) =>
    (get().liveStreamBuffers[sessionId]?.messages || []).slice(),

  applyLiveStream: (sessionId, messages, meta) => {
    if (!messages.length) return false;
    let accepted = false;
    set((s) => {
      const terminal = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, terminal, 'stream')) return s;
      const previous = s.liveStreamBuffers[sessionId];
      if (previous && isOlderMeta(meta, previous)) return s;
      const revision = Math.max(
        previous?.revision ?? 0,
        terminal?.revision ?? 0,
      ) + 1;
      const buffer: LiveStreamBuffer = {
        ...meta,
        revision,
        messages: messages.slice(),
      };
      accepted = true;
      const session = s.sessions.find((candidate) => candidate.id === sessionId);
      const currentBase = s.currentSessionId === sessionId
        ? nonSystemMessages(s.currentMessages).filter((message) =>
          !(previous?.messages || []).some((live) =>
            (live.nativeItemId && message.nativeItemId === live.nativeItemId)
            || sameMessage(live, message)
            || (live.role === 'assistant' && message.role === 'assistant'
              && (live.content.startsWith(message.content) || message.content.startsWith(live.content)))),
        )
        : [];
      const historyWithLive = s.currentSessionId === sessionId
        ? mergeServerHistoryWithLive(currentBase, buffer.messages)
        : session
          ? mergeServerHistoryWithLive(session.history || [], buffer.messages)
          : buffer.messages;
      return {
        liveStreamBuffers: { ...s.liveStreamBuffers, [sessionId]: buffer },
        ...(s.currentSessionId === sessionId && session
          ? { currentMessages: isServerHistoryPrefix(s.currentMessages, historyWithLive)
            ? s.currentMessages
            : mergeServerHistoryPreservingLocal(s.currentMessages, historyWithLive) }
          : {}),
      };
    });
    return accepted;
  },

  reconcileWorkerResult: (sessionId, event, meta) => {
    let accepted = false;
    set((s) => {
      const terminal = s.terminalWatermarks[sessionId];
      if (terminal && isOlderMeta(meta, terminal)) return s;
      const previousBuffer = s.liveStreamBuffers[sessionId];
      if (previousBuffer && isOlderMeta(meta, previousBuffer)) return s;
      const result = typeof event.result === 'string' ? event.result : '';
      const status = event.status === 'error'
        ? 'error'
        : event.status === 'cancelled' || event.cancelled
          ? 'cancelled'
          : 'done';
      const liveMessages = previousBuffer?.messages || [];
      const liveAssistant = [...liveMessages].reverse().find((message) => message.role === 'assistant');
      const finalLiveMessages = liveMessages.map((message) => {
        const finalMessage = result.trim() && message === liveAssistant
          ? { ...message, content: result }
          : { ...message };
        inheritMessageIdentity(finalMessage, message);
        return finalMessage;
      });
      if (result.trim() && !liveAssistant) {
        finalLiveMessages.push({
          role: 'assistant',
          content: result,
          ...(meta.turnId ? { nativeItemId: `turn:${meta.turnId}` } : {}),
        });
      }
      const sessions = s.sessions.map((session) => {
        if (session.id !== sessionId) return session;
        const history = (session.history || []).slice();
        let historyTotal = session.historyTotal ?? history.length;
        let replaced = false;
        if (result.trim()) {
          for (let index = history.length - 1; index >= 0; index--) {
            const candidate = history[index];
            if (!candidate || candidate.role !== 'assistant') continue;
            const liveMatches = liveAssistant
              && (candidate.content === liveAssistant.content
                || candidate.content.startsWith(liveAssistant.content)
                || liveAssistant.content.startsWith(candidate.content));
            const exact = candidate.content === result;
            if (exact || liveMatches) {
              history[index] = {
                ...candidate,
                content: result,
                ...(liveAssistant?.nativeItemId
                  ? { nativeItemId: liveAssistant.nativeItemId }
                  : {}),
              };
              replaced = true;
              break;
            }
          }
          if (!replaced) {
            history.push({
              role: 'assistant',
              content: result,
              ...(liveAssistant?.nativeItemId
                ? { nativeItemId: liveAssistant.nativeItemId }
                : {}),
            });
            historyTotal += 1;
          }
        }
        return {
          ...session,
          history: history.length > 500 ? history.slice(-500) : history,
          historyTotal,
          lastMessage: result.trim() ? result.slice(0, 200) : session.lastMessage,
          lastResult: {
            status,
            result,
            timestamp: new Date().toISOString(),
            ...(meta.taskSeq !== undefined ? { taskSeq: meta.taskSeq } : {}),
          },
        };
      });
      const session = sessions.find((candidate) => candidate.id === sessionId);
      const canonical = session
        ? mergeServerHistoryWithLive(session.history || [], finalLiveMessages)
        : finalLiveMessages;
      const revision = Math.max(
        previousBuffer?.revision ?? 0,
        terminal?.revision ?? 0,
      ) + 1;
      accepted = true;
      return {
        sessions,
        liveStreamBuffers: Object.fromEntries(
          Object.entries(s.liveStreamBuffers).filter(([id]) => id !== sessionId),
        ),
        terminalWatermarks: {
          ...s.terminalWatermarks,
          [sessionId]: { ...meta, status, revision },
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: (localTouchSeq += 1),
        },
        ...(s.currentSessionId === sessionId
          ? { currentMessages: mergeFinalMessages(s.currentMessages, canonical) }
          : {}),
      };
    });
    return accepted;
  },

  applyWorkerStatus: (sessionId, status, meta, terminal = false) => {
    if (!sessionId) return false;
    let accepted = false;
    set((s) => {
      const watermark = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, watermark, status || 'idle')) return s;
      const previous = s.liveStreamBuffers[sessionId];
      if (previous && status === 'running' && meta.taskSeq !== undefined
          && previous.taskSeq !== undefined && meta.taskSeq > previous.taskSeq) {
        // A new task on the same worker starts a fresh transient turn.
        const nextBuffers = { ...s.liveStreamBuffers };
        delete nextBuffers[sessionId];
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
    set((s) => {
      if (s.currentSessionId !== sessionId) return {};
      if (s.currentMessages.some((message) => message.queueItemIds?.includes(item.id))) {
        return {};
      }
      return {
        currentMessages: [
          ...s.currentMessages,
          {
            role: 'user',
            content: item.text,
            ...(item.parts ? { parts: item.parts } : {}),
            queueItemIds: [item.id],
          },
        ],
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq,
        },
      };
    });
  },

  appendDeliveredMessages: (sessionId: string, msgs: Message[]) => {
    if (!msgs.length) return;
    const touchSeq = (localTouchSeq += 1);
    set((s) => {
      const queueIds = (message: Message): string[] => (
        Array.isArray(message.queueItemIds)
          ? message.queueItemIds.filter((id): id is string => typeof id === 'string' && id.length > 0)
          : []
      );
      const existingIds = new Set(
        s.currentMessages.flatMap((message) => queueIds(message)),
      );
      const currentAppend: Message[] = [];
      for (const message of msgs) {
        const ids = queueIds(message);
        // A duplicate WS delivery event must not render the same hand-off
        // twice. Distinct queue ids are allowed to carry identical text.
        if (ids.length && ids.some((id) => existingIds.has(id))) continue;
        currentAppend.push(message);
        ids.forEach((id) => existingIds.add(id));
      }

      const deliveredIds = new Set(s._deliveredQueueIds?.[sessionId] ?? []);
      const sessions = s.sessions.map((session) => {
        if (session.id !== sessionId) return session;
        const history = session.history || [];
        const historyIds = new Set(
          history.flatMap((message) => queueIds(message)),
        );
        let newlyDelivered = 0;
        // A fresh history response intentionally strips transient queue ids.
        // The current chat still has them, so use that view as an additional
        // dedupe source when a duplicate notification arrives after refresh.
        const historyAppend = msgs.filter((message) => {
          const ids = queueIds(message);
          if (ids.length && ids.some((id) => deliveredIds.has(id))) return false;
          if (ids.length && ids.some((id) => historyIds.has(id))) return false;
          if (history.some((candidate) => sameMessage(candidate, message))) return false;
          ids.forEach((id) => historyIds.add(id));
          ids.forEach((id) => deliveredIds.add(id));
          newlyDelivered += 1;
          return true;
        });
        // summary=1 intentionally has no history. Keep it empty and update
        // only its card counters; a later selectSession fetches the canonical
        // history instead of turning one delivered event into a fake snapshot.
        const hasLoadedHistory = history.length > 0 || (session.historyTotal ?? 0) === 0;
        const nextHistory = hasLoadedHistory
          ? [...history, ...historyAppend]
          : history;
        const added = hasLoadedHistory
          ? historyAppend.length
          : newlyDelivered;
        const last = msgs[msgs.length - 1];
        return {
          ...session,
          history: nextHistory,
          historyTotal: (session.historyTotal ?? history.length) + added,
          lastMessage: last?.content.slice(0, 200) ?? session.lastMessage,
        };
      });

      return {
        sessions,
        _deliveredQueueIds: {
          ...(s._deliveredQueueIds ?? {}),
          [sessionId]: deliveredIds,
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq,
        },
        ...(s.currentSessionId === sessionId && currentAppend.length
          ? { currentMessages: [...s.currentMessages, ...currentAppend] }
          : {}),
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

  getUnread: () => {
    const { currentSessionId, sessionUnread } = get();
    if (!currentSessionId) return EMPTY_UNREAD_SET;
    return sessionUnread[currentSessionId] ?? EMPTY_UNREAD_SET;
  },

  markUnread: (sessionId: string, content: string) => {
    if (!sessionId) return;
    set((s) => {
      const previous = s.sessionUnread[sessionId] ?? EMPTY_UNREAD_SET;
      if (previous.has(content)) return {};
      const perSession = new Set(previous);
      perSession.add(content);
      return {
        sessionUnread: {
          ...s.sessionUnread,
          [sessionId]: perSession,
        },
      };
    });
  },

  clearUnread: () => {
    const { currentSessionId } = get();
    if (!currentSessionId) return;
    set((s) => {
      const copy = { ...s.sessionUnread };
      copy[currentSessionId] = new Set();
      return { sessionUnread: copy };
    });
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
