/**
 * Per-Session message bookmarks: the client mirror of the durable sidecar
 * store (`packages/core/session_bookmarks.py`).
 *
 * Concurrency model (all three layers are load-bearing):
 *
 *  - **A full snapshot and an incremental delta are different evidence.**
 *    `fetchSessionBookmarks` returns the COMPLETE set at its revision;
 *    `bookmarks.changed` carries ONE ordered delta.  The authoritative set is
 *    a snapshot folded with the deltas that followed it, so a slow snapshot
 *    whose revision is older than applied deltas is FOLDED (per-id evidence
 *    revision decides who wins) instead of being discarded — it can carry
 *    bookmarks no delta has mentioned yet.  Two watermarks track the two
 *    evidence domains: `appliedThrough` orders DELTAS, while
 *    `snapshotRevision` marks the newest COMPLETE snapshot — its absence
 *    domain covers every id it does not list, so an older complete snapshot
 *    may never add an id the newer one has already proven absent (absence is
 *    evidence too, and deletion tombstones survive).
 *  - **Deltas apply in contiguous revision order.**  `appliedThrough` is the
 *    highest revision such that every delta up to it is accounted for.  A
 *    delta at `appliedThrough + 1` applies directly; an older one is already
 *    included (ignore); a NEWER one is a GAP: it is buffered (bounded) and
 *    one coalesced REAL refetch reconciles the missing evidence — never
 *    unbounded polling, never a dropped delta.  Folding a complete snapshot
 *    at revision R also raises `appliedThrough` to R (the snapshot subsumes
 *    every delta up to it).
 *  - **Optimistic local intent lives in per-message transactions** (token +
 *    desired), overlaid on the authoritative set.  Only a transaction's own
 *    response or failure retires it (a broadcast cannot prove it is that
 *    request's ack), so stale callbacks cannot release newer transactions and
 *    a failure rolls back nothing but its own overlay.
 *  - **A response without a revision is out of contract**: it never invents
 *    authority; it retires its transaction and requests the same real
 *    reconciliation.
 *
 * Per-Session isolation: all state is keyed by session id and a bookmark
 * never addresses another Session's row.
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

export interface PendingBookmarkEvent {
  revision: number;
  action: 'added' | 'removed';
  messageIds: string[];
}

const PENDING_EVENT_LIMIT = 50;

export interface SessionBookmarkState {
  /** Highest revision such that every delta up to it is accounted for. */
  appliedThrough: number;
  /** Highest revision of a COMPLETE snapshot folded for this Session.  Its
   *  absence domain covers every id it does not list, so a complete snapshot
   *  older than this watermark must never add an id that revision has
   *  already proven absent. */
  snapshotRevision: number;
  /** Authoritative marks (snapshot folded with contiguous deltas). */
  authoritativeMarks: Record<string, boolean>;
  /** Per-id revision of the evidence that last set its authoritative value. */
  authoritativeTouched: Record<string, number>;
  /** Gapped deltas waiting for the missing evidence (bounded). */
  pendingEvents: PendingBookmarkEvent[];
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

interface BookmarkEnvelope {
  revision: number;
  bookmarks: Record<string, { messageId: string; createdAt: string }>;
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
   *  Contiguity-ordered; never settles local transactions. */
  applyBookmarkEvent: (event: BookmarkChangedEvent) => void;
  isBookmarked: (sessionId: string, messageId: string) => boolean;
  clearError: (sessionId: string) => void;
}

const loadsInFlight = new Map<string, Promise<void>>();
const reconcilesInFlight = new Map<string, Promise<void>>();
let transactionSeq = 0;

/** A durable Pan message identity — the only bookmark-addressable form the
 *  backend accepts.  Positional `legacy:` ids and runtime rows are never
 *  bookmarkable, so the UI does not offer an entry for them. */
export function isPanMessageId(messageId: unknown): messageId is string {
  return typeof messageId === 'string' && /^pan:[0-9a-f]{32}$/.test(messageId);
}

