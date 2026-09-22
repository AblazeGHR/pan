import type { Message, Session } from '@/types';
import type { LiveStreamMeta } from '@/stores/sessionStore';

/**
 * Message ordering / identity helpers shared by the Session transcript paths.
 *
 * The product contract this file encodes (see audit/FIELD_TABLE.md):
 *
 *  - The durable canonical history is **append-only within one `historyEpoch`**
 *    (`packages/core/session.py:append_history`). `history_revision` is the
 *    persisted, Session-level monotonic cursor: both `append_history()` and
 *    `replace_history()` increment it, while `replace_history()` also mints a
 *    new epoch. A cross-epoch page is therefore orderable only when its
 *    revision is strictly newer; equal revisions are ambiguous.
 *  - A history page carries the **absolute storage offset** of its rows
 *    (`start`/`total`), so the loaded window must be tracked by offset — never by
 *    array length, and a tail refresh must never move the oldest loaded offset.
 *  - Real provider rows carry **no id**: CBC emits `{role, content}` and
 *    `_api_history()` synthesises `messageId` from `session + epoch + offset`.
 *    Live CBC/Codex text blocks therefore have no provider identity at all, so a
 *    live row's transient identity is *the current task plus its block slot* —
 *    never `role + body text` (two tasks answering the same text are two rows).
 */

// ── Frontend-only rows ──────────────────────────────────────────────────────
// `[DONE] Task completed` style markers are display state, not server history.
// They must keep their position without being re-inserted by a later merge, and
// they must not be counted as canonical rows when durable coverage is computed.
const localMarkers = new WeakSet<Message>();

export function markLocalMarker(message: Message): void {
  localMarkers.add(message);
}

export function isLocalMarker(message: Message): boolean {
  return localMarkers.has(message);
}

/** Canonical (server-shaped) projection of the rendered transcript. */
export function canonicalHistory(display: readonly Message[]): Message[] {
  return display.filter((message) => !isLocalMarker(message));
}

// ── Durable-row tagging ─────────────────────────────────────────────────────
const durableOffsets = new WeakMap<Message, number>();

export function markDurableRow(message: Message, offset: number): void {
  durableOffsets.set(message, offset);
}

export function durableOffsetOf(message: Message): number | undefined {
  return durableOffsets.get(message);
}

/**
 * True when this row came from a canonical history response. Runtime rows
 * (live blocks, local user rows, display markers) are deliberately not tagged,
 * which is how epoch replacement tells them apart even though
 * `Session.history` mirrors the rendered transcript.
 */
export function isDurableRow(message: Message): boolean {
  return durableOffsets.has(message);
}

// ── Task-scoped live identity ───────────────────────────────────────────────

/**
 * Identity of the task a live frame belongs to. `taskSeq` is the durable
 * cursor; `taskId` only disambiguates the same cursor after a restart.
 */
export function taskScopeKey(
  sessionId: string,
  meta: LiveStreamMeta,
  previous?: LiveStreamMeta,
): string {
  const worker = meta.workerId ?? previous?.workerId ?? 'worker';
  const generation = meta.generation ?? previous?.generation ?? 0;
  const task = meta.taskSeq ?? previous?.taskSeq
    ?? meta.taskId ?? previous?.taskId ?? 'task';
  return `${sessionId}:${worker}:${generation}:${task}`;
}

export interface LiveKeyScope {
  taskKey: string;
  slot: number;
}

/**
 * Projection keys for one live row.
 *
 * Rows with explicit identity use it. Rows without (the real CBC/Codex text
 * shape) use the task scope + block slot: stable while the task streams,
 * distinct across tasks, and never derived from the body text.
 */
export function liveProjectionKeys(message: Message, scope: LiveKeyScope): string[] {
  const identity = [
    ...(message.messageId ? [`message:${message.messageId}`] : []),
    ...(message.blockId ? [`block:${message.blockId}`] : []),
    ...(message.nativeItemId ? [`native:${message.nativeItemId}`] : []),
  ];
  if (identity.length > 0) return identity.map((id) => `${message.role}:${id}`);
  return [`${message.role}:slot:${scope.taskKey}:${scope.slot}`];
}

// ── Loaded durable window ───────────────────────────────────────────────────

export interface HistoryPage {
  history: Message[];
  start: number;
  total: number;
  hasMore: boolean;
  historyEpoch?: string | null;
  /** Persisted Session-level revision, monotonic across epoch replacement. */
  historyRevision?: number;
}

export interface LoadedWindow {
  /** Absolute storage offset → canonical row. */
  rows: Map<number, Message>;
  /** Oldest loaded absolute offset, or null when nothing is loaded. */
  start: number | null;
  /** Exclusive end of the loaded window. */
  end: number | null;
  /** Server-reported row count for the loaded epoch. */
  total: number;
  epoch: string | null;
  revision: number;
}

