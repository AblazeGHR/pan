/**
 * Per-Session message bookmarks: the client mirror of the durable sidecar
 * store (`packages/core/session_bookmarks.py`).
 *
 * Design constraints this file exists to satisfy:
 *
 *  - **The server is authoritative; the UI never waits for it.** A mutation
 *    is visible optimistically and the request is a background follower.
 *  - **Authoritative snapshot + per-item overlay.**  `authoritativeMarks` /
 *    `authoritativeRevision` only ever change from server evidence (a
 *    response envelope or a `bookmarks.changed` broadcast); optimistic local
 *    intent lives in `transactions`.  The consumer-facing `marks` is derived
 *    from the two, so a failed or stale operation can never roll back a
 *    newer server state — removing its overlay entry is the whole rollback.
 *  - **Revisions order every source.**  A load or broadcast older than the
 *    authoritative revision is content-skipped (it still marks the load as
 *    loaded).  Response envelopes reconcile through their real revision, so
 *    a slow response cannot resurrect a stale view.
 *  - **Per-item transaction identity.**  Each in-flight mutation carries a
 *    token; only its own settlement (response or failure) may retire it, so
 *    a stale callback cannot release a newer transaction.  A broadcast never
 *    settles a local transaction: it cannot prove it is that request's ACK.
 *  - **Dual-client convergence over the existing WS channel.**  Commits from
 *    any client arrive as ordered `bookmarks.changed` events and update the
 *    authoritative snapshot without polling.
 *  - **Per-Session isolation.**  State is keyed by session id; a bookmark
 *    never addresses another Session's row.
 */

import { create } from 'zustand';
import {
  addSessionBookmark,
  fetchSessionBookmarks,
  removeSessionBookmark,
} from '@/services/api';
import { useUIStore } from '@/stores/uiStore';

export interface BookmarkTransaction {
  token: number;
  desired: boolean;
}

export interface SessionBookmarkState {
  /** Last authoritative envelope revision (response or broadcast). */
  authoritativeRevision: number;
  /** Marks that server evidence proves (never optimistic). */
  authoritativeMarks: Record<string, boolean>;
  /** Consumer-facing view: authoritative marks overlaid with in-flight
   *  transaction intents.  Derived on every write; never set independently. */
  marks: Record<string, boolean>;
  /** True once the cold load for this Session reached the server. */
  loaded: boolean;
  /** In-flight local mutations keyed by messageId (single-flight each). */
  transactions: Record<string, BookmarkTransaction>;
  /** Last mutation/load error for this Session, cleared on the next success. */
  error: string | null;
}

export interface BookmarkChangedEvent {
  type: 'bookmarks.changed';
  sessionId: string;
  revision: number;
  action: 'added' | 'removed';
  messageIds: string[];
}

const EMPTY_MARKS: Record<string, boolean> = {};

interface BookmarkStore {
  sessions: Record<string, SessionBookmarkState>;
  /** Cold-load one Session's envelope.  Repeated calls reuse the in-flight
   *  request; `force` re-reads after a failure. */
  loadBookmarks: (sessionId: string, force?: boolean) => Promise<void>;
  addBookmark: (sessionId: string, messageId: string) => Promise<boolean>;
  removeBookmark: (sessionId: string, messageId: string) => Promise<boolean>;
  /** Apply one `bookmarks.changed` broadcast (any client, including self).
   *  Ordered by revision; never settles local transactions. */
  applyBookmarkEvent: (event: BookmarkChangedEvent) => void;
  isBookmarked: (sessionId: string, messageId: string) => boolean;
  clearError: (sessionId: string) => void;
}

const loadsInFlight = new Map<string, Promise<void>>();
let transactionSeq = 0;

/** A durable Pan message identity — the only bookmark-addressable form the
 *  backend accepts.  Positional `legacy:` ids and runtime rows are never
 *  bookmarkable, so the UI does not offer an entry for them. */
export function isPanMessageId(messageId: unknown): messageId is string {
  return typeof messageId === 'string' && /^pan:[0-9a-f]{32}$/.test(messageId);
}

function emptyState(): SessionBookmarkState {
  return {
    authoritativeRevision: 0,
    authoritativeMarks: EMPTY_MARKS,
    marks: EMPTY_MARKS,
    loaded: false,
    transactions: {},
    error: null,
  };
}

