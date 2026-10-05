/**
 * Durable persistence for the normal chat composer's unsent draft.
 *
 * Design constraints this file exists to satisfy:
 *
 *  - **Typing never waits for the network.** `record()` is synchronous, O(1)
 *    in draft size beyond the caller's own clone, and touches no `fetch`.  The
 *    UI reads memory; the network is a background follower.
 *  - **No per-keystroke requests.** A save is debounced (600 ms of quiet) *and*
 *    rate-limited (at most one per 2 s) *and* bounded (at most one per 5 s of
 *    continuous typing).  See `TIMING` for why each number is what it is.
 *  - **Bounded work per Session.** One in-flight request per Session with a
 *    single coalesced pending payload, so a slow or failing network cannot
 *    grow a queue or interleave writes.
 *  - **No polling.** A save happens on edit, on a lifecycle edge, or not at
 *    all.  There is no interval timer, and a failed save retries a bounded
 *    number of times rather than forever.
 *  - **Server authority for ordering.** Writes are compare-and-set against a
 *    `baseRevision`; a conflict surfaces instead of resolving on a client
 *    clock.  A client-side monotonic counter orders *this* tab's own
 *    same-Session writes, which is the only ordering a single client can
 *    meaningfully own.
 *
 * Multi-client policy: Pan is a local single-user app that can legitimately be
 * open in two tabs.  Last-writer-wins on a *server* revision is the honest
 * resolution — a client whose `baseRevision` is stale is told so and keeps its
 * unsaved local text rather than overwriting another tab's newer draft with a
 * stale read.  It does not merge silently; the caller decides.
 */

import {
  DraftConflictError,
  getSessionDraft,
  putSessionDraft,
  type ApiSessionDraft,
  type ApiSessionDraftAttachment,
} from '@/services/api';
import { restorableAttachments } from '@/stores/composerDraftCodec';

export type { ApiSessionDraft, ApiSessionDraftAttachment };
export { restorableAttachments };

/**
 * Save timing.  Each bound closes a different hole; none of them alone is
 * sufficient, which is why all three exist.
 *
 * - `DEBOUNCE_MS = 600` — coalesces a burst of typing into one write.  Sits
 *   above the ~150–300 ms gap between keystrokes in fast typing (so ordinary
 *   words never each trigger a request) and below the ~1 s at which a user
 *   starts to expect "I paused, it should be saved" (so a natural pause still
 *   persists promptly).
 * - `MIN_INTERVAL_MS = 2000` — floor between two writes for one Session.  This
 *   is what bounds the *rate* during sustained typing: without it, a user who
 *   pauses for 600 ms every few words would emit a request per pause, which is
 *   the per-keystroke-ish traffic this feature must not have.
 * - `MAX_WAIT_MS = 5000` — ceiling on how long unsaved text may sit unsaved
 *   during *continuous* typing, where the debounce never fires.  This is the
 *   honest worst-case data-loss window if the tab dies mid-sentence.
 *
 * The three interact as intended: a pause writes after 600 ms if the 2 s floor
 * has elapsed; continuous typing writes every 5 s regardless.
 */
export const TIMING = {
  DEBOUNCE_MS: 600,
  MIN_INTERVAL_MS: 2_000,
  MAX_WAIT_MS: 5_000,
  /** Bounded retry: 3 attempts with a fixed 2 s gap, then the local copy is
   *  kept dirty and unsaved.  No unbounded retry, no background polling — a
   *  failed draft is reported to the user rather than retried forever. */
  RETRY_ATTEMPTS: 3,
  RETRY_DELAY_MS: 2_000,
} as const;