function emptyState(): SessionBookmarkState {
  return {
    appliedThrough: 0,
    snapshotRevision: 0,
    authoritativeMarks: EMPTY_MARKS,
    authoritativeTouched: {},
    pendingEvents: [],
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

type Authoritative = Pick<SessionBookmarkState, 'authoritativeMarks' | 'authoritativeTouched'>;
type FoldedState = Authoritative & Pick<SessionBookmarkState, 'snapshotRevision'>;

/** Fold a COMPLETE snapshot into the authoritative set.  Per-id evidence
 *  revisions decide: anything a newer delta owns is preserved, everything
 *  else takes the snapshot's value (including its absences).  An id this
 *  client has never seen may only be ADDED when the snapshot is not older
 *  than the newest complete snapshot already folded — that newer snapshot
 *  has already proven every unknown id absent, and a stale snapshot must not
 *  resurrect a deleted bookmark. */
function foldSnapshot(current: FoldedState, envelope: BookmarkEnvelope, revision: number): FoldedState {
  const bookmarked = new Set(Object.keys(envelope.bookmarks ?? {}));
  const marks = { ...current.authoritativeMarks };
  const touched = { ...current.authoritativeTouched };
  for (const id of bookmarked) {
    const evidence = touched[id];
    if (evidence !== undefined
      ? evidence > revision                              // a newer delta owns this id
      : revision < current.snapshotRevision) continue;   // absent in a newer complete snapshot
    marks[id] = true;
    touched[id] = revision;
  }
  for (const id of Object.keys(marks)) {
    if (bookmarked.has(id)) continue;
    if ((touched[id] ?? -1) > revision) continue;        // a newer delta owns this id
    delete marks[id];                                    // the snapshot at `revision` lacks it
    touched[id] = revision;
  }
  return {
    authoritativeMarks: marks,
    authoritativeTouched: touched,
    snapshotRevision: Math.max(current.snapshotRevision, revision),
  };
}

/** Apply one contiguous delta to the authoritative set. */
function applyDelta(current: Authoritative, event: PendingBookmarkEvent, revision: number): Authoritative {
  const marks = { ...current.authoritativeMarks };
  const touched = { ...current.authoritativeTouched };
  for (const id of event.messageIds) {
    if (typeof id !== 'string' || !id) continue;
    if (event.action === 'added') marks[id] = true;
    else delete marks[id];
    touched[id] = revision;
  }
  return { authoritativeMarks: marks, authoritativeTouched: touched };
}

/** Drain buffered deltas that have become contiguous; keep the still-gapped
 *  ones and drop whatever the watermark already covers.  Every applied delta
 *  sits ABOVE `appliedThrough` (and therefore above `snapshotRevision`),
 *  because contiguity only advances one revision at a time. */
function drainPending(start: FoldedState & { appliedThrough: number; pendingEvents: PendingBookmarkEvent[] }): FoldedState & { appliedThrough: number; pendingEvents: PendingBookmarkEvent[] } {
  let { authoritativeMarks: marks, authoritativeTouched: touched, appliedThrough } = start;
  const { snapshotRevision } = start;
  let pendingEvents = start.pendingEvents;
  let advanced = true;
  while (advanced) {
    advanced = false;
    const next = pendingEvents.find((event) => event.revision === appliedThrough + 1);
    if (next) {
      ({ authoritativeMarks: marks, authoritativeTouched: touched } =
        applyDelta({ authoritativeMarks: marks, authoritativeTouched: touched }, next, next.revision));
      appliedThrough = next.revision;
      pendingEvents = pendingEvents.filter((event) => event !== next);
      advanced = true;
    }
  }
  pendingEvents = pendingEvents.filter((event) => event.revision > appliedThrough);
  return { authoritativeMarks: marks, authoritativeTouched: touched, appliedThrough, pendingEvents, snapshotRevision };
}

function bufferPending(pendingEvents: PendingBookmarkEvent[], event: PendingBookmarkEvent): PendingBookmarkEvent[] {
  if (pendingEvents.some((existing) => existing.revision === event.revision)) return pendingEvents;
  const next = [...pendingEvents, event].sort((a, b) => a.revision - b.revision);
  return next.slice(-PENDING_EVENT_LIMIT);
}

/** Fold one snapshot response into the store state (shared by the cold load,
 *  the reconciliation refetch and versioned mutation responses). */
function foldSnapshotInto(sessionId: string, envelope: BookmarkEnvelope, fallbackError: string | null = null) {
  return (state: BookmarkStore): Partial<BookmarkStore> => {
    const latest = sessionState(state, sessionId);
    const revision = typeof envelope.revision === 'number' ? envelope.revision : 0;
    const folded = foldSnapshot(latest, envelope, revision);
    const drained = drainPending({
      ...folded,
      appliedThrough: Math.max(latest.appliedThrough, revision),
      pendingEvents: latest.pendingEvents,
    });
    return {
      sessions: {
        ...state.sessions,
        [sessionId]: {
          ...latest,
          ...drained,
          loaded: true,
          error: fallbackError,
          marks: deriveMarks(drained.authoritativeMarks, latest.transactions),
        },
      },
    };
  };
}

/** One coalesced REAL refetch per Session; never polls.  Triggered only by a
 *  detected gap or an out-of-contract response. */
function reconcile(sessionId: string): Promise<void> {
  const inFlight = reconcilesInFlight.get(sessionId);
  if (inFlight) return inFlight;
  const request = (async () => {
    try {
      const envelope = await fetchSessionBookmarks(sessionId);
      useBookmarkStore.setState(foldSnapshotInto(sessionId, envelope, null));
    } catch {
      // The gap stays buffered; a later trigger retries.  No polling.
    } finally {
      reconcilesInFlight.delete(sessionId);
    }
  })();
  reconcilesInFlight.set(sessionId, request);
  return request;
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
        set(foldSnapshotInto(sessionId, envelope, null));
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
    const delta: PendingBookmarkEvent = {
      revision, action: event.action,
      messageIds: event.messageIds.filter((id): id is string => typeof id === 'string' && !!id),
    };
    let needsReconcile = false;
    set((state) => {
      const current = sessionState(state, event.sessionId);
      if (revision <= current.appliedThrough) {
        // Already accounted for (duplicates and same-id stale events land
        // here): re-applying could revert newer evidence for the same row.
        return {};
      }
      if (revision > current.appliedThrough + 1) {
        // A GAP: the missing revisions are unknown.  Buffer this delta and
        // ask for one real refetch; never drop it, never poll.
        needsReconcile = true;
        return {
          sessions: {
            ...state.sessions,
            [event.sessionId]: {
              ...current,
              pendingEvents: bufferPending(current.pendingEvents, delta),
            },
          },
        };
      }
      const applied = applyDelta(current, delta, revision);
      const drained = drainPending({
        ...applied,
        snapshotRevision: current.snapshotRevision,
        appliedThrough: revision,
        pendingEvents: current.pendingEvents,
      });
      return {
        sessions: {
          ...state.sessions,
          [event.sessionId]: {
            ...current,
            ...drained,
            // A broadcast updates server truth but never settles our own
            // in-flight transaction: it cannot prove it is that request's ack.
            marks: deriveMarks(drained.authoritativeMarks, current.transactions),
          },
        },
      };
    });
    if (needsReconcile) void reconcile(event.sessionId);
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
  // "Already there" is only proven once this client has evidence.  A row can
  // offer Remove from the backend projection before the envelope has loaded;
  // that click must still reach the (idempotent) endpoint instead of being
  // mistaken for a repeat of the unknown state.
  const known = current.loaded || messageId in current.authoritativeTouched;
  if (known && effectiveMark(current, messageId) === add) return true;

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
      error instanceof Error ? error.message : (add ? '书签保存失败' : '书签删除失败'),
      'error',
    );
    return false;
  }

  // Success: settle OUR transaction (token-guarded, so a stale callback can
  // never release a newer one).  A versioned response folds in as a real
  // snapshot; an out-of-contract response without a revision never invents
  // authority — it just asks for the same real reconciliation.
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
        },
      },
    };
  });
  const revision = typeof envelope.revision === 'number' ? envelope.revision : null;
  if (revision !== null) {
    set(foldSnapshotInto(sessionId, envelope as BookmarkEnvelope, null));
  } else {
    void reconcile(sessionId);
  }
  return true;
}

