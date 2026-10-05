import type { Message } from '@/types';
import type { AppSettings } from '@/stores/appSettingsStore';
import type { CanonicalRow } from '@/stores/messageOrdering';

/** Meta-agent orchestration messages (`worker_send` auto-prepends this marker). */
export const META_AGENT_PREFIX = '////by agent';
/** Task-agent completion reports. */
export const TASK_AGENT_PREFIX = '@@@@by agent';
/** QQ-injected messages (inbox reminders / subscription pushes). */
export const QQ_PREFIX = '@@@@by qq';

/** Current production header and the historical System header. Match a whole
 * marker after leading whitespace, never a mention or a longer word. */
const SYSTEM_HEADER = /^\/\/\/\/by (?:pan system|system)(?=\s|:|$)/;

function hasSystemHeader(message: Message): boolean {
  return (message.role === 'user' || message.role === 'assistant' || message.role === 'system')
    && SYSTEM_HEADER.test(message.content.trimStart());
}

export type MessageVisibilitySettings = Pick<
  AppSettings,
  'showMetaAgent' | 'showTaskAgent' | 'showQQ'
>;

/**
 * Frontend-only display filter. Drops messages whose source-marker prefix is
 * hidden by a disabled toggle. The input array is never mutated — the store
 * keeps every message and toggling a switch back on restores them.
 */
export function filterVisibleMessages(
  messages: Message[],
  settings: MessageVisibilitySettings,
): Message[] {
  const { showMetaAgent, showTaskAgent, showQQ } = settings;
  return messages.filter((m) => {
    const content = m.content.trimStart();
    if (!showMetaAgent && content.startsWith(META_AGENT_PREFIX)) return false;
    if (!showTaskAgent && content.startsWith(TASK_AGENT_PREFIX)) return false;
    if (!showQQ && content.startsWith(QQ_PREFIX)) return false;
    return true;
  });
}


/**
 * Navigation marker kinds (the rail consumption contract).
 *
 * - `user`     — user-role rows without structured agent provenance
 *   (browser sends, automation fills, prompt injection, QQ, user-typed
 *   markers).
 * - `worker`   — TA report (`@@@@by agent`); existing value kept verbatim.
 * - `system`   — current `////by pan system` or legacy `////by system`
 *   header on a user/assistant/system body row, independent of provenance.
 * - `maAssign` — MA assign: formal dispatch — `role: "user"`,
 *   `source: "agent"`, no inherited task id and no `////by agent` prefix
 *   (mirrors the `ma-assign` label tag).
 * - `maMsg`    — MA msg: every other agent-sourced row — the `////by agent`
 *   identity prefix that `agent_send` / `agent_send_force` prepend, or an
 *   inherited-task-id follow-up (`taskIdSource: "active"`).
 *
 * MA classification is structured: `source: "agent"` is required for both MA
 * kinds; a body that merely starts with `////by agent` never classifies.
 */
export type QuickJumpKind = 'user' | 'worker' | 'system' | 'maAssign' | 'maMsg';

export interface QuickJumpMessage {
  message: Message;
  /** Index in the filtered message list used by ChatMessages. */
  index: number;
  kind: QuickJumpKind;
  preview: string;
}

/** Compact target stored by the full-history navigation index. fromEnd is
 * stable while older pages are prepended to the chat window. */
export interface QuickJumpIndexItem {
  fromEnd: number;
  kind: QuickJumpKind;
  preview: string;
}

/**
 * Return the marker category used by the quick-location rail, if any.
 *
 * MA classification basis — structured agent provenance only.  A body that
 * merely starts with `////by agent` is never enough: a user-typed or
 * source-less row with that literal text stays `user` (the existing
 * `filterVisibleMessages` prefix rule is kept for visibility toggles and is
 * deliberately not reused here).
 * - `@@@@by agent` → `worker` (TA report, the old judgment; checked first and
 *   role-independent because some adapters serialize reports as user rows).
 * - System header on a body row → `system`, before any role/source split.
 *   Leading whitespace is ignored; current and legacy headers are accepted.
 * - `role: "user"` + `source: "agent"` → inside the confirmed provenance,
 *   the send/follow-up split: the `////by agent` identity prefix of
 *   `agent_send` / `agent_send_force`, or an inherited
 *   `taskIdSource: "active"` follow-up, yields `maMsg`; otherwise
 *   `maAssign`.  The prefix only splits an already-confirmed source — it
 *   never proves the source itself.  Invariant: `maAssign` ⇔
 *   `getMessageSourceTag(...)` is `'ma-assign'`.
 * - any other `user` row → `user` (browser sends, scheduler/background jobs,
 *   prompt injection, QQ, user-typed markers); non-user rows without the
 *   report prefix → `null`.
 *
 * Indistinguishable boundary (documented on purpose): an env-less
 * `agent_send` / `agent_send_force` without the identity prefix and without
 * an inherited active task id carries exactly the same durable fields as an
 * assign and classifies as `maAssign`; only the prefix and the inherited id
 * separate send/force from assign.  History persisted before the structured
 * source fields existed carries no provenance and stays `user` / `null` —
 * the current managed relation is never used to guess an old origin.
 */