interface SessionDraftState {
  /** Latest local draft, or null when the composer is empty. */
  draft: ApiSessionDraft | null;
  /** Monotonic per-Session counter, bumped on every record(). Orders this
   *  tab's writes and detects that a response is stale.  Not a clock: it never
   *  orders against another client's state. */
  localRevision: number;
  /** Last revision the server confirmed.  `null` means "never saved", which
   *  the next write sends as create-if-absent. */
  serverRevision: number | null;
  /** A local change exists that the server has not acknowledged. */
  dirty: boolean;
  /** True once a record() has happened for this Session in this tab.  Guards
   *  the cold-load path: a Session that was never touched locally must accept
   *  the server draft, one that was must not be overwritten by a late read. */
  touchedLocally: boolean;
  inFlight: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  /** When the current debounce window opened, for MAX_WAIT. */
  windowStartedAt: number;
  /** When the last write was *issued*, for MIN_INTERVAL.  0 means "never". */
  lastWriteAt: number;
  retries: number;
  /** In-flight cold load, so repeated selections reuse one request. */
  loadPromise: Promise<ApiSessionDraft | null> | null;
  loaded: boolean;
  /** The remembered result of the cold read, so a later A→B→A does not
   *  re-request.  Null means "the server has no draft for this Session". */
  loadedDraft: ApiSessionDraft | null;
  error: string | null;
}

type Listener = (sessionId: string, error: string | null) => void;

const states = new Map<string, SessionDraftState>();
const listeners = new Set<Listener>();
let lifecycleInstalled = false;

function state(sessionId: string): SessionDraftState {
  let current = states.get(sessionId);
  if (!current) {
    current = {
      draft: null,
      localRevision: 0,
      serverRevision: null,
      dirty: false,
      touchedLocally: false,
      inFlight: false,
      timer: null,
      windowStartedAt: 0,
      lastWriteAt: 0,
      retries: 0,
      loadPromise: null,
      loaded: false,
      loadedDraft: null,
      error: null,
    };
    states.set(sessionId, current);
  }
  return current;
}

/** Announce a Session's current persistence health (null when healthy).
 *  Read from state rather than passed in, so a subscriber can never be told
 *  "fine" while `error` is actually set. */
function notify(sessionId: string) {
  const error = draftError(sessionId);
  for (const listener of listeners) {
    try {
      listener(sessionId, error);
    } catch {
      // A subscriber must never break the save pipeline.
    }
  }
}