function sessionState(store: { sessions: Record<string, SessionBookmarkState> }, sessionId: string): SessionBookmarkState {
  return store.sessions[sessionId] ?? emptyState();
}

/** The consumer-facing view: server truth overlaid with in-flight intents. */
function deriveMarks(authoritative: Record<string, boolean>,
                     transactions: Record<string, BookmarkTransaction>): Record<string, boolean> {
  const ids = Object.keys(transactions);
  if (ids.length === 0) return authoritative;
  const marks = { ...authoritative };
  for (const id of ids) marks[id] = transactions[id]!.desired;
  return marks;
}

/** The effective state of one row: its transaction intent wins while in
 *  flight, otherwise the authoritative map. */
function effectiveMark(state: SessionBookmarkState, messageId: string): boolean {
  return state.transactions[messageId]?.desired ?? state.authoritativeMarks[messageId] ?? false;
}

function withoutTransaction(transactions: Record<string, BookmarkTransaction>, messageId: string): Record<string, BookmarkTransaction> {
  if (!(messageId in transactions)) return transactions;
  const next = { ...transactions };
  delete next[messageId];
  return next;
}

export const useBookmarkStore = create<BookmarkStore>((set, get) => ({
  sessions: {},

  loadBookmarks: async (sessionId, force = false) => {
    const current = get().sessions[sessionId];
    if (!force && current?.loaded) return;
    const inFlight = loadsInFlight.get(sessionId);
    if (inFlight) return inFlight;
    const request = (async () => {
      try {
        const envelope = await fetchSessionBookmarks(sessionId);
        const marks: Record<string, boolean> = {};
        for (const messageId of Object.keys(envelope.bookmarks)) marks[messageId] = true;
        set((state) => {
          const latest = sessionState(state, sessionId);
          // A load older than what server evidence already proved is
          // content-skipped: it must never roll back a newer broadcast or a
          // committed mutation.  It still counts as "we reached the store".
          const fresh = envelope.revision >= latest.authoritativeRevision;
          const authoritativeMarks = fresh ? marks : latest.authoritativeMarks;
          return {
            sessions: {
              ...state.sessions,
              [sessionId]: {
                ...latest,
                loaded: true,
                error: null,
                ...(fresh ? { authoritativeRevision: envelope.revision, authoritativeMarks } : {}),
                // Transactions are never touched by a load: only their own
                // request settles them.
                marks: deriveMarks(authoritativeMarks, latest.transactions),
              },
            },
          };
        });
      } catch (error) {
        set((state) => ({
          sessions: {
            ...state.sessions,
            [sessionId]: {
              ...sessionState(state, sessionId),
              loaded: false,
              error: error instanceof Error ? error.message : 'Bookmark load failed',
            },
          },
        }));
      } finally {
        loadsInFlight.delete(sessionId);
      }
    })();
    loadsInFlight.set(sessionId, request);
    return request;
  },

  addBookmark: async (sessionId, messageId) => mutate(set, get, sessionId, messageId, true),
  removeBookmark: async (sessionId, messageId) => mutate(set, get, sessionId, messageId, false),

  applyBookmarkEvent: (event) => {
    if (!event?.sessionId || (event.action !== 'added' && event.action !== 'removed')) return;
    if (!Array.isArray(event.messageIds)) return;
    const revision = typeof event.revision === 'number' ? event.revision : 0;
    const bookmarked = event.action === 'added';
    set((state) => {
      const current = sessionState(state, event.sessionId);
      // Out-of-order broadcast: an older revision can only be stale state.
      if (revision < current.authoritativeRevision) return {};
      const authoritativeMarks = { ...current.authoritativeMarks };
      for (const messageId of event.messageIds) {
        if (typeof messageId === 'string' && messageId) authoritativeMarks[messageId] = bookmarked;
      }
      return {
        sessions: {
          ...state.sessions,
          [event.sessionId]: {
            ...current,
            authoritativeRevision: Math.max(current.authoritativeRevision, revision),
            authoritativeMarks,
            // A broadcast updates server truth but never settles our own
            // in-flight transaction: it cannot prove it is that request's ack.
            marks: deriveMarks(authoritativeMarks, current.transactions),
          },
        },
      };
    });
  },

  isBookmarked: (sessionId, messageId) => effectiveMark(sessionState(get(), sessionId), messageId),

  clearError: (sessionId) => set((state) => ({
    sessions: {
      ...state.sessions,
      [sessionId]: { ...sessionState(state, sessionId), error: null },
    },
  })),
}));

