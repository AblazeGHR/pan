/**
 * Per-Session message bookmarks: the client mirror of the durable sidecar
 * store (`packages/core/session_bookmarks.py`).
 *
 * Design constraints this file exists to satisfy:
 *
 *  - **The server is authoritative; the UI never waits for it.** A mutation
 *    applies optimistically (O(1), synchronous) and the request is a
 *    background follower.  A failure rolls the optimistic mark back and
 *    surfaces an error instead of leaving a lie on screen.
 *  - **Idempotent, unconditional mutations.** Add/remove are binary set/clear
 *    operations; the UI sends no compare-and-set base, so a retried click can
 *    never surface a revision conflict to the user.  The backend serializes
 *    concurrent writers and broadcasts the result.
 *  - **Dual-client convergence over the existing WS channel.** Every committed
 *    change arrives as `bookmarks.changed` (including this tab's own, which is
 *    an idempotent re-apply), so two open tabs converge without polling.
 *  - **Per-Session isolation.** State is keyed by session id and a bookmark
 *    never addresses another Session's row; switching Sessions never mixes
 *    marks.
 *  - **No history pollution.** Marks live only here; the projected
 *    `bookmarked` flag on history/navigation rows is a read-only mirror that
 *    this store overrides while a mutation is fresher.
 */

import { create } from 'zustand';
import {
  addSessionBookmark,
  fetchSessionBookmarks,
  removeSessionBookmark,
} from '@/services/api';

export interface SessionBookmarkState {
  /** Server revision of the last observed envelope (observability only). */
  revision: number;
  /** messageId → bookmarked.  The freshest known state: server envelope,
   *  optimistic mutation, or a broadcast from any client. */
  marks: Record<string, boolean>;
  /** True once the cold load for this Session completed at least once. */
  loaded: boolean;
  /** messageId → desired state of the in-flight mutation (single-flight). */
  pending: Record<string, boolean>;
  /** Last mutation error for this Session, cleared on the next success. */
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
  /** Apply one `bookmarks.changed` broadcast (any client, including self). */
  applyBookmarkEvent: (event: BookmarkChangedEvent) => void;
  isBookmarked: (sessionId: string, messageId: string) => boolean;
  clearError: (sessionId: string) => void;
}

const loadsInFlight = new Map<string, Promise<void>>();

/** A durable Pan message identity — the only bookmark-addressable form the
 *  backend accepts.  Positional `legacy:` ids and runtime rows are never
 *  bookmarkable, so the UI does not offer an entry for them. */
export function isPanMessageId(messageId: unknown): messageId is string {
  return typeof messageId === 'string' && /^pan:[0-9a-f]{32}$/.test(messageId);
}

function sessionState(store: { sessions: Record<string, SessionBookmarkState> }, sessionId: string): SessionBookmarkState {
  return store.sessions[sessionId] ?? {
    revision: 0, marks: {}, loaded: false, pending: {}, error: null,
  };
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
        set((state) => ({
          sessions: {
            ...state.sessions,
            [sessionId]: {
              revision: envelope.revision,
              marks,
              loaded: true,
              // A cold load supersedes any stale optimistic mark.
              pending: {},
              error: null,
            },
          },
        }));
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
    const bookmarked = event.action === 'added';
    set((state) => {
      const current = sessionState(state, event.sessionId);
      const marks = { ...current.marks };
      for (const messageId of event.messageIds) {
        if (typeof messageId === 'string' && messageId) marks[messageId] = bookmarked;
      }
      return {
        sessions: {
          ...state.sessions,
          [event.sessionId]: {
            ...current,
            revision: typeof event.revision === 'number' ? event.revision : current.revision,
            marks,
            // The broadcast acknowledges any in-flight mutation for these ids.
            pending: withoutKeys(current.pending, event.messageIds),
          },
        },
      };
    });
  },

  isBookmarked: (sessionId, messageId) => Boolean(get().sessions[sessionId]?.marks[messageId]),

  clearError: (sessionId) => set((state) => ({
    sessions: {
      ...state.sessions,
      [sessionId]: { ...sessionState(state, sessionId), error: null },
    },
  })),
}));

function withoutKeys(pending: Record<string, boolean>, keys: readonly unknown[]): Record<string, boolean> {
  if (!Object.keys(pending).length) return pending;
  const drop = new Set(keys.filter((key): key is string => typeof key === 'string'));
  if (!drop.size) return pending;
  const next: Record<string, boolean> = {};
  for (const [key, value] of Object.entries(pending)) {
    if (!drop.has(key)) next[key] = value;
  }
  return next;
}

/** One optimistic mutation: apply, request, confirm or roll back. */
async function mutate(
  set: (fn: (state: BookmarkStore) => Partial<BookmarkStore>) => void,
  get: () => BookmarkStore,
  sessionId: string,
  messageId: string,
  add: boolean,
): Promise<boolean> {
  const current = sessionState(get(), sessionId);
  const previous = Boolean(current.marks[messageId]);
  // Single-flight first: while a mutation is in flight the optimistic mark
  // already reflects the desired state, so the no-op check below must not
  // accept a duplicate click as "already done".
  if (messageId in current.pending) return false;
  if (previous === add) return true;                 // already in the desired state
  set((state) => ({
    sessions: {
      ...state.sessions,
      [sessionId]: {
        ...current,
        marks: { ...current.marks, [messageId]: add },
        pending: { ...current.pending, [messageId]: add },
        error: null,
      },
    },
  }));
  try {
    if (add) await addSessionBookmark(sessionId, messageId);
    else await removeSessionBookmark(sessionId, messageId);
    set((state) => {
      const latest = sessionState(state, sessionId);
      return {
        sessions: {
          ...state.sessions,
          [sessionId]: {
            ...latest,
            pending: withoutKeys(latest.pending, [messageId]),
          },
        },
      };
    });
    return true;
  } catch (error) {
    // Roll the optimistic mark back to the last known server state.
    set((state) => {
      const latest = sessionState(state, sessionId);
      const marks = { ...latest.marks };
      if (previous) marks[messageId] = true;
      else delete marks[messageId];
      return {
        sessions: {
          ...state.sessions,
          [sessionId]: {
            ...latest,
            marks,
            pending: withoutKeys(latest.pending, [messageId]),
            error: error instanceof Error ? error.message : 'Bookmark update failed',
          },
        },
      };
    });
    return false;
  }
}

/** Stable selector for one row's bookmark state (primitive → no rerender
 *  churn when unrelated bookmarks change). */
export function selectBookmarked(sessionId: string, messageId: string | undefined) {
  return (state: BookmarkStore): boolean =>
    Boolean(messageId && state.sessions[sessionId]?.marks[messageId]);
}

export const EMPTY_BOOKMARK_MARKS = EMPTY_MARKS;