export function subscribeDraftPersistence(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Last persistence error for one Session (null when healthy). */
function draftError(sessionId: string): string | null {
  return states.get(sessionId)?.error ?? null;
}

function isEmpty(draft: ApiSessionDraft | null): boolean {
  return !draft || (!draft.text && draft.parts.length === 0 && draft.attachments.length === 0);
}

/**
 * Structural equality over *every field the server persists*.
 *
 * This must be a complete comparison of the persisted semantics, not a proxy
 * for them.  An earlier version compared only `parts.length`, which silently
 * discarded two real edits: reordering an inline attachment chip (same parts,
 * same length, different order and offsets) and changing an attachment's
 * `location`.  Both produce a draft that differs on the wire while passing a
 * length check, so the server kept the stale one.
 *
 * Cost: O(parts + attachments) with an early exit on `text`, and no allocation
 * and no deep copy — the caller's already-projected payload is walked in
 * place.  The hot path is unaffected because `text` differs on every real
 * keystroke, which returns on the first comparison.  The full walk only runs
 * for the non-typing events this exists to absorb (upload byte progress,
 * re-renders) where attachments are single digits.
 */
function sameDraft(a: ApiSessionDraft | null, b: ApiSessionDraft | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  // First and cheapest: every keystroke changes this, so the common case
  // never reaches the loops below.
  if (a.text !== b.text) return false;
  if (a.parts.length !== b.parts.length) return false;
  if (a.attachments.length !== b.attachments.length) return false;
  // Order and content of parts. `parts` is the composer's document order, so
  // position is part of the meaning: [text, att] and [att, text] restore
  // differently and must not compare equal.
  for (let i = 0; i < a.parts.length; i += 1) {
    const left = a.parts[i]!;
    const right = b.parts[i]!;
    if (left.type !== right.type) return false;
    // Narrow each side independently: the discriminant has already been
    // compared, so both are the same variant here.
    if (left.type === 'text' && right.type === 'text') {
      if (left.value !== right.value) return false;
    } else if (left.type === 'attachment' && right.type === 'attachment') {
      // occurrenceId is the local occurrence identity; several occurrences may
      // share one attachmentId, so both must match.
      if (
        left.attachmentId !== right.attachmentId ||
        left.occurrenceId !== right.occurrenceId
      ) {
        return false;
      }
    }
  }
  for (let i = 0; i < a.attachments.length; i += 1) {
    const left = a.attachments[i]!;
    const right = b.attachments[i]!;
    if (
      left.occurrenceId !== right.occurrenceId ||
      left.displayName !== right.displayName ||
      left.attachmentId !== right.attachmentId ||
      left.path !== right.path ||
      left.href !== right.href ||
      left.mimeType !== right.mimeType ||
      left.fileKey !== right.fileKey ||
      left.source !== right.source
    ) {
      return false;
    }
    // location is nested, so an identity check is not enough: a chip that
    // moved to a different line is a different draft.
    const leftLocation = left.location;
    const rightLocation = right.location;
    if (leftLocation !== rightLocation) {
      if (!leftLocation || !rightLocation) return false;
      if (
        leftLocation.line !== rightLocation.line ||
        leftLocation.endLine !== rightLocation.endLine
      ) {
        return false;
      }
    }
  }
  return true;
}

function clearTimer(current: SessionDraftState) {
  if (current.timer !== null) {
    clearTimeout(current.timer);
    current.timer = null;
  }
}

function scheduleFlush(sessionId: string, current: SessionDraftState) {
  clearTimer(current);
  const now = Date.now();
  if (current.windowStartedAt === 0) current.windowStartedAt = now;
  const sinceWindow = now - current.windowStartedAt;
  // lastWriteAt === 0 means "this Session has never been written". Deriving a
  // real elapsed time from it would make the min-interval floor apply to a
  // first save purely because the clock has moved, delaying it by up to
  // MIN_INTERVAL_MS for no reason. Treating it as a full interval instead lets
  // the debounce alone govern a first write.
  const sinceWrite =
    current.lastWriteAt === 0 ? TIMING.MIN_INTERVAL_MS : now - current.lastWriteAt;
  let delay: number = TIMING.DEBOUNCE_MS;
  if (sinceWindow >= TIMING.MAX_WAIT_MS) {
    // Continuous typing: the debounce has not fired, so write now.
    delay = 0;
  } else if (sinceWrite < TIMING.MIN_INTERVAL_MS) {
    delay = Math.max(delay, TIMING.MIN_INTERVAL_MS - sinceWrite);
  }
  current.timer = setTimeout(() => {
    current.timer = null;
    void flush(sessionId);
  }, delay);
}

/**
 * Record the local draft.  Synchronous and network-free — this is the typing
 * hot path, so it must stay O(1) in the draft's size.  The caller passes an
 * already-cloned payload (the composer's `rememberSessionDraft` already clones),
 * so no defensive copy happens per keystroke here.
 */
export function recordDraft(sessionId: string, draft: ApiSessionDraft | null) {
  const current = state(sessionId);
  if (sameDraft(current.draft, draft)) {
    // Identical content. Whether anything should happen now depends entirely on
    // whether a save is already owed AND already scheduled:
    //
    //  - a timer is armed, or a write is in flight -> the pending save will
    //    carry this exact payload, so doing anything here would duplicate it;
    //  - dirty with nothing scheduled -> a previous attempt failed or
    //    conflicted and deliberately stopped. This call is the user's next
    //    chance to persist, so it must re-arm.
    //  - not dirty -> the server already holds this content; re-writing it
    //    would be a pointless request.
    if (current.timer !== null || current.inFlight) return;
    if (!current.dirty) return;
    current.error = null;
    current.retries = 0;
    scheduleFlush(sessionId, current);
    return;
  }
  const empty = isEmpty(draft);
  current.draft = draft;
  current.localRevision += 1;
  current.touchedLocally = true;
  // An empty composer is still a state change: it must reach the server as a
  // tombstone so a reload does not resurrect what was sent.  Only skip the
  // write when nothing is pending and there is nothing durable to clear.
  const hasSomethingToClear = current.serverRevision !== null;
  if (empty && !current.dirty && !hasSomethingToClear) {
    current.dirty = false;
    current.error = null;
    return;
  }
  current.dirty = true;
  current.error = null;
  current.retries = 0;
  scheduleFlush(sessionId, current);
}

/** Force a write attempt now, coalescing with any in-flight save. */
export function flushDraft(sessionId: string) {
  const current = states.get(sessionId);
  if (!current || !current.dirty) return;
  clearTimer(current);
  void flush(sessionId);
}

/** Force a write attempt for every Session with unsaved local text. */
export function flushAllDrafts() {
  for (const [sessionId, current] of states) {
    if (current.dirty) {
      clearTimer(current);
      void flush(sessionId);
    }
  }
}

async function flush(sessionId: string): Promise<void> {
  const current = states.get(sessionId);
  if (!current) return;
  // One write per Session at a time.  A burst of edits during a slow request is
  // not queued: `draft` always holds the newest payload, and the completion
  // path below re-arms a single coalesced follow-up if the local revision moved
  // while this request was open.
  if (current.inFlight) return;
  if (!current.dirty) return;

  const payload = current.draft;
  const localRevision = current.localRevision;
  const baseRevision = current.serverRevision;
  current.inFlight = true;
  current.dirty = false;
  current.windowStartedAt = 0;
  current.lastWriteAt = Date.now();
  // Set when this attempt's failure spends the retry budget.  Suppresses only
  // the automatic tail reschedule below, never the local copy.
  let retryExhausted = false;

  try {
    const result = await putSessionDraft(sessionId, payload, baseRevision);
    // Through the same monotonic guard as the read path: a write response is
    // authoritative, but adopting it unconditionally would let a slow response
    // to an OLD write clobber a revision already learned from a newer one.
    adoptRevision(current, result.revision);
    current.retries = 0;
    current.error = null;
    if (current.localRevision !== localRevision) {
      // Newer local edits landed while this request was in flight.  The
      // response is acknowledged (revision captured) but the newer text still
      // needs writing, so mark dirty and schedule the coalesced follow-up.
      current.dirty = true;
    }
    notify(sessionId);
  } catch (error) {
    if (error instanceof DraftConflictError) {
      // Another client wrote a newer revision.  Keep the local text: silently
      // overwriting another tab's draft with a stale read would lose *their*
      // work, and adopting theirs would lose this tab's.  The revision is
      // adopted so the next explicit edit can save on top; the conflict is
      // reported so the user is not left believing the text is durable.
      // No retry: a conflict is not a transport failure, and retrying the same
      // stale base would just fail again.  Monotonic for the same reason as the
      // success path — never step the known revision backwards.
      adoptRevision(current, error.currentRevision);
      current.dirty = true;
      current.error = 'Draft changed in another tab; this text is not saved yet';
      notify(sessionId);
      return;
    }
    // Transport or server failure: keep the draft dirty and retry a bounded
    // number of times.  After that it stays dirty in memory with `error` set,
    // so the composer can tell the user rather than looping forever.
    current.dirty = true;
    current.retries += 1;
    current.error = (error as Error)?.message || 'Draft not saved';
    if (current.retries < TIMING.RETRY_ATTEMPTS) {
      // Retry sooner than the debounce: the user is already waiting on a save
      // they were just told failed.
      clearTimer(current);
      current.timer = setTimeout(() => {
        current.timer = null;
        void flush(sessionId);
      }, TIMING.RETRY_DELAY_MS);
    } else {
      // Budget spent.  The draft stays dirty on purpose — that is the local
      // copy — but it must not be re-armed automatically, or "bounded retry"
      // becomes an idle poll.  The next real edit, a session switch, or a
      // lifecycle flush calls recordDraft/flushDraft and tries again.
      retryExhausted = true;
      notify(sessionId);
    }
  } finally {
    current.inFlight = false;
  }

  if (current.dirty && !current.timer && !retryExhausted) {
    // Newer local edits landed while this request was in flight, or a retry is
    // already scheduled.  Either way, coalesce them into one follow-up write.
    scheduleFlush(sessionId, current);
  }
}

/**
 * Adopt a server revision, never moving it backwards.
 *
 * GET and PUT race: a slow read issued at revision 0 can land after a write has
 * already advanced the server to 1.  Adopting the read's 0 would make the next
 * write compare against a revision the server has moved past, so it could never
 * succeed — a permanent, self-inflicted conflict loop.  A revision is therefore
 * only accepted if it is at least the highest this tab has already observed.
 *
 * Returns true when the value was adopted, so the caller can tell whether the
 * content it is holding is still current.
 */
function adoptRevision(current: SessionDraftState, revision: number): boolean {
  if (typeof revision !== 'number' || !Number.isInteger(revision) || revision < 0) return false;
  if (current.serverRevision !== null && revision < current.serverRevision) return false;
  current.serverRevision = revision;
  return true;
}

/**
 * Load the persisted draft for one Session, at most once per Session and only
 * when needed.  Both the in-flight promise and the completed result are
 * remembered, so an A→B→A switch reuses the first read instead of issuing one
 * per click, and a Session with no draft is not re-read on every visit.
 *
 * The returned content is bound to the revision it was read at: if a write
 * advanced the server past it while the read was in flight, the content is
 * dropped (returns null) rather than applied over newer state.  The revision
 * itself is still adopted when it is not stale, so a later save is a valid CAS.
 *
 * Returns the server draft, or null when there is none.  Never throws: a failed
 * cold read leaves the composer empty, which is where a first-ever visit
 * already is.  A failed read is not cached, so a later visit can retry.
 */
export async function loadDraft(sessionId: string): Promise<ApiSessionDraft | null> {
  const current = state(sessionId);
  if (current.loadPromise) return current.loadPromise;
  if (current.loaded) return current.loadedDraft;
  const promise = (async () => {
    try {
      const result = await getSessionDraft(sessionId);
      // Monotonic: a stale read cannot roll the CAS base backwards, and a
      // rejected adoption means a write already moved past this revision.
      const adopted = adoptRevision(current, result.revision);
      current.loaded = true;
      if (current.touchedLocally) return null; // local edits win; never clobber
      if (!adopted) {
        // The content belongs to a revision this tab has already superseded.
        // Record that there is nothing durable to restore so a later visit does
        // not re-read, and let the local draft stand.
        current.loadedDraft = null;
        return null;
      }
      current.loadedDraft = result.draft;
      return result.draft;
    } catch {
      return null;
    } finally {
      current.loadPromise = null;
    }
  })();
  current.loadPromise = promise;
  return promise;
}

/**
 * Whether a cold-load response may still be applied to the composer.
 *
 * The guards, in order, cover the three ways a late read can do damage:
 *  1. the user has typed since the load started (local wins);
 *  2. the Session changed while the read was in flight (a response for the
 *     previous Session must not land in the new one — this is the A→B→A case,
 *     where the same Session id is current again but at a later point in
 *     time);
 *  3. nothing was recorded locally, so the server draft is the only content
 *     there is.
 *
 * `requestedSessionId` and `currentSessionId` being equal is not sufficient on
 * its own; a monotonic selection epoch distinguishes the two visits of an
 * A→B→A cycle that share one Session id.
 */
export function canApplyLoadedDraft(
  sessionId: string,
  selectionEpoch: number,
  currentSelectionEpoch: number,
): boolean {
  if (selectionEpoch !== currentSelectionEpoch) return false;
  const current = states.get(sessionId);
  if (!current) return false;
  return !current.touchedLocally && !current.dirty;
}

/**
 * Register the lifecycle flush.  `visibilitychange -> hidden` is the primary
 * trigger because Chromium may freeze the event loop while a page is hidden
 * and `pagehide` may not run at all; `pagehide` is the backup for navigation
 * and close.
 *
 * Neither is a durability guarantee.  The flush is an ordinary `fetch`, and a
 * browser may cancel an in-flight request when a tab closes; the honest loss
 * window is "unsaved text since the last successful save", bounded by
 * `MAX_WAIT_MS` while typing, plus whatever the browser drops at close.  No
 * `sendBeacon` is used: it cannot report success, cannot be retried, and would
 * make a failed save indistinguishable from a delivered one.
 */
export function installDraftLifecycleFlush() {
  if (lifecycleInstalled || typeof document === 'undefined') return;
  lifecycleInstalled = true;
  const onHide = () => {
    if (document.visibilityState === 'hidden') flushAllDrafts();
  };
  document.addEventListener('visibilitychange', onHide);
  window.addEventListener('pagehide', flushAllDrafts);
}