/** One optimistic mutation with its own transaction identity. */
async function mutate(
  set: (fn: (state: BookmarkStore) => Partial<BookmarkStore>) => void,
  get: () => BookmarkStore,
  sessionId: string,
  messageId: string,
  add: boolean,
): Promise<boolean> {
  const current = sessionState(get(), sessionId);
  // Single-flight per target: while a transaction is in flight its optimistic
  // intent already reflects the desired state, so a duplicate click must not
  // start a second request.
  if (messageId in current.transactions) return false;
  if (effectiveMark(current, messageId) === add) return true;   // already there

  const token = ++transactionSeq;
  set((state) => {
    const latest = sessionState(state, sessionId);
    const transactions = { ...latest.transactions, [messageId]: { token, desired: add } };
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...latest,
          transactions,
          marks: deriveMarks(latest.authoritativeMarks, transactions),
          error: null,
        },
      },
    };
  });

  let envelope: Awaited<ReturnType<typeof addSessionBookmark>> | Awaited<ReturnType<typeof removeSessionBookmark>>;
  try {
    envelope = add
      ? await addSessionBookmark(sessionId, messageId)
      : await removeSessionBookmark(sessionId, messageId);
  } catch (error) {
    // Definite/uncertain failure: retire OUR transaction only.  The overlay
    // falls back to the authoritative snapshot, so a newer server state that
    // arrived meanwhile is preserved instead of being rolled back.
    set((state) => {
      const latest = sessionState(state, sessionId);
      if (latest.transactions[messageId]?.token !== token) return {};
      const transactions = withoutTransaction(latest.transactions, messageId);
      return {
        sessions: {
          ...state.sessions,
          [sessionId]: {
            ...latest,
            transactions,
            marks: deriveMarks(latest.authoritativeMarks, transactions),
            error: error instanceof Error ? error.message : 'Bookmark update failed',
          },
        },
      };
    });
    useUIStore.getState().showToast(
      error instanceof Error ? error.message : '书签保存失败',
      'error',
    );
    return false;
  }

  // Success: reconcile through the response's REAL envelope revision rather
  // than discarding it — the response is the authoritative snapshot at commit
  // time.  A later broadcast may already have advanced past it, in which case
  // the older envelope is content-skipped but this transaction is settled.
  set((state) => {
    const latest = sessionState(state, sessionId);
    if (latest.transactions[messageId]?.token !== token) return {};
    const transactions = withoutTransaction(latest.transactions, messageId);
    const revision = typeof envelope.revision === 'number' ? envelope.revision : null;
    let authoritativeRevision = latest.authoritativeRevision;
    let authoritativeMarks = latest.authoritativeMarks;
    if (revision !== null && revision >= latest.authoritativeRevision
        && envelope.bookmarks && typeof envelope.bookmarks === 'object') {
      const marks: Record<string, boolean> = {};
      for (const id of Object.keys(envelope.bookmarks)) marks[id] = true;
      authoritativeRevision = revision;
      authoritativeMarks = marks;
    } else if (revision === null) {
      // A response without an envelope (out-of-contract): the success itself
      // proves this row's committed state, so fold the intent into the
      // authoritative map.  A KNOWN-but-stale revision never does this — the
      // newer broadcast it lost to stays authoritative.
      authoritativeMarks = { ...latest.authoritativeMarks, [messageId]: add };
    }
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...latest,
          transactions,
          authoritativeRevision,
          authoritativeMarks,
          marks: deriveMarks(authoritativeMarks, transactions),
        },
      },
    };
  });
  return true;
}

/** Stable selector for one row's bookmark state (primitive → no rerender
 *  churn when unrelated bookmarks change). */
export function selectBookmarked(sessionId: string, messageId: string | undefined) {
  return (state: BookmarkStore): boolean =>
    Boolean(messageId && effectiveMark(sessionState(state, sessionId), messageId));
}

export const EMPTY_BOOKMARK_MARKS = EMPTY_MARKS;