export function getQuickJumpKind(message: Message): QuickJumpKind | null {
  const content = message.content.trimStart();
  // A task-agent report wins over the role because reports can be serialized
  // as user messages by some adapters.
  if (content.startsWith(TASK_AGENT_PREFIX)) return 'worker';
  if (hasSystemHeader(message)) return 'system';
  if (message.role !== 'user') return null;
  if (message.source === 'agent') {
    // Within confirmed agent provenance, the send/follow-up markers split
    // messages from assigns; the prefix never proves the source itself.
    return content.startsWith(META_AGENT_PREFIX) || message.taskIdSource === 'active'
      ? 'maMsg'
      : 'maAssign';
  }
  return 'user';
}

/** Durable source tag rendered next to a message body. */
export type MessageSourceTag = 'ta-report' | 'system' | 'ma-assign';

/**
 * Classify the source tag for a message body, or null when it carries none.
 * The navigation kinds mirror this determination, so the body pill and the
 * rail tooltip can never disagree: `ma-assign` ⇔ kind `maAssign`,
 * `ta-report` ⇔ kind `worker`, `system` ⇔ kind `system`.
 *
 * - `ta-report` — the task-agent completion report marker (`@@@@by agent`).
 *   Kept exactly as before: prefix-based and role-independent because some
 *   adapters serialize reports as user rows.
 * - `system` — current/legacy System header on a body row; checked after
 *   TA report and before MA assign, including histories without source fields.
 * - `ma-assign` — a formal dispatch into this Session: `role: "user"`,
 *   structured `source: "agent"` (编排注入), no inherited task id
 *   (`taskIdSource: "active"`, backend `_is_formal_task_item`) and no
 *   `////by agent` identity prefix (that prefix marks `agent_send` /
 *   `agent_send_force` messages instead).
 *
 * Browser messages (`user`), scheduler/background jobs (`automation`),
 * prompt injection (`system_prompt`) and report/notice rows (`report`) can
 * never carry the MA assign tag; literal `////by agent` text is never treated as an
 * assign.  History persisted before the structured fields existed stays
 * without an MA label on purpose: the current managed relation is never used to guess
 * an old origin.
 */
export function getMessageSourceTag(message: Message): MessageSourceTag | null {
  const content = message.content.trimStart();
  if (content.startsWith(TASK_AGENT_PREFIX)) return 'ta-report';
  if (hasSystemHeader(message)) return 'system';
  if (
    message.role === 'user'
    && message.source === 'agent'
    && message.taskIdSource !== 'active'
    && !content.startsWith(META_AGENT_PREFIX)
  ) {
    return 'ma-assign';
  }
  return null;
}

/** Remove transport/source headers before showing a compact hover preview. */
export function getQuickJumpPreview(content: string, maxLength = 120): string {
  const trimmed = content.trimStart();
  // System headers carry no sender metadata. Preserve inline text after an
  // optional colon as well as the body after the production newline header.
  const withoutHeader = (SYSTEM_HEADER.test(trimmed)
    ? trimmed.replace(SYSTEM_HEADER, '').replace(/^[^\S\r\n]*:/, '')
    : trimmed.replace(
        /^(?:@@@@by agent|\/\/\/\/by agent|@@@@by qq)\s*:\s*[^\r\n]*(?:\r?\n|$)/,
        '',
      )
  ).trimStart();
  const normalized = withoutHeader.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}\u2026`;
}

/** Build compact navigation targets for a server history page. start is
 * the absolute index of the first message in messages. */
export function getQuickJumpIndexItems(
  messages: Message[],
  total: number,
  start: number,
  settings: MessageVisibilitySettings,
): QuickJumpIndexItem[] {
  return messages.flatMap((message, index) => {
    if (filterVisibleMessages([message], settings).length === 0) return [];
    const kind = getQuickJumpKind(message);
    return kind
      ? [{
          fromEnd: total - 1 - (start + index),
          kind,
          preview: getQuickJumpPreview(message.content),
        }]
      : [];
  });
}

/**
 * Build compact navigation targets from canonical rows that already know their
 * own absolute history offset.
 *
 * The rendered transcript interleaves canonical history rows with local
 * display markers (`[DONE] Task completed`) and runtime rows, so an array
 * position is not a canonical offset: counting positions instead of reading
 * offsets shifted every `fromEnd` behind a terminal bar and made the rail jump
 * to a neighbouring message.
 *
 * The caller supplies only rows whose offset `markDurableRow` proved, so a
 * terminal status bar is excluded for lack of a canonical offset — not by
 * matching its text. Real TA reports, System notices and any body that merely
 * contains "task completed" keep their marker-based classification and stay
 * navigable. The visible filter is still applied per row, so the
 * showMetaAgent/showTaskAgent/showQQ toggles behave exactly as before.
 */
export function getQuickJumpIndexItemsByOffset(
  rows: readonly CanonicalRow[],
  total: number,
  settings: MessageVisibilitySettings,
): QuickJumpIndexItem[] {
  return rows.flatMap(({ offset, message }) => {
    if (filterVisibleMessages([message], settings).length === 0) return [];
    const kind = getQuickJumpKind(message);
    return kind
      ? [{
          fromEnd: total - 1 - offset,
          kind,
          preview: getQuickJumpPreview(message.content),
        }]
      : [];
  });
}

/** Build the visible navigation targets shown by the navigation rail. */
export function getQuickJumpMessages(
  messages: Message[],
  settings: MessageVisibilitySettings,
): QuickJumpMessage[] {
  return filterVisibleMessages(messages, settings).flatMap((message, index) => {
    const kind = getQuickJumpKind(message);
    return kind
      ? [{
          message,
          index,
          kind,
          preview: getQuickJumpPreview(message.content),
        }]
      : [];
  });
}