export function createWindow(): LoadedWindow {
  return { rows: new Map(), start: null, end: null, total: 0, epoch: null, revision: 0 };
}

/** Build a window from a Session's loaded history projection. */
export function windowFromSession(session: Session): LoadedWindow {
  const history = session.history ?? [];
  const start = session.historyStart
    ?? Math.max(0, (session.historyTotal ?? history.length) - history.length);
  const window = createWindow();
  history.forEach((message, index) => {
    const offset = start + index;
    markDurableRow(message, offset);
    window.rows.set(offset, message);
  });
  window.start = history.length > 0 ? start : null;
  window.end = history.length > 0 ? start + history.length : null;
  window.total = session.historyTotal ?? history.length;
  window.epoch = session.historyEpoch ?? null;
  window.revision = session.historyRevision ?? 0;
  return window;
}

export interface WindowMergeResult {
  window: LoadedWindow;
  accepted: boolean;
  /** True when the response replaced the whole epoch (old canonical rows are gone). */
  replacedEpoch: boolean;
  /** Offsets this page added that were not loaded before. */
  newOffsets: number;
  reason?: string;
}

/**
 * Merge one history page into the loaded window.
 *
 *  - A different non-empty epoch replaces the window only when the response
 *    is strictly newer: the old canonical rows belong to a dead identity
 *    scope and must not survive as a tail, while an equal-revision response
 *    cannot prove which epoch is authoritative and is therefore stale.
 *  - Inside one epoch, rows are keyed by absolute offset. Missing offsets are
 *    always filled (append-only epochs legitimately deliver disjoint pages),
 *    but an *overlapping* row is only overwritten by a response whose revision
 *    is at least as new — an older revision must not roll newer content back.
 *  - A page that neither adds an offset nor advances the revision is rejected.
 */
export function mergeWindowPage(
  current: LoadedWindow,
  page: HistoryPage,
): WindowMergeResult {
  const epoch = typeof page.historyEpoch === 'string' && page.historyEpoch
    ? page.historyEpoch
    : null;
  const revision = typeof page.historyRevision === 'number' ? page.historyRevision : 0;
  const rows = page.history ?? [];

  // An epoch change is a full-history boundary, but it is only authoritative
  // when the response is strictly newer on the persisted Session-level
  // revision cursor. A page from an *older* epoch (lower revision), or an
  // equal-revision epoch whose ordering is unknowable, must be rejected
  // outright — accepting every epoch change is what let an old epoch's page
  // roll a newer canonical projection back.
  if (epoch && current.epoch && epoch !== current.epoch) {
    if (revision <= current.revision) {
      return {
        window: current,
        accepted: false,
        replacedEpoch: false,
        newOffsets: 0,
        reason: revision === current.revision
          ? 'ambiguous-epoch-page'
          : 'older-epoch-page',
      };
    }
    const next = createWindow();
    next.epoch = epoch;
    next.revision = revision;
    next.total = page.total;
    rows.forEach((message, index) => {
      const offset = page.start + index;
      markDurableRow(message, offset);
      next.rows.set(offset, message);
    });
    next.start = rows.length > 0 ? page.start : null;
    next.end = rows.length > 0 ? page.start + rows.length : null;
    return { window: next, accepted: true, replacedEpoch: true, newOffsets: rows.length };
  }

  const next: LoadedWindow = {
    ...current,
    rows: new Map(current.rows),
  };
  if (epoch) next.epoch = epoch;
  next.revision = Math.max(current.revision, revision);
  next.total = Math.max(current.total, page.total);

  let changed = false;
  let newOffsets = 0;
  let blockedOverlap = false;
  rows.forEach((message, index) => {
    const offset = page.start + index;
    const existing = next.rows.get(offset);
    if (existing === undefined) {
      markDurableRow(message, offset);
      next.rows.set(offset, message);
      changed = true;
      newOffsets += 1;
      return;
    }
    if (revision < current.revision) {
      // Older revision overlapping newer content: keep the newer row.
      blockedOverlap = true;
      return;
    }
    if (existing !== message) {
      markDurableRow(message, offset);
      next.rows.set(offset, message);
      changed = true;
    }
  });

  if (changed || next.total !== current.total) {
    const offsets = [...next.rows.keys()];
    next.start = offsets.length > 0 ? Math.min(...offsets) : null;
    next.end = offsets.length > 0 ? Math.max(...offsets) + 1 : null;
    return { window: next, accepted: true, replacedEpoch: false, newOffsets };
  }

  return {
    window: current,
    accepted: false,
    replacedEpoch: false,
    newOffsets: 0,
    reason: blockedOverlap ? 'older-revision-overlap' : 'no-new-content',
  };
}

/** Durable rows of the window, ordered by absolute offset. */
export function windowRows(window: LoadedWindow): Message[] {
  return [...window.rows.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, message]) => message);
}
