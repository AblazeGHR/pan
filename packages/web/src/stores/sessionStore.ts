import { create } from 'zustand';
import type {
  Session,
  Message,
  ApiSessionHistoryResponse,
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
  // Global monotonic touch counter; per-session snapshots of it let
  // loadSessions() skip reverting workerStatus/workerId that WS events
  // freshened while its own HTTP request was in flight.
  _touchSeq: number;
  // sessionId → touchSeq of the last workerStatus/workerId update (WS events).
  _sessionWsTouchedSeq: Record<string, number>;

  // Actions
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
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
  batchRemoveSessions: () => Promise<void>;
  rename: (id: string, name: string) => Promise<void>;
  branch: (id: string, name: string) => Promise<void>;
  reimport: (id: string) => Promise<void>;
  setInputDraft: (id: string, draft: string) => void;
  addMessage: (msg: Message) => void;
  appendMessages: (msgs: Message[]) => void;
  updateSession: (id: string, data: Partial<Session>) => void;
  /** 就地更新某 session 卡片：追加结果文本到 history + lastResult + historyTotal，
   *  不等 300ms 防抖全量兜底即可让「最后消息 summary」立即最新（镜像 vanilla
   *  `_applyWorkerUpdate` 的就地更新路径）。 */
  applyResultToSession: (
    id: string,
    e: { status?: string; result?: string },
  ) => void;
  toggleMultiSelect: (id?: string) => void;
  toggleSelection: (id: string) => void;
  exitMultiSelect: () => void;
  setRendering: (v: boolean) => void;
  getUnread: () => Set<string>;
  markUnread: (content: string) => void;
  clearUnread: () => void;
}

// Stable empty Set returned by getUnread() when there are no unread items.
// MUST be a shared reference — returning `new Set()` each call would make
// `useSessionStore((s) => s.getUnread())` an unstable selector, which
// (via useSyncExternalStore) triggers infinite re-renders → React #185.
const EMPTY_UNREAD_SET: Set<string> = new Set();

/** True when the server-reported history is a prefix of the locally-rendered
 *  history (element-wise by role+content). A stale snapshot during streaming
 *  is exactly this — the backend persists each streamed block slightly after
 *  broadcasting it, so its history lags what we already show locally. Blindly
 *  overwriting `currentMessages` with such a prefix would wipe the in-flight
 *  assistant reply. Mirrors the legacy frontend's `_isServerHistoryPrefix`. */