/** The freshest PROVEN mark for one row, or `undefined` when this client has
 *  no evidence yet.  A loaded envelope (or an applied delta for the id) makes
 *  absence authoritative, so a stale `bookmarked: true` projection on a
 *  history row can never outlive a confirmed removal.  Callers fall back to
 *  the projection only while the answer is genuinely unknown. */
export function bookmarkMark(state: BookmarkStore, sessionId: string, messageId: string | undefined): boolean | undefined {
  if (!sessionId || !messageId) return undefined;
  const current = sessionState(state, sessionId);
  const transaction = current.transactions[messageId];
  if (transaction) return transaction.desired;
  if (current.loaded || messageId in current.authoritativeTouched) {
    return Boolean(current.authoritativeMarks[messageId]);
  }
  return undefined;
}

/** Stable selector for one row's bookmark state (primitive → no rerender
 *  churn when unrelated bookmarks change). */
export function selectBookmarked(sessionId: string, messageId: string | undefined) {
  return (state: BookmarkStore): boolean =>
    Boolean(messageId && effectiveMark(sessionState(state, sessionId), messageId));
}

/** Stable tri-state selector for consumers that also carry the backend's
 *  `bookmarked` projection.  `undefined` means "the store has no evidence";
 *  `false` is proven absence and wins over the projection. */
export function selectBookmarkMark(sessionId: string, messageId: string | undefined) {
  return (state: BookmarkStore): boolean | undefined =>
    bookmarkMark(state, sessionId, messageId);
}

export const EMPTY_BOOKMARK_MARKS = EMPTY_MARKS;