function isServerHistoryPrefix(
  localHistory: Message[],
  serverHistory: Message[],
): boolean {
  if (serverHistory.length > localHistory.length) return false;
  for (let i = 0; i < serverHistory.length; i++) {
    const s = serverHistory[i];
    const c = localHistory[i];
    if (!s || !c || s.role !== c.role || s.content !== c.content) return false;
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
  _touchSeq: 0,
  _sessionWsTouchedSeq: {},

  loadSessions: async () => {
    // Reserve this refresh's sequence + snapshot the touch counter so a stale
    // in-flight response can neither overwrite a newer refresh nor revert
    // sessions that were locally freshened while the request was in flight.
    const loadSeq = get()._loadSeq + 1;
    const touchSeqAtStart = get()._touchSeq;
    set({ _loadSeq: loadSeq, sessionsLoading: true });
    try {
      // summary=1: lean list (no per-session history download). Card preview
      // comes from `lastMessage`, count from `historyTotal`; the selected
      // session's messages are loaded by selectSession via fetchSessionHistory.
      const sessions = await fetchSessions(true);
      if (get()._loadSeq !== loadSeq) return; // superseded by a newer refresh
      const { currentSessionId, currentMessages } = get();
      const wsTouchedSeq = get()._sessionWsTouchedSeq;

      set((s) => {
        // Merge the snapshot with locally-fresher workerStatus/workerId: WS
        // worker events that landed at/after this fetch began are newer than
        // the snapshot — don't let a stale snapshot revert the card's status
        // dot (mirrors legacy `_wsWorkerTs` re-apply). `>=` also protects the
        // result path, where handleWorkerUpdate sets idle immediately before a
        // refresh starts, so the backend's transient "done"/"error" status
        // can't override the local idle WorkerDot.
        const merged = sessions.map((sess) => {
          const sid = sess.id;
          const cur = s.sessions.find((x) => x.id === sid);
          if (!cur) return sess;
          if ((wsTouchedSeq[sid] ?? 0) >= touchSeqAtStart) {
            return {
              ...sess,
              workerStatus: cur.workerStatus ?? sess.workerStatus,
              workerId: cur.workerId ?? sess.workerId,
            };
          }
          // summary=1 omits workerId (server.py `_session_summary`) — carry the
          // last-known value forward so the toolbar / worker actions keep
          // resolving the worker after a plain list refresh. Only when the
          // server still reports a live worker (workerStatus present): once the
          // worker is killed/crashed the summary flips workerStatus to null,
          // and a dead workerId must not keep the action buttons alive.
          const carryWorkerId =
            cur.workerId && sess.workerStatus ? cur.workerId : sess.workerId;
          // summary=1 omits the per-session settings — keep the current
          // session's known values (loaded on demand via the settings popover)
          // across refreshes so the pills / effort select don't flip to
          // defaults.
          if (sid === currentSessionId && cur.model) {
            return {
              ...sess,
              model: sess.model ?? cur.model,
              permissionMode: sess.permissionMode ?? cur.permissionMode,
              alwaysThinkingEnabled:
                sess.alwaysThinkingEnabled ?? cur.alwaysThinkingEnabled,
              effort: sess.effort || cur.effort || '',
              workdir: sess.workdir ?? cur.workdir,
              workerId: carryWorkerId,
            };
          }
          return carryWorkerId ? { ...sess, workerId: carryWorkerId } : sess;
        });
        return { sessions: merged };
      });

      // Restore current session messages after refresh — but NEVER clobber the
      // live-rendered messages with a stale snapshot. While streaming, the
      // server history is a prefix of what we already show locally (the
      // backend persists each block slightly after broadcasting it), so a
      // blind overwrite would wipe the in-flight assistant reply (bug: needs
      // a manual refresh to reappear).
      if (currentSessionId) {
        const found = sessions.find((s) => s.id === currentSessionId);
        if (found) {
          const serverHistory = found.history || [];
          const keepLocal = isServerHistoryPrefix(
            currentMessages,
            serverHistory,
          );
          set({
            currentMessages: keepLocal ? currentMessages : serverHistory,
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
      currentMessages: session.history || [],
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
    // 这正是「vanilla 已更新、React 进入的对话历史还是旧的」的根因。
    // vanilla 因每个 worker 事件都触发 refreshSessions，快照几乎总是新的。
    try {
      const data: ApiSessionHistoryResponse = await fetchSessionHistory(
        id,
        0,
        50,
      );
      if (get().currentSessionId !== id) {
        // 用户已切走，丢弃过期结果。若当前已无选中 session（如该 session 在
        // 请求期间被删除），不会有后续 selectSession 重置 initialLoading——
        // 这里兜底清掉，避免 chat 面板一直停在转圈。
        if (!get().currentSessionId) set({ initialLoading: false });
        return;
      }
      const serverHistory = data.history || [];
      // 流式窗口防护：若服务端历史只是本地已渲染消息的前缀（部分块尚未
      // 落盘），保留本地，避免把正在流式的回复抹掉。
      const keepLocal = isServerHistoryPrefix(
        get().currentMessages,
        serverHistory,
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
        currentMessages: keepLocal ? s.currentMessages : serverHistory,
        hasMoreMessages: data.hasMore,
        historyLoadEnd: data.start,
        initialLoading: false,
      }));
    } catch {
      // 网络失败：保留快照（上方已 set），不阻塞切换。同样清掉
      // initialLoading——否则「空快照 + 拉取失败」会让 chat 面板一直转圈。
      if (get().currentSessionId === id || !get().currentSessionId) {
        set({ initialLoading: false });
      }
      console.warn('[sessionStore] selectSession fresh-history fetch failed', id);
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
    try {
      await renameSession(id, name);
      set((s) => ({
        sessions: s.sessions.map((session) =>
          session.id === id ? { ...session, name } : session,
        ),
      }));
    } catch (e) {
      throw e;
    }
  },

  branch: async (id: string, name: string) => {
    try {
      await branchSession(id, name);
      await get().loadSessions();
    } catch (e) {
      throw e;
    }
  },

  reimport: async (id: string) => {
    const session = get().sessions.find((s) => s.id === id);
    if (!session?.cliSessionId) return;

    try {
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
    } catch (e) {
      throw e;
    }
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

  updateSession: (id: string, data: Partial<Session>) => {
    const touchSeq = get()._touchSeq + 1;
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === id ? { ...session, ...data } : session,
      ),
      _touchSeq: touchSeq,
      _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [id]: touchSeq },
    }));
  },

  applyResultToSession: (id, e) => {
    const status = e.status === 'error' ? 'error' : 'done';
    const result = e.result;
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
            typeof result === 'string'
              ? result.slice(0, 200)
              : x.lastMessage ?? '',
          lastResult: {
            status,
            result: result ?? '',
            timestamp: new Date().toISOString(),
          },
        };
      });
      return { sessions };
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

  markUnread: (content: string) => {
    const { currentSessionId } = get();
    if (!currentSessionId) return;
    set((s) => {
      const perSession = s.sessionUnread[currentSessionId] ?? new Set();
      perSession.add(content);
      return {
        sessionUnread: {
          ...s.sessionUnread,
          [currentSessionId]: perSession,
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
