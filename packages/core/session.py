"""Session store — persistent, UUID-keyed, independent of Worker lifecycle.

概念模型（agent-naming 确立）：Session = Agent —— 即逻辑编排对象：持久身份
（收件箱 queue_pending、agentLevel、managedBy 链）都在这里；Worker（worker.py）
只是本 Session 名下临时的 CLI 进程实例，可随时重建。外部编排语义（MCP 的
agent_* 工具、/api/send）以 Session 为寻址目标。

Each session is stored as data/sessions/<id>.json.
The ID format is ses_<16-hex-chars> (e.g. ses_a1b2c3d4e5f67890).

Prompt schema: JSON persists original_prompt and handoff_prompt. system_prompt
is a computed compatibility property and to_dict() export alias. Constructors
still accept the legacy keyword. If original_prompt is absent, legacy text is
preserved verbatim as the baseline, even if it already contains old handoffs;
there is no reliable way to recover its author-intended original by splitting
headings. Explicit original_prompt (including null/empty) takes precedence.
Loading never rewrites files; the next save migrates to the canonical fields.
New JSON requires a reader supporting this schema; old Pan versions that reject
unknown Session fields cannot read it. No lossy downgrade is attempted.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from pathlib import Path

from packages.core.notifications import normalize_notification_settings

SESSION_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sessions"

# 增量持久化（方案 4）：history 落盘到独立的 <id>.history.jsonl 追加文件，
# 不再每次全量重写主文件。主文件只含元数据 + 尾部 history（供人工查看/旧读者
# 兼容），jsonl 存在时加载一律以 jsonl 为 history 权威来源。
_MAIN_HISTORY_TAIL = 20          # 主文件内保留的尾部 history 条数（常量开销）
# Long-running sessions must not make a metadata read or a duplicate receipt
# check proportional to their complete conversation history.  These are
# compatibility-preserving bounds: pending/uncertain queue rows are never
# evicted, while completed receipt metadata may expire after this window.
HISTORY_PAGE_MAX = 200
# Search jumps opt in to larger bounded pages; ordinary history callers stay at 200.
HISTORY_SEARCH_JUMP_PAGE_MAX = 1000
ACCEPTED_INPUT_ID_MAX = 256
QUEUE_RECEIPT_MAX_ENTRIES = 2048
QUEUE_RECEIPT_TTL_SEC = 7 * 24 * 60 * 60
QUEUE_IDEMPOTENCY_INDEX_MAX_ENTRIES = 4096
QUEUE_RECEIPT_MIN_RETRY_SEC = 60.0
_QUEUE_TERMINAL_STATES = frozenset({"sent_to_cli", "deleted"})
# ``worker.result`` replay is a bounded recovery aid, not an event log.  The
# durable cursor is kept on the Session so a Pan restart cannot make a
# reconnect mistake a newer result for an older one.  Results outside this
# window require the resync snapshot rather than an unbounded replay cache.
RESULT_REPLAY_MAX_ENTRIES = 64
# Locking contract:
#   _STORE_LOCK serializes global Session-index operations (names, handoff,
#   relationships, and ordering).  A _SessionSaveState serializes file writes
#   for one Session only.  Save code never acquires _STORE_LOCK after entering
#   its per-Session state, so the only nested order is _STORE_LOCK -> state.
#   In particular, a slow history/metadata flush for Session A does not hold
#   the store lock and cannot delay Session B's flush.
_STORE_LOCK = threading.RLock()
_PIN_LOCK = threading.RLock()
_PIN_STATE_CACHE: tuple[Path, int, tuple[str, ...]] | None = None
_PIN_STATE_CACHE_STAMP: tuple[int, int, int] | None = None
_SAVE_STATES: dict[str, "_SessionSaveState"] = {}
_MAX_SAVE_DIAGNOSTIC_SESSIONS = 128
_newline_terminated_jsonl: set[str] = set()  # 进程内已知以 \n 结尾的 jsonl 路径（热路径跳过探测）


@dataclass
class _SessionSaveState:
    """Ordered, per-Session persistence gate and bounded counters.

    ``save_async`` requests receive a ticket before entering the executor.
    This gives one Session FIFO ordering even when the default executor starts
    later requests first, while allowing different Session IDs to use separate
    executor threads.  The ticket is always retired in a ``finally`` block;
    cancellation of the awaiting coroutine is shielded until the underlying
    filesystem operation releases this state.

    Only counters/timestamps are retained here.  No history, queue text, or
    exception message is kept in diagnostics.
    """

    condition: threading.Condition = field(default_factory=threading.Condition)
    next_ticket: int = 0
    serving_ticket: int = 0
    pending: int = 0
    active: bool = False
    cancelled: set[int] = field(default_factory=set)
    last_wait_ms: float = 0.0
    last_flush_ms: float = 0.0
    last_pending_age_ms: float = 0.0
    flush_count: int = 0
    failure_count: int = 0
    last_error_type: str | None = None
    last_completed_at: str | None = None
    last_activity: float = field(default_factory=time.monotonic)

    def reserve(self) -> tuple[int, float]:
        now = time.monotonic()
        with self.condition:
            ticket = self.next_ticket
            self.next_ticket += 1
            self.pending += 1
            self.last_activity = now
            return ticket, now

    def _skip_cancelled_locked(self) -> None:
        while self.serving_ticket in self.cancelled:
            self.cancelled.remove(self.serving_ticket)
            self.serving_ticket += 1

    def begin(self, ticket: int, enqueued_at: float) -> float:
        with self.condition:
            while ticket != self.serving_ticket:
                self.condition.wait()
            self.pending = max(0, self.pending - 1)
            self.active = True
            now = time.monotonic()
            self.last_activity = now
            wait_ms = max(0.0, (now - enqueued_at) * 1000.0)
            self.last_wait_ms = wait_ms
            self.last_pending_age_ms = wait_ms
            return wait_ms

    def cancel_before_begin(self, ticket: int) -> None:
        """Retire a ticket if executor submission fails before it starts."""
        with self.condition:
            self.pending = max(0, self.pending - 1)
            self.cancelled.add(ticket)
            self._skip_cancelled_locked()
            self.last_activity = time.monotonic()
            self.condition.notify_all()

    def finish(self, *, flush_ms: float, error_type: str | None) -> None:
        with self.condition:
            self.active = False
            self.serving_ticket += 1
            self._skip_cancelled_locked()
            self.last_flush_ms = max(0.0, flush_ms)
            self.flush_count += 1
            if error_type is not None:
                self.failure_count += 1
                self.last_error_type = error_type
            else:
                self.last_error_type = None
            self.last_completed_at = datetime.now().isoformat()
            self.last_activity = time.monotonic()
            self.condition.notify_all()

    def snapshot(self) -> dict:
        with self.condition:
            return {
                "queueDepth": self.pending,
                "active": self.active,
                "lastWaitMs": round(self.last_wait_ms, 3),
                "lastFlushMs": round(self.last_flush_ms, 3),
                "lastPendingAgeMs": round(self.last_pending_age_ms, 3),
                "flushCount": self.flush_count,
                "failureCount": self.failure_count,
                "lastErrorType": self.last_error_type,
                "lastCompletedAt": self.last_completed_at,
                "_lastActivity": self.last_activity,
            }


def _store_serialized(func):
    """Run a low-frequency cross-Session/index operation under _STORE_LOCK."""
    @wraps(func)
    def locked(*args, **kwargs):
        with _STORE_LOCK:
            return func(*args, **kwargs)
    return locked


def _reserve_save_ticket(session_id: str) -> tuple[_SessionSaveState, int, float]:
    """Atomically obtain a state and reserve its next FIFO ticket.

    Keeping lookup and reservation under ``_STORE_LOCK`` lets ``delete`` retire
    an idle state without a stale caller reserving on the old state after it has
    been removed from the registry.
    """
    with _STORE_LOCK:
        state = _SAVE_STATES.get(session_id)
        if state is None:
            state = _SessionSaveState()
            _SAVE_STATES[session_id] = state
        ticket, enqueued_at = state.reserve()
        return state, ticket, enqueued_at


def save_diagnostics(session_id: str | None = None, *, limit: int = 32) -> dict:
    """Return bounded save queue/latency counters without message contents.

    With ``session_id`` the result is the exact per-Session snapshot.  Without
    it, at most ``limit`` recently active Session snapshots are returned along
    with aggregate queue/active counts.  This is intentionally process-local:
    the counters diagnose contention and are not durable Session state.
    """
    if session_id is not None:
        with _STORE_LOCK:
            state = _SAVE_STATES.get(session_id)
        if state is None:
            return {
                "sessionId": session_id,
                "queueDepth": 0,
                "active": False,
                "lastWaitMs": 0.0,
                "lastFlushMs": 0.0,
                "lastPendingAgeMs": 0.0,
                "flushCount": 0,
                "failureCount": 0,
                "lastErrorType": None,
                "lastCompletedAt": None,
            }
        result = state.snapshot()
        result.pop("_lastActivity", None)
        return {"sessionId": session_id, **result}

    try:
        bounded_limit = max(1, min(int(limit), _MAX_SAVE_DIAGNOSTIC_SESSIONS))
    except (TypeError, ValueError):
        bounded_limit = 32
    with _STORE_LOCK:
        states = list(_SAVE_STATES.items())
    snapshots = [
        {"sessionId": sid, **state.snapshot()}
        for sid, state in states
    ]
    snapshots.sort(key=lambda item: item.get("_lastActivity", 0.0), reverse=True)
    visible = snapshots[:bounded_limit]
    for item in visible:
        item.pop("_lastActivity", None)
    return {
        "sessions": visible,
        "totalTracked": len(snapshots),
        "totalQueued": sum(item["queueDepth"] for item in snapshots),
        "activeSessions": sum(1 for item in snapshots if item["active"]),
    }

# Session-list previews are a persisted projection, not a second history
# representation.  Keep this bound compatible with the old ``lastMessage``
# contract, but never project attachment/editor links here: those helpers may
# read or mutate the attachment registry and are intentionally history-view
# only.
SUMMARY_PREVIEW_MAX = 200
_SUMMARY_MAIN_ROLES = frozenset({"user", "assistant", "system"})
_SUMMARY_AUXILIARY_ROLES = frozenset({"thinking", "tool"})
_SUMMARY_PROJECTION_KEYS = (
    "revision", "last_user_preview", "last_assistant_preview",
    "last_display_preview", "last_system_preview", "last_thinking_preview",
    "last_tool_preview", "last_main_role", "history_total", "updated_at",
)
_SUMMARY_PROJECTION_ALIASES = {
    "revision": "summaryRevision",
    "last_user_preview": "lastUserPreview",
    "last_assistant_preview": "lastAssistantPreview",
    "last_display_preview": "lastDisplayPreview",
    "last_system_preview": "lastSystemPreview",
    "last_thinking_preview": "lastThinkingPreview",
    "last_tool_preview": "lastToolPreview",
    "last_main_role": "lastMainRole",
    "history_total": "historyTotal",
    "updated_at": "updatedAt",
}
_SUMMARY_PROJECTION_DATA_ALIASES = ("summaryProjection", "summary")


def _canonicalize_summary_projection_data(data: dict) -> dict:
    """Extract the top-level projection alias into canonical storage.

    Older/intermediate writers used ``summaryProjection`` or ``summary`` as
    the field name.  Keep unrelated forward-compatible fields untouched, but
    consume both known aliases so a later ``Session(**data)`` cannot receive
    an unexpected constructor keyword.
    """
    if "summary_projection" not in data:
        for alias in _SUMMARY_PROJECTION_DATA_ALIASES:
            if alias in data:
                data["summary_projection"] = data[alias]
                break
    for alias in _SUMMARY_PROJECTION_DATA_ALIASES:
        data.pop(alias, None)
    return data


def _projection_value(value: dict, key: str, default=None):
    """Read one projection field in either persisted spelling."""
    if key in value:
        return value[key]
    return value.get(_SUMMARY_PROJECTION_ALIASES[key], default)


def _is_nonnegative_int(value: object) -> bool:
    # bool is an int subclass, but it is never a valid projection counter.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_complete_summary_projection(value: object) -> bool:
    """Return whether a durable projection is authoritative.

    Shape alone is intentionally insufficient.  Every field needed to render
    the bounded summary must be present with its canonical type, including an
    explicit integer total (zero is meaningful).  This is shared by cold
    loads, full loads, saves, and the repair worker so an interrupted upgrade
    cannot be mistaken for a valid projection.
    """
    if not isinstance(value, dict):
        return False
    for key in _SUMMARY_PROJECTION_KEYS:
        if key not in value and _SUMMARY_PROJECTION_ALIASES[key] not in value:
            return False
    if not _is_nonnegative_int(_projection_value(value, "revision")):
        return False
    if not _is_nonnegative_int(_projection_value(value, "history_total")):
        return False
    for key in _SUMMARY_PROJECTION_KEYS:
        if key in {"revision", "history_total"}:
            continue
        candidate = _projection_value(value, key)
        if not isinstance(candidate, str):
            return False
    return _projection_value(value, "last_main_role") in (_SUMMARY_MAIN_ROLES | {""})


def _summary_preview(message: object) -> str:
    """Return a bounded, raw text preview without interpreting its contents."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, str):
        return ""
    # Deliberately slice the stored text.  No markdown/link parsing, pathlib
    # access, stat, registry lookup, or dynamic editor-reference registration
    # belongs on a Session summary path.
    return content[:SUMMARY_PREVIEW_MAX]


def _empty_summary_projection(*, revision: int = 0,
                              history_total: int | None = 0,
                              updated_at: str = "") -> dict:
    return {
        "revision": max(0, int(revision or 0)),
        "last_user_preview": "",
        "last_assistant_preview": "",
        "last_display_preview": "",
        "last_system_preview": "",
        "last_thinking_preview": "",
        "last_tool_preview": "",
        "last_main_role": "",
        "history_total": history_total,
        "updated_at": updated_at or "",
    }


def _normalize_summary_projection(value: object, *, fallback_updated_at: str = "") -> dict | None:
    """Normalize the optional durable projection without doing any I/O."""
    if not isinstance(value, dict):
        return None
    # Accept both the canonical snake_case storage spelling and a hand-authored
    # camelCase snapshot from an intermediate build.
    def read(name: str, camel: str, default):
        return value[name] if name in value else value.get(camel, default)

    raw_revision = read("revision", "summaryRevision", 0)
    revision = raw_revision if _is_nonnegative_int(raw_revision) else 0

    projection = _empty_summary_projection(
        revision=revision,
        history_total=read("history_total", "historyTotal", None),
        updated_at=read("updated_at", "updatedAt", fallback_updated_at) or "",
    )
    for snake, camel in (
        ("last_user_preview", "lastUserPreview"),
        ("last_assistant_preview", "lastAssistantPreview"),
        ("last_display_preview", "lastDisplayPreview"),
        ("last_system_preview", "lastSystemPreview"),
        ("last_thinking_preview", "lastThinkingPreview"),
        ("last_tool_preview", "lastToolPreview"),
    ):
        candidate = read(snake, camel, "")
        projection[snake] = candidate[:SUMMARY_PREVIEW_MAX] if isinstance(candidate, str) else ""
    main_role = read("last_main_role", "lastMainRole", "")
    projection["last_main_role"] = main_role if main_role in _SUMMARY_MAIN_ROLES else ""
    total = projection["history_total"]
    if total is not None:
        projection["history_total"] = (
            total if _is_nonnegative_int(total) else None
        )
    return projection


def _summary_projection_from_history(history: list[dict], *, revision: int = 0,
                                     updated_at: str = "") -> dict:
    """Build a projection for an explicitly loaded history.

    This is used at Session construction/reimport time, never by the summary
    API for an otherwise cold Session.  Its cost is therefore proportional to
    the history operation that explicitly supplied the list.
    """
    projection = _empty_summary_projection(
        revision=revision, history_total=0, updated_at=updated_at,
    )
    for message in history:
        _apply_summary_message(projection, message, bump=False)
    if history:
        projection["revision"] = max(projection["revision"], 1)
    return projection


def _apply_summary_message(projection: dict, message: object, *, bump: bool = True) -> bool:
    """Apply one appended message to the bounded projection in O(1)."""
    if not isinstance(message, dict):
        return False
    role = str(message.get("role") or "").strip().lower()
    preview = _summary_preview(message)
    total = projection.get("history_total")
    projection["history_total"] = (total + 1) if isinstance(total, int) else None

    changed = True
    if role == "user":
        projection["last_user_preview"] = preview
        projection["last_display_preview"] = preview
        projection["last_main_role"] = role
    elif role == "assistant":
        projection["last_assistant_preview"] = preview
        projection["last_display_preview"] = preview
        projection["last_main_role"] = role
    elif role == "system":
        projection["last_system_preview"] = preview
        projection["last_display_preview"] = preview
        projection["last_main_role"] = role
    elif role == "thinking":
        projection["last_thinking_preview"] = preview
        # Thinking/tool rows are auxiliary. They may be used only before any
        # user/assistant/system preview exists and must never overwrite the
        # established assistant (or other main-role) preview.
        if not projection.get("last_main_role"):
            projection["last_display_preview"] = preview
    elif role == "tool":
        projection["last_tool_preview"] = preview
        if not projection.get("last_main_role"):
            projection["last_display_preview"] = preview
    else:
        # Unknown roles still count toward historyTotal, but cannot become a
        # user-visible summary preview.
        changed = False

    if bump:
        projection["revision"] = max(0, int(projection.get("revision") or 0)) + 1
        projection["updated_at"] = datetime.now().isoformat()
    return True


_PAN_MESSAGE_ID_PATTERN = re.compile(
    r"pan:(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def is_pan_message_id(value: object) -> bool:
    """Return whether ``value`` is one of Pan's durable message identities."""
    # Same canonical formats as UUID(...).hex/str, without constructing three
    # UUID/string objects for every history row visited by a cold search.
    return isinstance(value, str) and _PAN_MESSAGE_ID_PATTERN.fullmatch(value) is not None


def _is_searchable_history_body(message: object) -> bool:
    """Give persisted searchable text blocks an identity, not transport metadata."""
    if not isinstance(message, dict):
        return False
    if message.get("role") not in ("user", "assistant", "tool", "thinking"):
        return False
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    # A fresh stream Session may inject this queue item as a user message.
    # It is system context, not a user-authored searchable body.
    return message.get("source") != "system_prompt"


def _native_item_identity(message: dict) -> tuple[str, str] | None:
    value = message.get("nativeItemId")
    if isinstance(value, str) and value:
        native_id = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        native_id = str(value)
    else:
        return None
    return str(message.get("role") or ""), native_id


def _history_body_fingerprint(message: dict) -> tuple[str, str]:
    # Exact role/content matching preserves text as provider data; order and
    # neighboring stable rows below decide which duplicate occurrence is safe.
    return str(message.get("role") or ""), message["content"]


def _remember_steer_client_identity(message: dict) -> None:
    prior_id = message.get("messageId")
    if (isinstance(prior_id, str) and prior_id.startswith("steer:")
            and prior_id and not message.get("clientMessageId")):
        # The browser uses steer:<caller token> only while its optimistic row
        # is in flight. Keep it as an alias while assigning the durable Pan id.
        message["clientMessageId"] = prior_id


def _new_pan_message_id() -> str:
    return f"pan:{uuid.uuid4().hex}"


def assign_pan_message_ids(
    history: list[dict], *, previous_history: list[dict] | None = None,
) -> list[dict]:
    """Copy imported/forked history and give searchable text blocks Pan IDs.

    Existing Pan IDs survive a native reimport. Stable provider item IDs are
    the first match key; exact role/content is used only when the match is
    unique or when duplicate occurrences retain the same ordered sequence
    between unambiguous neighbors. Ambiguous rows get fresh IDs. Provider
    ``nativeItemId`` remains evidence and is never promoted to ``messageId``.

    This helper is intentionally called only by import/fork boundaries. It
    does not migrate ordinary legacy history while loading or saving it.
    """
    rows = [dict(row) if isinstance(row, dict) else row for row in (history or [])]
    previous = previous_history or []
    new_body = [index for index, row in enumerate(rows)
                if _is_searchable_history_body(row)]
    old_body = [index for index, row in enumerate(previous)
                if _is_searchable_history_body(row)]
    if not new_body:
        return rows

    old_unmatched = set(old_body)
    new_unmatched = set(new_body)
    pairs: list[tuple[int, int]] = []

    def pair_unique_by(key_for):
        old_keys: dict[object, list[int]] = defaultdict(list)
        new_keys: dict[object, list[int]] = defaultdict(list)
        for index in old_unmatched:
            key = key_for(previous[index])
            if key is not None:
                old_keys[key].append(index)
        for index in new_unmatched:
            key = key_for(rows[index])
            if key is not None:
                new_keys[key].append(index)
        matches = []
        for key, old_indexes in old_keys.items():
            new_indexes = new_keys.get(key, [])
            if len(old_indexes) == len(new_indexes) == 1:
                matches.append((old_indexes[0], new_indexes[0]))
        for old_index, new_index in matches:
            pairs.append((old_index, new_index))
            old_unmatched.discard(old_index)
            new_unmatched.discard(new_index)

    pair_unique_by(_native_item_identity)

    def content_key_compatible(old_index: int, new_index: int) -> bool:
        old_native = _native_item_identity(previous[old_index])
        new_native = _native_item_identity(rows[new_index])
        return not (old_native and new_native and old_native != new_native)

    old_content: dict[object, list[int]] = defaultdict(list)
    new_content: dict[object, list[int]] = defaultdict(list)
    for index in old_unmatched:
        old_content[_history_body_fingerprint(previous[index])].append(index)
    for index in new_unmatched:
        new_content[_history_body_fingerprint(rows[index])].append(index)
    unique_content_pairs = []
    for key, old_indexes in old_content.items():
        new_indexes = new_content.get(key, [])
        if (len(old_indexes) == len(new_indexes) == 1
                and content_key_compatible(old_indexes[0], new_indexes[0])):
            unique_content_pairs.append((old_indexes[0], new_indexes[0]))
    for old_index, new_index in unique_content_pairs:
        pairs.append((old_index, new_index))
        old_unmatched.discard(old_index)
        new_unmatched.discard(new_index)

    # Match repeated text by appearance order only inside stable, monotonic
    # neighbors. If the unique anchors cross, their surrounding duplicate
    # occurrences cannot be assigned conservatively.
    anchors = sorted(pairs, key=lambda pair: pair[1])
    monotonic = all(
        anchors[index - 1][0] < anchors[index][0]
        for index in range(1, len(anchors))
    )
    if monotonic:
        boundaries = [(-1, -1), *anchors, (len(previous), len(rows))]
        for (old_left, new_left), (old_right, new_right) in zip(
            boundaries, boundaries[1:],
        ):
            old_gap = sorted(index for index in old_unmatched
                             if old_left < index < old_right)
            new_gap = sorted(index for index in new_unmatched
                             if new_left < index < new_right)
            old_counts = Counter(
                _history_body_fingerprint(previous[index]) for index in old_gap
            )
            new_counts = Counter(
                _history_body_fingerprint(rows[index]) for index in new_gap
            )
            common = {
                key for key, count in old_counts.items()
                if count > 0 and new_counts.get(key) == count
            }
            old_order = [index for index in old_gap
                         if _history_body_fingerprint(previous[index]) in common]
            new_order = [index for index in new_gap
                         if _history_body_fingerprint(rows[index]) in common]
            old_keys = [_history_body_fingerprint(previous[index])
                        for index in old_order]
            new_keys = [_history_body_fingerprint(rows[index])
                        for index in new_order]
            if old_keys != new_keys:
                continue
            for old_index, new_index in zip(old_order, new_order):
                if content_key_compatible(old_index, new_index):
                    pairs.append((old_index, new_index))
                    old_unmatched.discard(old_index)
                    new_unmatched.discard(new_index)

    old_id_counts = Counter()
    for index in old_body:
        row = previous[index]
        if isinstance(row, dict) and is_pan_message_id(row.get("messageId")):
            old_id_counts[row["messageId"]] += 1
    new_id_counts = Counter()
    for index in new_body:
        row = rows[index]
        if isinstance(row, dict) and is_pan_message_id(row.get("messageId")):
            new_id_counts[row["messageId"]] += 1
    assigned: dict[int, str] = {}
    used_ids: set[str] = set()
    for old_index, new_index in pairs:
        old_row = previous[old_index]
        if not isinstance(old_row, dict):
            continue
        old_id = old_row.get("messageId")
        if (is_pan_message_id(old_id) and old_id_counts[old_id] == 1
                and old_id not in used_ids):
            assigned[new_index] = old_id
            used_ids.add(old_id)

    # A valid Pan ID already present in the incoming row is also Pan-owned and
    # may be continued (for example, a previously materialized branch copy).
    for index in new_body:
        row = rows[index]
        if not isinstance(row, dict) or index in assigned:
            continue
        incoming_id = row.get("messageId")
        if (is_pan_message_id(incoming_id) and new_id_counts[incoming_id] == 1
                and incoming_id not in used_ids):
            assigned[index] = incoming_id
            used_ids.add(incoming_id)

    for index in new_body:
        row = rows[index]
        if not isinstance(row, dict):
            continue
        if index not in assigned:
            _remember_steer_client_identity(row)
            message_id = _new_pan_message_id()
            while message_id in used_ids:
                message_id = _new_pan_message_id()
            assigned[index] = message_id
            used_ids.add(message_id)
        row["messageId"] = assigned[index]
    return rows


def append_history(s: "Session", message: dict) -> None:
    """Append one history row and advance the summary projection.

    新追加的非空 user/assistant/tool/thinking 内容块在此分配 Pan messageId；
    error/system 与 system_prompt 不分配。steer:<caller> 仅保留为 clientMessageId 别名。
    新追加的条目在此处打本地 ISO-8601 ts（setdefault：已有 ts 的行不被改写）。
    打点刻意放在追加边界而不是落盘边界：整体替换 / 导入 / branch 复制进来的
    provider 行（它们没有真实时间，只有 role/content）不带 ts，前端对缺失 ts
    不显示时间——「时间不确定就不显示」。若放在保存时打点，这些行会被伪造成
    当前的落盘/导入时刻（旧实现的行为）。
    """
    with s._summary_lock:
        ensure_summary_projection(s)
        if isinstance(message, dict):
            if (_is_searchable_history_body(message)
                    and not is_pan_message_id(message.get("messageId"))):
                _remember_steer_client_identity(message)
                message["messageId"] = _new_pan_message_id()
            message.setdefault("ts", datetime.now().isoformat())
        s.history.append(message)
        s.history_revision = max(0, int(getattr(s, "history_revision", 0) or 0)) + 1
        _apply_summary_message(s.summary_projection, message)
        s._summary_history_index = len(s.history)


def replace_history(s: "Session", history: list[dict]) -> None:
    """Replace history and rebuild only at an explicit full-history boundary."""
    with s._summary_lock:
        s.history = list(history or [])
        s.history_epoch = uuid.uuid4().hex
        s.history_revision = max(0, int(getattr(s, "history_revision", 0) or 0)) + 1
        s._history_loaded = True
        previous_revision = int(s.summary_projection.get("revision") or 0)
        s.summary_projection = _summary_projection_from_history(
            s.history,
            revision=previous_revision + 1,
            updated_at=datetime.now().isoformat(),
        )
        s._summary_projection_complete = True
        s._summary_history_index = len(s.history)


def ensure_summary_projection(s: "Session") -> None:
    """Reconcile direct legacy history mutations without a full-history scan."""
    projection = getattr(s, "summary_projection", None)
    if not isinstance(projection, dict):
        s.summary_projection = _summary_projection_from_history(
            s.history, updated_at=getattr(s, "updated_at", ""),
        )
        s._summary_projection_complete = getattr(s, "_history_loaded", True)
        s._summary_history_index = len(s.history)
        return
    if (
        not getattr(s, "_summary_projection_complete", False)
        or not is_complete_summary_projection(projection)
    ):
        # A shallow session has only a bounded main-file tail.  Keep its total
        # unknown and let append_history update previews; only a full load or
        # the background repair worker may promote it to authoritative.
        if getattr(s, "_history_loaded", True):
            previous_revision = int(projection.get("revision") or 0)
            s.summary_projection = _summary_projection_from_history(
                s.history,
                revision=max(1, previous_revision + 1),
                updated_at=datetime.now().isoformat(),
            )
            s._summary_projection_complete = True
            s._summary_history_index = len(s.history)
        return
    seen = getattr(s, "_summary_history_index", 0)
    if not isinstance(seen, int) or seen < 0:
        seen = 0
    if len(s.history) < seen:
        # A replacement performed by an old caller is an explicit full-history
        # boundary.  Rebuild here (normally called from save/import, not GET).
        replace_history(s, s.history)
        return
    for message in s.history[seen:]:
        _apply_summary_message(projection, message)
    s._summary_history_index = len(s.history)


def summary_projection(s: "Session") -> dict:
    """Return a copy of the bounded, raw summary projection."""
    with s._summary_lock:
        ensure_summary_projection(s)
        return dict(s.summary_projection)


def update_worker_summary(s: "Session", *, status: str | None,
                         worker_id: str | None, generation: int | None,
                         task_id: str | None, task_seq: int | None) -> bool:
    """Advance revision when live worker summary state changes.

    Worker identity/status is runtime state and is intentionally not written
    as durable Session metadata.  The revision is shared with the persisted
    history/metadata projection so WS patches and HTTP summaries are ordered.
    """
    state = {
        "status": status,
        "worker_id": worker_id,
        "generation": generation,
        "task_id": task_id,
        "task_seq": task_seq,
    }
    if getattr(s, "_summary_worker_state", None) == state:
        return False
    s._summary_worker_state = state
    s.summary_projection["revision"] = max(0, int(s.summary_projection.get("revision") or 0)) + 1
    s.summary_projection["updated_at"] = datetime.now().isoformat()
    return True


def _path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _history_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.history.jsonl"


def _encode_line(item: dict) -> bytes:
    return (json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")


def _write_jsonl(path: Path, items: list[dict]):
    """整文件重写（截断 + 全量写入），迁移 / force_full / 首次创建时用。"""
    with open(path, "wb") as f:
        for it in items:
            f.write(_encode_line(it))
        f.flush()
    _newline_terminated_jsonl.add(str(path))  # 全量写必然以 \n 结尾


def _append_jsonl(path: Path, items: list[dict]):
    """追加写，写后 flush——热路径只追加，O(new entries)。

    崩溃恢复：文件不以换行结尾（上次 append 中断的半行）时先补一个换行，
    避免后续新记录粘在损坏半行上一起丢。ab 模式不支持读（无法探测末字节）。

    性能：Windows 上"读末字节探测 + 写"两次 open 开销极大（实测 5-7ms），
    而纯 ab 追加仅 0.3ms。故用进程内集合 _newline_terminated_jsonl 缓存
    "文件已知以换行结尾"——本进程每次写入都带换行，正常路径无需重复探测。
    冷路径（首次写 / 崩溃重启后 / 文件被外部改写）才探测一次并修复半行。
    """
    if not items:
        return
    key = str(path)
    if key in _newline_terminated_jsonl:
        with open(path, "ab") as f:
            for it in items:
                f.write(_encode_line(it))
            f.flush()
        return
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            need_newline = False
            if size > 0:
                f.seek(size - 1)
                need_newline = f.read(1) != b"\n"
    except FileNotFoundError:
        _write_jsonl(path, items)  # 首次创建：无既有半行风险
        _newline_terminated_jsonl.add(key)
        return
    with open(path, "ab") as f:
        if need_newline:
            f.write(b"\n")
        for it in items:
            f.write(_encode_line(it))
        f.flush()
    _newline_terminated_jsonl.add(key)


def _read_jsonl(path: Path) -> list[dict]:
    """读 jsonl 追加文件。损坏行（崩溃半行）跳过、其后的有效行继续读取，
    最大程度恢复已提交记录。"""
    out: list[dict] = []
    try:
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 损坏行 = 上次 append 崩溃留下的半行 → 文件不再以 \n 结尾，
                    # 使 _append_jsonl 的"以换行结尾"缓存失效（下次写会重新探测补换行）。
                    _newline_terminated_jsonl.discard(str(path))
                    continue
    except OSError:
        pass
    return out


def _summary_projection_from_jsonl(
    path: Path, *, revision: int = 0, updated_at: str = "",
) -> dict:
    """Rebuild one projection in a single streaming pass with O(1) rows held."""
    projection = _empty_summary_projection(
        revision=revision, history_total=0, updated_at=updated_at,
    )
    # OSError is deliberately allowed to reach the repair caller.  A failed
    # read is not an empty history and must never be persisted as a complete
    # zero projection.
    with open(path, "rb") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                _newline_terminated_jsonl.discard(str(path))
                continue
            if not isinstance(value, dict):
                continue
            # Do not mutate the decoded row while stripping a legacy
            # delivery marker; this helper is also safe for diagnostics.
            if isinstance(value.get("content"), str) and "[delivered:" in value["content"]:
                value = dict(value)
                _strip_delivery_marks([value])
            _apply_summary_message(projection, value, bump=False)
    projection["revision"] = max(0, int(revision or 0))
    return projection


def _receipt_timestamp(record: object) -> float:
    """Best-effort age used only for bounded terminal-receipt retention."""
    if not isinstance(record, dict):
        return 0.0
    for key in ("receiptAt", "deliveredAt", "deletedAt", "createdAt"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _bound_loaded_receipt_ledger(
    ledger: object, pending: list | None = None,
) -> tuple[dict, set[str]]:
    """Keep legacy session metadata from exploding during a cold load.

    This is intentionally conservative.  Rows that are not terminal, or that
    still have a matching pending row, are retained because they may be needed
    for crash recovery.  Only old terminal receipts are eligible for the
    count-bound eviction; the worker performs the age/index-aware sweep before
    the next durable queue write.
    """
    if not isinstance(ledger, dict):
        return {}, set()
    result = {
        str(key): value for key, value in ledger.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    pending_ids = set()
    for item in pending or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("queueItemId") or item.get("id")
        if isinstance(item_id, str) and item_id:
            pending_ids.add(item_id)
    terminal = [
        (key, record)
        for key, record in result.items()
        if record.get("deliveryState") in _QUEUE_TERMINAL_STATES
        and key not in pending_ids
    ]
    overflow = len(terminal) - QUEUE_RECEIPT_MAX_ENTRIES
    if overflow <= 0:
        return result, set()
    # JSON object insertion order is the only ordering available for old
    # records without receiptAt.  Use it as a stable fallback after timestamps.
    now = time.time()
    eligible = [
        value for value in terminal
        if (now - _receipt_timestamp(value[1])) >= QUEUE_RECEIPT_MIN_RETRY_SEC
    ]
    ranked = sorted(
        enumerate(eligible),
        key=lambda pair: (_receipt_timestamp(pair[1][1]), pair[0]),
    )
    evicted = set()
    for _, (key, _) in ranked[:overflow]:
        result.pop(key, None)
        evicted.add(key)
    return result, evicted


def _history_page_from_jsonl(
    path: Path, *, before: int, limit: int,
    known_total: int | None = None,
) -> tuple[list[dict], int]:
    """Read one bounded history page without materializing the full JSONL."""
    # The common cold-list path asks for the last page (before=0).  A complete
    # durable summary projection gives us the exact row count, so use that to
    # avoid parsing every historical JSON object just to count it.  Reading
    # bytes and counting line separators is implemented in C; only the bounded
    # tail is decoded and parsed in Python.  If the projection and file disagree
    # (for example, a writer appended JSONL before committing metadata), or the
    # file has a crash tail, fall through to the compatibility scan below.
    if before <= 0 and _is_nonnegative_int(known_total):
        try:
            raw = path.read_bytes()
        except OSError:
            return [], 0
        if raw.endswith(b"\n") and raw.count(b"\n") == known_total:
            lines = raw.rsplit(b"\n", min(limit, known_total) + 1)
            tail = lines[-(min(limit, known_total) + 1):-1]
            parsed: list[dict] = []
            for line in tail:
                try:
                    value = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    break
                if not isinstance(value, dict):
                    break
                parsed.append(value)
            else:
                return parsed, known_total

    page: deque[dict] = deque(maxlen=limit)
    total = 0
    try:
        with open(path, "rb") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Match _read_jsonl: a crash-tail half-line is not a
                    # history row and must not change pagination positions.
                    _newline_terminated_jsonl.discard(str(path))
                    continue
                if not isinstance(value, dict):
                    continue
                if before <= 0 or total < before:
                    page.append(value)
                total += 1
    except OSError:
        return [], 0
    return list(page), total


def _history_file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    """Return a signature that changes when the companion history file does."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def history_page(session_id: str, *, before: int = 0,
                 limit: int = 50, search_jump: bool = False) -> dict | None:
    """Return a bounded history page while keeping cold Sessions shallow.

    A fully hydrated Session is served from its in-memory history so unsaved
    worker appends remain visible.  A shallow/cold Session reads only the
    requested tail window from the companion JSONL and never replaces the
    cached shallow object with the complete history. Search jumps may request
    up to 1000 rows per page; ordinary callers retain the 200-row cap. Repeated tail requests
    reuse one bounded page while the file signature is unchanged; appends and
    replacements invalidate it. This is the read/page boundary for
    Manage/session-history callers; explicit ``get()`` remains the compatibility
    full-history API.
    """
    try:
        page_max = HISTORY_SEARCH_JUMP_PAGE_MAX if search_jump else HISTORY_PAGE_MAX
        bounded_limit = max(1, min(int(limit), page_max))
    except (TypeError, ValueError):
        bounded_limit = 50
    try:
        requested_before = max(0, int(before or 0))
    except (TypeError, ValueError):
        requested_before = 0

    cached = _cache.get(session_id)
    if cached is not None and getattr(cached, "_history_loaded", True):
        total = len(cached.history)
        effective_before = total if requested_before <= 0 else min(requested_before, total)
        start = max(0, effective_before - bounded_limit)
        # Shallow-copy the rows instead of handing out the live Session
        # objects.  This page is serialized from FastAPI worker threads, so
        # the boundary must neither publish nor rewrite (``_strip_delivery_
        # marks`` below) memory the event loop and the streaming worker keep
        # mutating.  It also matches the disk branch, which already returns
        # freshly parsed rows.
        page = [dict(row) for row in cached.history[start:effective_before]]
        return {
            "history": _strip_delivery_marks(page),
            "total": total,
            "hasMore": start > 0,
            "start": start,
            "historyEpoch": getattr(cached, "history_epoch", None),
            "historyRevision": getattr(cached, "history_revision", 0),
        }

    path = _path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    history_path = _history_path(session_id)
    if history_path.exists():
        projection = data.get("summary_projection")
        known_total = (
            _projection_value(projection, "history_total")
            if is_complete_summary_projection(projection) else None
        )
        cacheable_tail = (
            cached is not None
            and requested_before <= 0
            and _is_nonnegative_int(known_total)
        )
        signature = _history_file_signature(history_path) if cacheable_tail else None
        cache_key = (signature, known_total, bounded_limit)
        page_cache = getattr(cached, "_history_tail_page_cache", None)
        if (signature is not None and isinstance(page_cache, tuple)
                and page_cache[:3] == cache_key):
            _, _, _, cached_page_json, total, start = page_cache
            # Decode a private JSON snapshot for each caller. History rows are
            # JSON-shaped already, and the C decoder avoids a Python-level
            # deepcopy walk on every request while preserving nested isolation.
            page = json.loads(cached_page_json)
        else:
            page, total = _history_page_from_jsonl(
                history_path, before=requested_before, limit=bounded_limit,
                known_total=known_total,
            )
            effective_before = (
                total if requested_before <= 0 else min(requested_before, total)
            )
            start = max(0, effective_before - min(bounded_limit, len(page)))
            if signature is not None and _history_file_signature(history_path) == signature:
                # Retain a private serialized snapshot. It prevents downstream
                # caller mutations from aliasing the cache and makes cache hits
                # cheaper than repeated Python deepcopy walks.
                cached._history_tail_page_cache = (
                    *cache_key,
                    json.dumps(page, ensure_ascii=False, separators=(",", ":")),
                    total,
                    start,
                )
    else:
        raw_history = data.get("history")
        rows = raw_history if isinstance(raw_history, list) else []
        total = len(rows)
        effective_before = total if requested_before <= 0 else min(requested_before, total)
        start = max(0, effective_before - bounded_limit)
        page = [item for item in rows[start:effective_before]
                if isinstance(item, dict)]

    return {
        "history": _strip_delivery_marks(page),
        "total": total,
        "hasMore": start > 0,
        "start": start,
        "historyEpoch": data.get("history_epoch") or data.get("historyEpoch"),
        "historyRevision": data.get("history_revision", data.get("historyRevision", 0)),
    }


def _new_id() -> str:
    return "ses_" + secrets.token_hex(8)


# 存量清理：旧版投递对账把 `[delivered: task/report:<id>:<12位指纹>]` 作为独立
# 文本行注入消息正文（fix/delivery-mark 起改为 history 条目的 delivered_keys
# 元数据，见 packages/core/worker.py）。加载时剥离，避免旧消息继续在 UI / 上下
# 文中显示前缀。整行精确匹配（含 12 位十六进制指纹）才剥离——真实用户消息恰为
# 该格式独立行的概率可忽略，不误删。
_DELIVERY_MARK_LINE_RE = re.compile(
    r"^\[delivered: (?:task|report):[^\]\n]*:[0-9a-f]{12}\]$")


def _strip_delivery_marks(history: list[dict]) -> list[dict]:
    """剥离 history 条目 content 中的旧版 `[delivered: ...]` 标记行（就地修改）。"""
    for h in history:
        content = h.get("content")
        if not isinstance(content, str) or "[delivered:" not in content:
            continue
        lines = [ln for ln in content.split("\n")
                 if not _DELIVERY_MARK_LINE_RE.match(ln)]
        h["content"] = "\n".join(lines).lstrip("\n")
    return history


# The three capability flags are stored nested under ``pan_access``. Old JSON
# wrote them as top-level fields; migration lives in _from_data / __post_init__.
_PAN_ACCESS_KEYS = ("restrict_to_managed", "can_claim_unmanaged", "auto_claim_created")
_PROMPT_UNSET = object()  # distinguish an omitted original from explicit None/""

# Queue receipts written by the previous queue implementation are retained as
# compatibility metadata.  They are not a second queue: ``queue_pending`` is
# still the only durable delivery queue.  Keeping the receipt ledger here lets
# a late taskId/clientMessageId retry resolve to the original receipt after an
# upgrade instead of creating a second queue item.


@dataclass(init=False)
class Session:
    id: str
    name: str
    adapter: str = "cbc"   # CLI adapter name, default "cbc"
    model: str | None = None
    permission_mode: str | None = None
    pan_access: dict = field(default_factory=dict)  # capability flags, nested (restrict_to_managed/can_claim_unmanaged/auto_claim_created)
    adapter_config: dict = field(default_factory=dict)  # adapter-specific settings
    character_id: str | None = None   # bound character ID (for memory + assets)
    session_template: str | None = None  # session_template name this session was configured with (None = built-in default)
    original_prompt: str | None = None  # stable instructions, never a generated handoff
    handoff_prompt: str | None = None  # only the latest handoff brief
    game_id: str | None = None        # RuleWhisper game identifier for MCP tool calls
    raw_usage: dict | None = None
    total_usage: dict | None = None
    # Durable, retryable post-terminal usage work.  This is deliberately
    # separate from queue_pending: it is not a user/provider message and must
    # never affect FIFO delivery or report semantics.
    usage_enrichment_pending: list[dict] = field(default_factory=list)
    workdir: str = ""
    history: list[dict] = field(default_factory=list)
    history_epoch: str = ""
    history_revision: int = 0
    last_result: dict | None = None
    result_cursor: int = 0
    terminal_results: list[dict] = field(default_factory=list)
    # Last Worker state reached through an explicitly successful Pan
    # lifecycle transition.  This is deliberately separate from the live
    # worker status, which is derived from the in-memory Worker runtime.
    last_legal_worker_state: str | None = None
    created_at: str = ""
    updated_at: str = ""
    order: int | None = None  # 用户自定义展示顺序（None = 未排序，按 created_at 排在末尾）
    workspace_ids: list[str] = field(default_factory=list)  # zero or one; empty means ungrouped
    managed: list[str] = field(default_factory=list)  # session ids this session manages
    managed_by: str | None = None  # session id of the session managing this one
    readonly_session: bool = False  # manager blocks operations sent to this session
    queue_pending: list = field(default_factory=list)  # persisted message queue (for report consumption)
    # Pause durable agent completion/error/zombie reports while allowing other
    # queue sources to keep their normal delivery order.
    agent_reports_paused: bool = False
    queue_item_locks_version: int = 1
    # Expiring browser edit leases keep a queued item at the same durable
    # position while preventing the Worker from handing it to the provider.
    queue_edit_locks: dict[str, dict] = field(default_factory=dict)
    # Durable identity/state ledger for queue items that have crossed the
    # at-most-once reservation boundary.  queue_pending is deliberately only
    # the retryable (queued) subset; this ledger is what makes a removed item
    # idempotent after a crash or a late client retry.
    queue_delivery_ledger: dict = field(default_factory=dict)
    # Durable O(1) lookup from formal taskId/clientMessageId to the original
    # queue receipt.  The worker maintains the index; old Sessions without it
    # rebuild a bounded tail once on first idempotency access.
    queue_idempotency_index: dict = field(default_factory=dict)
    queue_revision: int = 0
    task_seq: int = 0  # 已分配的任务序号计数（send_task 入队时自增；持久化在 session 上，跨 worker respawn 保持单调递增）
    # The latest formal assign task selected for this Session.  This is a
    # routing context for subsequent agent_send messages, not a second queue
    # or an idempotency registry.  It is persisted so a Worker respawn cannot
    # fall back to a process-local "last task" guess.
    active_task_id: str | None = None
    # Browser-originated messages are acknowledged only after the queue item is
    # durable.  Keep a bounded receipt ledger so a WebSocket reconnect can
    # safely retransmit the same clientMessageId without starting the task twice.
    accepted_input_ids: list[str] = field(default_factory=list)
    report_subscriptions: set[str] = field(default_factory=set)  # managed sessions whose completion reports this session subscribes to
    qq_subscriptions: set[str] = field(default_factory=set)  # QQ conversations this session subscribes to ("user:<qq>"/"group:<group_id>")
    wechat_subscriptions: set[str] = field(default_factory=set)  # 微信会话（WeChat conversations）this session subscribes to ("user:<wxid>")
    notification_settings: dict = field(default_factory=dict)  # Pan completion notifications

    # ── adapter_config convenience accessors ──

    @property
    def cli_session_id(self) -> str | None:
        """Adapter-native session ID (for --resume, --continue, etc.)."""
        return self.adapter_config.get("cli_session_id")

    @cli_session_id.setter
    def cli_session_id(self, value: str | None):
        if value:
            self.adapter_config["cli_session_id"] = value
        else:
            self.adapter_config.pop("cli_session_id", None)

    def __init__(self, id: str, name: str, adapter: str = "cbc",
                 model: str | None = None, permission_mode: str | None = None,
                 pan_access: dict | None = None,
                 restrict_to_managed: bool | None = None,
                 can_claim_unmanaged: bool | None = None,
                 auto_claim_created: bool | None = None,
                 adapter_config: dict | None = None,
                 character_id: str | None = None,
                 session_template: str | None = None,
                 system_prompt: str | None = None,
                 game_id: str | None = None,
                 raw_usage: dict | None = None,
                 total_usage: dict | None = None,
                 usage_enrichment_pending: list[dict] | None = None,
                 workdir: str = "",
                 history: list[dict] | None = None,
                 last_result: dict | None = None,
                 result_cursor: int = 0,
                 terminal_results: list[dict] | None = None,
                 last_legal_worker_state: str | None = None,
                 created_at: str = "",
                 updated_at: str = "",
                 order: int | None = None,
                 workspace_ids: list[str] | None = None,
                 managed: list[str] | None = None,
                 managed_by: str | None = None,
                 readonly_session: bool = False,
                 queue_pending: list | None = None,
                 history_epoch: str | None = None,
                 history_revision: int = 0,
                 queue_delivery_ledger: dict | None = None,
                 queue_idempotency_index: dict | None = None,
                 queue_revision: int = 0,
                 task_seq: int = 0,
                 active_task_id: str | None = None,
                 accepted_input_ids: list[str] | None = None,
                 summary_projection: dict | None = None,
                 report_subscriptions=None,
                 qq_subscriptions=None,
                 wechat_subscriptions=None, notification_settings=None, *,
                 queue_edit_locks: dict[str, dict] | None = None,
                 agent_reports_paused: bool = False,
                 queue_item_locks_version: int = 1,
                 original_prompt: str | None | object = _PROMPT_UNSET,
                 handoff_prompt: str | None = None):
        """Manual init so legacy top-level capability kwargs still construct.

        ``pan_access`` is the single source of truth for the three capability
        flags; the old flat kwargs (``restrict_to_managed`` etc.) are merged in
        for backward compatibility and win over a pre-built ``pan_access``.
        """
        self.id = id
        self.name = name
        self.adapter = adapter
        self.model = model
        self.permission_mode = permission_mode
        pa = dict(pan_access) if pan_access else {}
        if restrict_to_managed is not None:
            pa["restrict_to_managed"] = restrict_to_managed
        if can_claim_unmanaged is not None:
            pa["can_claim_unmanaged"] = can_claim_unmanaged
        if auto_claim_created is not None:
            pa["auto_claim_created"] = auto_claim_created
        self.pan_access = pa
        self.adapter_config = adapter_config if adapter_config is not None else {}
        self.character_id = character_id
        self.session_template = session_template
        # Legacy prompts are opaque: even text resembling our handoff headings
        # may be user-authored. Preserve it verbatim; never guess a split.
        # Explicit canonical values (including None/"") beat the legacy alias.
        self.original_prompt = system_prompt if original_prompt is _PROMPT_UNSET else original_prompt
        self.handoff_prompt = handoff_prompt
        self.game_id = game_id
        self.raw_usage = raw_usage
        self.total_usage = total_usage
        self.usage_enrichment_pending = (
            usage_enrichment_pending if usage_enrichment_pending is not None else []
        )
        self.workdir = workdir
        self.history = history if history is not None else []
        self.history_epoch = (
            history_epoch if isinstance(history_epoch, str) and history_epoch
            else uuid.uuid4().hex
        )
        try:
            self.history_revision = max(0, int(history_revision or 0))
        except (TypeError, ValueError):
            self.history_revision = 0
        self.last_result = last_result
        try:
            self.result_cursor = max(0, int(result_cursor or 0))
        except (TypeError, ValueError):
            self.result_cursor = 0
        self.terminal_results = [
            dict(item) for item in (terminal_results or [])
            if isinstance(item, dict)
        ][-RESULT_REPLAY_MAX_ENTRIES:]
        # Older sessions only have last_result.  Do not synthesize a durable
        # replay entry here: its result cursor is unknown and a future save
        # must not pretend that an old result can fill a historical gap.
        self.last_legal_worker_state = (
            last_legal_worker_state if isinstance(last_legal_worker_state, str)
            else None
        )
        self.created_at = created_at
        self.updated_at = updated_at
        try:
            self.order = int(order) if order is not None else None
        except (TypeError, ValueError):
            self.order = None  # 落盘 JSON 中 order 损坏时降级为未排序
        self.workspace_ids = list(dict.fromkeys(
            item for item in (workspace_ids or []) if isinstance(item, str) and item
        ))
        self.managed = managed if managed is not None else []
        self.managed_by = managed_by
        self.readonly_session = bool(readonly_session)
        self.queue_pending = queue_pending if queue_pending is not None else []
        self.agent_reports_paused = bool(agent_reports_paused)
        self.queue_item_locks_version = 1
        self.queue_edit_locks = {
            key: copy.deepcopy(value)
            for key, value in (queue_edit_locks or {}).items()
            if isinstance(key, str) and key and isinstance(value, dict)
        }
        self.queue_delivery_ledger, evicted_receipt_ids = _bound_loaded_receipt_ledger(
            queue_delivery_ledger, self.queue_pending,
        )
        if isinstance(queue_idempotency_index, dict):
            self.queue_idempotency_index = copy.deepcopy(queue_idempotency_index)
        else:
            self.queue_idempotency_index = {}
        if evicted_receipt_ids:
            # A cold-load count bound may remove old terminal bodies before
            # worker.py gets a chance to run its age-aware sweep.  Do not
            # leave a dangling durable index entry that would turn a removed
            # receipt into a permanent synthetic duplicate.
            for bucket in ("taskId", "clientMessageId"):
                values = self.queue_idempotency_index.get(bucket)
                if not isinstance(values, dict):
                    continue
                for key, entry in list(values.items()):
                    if (isinstance(entry, dict)
                            and entry.get("queueItemId") in evicted_receipt_ids):
                        values.pop(key, None)
        try:
            self.queue_revision = int(queue_revision or 0)
        except (TypeError, ValueError):
            self.queue_revision = 0
        if queue_item_locks_version != 1 and self.agent_reports_paused:
            # Previous Pause reports stored only a Session flag. Upgrade its
            # pending reports once, so later manual unlocks survive restarts.
            migrated = False
            for item in self.queue_pending:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                source = item.get("source")
                report = (
                    item_type in {"report", "zombie"} and source in {None, "report"}
                ) or (
                    item_type is None and source == "report" and "result" in item
                )
                if (report and item.get("deliveryState", "queued") == "queued"
                        and not item.get("queueLockAutoReport")):
                    item["queueLockAutoReport"] = True
                    migrated = True
            if migrated:
                self.queue_revision += 1
        self.task_seq = task_seq
        self.active_task_id = active_task_id
        raw_accepted_ids = accepted_input_ids if accepted_input_ids is not None else []
        self.accepted_input_ids = list(dict.fromkeys(
            item for item in raw_accepted_ids
            if isinstance(item, str) and item
        ))[-ACCEPTED_INPUT_ID_MAX:]
        self._summary_lock = threading.RLock()
        # worker.py validates/rebuilds the persisted map once, then marks this
        # private flag.  Treat even version-1 data as not yet reconciled with
        # queue_pending/ledger: a crash can commit those fields separately.
        self._idempotency_index_built = False
        self._idempotency_index_present = (
            isinstance(queue_idempotency_index, dict)
            and queue_idempotency_index.get("version") == 1
        )
        normalized_projection = _normalize_summary_projection(
            summary_projection, fallback_updated_at=updated_at,
        )
        self.summary_projection = (
            normalized_projection
            or _summary_projection_from_history(self.history, updated_at=updated_at)
        )
        self._summary_projection_complete = (
            is_complete_summary_projection(summary_projection)
            if summary_projection is not None
            else True
        )
        self._summary_history_index = len(self.history)
        self._history_loaded = True
        self._summary_worker_state = None
        self.report_subscriptions = report_subscriptions if report_subscriptions is not None else set()
        self.qq_subscriptions = qq_subscriptions if qq_subscriptions is not None else set()
        self.wechat_subscriptions = (
            wechat_subscriptions if wechat_subscriptions is not None else set()
        )
        self.notification_settings = normalize_notification_settings(notification_settings)
        self.__post_init__()

    @property
    def system_prompt(self) -> str | None:
        """Effective worker prompt; a compatibility view, never an inheritance source.

        Keep stored text verbatim. Empty/whitespace-only briefs add no wrapper;
        sessions without a brief retain their original prompt exactly.
        """
        brief = self.handoff_prompt
        original = self.original_prompt
        if not brief or not brief.strip():
            return original
        if not original or not original.strip():
            return brief
        return (
            "【交接上下文（由被交接 session A 的 agent 编写）】\n"
            f"{brief}\n\n"
            "【原 session 的 system prompt】\n"
            f"{original}"
        )

    @system_prompt.setter
    def system_prompt(self, value: str | None):
        """Legacy full-prompt replacement starts a new baseline without a brief.

        Inheritance must copy the two canonical fields instead. Settings editors
        should edit original_prompt to retain the current brief.
        """
        self.original_prompt = value
        self.handoff_prompt = None

    # ── pan_access convenience accessors (capability flags) ──

    @property
    def restrict_to_managed(self) -> bool:
        """Operations on other sessions are gated by `managed`."""
        return bool(self.pan_access.get("restrict_to_managed", False))

    @restrict_to_managed.setter
    def restrict_to_managed(self, value: bool):
        self.pan_access["restrict_to_managed"] = bool(value)

    @property
    def can_claim_unmanaged(self) -> bool:
        """May claim an unclaimed session into `managed`."""
        return bool(self.pan_access.get("can_claim_unmanaged", False))

    @can_claim_unmanaged.setter
    def can_claim_unmanaged(self, value: bool):
        self.pan_access["can_claim_unmanaged"] = bool(value)

    @property
    def auto_claim_created(self) -> bool:
        """Sessions this session creates are auto-claimed."""
        return bool(self.pan_access.get("auto_claim_created", False))

    @auto_claim_created.setter
    def auto_claim_created(self, value: bool):
        self.pan_access["auto_claim_created"] = bool(value)

    def adapter_field(self, key: str, default=None):
        """Read a value from adapter_config."""
        return self.adapter_config.get(key, default)

    def set_adapter_field(self, key: str, value):
        """Set a value in adapter_config in-place."""
        if value is not None and value != "" and value is not False:
            self.adapter_config[key] = value
        else:
            self.adapter_config.pop(key, None)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        # 落盘 JSON 里 set 序列化为 list → 读回时还原
        if isinstance(self.report_subscriptions, (list, tuple)):
            self.report_subscriptions = set(self.report_subscriptions)
        if isinstance(self.qq_subscriptions, (list, tuple)):
            self.qq_subscriptions = set(self.qq_subscriptions)
        if isinstance(self.wechat_subscriptions, (list, tuple)):
            self.wechat_subscriptions = set(self.wechat_subscriptions)
        # pan_access: normalize to a dict with all three capability keys,
        # defaulting to False. Migrate legacy top-level instance attrs (old
        # JSON / old constructor paths) into the nested dict.
        pa = self.pan_access if isinstance(self.pan_access, dict) else {}
        for key in _PAN_ACCESS_KEYS:
            legacy = self.__dict__.pop(key, None)
            if legacy is not None:
                pa[key] = legacy
            pa.setdefault(key, False)
        self.pan_access = pa
        # migrate any legacy top-level fields that ended up on the instance
        # (from Session(**data) with old JSON having cbc_session_id, etc.)
        _migrate_legacy_fields(self)
        self._summary_meta_sig = _summary_metadata_signature(self)

    @classmethod
    def _from_data(cls, data: dict) -> Session:
        """Construct Session from legacy or new JSON data.

        Pops legacy adapter-specific fields from data and puts
        them into adapter_config before constructing the instance.
        Old top-level capability fields are migrated into nested pan_access
        (and the old keys removed) so pre-refactor JSON keeps loading.
        """
        _canonicalize_summary_projection_data(data)
        # Accept the API spelling as a defensive compatibility bridge for
        # hand-authored/older metadata, while the canonical session JSON keeps
        # the repository's existing snake_case field style.
        if "last_legal_worker_state" not in data and "lastLegalWorkerState" in data:
            data["last_legal_worker_state"] = data.pop("lastLegalWorkerState")
        if "active_task_id" not in data and "activeTaskId" in data:
            data["active_task_id"] = data.pop("activeTaskId")
        if "history_epoch" not in data and "historyEpoch" in data:
            data["history_epoch"] = data.pop("historyEpoch")
        if "history_revision" not in data and "historyRevision" in data:
            data["history_revision"] = data.pop("historyRevision")
        # Old JSONL/main JSON had no history epoch. Derive a stable legacy
        # scope from the durable Session id instead of allocating a new UUID
        # on every cold process restart; explicit replace_history still gets a
        # fresh epoch below.
        if not isinstance(data.get("history_epoch"), str) or not data.get("history_epoch"):
            data["history_epoch"] = f"legacy:{data.get('id', 'unknown')}"
        if "queue_idempotency_index" not in data:
            for alias in ("queueIdempotencyIndex", "idempotency_index"):
                if alias in data:
                    data["queue_idempotency_index"] = data.pop(alias)
                    break
        data.setdefault("queue_item_locks_version", 0)
        ac = data.pop("adapter_config", {}) or {}
        for old_key, new_key in [
            ("cbc_session_id", "cli_session_id"),
            ("always_thinking_enabled", "always_thinking_enabled"),
            ("effort", "effort"),
            ("max_thinking_tokens", "max_thinking_tokens"),
        ]:
            val = data.pop(old_key, None)
            if val is not None and val != "" and val is not False:
                ac[new_key] = val
        # Migrate legacy top-level capability fields into nested pan_access.
        pa = dict(data.pop("pan_access", {}) or {})
        for key in _PAN_ACCESS_KEYS:
            if key in data:
                pa[key] = data.pop(key)
        data["pan_access"] = pa
        data["adapter_config"] = ac
        return cls(**data)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "pan_access": dict(self.pan_access),
            "adapter_config": self.adapter_config,
            "character_id": self.character_id,
            "session_template": self.session_template,
            "system_prompt": self.system_prompt,
            "original_prompt": self.original_prompt,
            "handoff_prompt": self.handoff_prompt,
            "game_id": self.game_id,
            "raw_usage": self.raw_usage,
            "total_usage": self.total_usage,
            "usage_enrichment_pending": self.usage_enrichment_pending,
            "workdir": self.workdir,
            "history": self.history,
            "history_epoch": self.history_epoch,
            "history_revision": self.history_revision,
            "last_result": self.last_result,
            "result_cursor": self.result_cursor,
            "terminal_results": self.terminal_results,
            "last_legal_worker_state": self.last_legal_worker_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "order": self.order,
            "workspace_ids": list(self.workspace_ids),
            "managed": self.managed,
            "managed_by": self.managed_by,
            "readonly_session": self.readonly_session,
            "queue_pending": self.queue_pending,
            "agent_reports_paused": self.agent_reports_paused,
            "queue_item_locks_version": self.queue_item_locks_version,
            "queue_edit_locks": self.queue_edit_locks,
            "queue_delivery_ledger": self.queue_delivery_ledger,
            "queue_idempotency_index": self.queue_idempotency_index,
            "queue_revision": self.queue_revision,
            "task_seq": self.task_seq,
            "active_task_id": self.active_task_id,
            "accepted_input_ids": self.accepted_input_ids,
            "summary_projection": dict(self.summary_projection),
            "report_subscriptions": sorted(self.report_subscriptions),
            "qq_subscriptions": sorted(self.qq_subscriptions),
            "wechat_subscriptions": sorted(self.wechat_subscriptions),
            "notification_settings": normalize_notification_settings(self.notification_settings),
        }


# ── in-memory cache ──
_cache: dict[str, Session] = {}


# ── CRUD ──

@_store_serialized
def create(name: str, model: str | None = None,
           permission_mode: str | None = None,
           adapter: str = "cbc",
           adapter_config: dict | None = None,
           raw_usage: dict | None = None,
           total_usage: dict | None = None,
           workdir: str = "",
           history: list[dict] | None = None,
           character_id: str | None = None,
           session_template: str | None = None,
           system_prompt: str | None = None,
           game_id: str | None = None,
           pan_access: dict | None = None,
           restrict_to_managed: bool = False,
           can_claim_unmanaged: bool = False,
           auto_claim_created: bool = False,
           notification_settings: dict | None = None,
           # backward-compat kwargs (migrated to adapter_config)
           cli_session_id: str | None = None,
           always_thinking_enabled: bool = False,
           effort: str = "",
           max_thinking_tokens: int | None = None, *,
           workspace_ids: list[str] | None = None,
           original_prompt: str | None | object = _PROMPT_UNSET,
           handoff_prompt: str | None = None) -> Session:
    if workspace_ids is not None and len(workspace_ids) > 1:
        raise ValueError("A Session can belong to at most one Workspace")
    # build adapter_config
    ac = dict(adapter_config) if adapter_config else {}
    if cli_session_id and "cli_session_id" not in ac:
        ac["cli_session_id"] = cli_session_id
    if always_thinking_enabled and "always_thinking_enabled" not in ac:
        ac["always_thinking_enabled"] = True
    if effort and "effort" not in ac:
        ac["effort"] = effort
    if max_thinking_tokens and "max_thinking_tokens" not in ac:
        ac["max_thinking_tokens"] = max_thinking_tokens

    # pan_access: explicit nested dict wins; legacy flat kwargs fill gaps.
    pa = dict(pan_access) if pan_access else {}
    pa.setdefault("restrict_to_managed", restrict_to_managed)
    pa.setdefault("can_claim_unmanaged", can_claim_unmanaged)
    pa.setdefault("auto_claim_created", auto_claim_created)

    s = Session(
        id=_new_id(),
        name=name,
        adapter=adapter,
        model=model,
        permission_mode=permission_mode,
        pan_access=pa,
        adapter_config=ac,
        character_id=character_id,
        session_template=session_template,
        system_prompt=system_prompt,
        original_prompt=original_prompt,
        handoff_prompt=handoff_prompt,
        game_id=game_id,
        notification_settings=notification_settings,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=workdir,
        history=history or [],
        workspace_ids=workspace_ids,
    )
    save(s)
    _cache[s.id] = s
    return s


def _available_name(name: str, *, exclude_ids: set[str] | None = None) -> str:
    """Return the first unused session name, starting with ``name``.

    The caller must hold ``_STORE_LOCK`` when the result is used to create a
    session. ``exclude_ids`` is used by handoff so the session being replaced
    does not make its own name appear occupied.
    """
    excluded = exclude_ids or set()
    used = {s.name for s in list_all(load_history=False) if s.id not in excluded}
    if name not in used:
        return name
    suffix = 1
    while f"{name}-{suffix}" in used:
        suffix += 1
    return f"{name}-{suffix}"


_META_PROJECTION_UNSET = object()


def _meta_signature(
    s: Session, *, summary_projection: object = _META_PROJECTION_UNSET,
) -> str:
    """元数据（不含 history / updated_at）的稳定签名。

    用于判断主文件是否需要重写：history append 不改变元数据 → 跳过主文件写。

    ``summary_projection`` is an explicit durable-snapshot override used by
    the background repairer.  A cache can contain a newer unsaved projection;
    signing the cache as if that projection were already on disk would make
    the next ordered save incorrectly skip the main-file rewrite.
    """
    meta = s.to_dict()
    if summary_projection is not _META_PROJECTION_UNSET:
        meta["summary_projection"] = dict(summary_projection)
    meta.pop("history", None)
    meta.pop("updated_at", None)
    return repr(meta)


def _summary_metadata_signature(s: Session) -> str:
    """Stable signature of fields exposed by the lean Session summary."""
    value = {
        "id": s.id,
        "name": s.name,
        "adapter": s.adapter,
        "cli_session_id": s.cli_session_id,
        "model": s.model,
        "permission_mode": s.permission_mode,
        "adapter_config": s.adapter_config,
        "session_template": s.session_template,
        "workdir": s.workdir,
        "last_legal_worker_state": s.last_legal_worker_state,
        "total_usage": s.total_usage,
        "order": s.order,
        "workspace_ids": list(s.workspace_ids),
        "managed": list(s.managed),
        "managed_by": s.managed_by,
        "readonly_session": s.readonly_session,
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _ensure_summary_metadata(s: Session) -> None:
    with s._summary_lock:
        ensure_summary_projection(s)
        signature = _summary_metadata_signature(s)
        if signature != getattr(s, "_summary_meta_sig", None):
            s.summary_projection["revision"] = max(
                0, int(s.summary_projection.get("revision") or 0),
            ) + 1
            s.summary_projection["updated_at"] = datetime.now().isoformat()
            s._summary_meta_sig = signature


def _from_data_with_history(sid: str, data: dict) -> Session:
    """从主文件 data 构造 Session，并用 <id>.history.jsonl 合并 history。

    兼容新旧两种格式：
    - 旧格式（history 内嵌主文件）：无 jsonl → history 取 data["history"]；
    - 新格式（增量）：jsonl 存在 → history 以 jsonl 为准（可能比主文件新）。
    同时设置进程内增量游标 s._hist_persisted（已在 jsonl 中的条数）。
    """
    migrate_prompt = (
        "original_prompt" not in data or "handoff_prompt" not in data
        or "system_prompt" in data
    )
    _canonicalize_summary_projection_data(data)
    had_projection = is_complete_summary_projection(data.get("summary_projection"))
    s = Session._from_data(data)
    _migrate_legacy_fields(s)
    _migrate_session_usage(s)
    hist_path = _history_path(sid)
    if hist_path.exists():
        s.history = _strip_delivery_marks(_read_jsonl(hist_path))
    else:
        _strip_delivery_marks(s.history)
    s._hist_persisted = len(s.history)
    if had_projection:
        # The persisted projection is authoritative and already accounts for
        # the complete JSONL history.  A crash can nevertheless leave a newer
        # JSONL append beside the old metadata; compare the bounded fields now
        # that this is an explicit full load and repair that stale snapshot.
        persisted = _normalize_summary_projection(
            data.get("summary_projection"), fallback_updated_at=s.updated_at,
        )
        rebuilt = _summary_projection_from_history(
            s.history,
            revision=(int(persisted.get("revision") or 0) if persisted else 0),
            updated_at=persisted.get("updated_at", s.updated_at) if persisted else s.updated_at,
        )
        comparable = tuple(
            key for key in _SUMMARY_PROJECTION_KEYS
            if key not in {"revision", "updated_at"}
        )
        if persisted and any(rebuilt[key] != persisted[key] for key in comparable):
            s.summary_projection = _summary_projection_from_history(
                s.history,
                revision=max(1, int(persisted.get("revision") or 0) + 1),
                updated_at=datetime.now().isoformat(),
            )
            s._summary_projection_complete = True
            s._summary_history_index = len(s.history)
            had_projection = False  # persist the stale-snapshot repair below
        else:
            # Only its cursor needs to follow the loaded list so a
            # compatibility append can be reconciled incrementally.
            s._summary_history_index = len(s.history)
    else:
        # Missing or malformed projections are not authoritative even when
        # they are dict-shaped. Full Session GET is an explicit history load,
        # so rebuild from JSONL and persist only the metadata projection.
        prior = _normalize_summary_projection(
            data.get("summary_projection"), fallback_updated_at=s.updated_at,
        )
        prior_revision = (
            int(prior.get("revision") or 0) if isinstance(prior, dict) else 0
        )
        s.summary_projection = _summary_projection_from_history(
            s.history,
            revision=max(1, prior_revision + 1) if prior else 1,
            updated_at=s.updated_at,
        )
        s._summary_projection_complete = True
        s._summary_history_index = len(s.history)
    s._summary_meta_sig = _summary_metadata_signature(s)
    # Loading is read-only. The next explicit save writes canonical prompts,
    # even when no other metadata changed (including old JSONL-backed stores).
    # Loading remains read-only.  The startup repair worker owns automatic
    # persistence; an explicit caller save may still migrate the repaired
    # in-memory projection through the normal ordered writer.
    s._last_meta_sig = None if migrate_prompt or not had_projection else _meta_signature(s)
    return s


def _from_data_without_history(sid: str, data: dict) -> Session:
    """Load only Session metadata plus the persisted bounded projection.

    Older main JSON files may contain a small history tail but no projection.
    Use at most that already-parsed tail as a compatibility preview and mark
    the total unknown; never open the companion JSONL from a summary request.
    """
    payload = dict(data)
    raw_history = payload.pop("history", None)
    _canonicalize_summary_projection_data(payload)
    raw_projection = payload.get("summary_projection")
    had_projection = is_complete_summary_projection(raw_projection)
    s = Session._from_data(payload)
    if not had_projection and isinstance(raw_history, list):
        tail = [item for item in raw_history[-_MAIN_HISTORY_TAIL:]
                if isinstance(item, dict)]
        s.summary_projection = _summary_projection_from_history(
            tail, updated_at=s.updated_at,
        )
        # A companion JSONL or a non-empty main history may be longer than the
        # bounded cold tail. An explicitly empty main-only history is the one
        # legacy shape whose zero is authoritative without a scan.
        if raw_history or _history_path(sid).exists():
            s.summary_projection["history_total"] = None
            s._summary_projection_complete = False
        else:
            s._summary_projection_complete = True
        s._summary_history_index = 0
    else:
        tail = [item for item in (raw_history or [])[-_MAIN_HISTORY_TAIL:]
                if isinstance(item, dict)] if isinstance(raw_history, list) else []
        if not had_projection:
            # A companion JSONL may contain rows beyond the finite main-file
            # tail. Existence alone is enough to keep a cold total unknown;
            # the repair worker/full load is the only path that scans it.
            if _history_path(sid).exists():
                s.summary_projection["history_total"] = None
                s._summary_projection_complete = False
            else:
                s.summary_projection = _summary_projection_from_history(
                    tail, updated_at=s.updated_at,
                )
                s._summary_projection_complete = True
    # Keep the already-parsed main-file tail available to a metadata-only save
    # without promoting the companion JSONL into resident history.
    s._history_tail = tail
    s._history_loaded = False
    s._summary_meta_sig = _summary_metadata_signature(s)
    return s


def _hydrate_cached_session(session_id: str, cached: Session) -> Session | None:
    """Hydrate a shallow summary cache entry in place for full callers."""
    path = _path(session_id)
    if not path.exists():
        return None
    try:
        loaded = _from_data_with_history(
            session_id, json.loads(path.read_text(encoding="utf-8")),
        )
    except (json.JSONDecodeError, OSError):
        return None
    cached.__dict__.update(loaded.__dict__)
    cached._history_loaded = True
    _cache[session_id] = cached
    return cached


def get(session_id: str, *, load_history: bool = True) -> Session | None:
    if session_id in _cache:
        cached = _cache[session_id]
        if load_history and not getattr(cached, "_history_loaded", True):
            return _hydrate_cached_session(session_id, cached)
        return cached
    path = _path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not load_history:
            return _from_data_without_history(session_id, data)
        s = _from_data_with_history(session_id, data)
        _cache[session_id] = s
        return s
    except (json.JSONDecodeError, OSError):
        return None


def agent_level(session_id: str, *, load_history: bool = True) -> int:
    """Compute a session's agent level along its managedBy chain.

    Level 1 = no manager (managedBy is None). Each resolvable hop upward
    (session.managed_by → manager) adds 1, so a session managed by a level-1
    session is level 2, and so on.

    Edge cases:
    - Dangling managedBy (manager session deleted): the chain stops there and
      the session keeps the level reached so far (a broken link is treated as
      the top of the chain).
    - Cycles: a visited-set guards against managedBy loops (which claim()
      prevents but old data might contain) — the walk stops when a manager id
      repeats.
    - Unknown session_id → 1.

    Cost: O(depth) cache lookups per call (get() is an in-memory dict hit).
    """
    def lookup(sid: str):
        # A few embedders/tests replace get() with the historical one-argument
        # callable.  Keep the optional shallow-load optimization compatible
        # with those callers.
        try:
            return get(sid, load_history=load_history)
        except TypeError:
            return get(sid)

    seen: set[str] = {session_id}
    level = 1
    cur = lookup(session_id)
    while cur is not None:
        mb = cur.managed_by
        if not mb or mb in seen:
            break
        manager = lookup(mb)
        if manager is None:
            break  # dangling reference → treat as chain top
        seen.add(mb)
        level += 1
        cur = manager
    return level


def _prepare_history_for_save(s: Session, hist_path: Path, *, force_full: bool = False) -> None:
    """Hydrate a shallow Session before a metadata/queue save.

    Summary and queue readers intentionally keep cold Sessions shallow.  A
    later metadata mutation must not write an empty history tail over a legacy
    main-file-only Session, nor reset the JSONL cursor.  This is a save-time
    boundary: the explicit writer may pay the one full-history read, while
    ordinary list/summary/history-page reads remain bounded.
    """
    if getattr(s, "_history_loaded", True):
        return

    # New-format metadata/queue saves need no history hydration when the
    # shallow object has not received a history mutation.  _save_body will
    # preserve the parsed main-file tail and leave the JSONL cursor untouched.
    # An explicit append/replace makes s.history non-empty (or marks the
    # Session loaded), and then takes the full save-time boundary below.
    if (hist_path.exists() and not s.history and not force_full
            and getattr(s, "_summary_projection_complete", False)
            and is_complete_summary_projection(s.summary_projection)):
        return

    pending_history = list(s.history or [])
    if hist_path.exists():
        recovered = _strip_delivery_marks(_read_jsonl(hist_path))
        persisted = len(recovered)
    else:
        recovered = []
        persisted = 0
        try:
            data = json.loads(_path(s.id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        raw_history = data.get("history") if isinstance(data, dict) else None
        if isinstance(raw_history, list):
            recovered = _strip_delivery_marks(
                [row for row in raw_history if isinstance(row, dict)]
            )

    s.history = recovered
    s._hist_persisted = persisted
    s._history_loaded = True
    projection = getattr(s, "summary_projection", None)
    if not isinstance(projection, dict) or not is_complete_summary_projection(projection):
        revision = (
            int(projection.get("revision") or 0)
            if isinstance(projection, dict) else 0
        )
        s.summary_projection = _summary_projection_from_history(
            s.history, revision=revision, updated_at=s.updated_at,
        )
        s._summary_projection_complete = True
    s._summary_history_index = len(s.history)
    # A caller may have appended to the shallow object before reaching save;
    # preserve those rows after the on-disk baseline and let the normal
    # incremental projection reconciliation account for them.
    if pending_history:
        s.history.extend(pending_history)


def _run_persistence_ticket(state: _SessionSaveState, ticket: int,
                            enqueued_at: float, operation):
    """Run one ordered per-Session operation and always release its ticket."""
    started = False
    flush_started = 0.0
    error_type: str | None = None
    try:
        state.begin(ticket, enqueued_at)
        started = True
        flush_started = time.monotonic()
        return operation()
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if started:
            state.finish(
                flush_ms=(time.monotonic() - flush_started) * 1000.0,
                error_type=error_type,
            )
        else:
            state.cancel_before_begin(ticket)


def _save_body(s: Session, force_full: bool = False):
    """Write one Session while its per-Session ticket is active.

    The history ``[start, end)`` cursor is deliberately kept from T-062.2:
    appends that happen while the filesystem call is blocked remain for the
    next ticket and cannot be skipped.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = _history_path(s.id)
    _prepare_history_for_save(s, hist_path, force_full=force_full)
    shallow_existing_history = (
        not getattr(s, "_history_loaded", True)
        and hist_path.exists()
        and not s.history
    )
    # Append can happen from the event loop while this synchronous writer is
    # blocked in the filesystem.  Take both the cursor and this round's end
    # before the write, then never advance past that end.
    _ensure_summary_metadata(s)
    s.updated_at = datetime.now().isoformat()  # API reads the live object
    meta_sig = _meta_signature(s)
    start = getattr(s, "_hist_persisted", 0)
    if not isinstance(start, int) or start < 0:
        start = 0
    end = len(s.history)
    # 需要整重写的三种情况：显式 force、history 被整体替换（游标超出当前
    # 长度，说明 jsonl 里有作废条目）、jsonl 尚不存在（首次/旧格式迁移）。
    if not shallow_existing_history:
        rewrite_full = force_full or start > end or not hist_path.exists()
        if rewrite_full:
            history_to_write = s.history[:end]
            _write_jsonl(hist_path, history_to_write)
        else:
            history_to_write = s.history[start:end]
            _append_jsonl(hist_path, history_to_write)
        # Do not use len(s.history) here: appends that raced the write belong
        # to the next save and must not be skipped.
        s._hist_persisted = end

    # 元数据变化 → 重写主文件（元数据 + 尾部 history，常量序列化开销）。
    # 写临时文件 + os.replace 原子替换：主文件写一半崩溃也不会损坏
    # （history 真源在 jsonl，主文件只是元数据镜像 + 存在标记）。
    if force_full or meta_sig != getattr(s, "_last_meta_sig", None):
        d = s.to_dict()
        d.pop("system_prompt")  # derived API/export alias is not durable state
        d["history"] = (
            getattr(s, "_history_tail", [])
            if shallow_existing_history else s.history[-_MAIN_HISTORY_TAIL:]
        )
        main_path = _path(s.id)
        tmp_path = main_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(d, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp_path, main_path)
        s._last_meta_sig = meta_sig
    _cache[s.id] = s


def _save_sync_reserved(s: Session, force_full: bool,
                        state: _SessionSaveState, ticket: int,
                        enqueued_at: float):
    return _run_persistence_ticket(
        state, ticket, enqueued_at,
        lambda: _save_body(s, force_full=force_full),
    )


def _save_sync(s: Session, force_full: bool = False):
    """Synchronously enqueue one ordered per-Session persistence operation.

    - history 追加（自 s._hist_persisted 起的未落盘条目）到 <id>.history.jsonl；
    - 主文件仅在元数据变化（或首次 / 迁移 / force_full）时重写——纯 history
      append 不碰主文件，彻底消除 O(history) 全量序列化。

    force_full=True（首次创建 / 迁移 / history 整体替换）时整重写 jsonl。
    进程内内存 history 是权威，落盘是镜像；_hist_persisted 记录已镜像条数。
    """
    state, ticket, enqueued_at = _reserve_save_ticket(s.id)
    return _save_sync_reserved(s, force_full, state, ticket, enqueued_at)


def save(s: Session):
    """Sync save (for low-frequency server API calls)."""
    _save_sync(s)


def save_full(s: Session):
    """全量重写（主文件 + 完整 history jsonl）。

    用于 history 被整体替换而非追加的路径（reimport 覆盖 / 导入），
    避免增量游标把新 history 的头部误当作已落盘而跳过。
    """
    _save_sync(s, force_full=True)


async def save_async(s: Session):
    """Async save for high-frequency worker calls, ordered per Session.

    The executor task is shielded so cancelling the caller cannot abandon a
    ticket in the per-Session queue or leave a filesystem writer holding the
    state.  The caller still receives ``CancelledError`` after that durable
    operation has retired.
    """
    state, ticket, enqueued_at = _reserve_save_ticket(s.id)
    try:
        save_task = asyncio.create_task(asyncio.to_thread(
            _save_sync_reserved, s, False, state, ticket, enqueued_at,
        ))
    except BaseException:
        state.cancel_before_begin(ticket)
        raise
    try:
        return await asyncio.shield(save_task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(save_task)
        except BaseException:
            # Preserve the caller's cancellation while the worker thread has
            # nevertheless completed its release path and recorded failure.
            pass
        raise


# ── cold-start summary repair ──

_SUMMARY_BACKFILL_LOCK = threading.Lock()
_SUMMARY_BACKFILL_STATUS = {
    "state": "idle",
    "discovered": 0,
    "repaired": 0,
    "skipped": 0,
    "errors": 0,
    "startedAt": None,
    "completedAt": None,
}


def summary_backfill_status() -> dict:
    """Return bounded, process-local observability for cold-start repair."""
    with _SUMMARY_BACKFILL_LOCK:
        return dict(_SUMMARY_BACKFILL_STATUS)


def _set_summary_backfill_status(**updates) -> None:
    with _SUMMARY_BACKFILL_LOCK:
        _SUMMARY_BACKFILL_STATUS.update(updates)


def _repair_summary_projection_file(session_id: str) -> bool:
    """Repair one metadata file while holding its ordered save ticket."""
    main_path = _path(session_id)
    data = json.loads(main_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return False

    had_projection_alias = any(
        alias in data for alias in _SUMMARY_PROJECTION_DATA_ALIASES
    )
    _canonicalize_summary_projection_data(data)
    raw_projection = data.get("summary_projection")
    if is_complete_summary_projection(raw_projection) and not had_projection_alias:
        return False
    prior = _normalize_summary_projection(raw_projection, fallback_updated_at=data.get("updated_at", ""))
    prior_revision = int(prior.get("revision") or 0) if prior else 0
    if is_complete_summary_projection(raw_projection):
        # A complete aliased projection needs only canonicalization.  Do not
        # scan JSONL on this path; the durable projection is already the
        # bounded source of truth for a cold summary.
        projection = _normalize_summary_projection(
            raw_projection, fallback_updated_at=data.get("updated_at", ""),
        )
    else:
        hist_path = _history_path(session_id)
        try:
            hist_path.stat()
        except FileNotFoundError:
            history_file_present = False
        except OSError:
            # Path.exists() intentionally collapses some access errors into
            # False on modern Python.  stat() keeps an unreadable source from
            # being mistaken for a legitimate legacy no-JSONL session.
            raise
        else:
            history_file_present = True
        if history_file_present:
            # OSError must escape this helper.  A failed source read is not a
            # valid empty history and therefore must not be atomically
            # promoted to a complete zero projection.
            projection = _summary_projection_from_jsonl(
                hist_path,
                revision=max(1, prior_revision + 1) if prior else 1,
                updated_at=data.get("updated_at", ""),
            )
        else:
            raw_history = data.get("history")
            history = _strip_delivery_marks(
                [row for row in raw_history if isinstance(row, dict)]
                if isinstance(raw_history, list) else []
            )
            projection = _summary_projection_from_history(
                history,
                revision=max(1, prior_revision + 1) if prior else 1,
                updated_at=data.get("updated_at", ""),
            )
    if projection is None:
        # This is defensive for malformed aliased input; all normal builders
        # above return a dict.  Treat it as a failed repair rather than writing
        # an incomplete object.
        raise ValueError("summary projection normalization failed")
    data["summary_projection"] = projection
    tmp_path = main_path.with_name(f"{main_path.name}.summary.tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(tmp_path, main_path)
    finally:
        # A failed replace must not leave a misleading repair artifact behind.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

    # Keep an already-cached cold Session aligned with the durable repair.  A
    # cache can race this worker: its history/projection may include an append
    # whose save ticket is still queued.  Preserve that newer in-memory view,
    # but sign the cache against the projection actually written above.  The
    # next ordered save then sees a real signature delta and rewrites the main
    # metadata after appending JSONL; it cannot mistake the cache for durable.
    cached = _cache.get(session_id)
    if cached is not None:
        with cached._summary_lock:
            cached_meta_sig = _meta_signature(cached)
            last_meta_sig = getattr(cached, "_last_meta_sig", None)
            history_loaded = getattr(cached, "_history_loaded", True)
            persisted_count = getattr(cached, "_hist_persisted", 0)
            if not isinstance(persisted_count, int) or persisted_count < 0:
                persisted_count = 0
            has_pending_history = (
                (history_loaded and persisted_count < len(cached.history))
                or (not history_loaded and bool(cached.history))
            )
            if history_loaded:
                has_unsaved_cache = (
                    last_meta_sig is None
                    or cached_meta_sig != last_meta_sig
                    or has_pending_history
                )
            else:
                # A shallow object created by list_all() has no in-memory
                # history and historically has no baseline signature.  That
                # absence is not an unsaved edit: promote it to the durable
                # repair result so same-process summaries converge too.
                has_unsaved_cache = (
                    bool(cached.history)
                    or (
                        last_meta_sig is not None
                        and cached_meta_sig != last_meta_sig
                    )
                )
            if not has_unsaved_cache:
                cached.summary_projection = projection
                cached._summary_projection_complete = True
                cached._summary_history_index = (
                    len(cached.history) if history_loaded else 0
                )
            elif history_loaded:
                # A hydrated object should already have a complete projection,
                # but repair defensively rebuilds it if a legacy caller left a
                # malformed private state.  It never replaces a newer valid
                # projection that includes an unsaved append.
                if not is_complete_summary_projection(cached.summary_projection):
                    cached_revision = int(
                        cached.summary_projection.get("revision") or 0
                    ) if isinstance(cached.summary_projection, dict) else 0
                    cached.summary_projection = _summary_projection_from_history(
                        cached.history,
                        revision=max(1, cached_revision, prior_revision + 1),
                        updated_at=cached.updated_at,
                    )
                    cached._summary_projection_complete = True
                cached._summary_history_index = len(cached.history)
            # A shallow cache with pending rows must retain its incomplete
            # state so _prepare_history_for_save hydrates the durable JSONL
            # baseline and applies only those pending rows once.
            cached._summary_meta_sig = _summary_metadata_signature(cached)
            cached._last_meta_sig = _meta_signature(
                cached, summary_projection=projection,
            )
    return True


def _backfill_cancel_requested(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def backfill_summary_projections_sync(
    *, cancel_event: threading.Event | None = None,
) -> dict:
    """Sequentially repair incomplete projections without reading JSONL on the loop.

    The scanner creates no per-session tasks and retains no message batches
    beyond the one Session currently being repaired.  Each file operation is
    behind the same per-Session FIFO ticket as normal save/save_async calls.
    Only ``summary_projection`` is changed in the main JSON; history JSONL is
    read as the source of truth and never rewritten.  ``cancel_event`` is
    checked between Sessions, so shutdown lets the current ticket finish its
    atomic operation and then stops before the next file.
    """
    started_at = datetime.now().isoformat()
    _set_summary_backfill_status(
        state="running", discovered=0, repaired=0, skipped=0, errors=0,
        startedAt=started_at, completedAt=None,
    )
    discovered = repaired = skipped = errors = 0
    try:
        paths = sorted(SESSION_DIR.glob("*.json")) if SESSION_DIR.exists() else []
        for main_path in paths:
            if _backfill_cancel_requested(cancel_event):
                break
            try:
                data = json.loads(main_path.read_text(encoding="utf-8"))
                sid = data.get("id") if isinstance(data, dict) else None
            except (OSError, json.JSONDecodeError):
                errors += 1
                continue
            if not isinstance(sid, str) or not sid:
                skipped += 1
                continue
            if not isinstance(data, dict):
                skipped += 1
                continue
            had_projection_alias = any(
                alias in data for alias in _SUMMARY_PROJECTION_DATA_ALIASES
            )
            _canonicalize_summary_projection_data(data)
            if (
                is_complete_summary_projection(data.get("summary_projection"))
                and not had_projection_alias
            ):
                skipped += 1
                continue
            if _backfill_cancel_requested(cancel_event):
                break
            discovered += 1
            state, ticket, enqueued_at = _reserve_save_ticket(sid)
            try:
                repaired_one = _run_persistence_ticket(
                    state, ticket, enqueued_at,
                    lambda sid=sid: _repair_summary_projection_file(sid),
                )
                repaired += int(bool(repaired_one))
                skipped += int(not repaired_one)
            except Exception:
                errors += 1
    except Exception:
        # Directory enumeration or another scanner-level failure is terminal
        # for this pass; report it as failed instead of falsely completing.
        errors += 1
    finally:
        if errors:
            final_state = "failed"
        elif _backfill_cancel_requested(cancel_event):
            final_state = "cancelled"
        else:
            final_state = "completed"
        _set_summary_backfill_status(
            state=final_state, discovered=discovered, repaired=repaired,
            skipped=skipped, errors=errors, completedAt=datetime.now().isoformat(),
        )
    return summary_backfill_status()


async def backfill_summary_projections(
    *, cancel_event: threading.Event | None = None,
) -> dict:
    """Run cold-start repair in one worker thread, never on asyncio's loop."""
    return await asyncio.to_thread(
        backfill_summary_projections_sync, cancel_event=cancel_event,
    )


@_store_serialized
def delete(session_id: str):
    # Delete is ordered behind already queued writes for this Session.  A
    # global name/index operation may hold _STORE_LOCK while waiting here; the
    # writer never takes _STORE_LOCK after entering its per-Session state.
    state, ticket, enqueued_at = _reserve_save_ticket(session_id)

    def remove_files():
        _remove_session_pin_locked(session_id)
        path = _path(session_id)
        if path.exists():
            path.unlink()
        hist_path = _history_path(session_id)
        if hist_path.exists():
            hist_path.unlink()
        _cache.pop(session_id, None)
        _newline_terminated_jsonl.discard(str(hist_path))  # 文件已删，缓存作废

    try:
        _run_persistence_ticket(state, ticket, enqueued_at, remove_files)
    finally:
        # No caller can reserve on this state between the atomic reservation
        # above and this cleanup.  Future writes to a deleted Session ID get a
        # fresh state rather than growing diagnostics forever.
        with _STORE_LOCK:
            if _SAVE_STATES.get(session_id) is state:
                _SAVE_STATES.pop(session_id, None)


def expand_managed_descendants(root_ids: list[str]) -> list[str]:
    """Return existing descendants of roots in child-before-parent order.

    ``managed`` is the authoritative persisted relationship for deletion.
    Missing child ids are ignored, and a visited set makes corrupt cycles and
    shared descendants harmless. Roots are not included in the result.
    """
    visited: set[str] = set()
    root_set = set(root_ids)
    descendants: list[str] = []

    def visit(session_id: str) -> None:
        current = get(session_id)
        if current is None or session_id in visited:
            return
        visited.add(session_id)
        for child_id in current.managed:
            visit(child_id)
        if session_id not in root_set:
            descendants.append(session_id)

    for root_id in root_ids:
        visit(root_id)
    return descendants


def effective_workspace_ids(session_or_id: "Session | str") -> list[str]:
    """Resolve membership from the managed-tree root without rewriting legacy rows.

    Broken/cyclic chains fail closed to ungrouped. Only a true root's persisted
    value is authoritative; old child workspace_ids are deliberately ignored.
    """
    current = get(session_or_id, load_history=False) if isinstance(session_or_id, str) else session_or_id
    seen: set[str] = set()
    while current is not None:
        if current.id in seen:
            return []
        seen.add(current.id)
        if not current.managed_by:
            return list(current.workspace_ids[:1])
        # Membership needs only metadata. A cold summary request must not
        # hydrate a manager's potentially long JSONL history for every child.
        parent = get(current.managed_by, load_history=False)
        if parent is None:
            return []
        current = parent
    return []


@_store_serialized
def claim(manager_id: str, session_id: str) -> str | None:
    """Set a bidirectional managed relationship (立项 4.2).

    Establishes: manager.managed += [session_id], session.managed_by = manager_id.

    Claim 建立 managed 关系时默认自动 report_subscribe：manager.report_subscriptions
    自动加入 session_id，使该 manager 收到目标 session 的完成报告（done/error）。
    关系两端按序持久化；若写入失败，会回滚内存状态并尽力持久化回滚，返回错误。

    Refuses (returns an error string) if the target session is already managed
    by a different existing session. A dangling manager reference is treated
    as unmanaged so the target can be recovered.
    Refuses self-claim (manager_id == session_id): a session cannot manage or
    subscribe to itself.

    Returns None on success, or an error message string on refusal.
    """
    if manager_id == session_id:
        return f"Cannot claim itself ({session_id})"
    manager = get(manager_id)
    if manager is None:
        return f"Manager session {manager_id} not found"
    target = get(session_id)
    if target is None:
        return f"Session {session_id} not found"
    # Enforce the acyclic manager-tree invariant at the persistence boundary;
    # browser drag validation alone cannot protect MCP/API callers.
    current = manager
    ancestry_seen: set[str] = set()
    while current is not None:
        if current.id == session_id:
            return f"Cannot claim {session_id}: it is an ancestor of manager {manager_id}"
        if current.id in ancestry_seen:
            return f"Cannot claim into corrupt manager cycle at {current.id}"
        ancestry_seen.add(current.id)
        if not current.managed_by:
            break
        current = get(current.managed_by)
        if current is None:
            return f"Cannot claim into broken manager chain for {manager_id}"
    # A deleted manager can leave historical data with a dangling managed_by.
    # Treat that reference as unmanaged so the target can be recovered.
    if target.managed_by and target.managed_by != manager_id \
            and get(target.managed_by) is not None:
        return f"Session {session_id} is managed by {target.managed_by}, not {manager_id}"
    old_managed = list(manager.managed)
    old_subscriptions = set(manager.report_subscriptions)
    old_managed_by = target.managed_by
    old_workspace_ids = list(target.workspace_ids)
    changed_manager = False
    changed_target = False
    try:
        if session_id not in manager.managed:
            manager.managed.append(session_id)
            changed_manager = True
        if session_id not in manager.report_subscriptions:
            manager.report_subscriptions.add(session_id)
            changed_manager = True
        if target.managed_by != manager_id or target.workspace_ids:
            target.managed_by = manager_id
            # A child never persists a competing/stale membership value.
            target.workspace_ids = []
            changed_target = True
        # Persist target first so a failure cannot leave a durable manager
        # backlink to a target whose managed_by was never written.
        if changed_target:
            save(target)
        if changed_manager:
            save(manager)
    except Exception as exc:
        # Restore both cached objects and best-effort persist the rollback. If
        # storage itself remains unavailable, return an error rather than
        # reporting a successful claim with a one-sided relationship.
        manager.managed = old_managed
        manager.report_subscriptions = old_subscriptions
        target.managed_by = old_managed_by
        target.workspace_ids = old_workspace_ids
        rollback_errors = []
        for changed, session in ((changed_target, target), (changed_manager, manager)):
            if changed:
                try:
                    save(session)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
        detail = f"Failed to persist managed relationship: {exc}"
        if rollback_errors:
            detail += f"; rollback persistence failed: {'; '.join(rollback_errors)}"
        return detail
    return None


@_store_serialized
def release(session_id: str) -> str | None:
    """Remove a Session and its relationship references before storage deletion.

    Direct children are promoted to roots when this Session has no manager.
    Otherwise they are reparented to its manager, including when that manager
    is itself a root. Descendants below those direct children retain their
    existing parent links and therefore keep inheriting the same root
    Workspace.

    Relationship metadata is validated before mutation. A broken, dangling,
    one-sided, duplicated, or cyclic managed graph refuses release so callers
    cannot delete a Session while leaving children pointing at a missing
    manager. Writes are ordered child-first and rolled back in memory and on
    disk as far as storage allows if any save fails.

    Report subscriptions to the deleted Session are removed globally. When
    children are reparented, their old manager's per-child subscription
    setting is copied to the new manager; subscriptions for other children
    already managed by the new manager are left untouched.

    Returns None on success, or an error message string.
    """
    try:
        all_sessions = list_all(load_history=False)
        by_id = {s.id: s for s in all_sessions}
        owners: dict[str, list[str]] = {}
        for manager in all_sessions:
            if not isinstance(manager.managed, list):
                continue
            for child_id in manager.managed:
                if isinstance(child_id, str):
                    owners.setdefault(child_id, []).append(manager.id)

        target = by_id.get(session_id)
        if target is None:
            dangling = [
                s.id for s in all_sessions
                if s.managed_by == session_id
                or session_id in s.managed
                or session_id in s.report_subscriptions
            ]
            if dangling:
                return (f"Cannot release missing Session {session_id}: "
                        f"relationship references remain on {', '.join(dangling)}")
            return None

        def validate_direct_links(manager: Session) -> str | None:
            if not isinstance(manager.managed, list):
                return f"Session {manager.id} has invalid managed links"
            if any(not isinstance(child_id, str) or not child_id
                   for child_id in manager.managed):
                return f"Session {manager.id} has invalid managed child id"
            if len(manager.managed) != len(set(manager.managed)):
                return f"Session {manager.id} has duplicate managed child links"
            if manager.id in manager.managed:
                return f"Session {manager.id} manages itself"
            expected = set(manager.managed)
            actual = {s.id for s in all_sessions if s.managed_by == manager.id}
            if expected != actual:
                return (f"Session {manager.id} managed links do not match child "
                        "managed_by backlinks")
            for child_id in manager.managed:
                child = by_id.get(child_id)
                if child is None:
                    return f"Session {manager.id} references missing child {child_id}"
                if child.managed_by != manager.id:
                    return (f"Session {manager.id} child {child_id} has a "
                            "different managed_by backlink")
                child_owners = owners.get(child_id, [])
                if child_owners != [manager.id]:
                    return f"Session {child_id} has inconsistent manager backlinks"
            return None

        # Validate the target's full managed subtree, catching cycles and
        # malformed links before deriving or applying any Workspace changes.
        active: set[str] = set()
        visited: set[str] = set()

        def validate_subtree(manager: Session) -> str | None:
            if manager.id in active:
                return f"Managed cycle detected at Session {manager.id}"
            if manager.id in visited:
                return f"Managed graph has a shared child at Session {manager.id}"
            link_error = validate_direct_links(manager)
            if link_error:
                return link_error
            parent_id = manager.managed_by
            parent_owners = owners.get(manager.id, [])
            if parent_id:
                parent = by_id.get(parent_id)
                if parent is None:
                    return (f"Session {manager.id} has missing manager "
                            f"{parent_id}")
                if parent_owners != [parent_id] or parent.managed.count(manager.id) != 1:
                    return f"Session {manager.id} has inconsistent manager backlinks"
            elif parent_owners:
                return f"Root Session {manager.id} is listed by a manager"
            active.add(manager.id)
            visited.add(manager.id)
            for child_id in manager.managed:
                child_error = validate_subtree(by_id[child_id])
                if child_error:
                    return child_error
            active.remove(manager.id)
            return None

        # Validate the full child subtree. Ancestors are walked separately so
        # cycles above the target cannot be hidden by a valid child subtree.
        graph_error = validate_subtree(target)
        if graph_error:
            return f"Cannot release Session {session_id}: {graph_error}"

        ancestor_seen = {session_id}
        current = target
        while current.managed_by:
            manager_id = current.managed_by
            manager = by_id.get(manager_id)
            if manager is None:
                return (f"Cannot release Session {session_id}: missing manager "
                        f"{manager_id}")
            if manager_id in ancestor_seen:
                return (f"Cannot release Session {session_id}: managed cycle "
                        f"detected at {manager_id}")
            # The manager itself is about to be edited when it is the direct
            # parent. Validate all of its direct edges before replacing A.
            link_error = validate_direct_links(manager)
            if link_error:
                return f"Cannot release Session {session_id}: {link_error}"
            if manager.managed.count(current.id) != 1:
                return (f"Cannot release Session {session_id}: manager "
                        f"{manager_id} does not contain exactly one backlink")
            if owners.get(current.id, []) != [manager_id]:
                return (f"Cannot release Session {session_id}: inconsistent "
                        f"manager backlinks for {current.id}")
            ancestor_seen.add(manager_id)
            current = manager

        parent = by_id.get(target.managed_by) if target.managed_by else None
        direct_children = [by_id[child_id] for child_id in target.managed]
        child_ids = [child.id for child in direct_children]
        old_target_subscriptions = set(target.report_subscriptions)

        # Capture only relationship fields touched by release. The target is
        # included because its old manager/list/subscription state is cleared
        # before the caller removes its files.
        originals: dict[str, dict] = {}
        desired: dict[str, dict] = {}

        def ensure_change(session: Session) -> dict:
            if session.id not in originals:
                originals[session.id] = {
                    "managed": list(session.managed),
                    "managed_by": session.managed_by,
                    "workspace_ids": list(session.workspace_ids),
                    "report_subscriptions": set(session.report_subscriptions),
                }
                desired[session.id] = {
                    "managed": list(session.managed),
                    "managed_by": session.managed_by,
                    "workspace_ids": list(session.workspace_ids),
                    "report_subscriptions": set(session.report_subscriptions),
                }
            return desired[session.id]

        # Remove subscriptions to the deleted Session from every surviving
        # record, regardless of who originally created that subscription.
        for session in all_sessions:
            if session.id == session_id:
                continue
            if session_id in session.report_subscriptions:
                ensure_change(session)["report_subscriptions"].discard(session_id)

        if parent is not None:
            parent_state = ensure_change(parent)
            replaced_managed: list[str] = []
            for child_id in parent_state["managed"]:
                if child_id == session_id:
                    replaced_managed.extend(child_ids)
                else:
                    replaced_managed.append(child_id)
            parent_state["managed"] = replaced_managed
            parent_subscriptions = parent_state["report_subscriptions"]
            for child_id in child_ids:
                if child_id in old_target_subscriptions:
                    parent_subscriptions.add(child_id)
                else:
                    parent_subscriptions.discard(child_id)

        root_workspace_ids = (
            effective_workspace_ids(target)[:1] if parent is None else []
        )
        for child in direct_children:
            child_state = ensure_change(child)
            child_state["managed_by"] = parent.id if parent is not None else None
            # Managed children inherit from the new parent. Only a child
            # promoted from a root manager receives the old effective value.
            child_state["workspace_ids"] = [] if parent is not None else list(root_workspace_ids)

        target_state = ensure_change(target)
        target_state["managed"] = []
        target_state["managed_by"] = None
        target_state["report_subscriptions"] = set()

        # Persist new children before the manager backlink, unrelated
        # subscription cleanup next, and the record being deleted last.
        save_order: list[str] = []

        def queue_save(session: Session) -> None:
            if session.id in desired and session.id not in save_order:
                save_order.append(session.id)

        for child in direct_children:
            queue_save(child)
        if parent is not None:
            queue_save(parent)
        for session in all_sessions:
            if session.id != session_id:
                queue_save(session)
        queue_save(target)

        # Include no-op parent/child objects only when an actual field changed.
        changed = {
            sid for sid, state in desired.items()
            if state != originals[sid]
        }
        save_order = [sid for sid in save_order if sid in changed]
        attempted: list[str] = []

        def assign_state(sid: str, state: dict) -> None:
            session = by_id[sid]
            session.managed = list(state["managed"])
            session.managed_by = state["managed_by"]
            session.workspace_ids = list(state["workspace_ids"])
            session.report_subscriptions = set(state["report_subscriptions"])

        try:
            for sid, state in desired.items():
                assign_state(sid, state)
            for sid in save_order:
                attempted.append(sid)
                save(by_id[sid])
        except Exception as exc:
            for sid, state in originals.items():
                assign_state(sid, state)
            rollback_errors = []
            for sid in reversed(attempted):
                try:
                    save(by_id[sid])
                except Exception as rollback_exc:
                    rollback_errors.append(f"{sid}: {rollback_exc}")
            detail = f"Failed to persist Session release: {exc}"
            if rollback_errors:
                detail += "; rollback persistence failed: " + "; ".join(rollback_errors)
            return f"Cannot release Session {session_id}: {detail}"
    except Exception as exc:
        return f"Cannot release Session {session_id}: {type(exc).__name__}: {exc}"
    return None


@_store_serialized
def delete_with_release(session_id: str) -> str | None:
    """Release managed relationships and delete one Session as one operation.

    The relationship changes are rolled back if the Session file removal
    fails. This is the deletion entry point for server APIs; direct ``delete``
    remains available for store maintenance that already owns relationship
    handling.
    """
    all_sessions = list_all(load_history=False)
    originals = {
        session.id: {
            "managed": list(session.managed),
            "managed_by": session.managed_by,
            "workspace_ids": list(session.workspace_ids),
            "report_subscriptions": set(session.report_subscriptions),
        }
        for session in all_sessions
    }
    target = next((session for session in all_sessions if session.id == session_id), None)

    release_error = release(session_id)
    if release_error:
        return release_error
    try:
        delete(session_id)
    except Exception as exc:
        rollback_errors = []
        changed: list[Session] = []
        for session in all_sessions:
            old = originals[session.id]
            current = {
                "managed": list(session.managed),
                "managed_by": session.managed_by,
                "workspace_ids": list(session.workspace_ids),
                "report_subscriptions": set(session.report_subscriptions),
            }
            if current != old or session.id == session_id:
                session.managed = list(old["managed"])
                session.managed_by = old["managed_by"]
                session.workspace_ids = list(old["workspace_ids"])
                session.report_subscriptions = set(old["report_subscriptions"])
                changed.append(session)
        if target is not None:
            # delete() may have removed metadata before a later filesystem
            # operation failed. Reinsert the old object so save can restore it.
            _cache[session_id] = target
        for session in reversed(changed):
            try:
                save(session)
            except Exception as rollback_exc:
                rollback_errors.append(f"{session.id}: {rollback_exc}")
        detail = f"Failed to delete Session {session_id}: {exc}"
        if rollback_errors:
            detail += "; relationship rollback persistence failed: " + "; ".join(rollback_errors)
        return detail
    return None


@_store_serialized
def unclaim(manager_id: str, session_id: str) -> str | None:
    """Remove the managed relationship (manager_id → session_id).

    Only the current manager may unclaim when it still exists. If the target's
    manager reference is dangling, an existing manager may recover/unclaim it.
    Also purges the caller's ``report_subscriptions`` for session_id
    (解除管理即退订完成报告).
    Refuses self-unclaim (manager_id == session_id, defensive).

    Returns None on success, or an error message string.
    """
    if manager_id == session_id:
        return f"Cannot unclaim itself ({session_id})"
    manager = get(manager_id)
    target = get(session_id)
    if target is None:
        return f"Session {session_id} not found"
    if manager is None:
        if target.managed_by == manager_id:
            # The manager was deleted outside the normal release path.  Clear
            # the dangling reference directly; there is no manager to update.
            target.workspace_ids = effective_workspace_ids(target)
            target.managed_by = None
            save(target)
            return None
        if target.managed_by and get(target.managed_by) is not None:
            return f"Session {session_id} is not managed by {manager_id}"
        return f"Manager session {manager_id} not found"
    # Normal relationships remain exclusive.  A missing manager is a stale
    # historical reference, so any existing manager may recover/unclaim it.
    if (target.managed_by != manager_id
            and target.managed_by
            and get(target.managed_by) is not None):
        return f"Session {session_id} is not managed by {manager_id}"
    manager.report_subscriptions.discard(session_id)
    if session_id in manager.managed:
        manager.managed.remove(session_id)
        save(manager)
    target.workspace_ids = effective_workspace_ids(target)
    target.managed_by = None
    save(target)
    return None


@_store_serialized
def handoff_session(
    session_id: str,
    handoff_prompt: str,
    *,
    copy_settings: bool = True,
    adapter: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
) -> tuple[Session, Session] | str:
    """替身交接（session_handoff v1）：创建孪生 session B 接替 session A。

    用途：精简上下文（B 全新会话，不继承 A 的 history / cli_session_id），或
    切换 adapter（普通 session 不能中途切 adapter）。

    行为：
    1. **关系网接替（自动、必然）**：B.managed = A.managed，A 的子会话
       managed_by 改 B；A 的 report_subscriptions / QQ postbox 绑定（qq_subscriptions）
       转移给 B；A 若曾被某 manager 管理，B 接替 A 在该 manager 下的位置。
    2. **B 自动 manage A**：B.managed 追加 A，A.managed_by = B（A 归档为 B 的
       被管理会话，B 订阅 A 的完成报告）。
    3. **可选设置复制（copy_settings）**：true 时 1:1 复制 A 的设置（adapter、
       adapter_config、model、permission_mode、session_template、pan_access、
       mcp_servers 等，**明确不含 system_prompt**；cli_session_id 清空——B 是
       全新会话）；false 时 B 用默认设置（此时调用方应显式传 adapter）。
    4. **B.original_prompt = A.original_prompt，B.handoff_prompt = 本次简报**。
       B.system_prompt 仅计算本次简报 + original_prompt，不继承旧简报。
    5. **重命名**：A → `(archive) <原名>`，B → `<原名>`。
    6. **解除 A 的原关系网**：A.managed / report_subscriptions / qq_subscriptions
       清空（A.managed_by 保留 = B，见第 2 条）。

    Returns (A, B) on success, or an error message string.
    """
    a = get(session_id)
    if a is None:
        return f"Session {session_id} not found"
    if not handoff_prompt or not handoff_prompt.strip():
        return "handoff_prompt is required — session A 的 agent 必须编写交接简报"

    orig_name = a.name

    # ── 1. 创建 B：可选 1:1 复制 A 的设置（不含 system_prompt）──
    if copy_settings:
        new_adapter = adapter or a.adapter
        new_model = model or a.model
        new_permission_mode = permission_mode or a.permission_mode
        new_adapter_config = copy.deepcopy(a.adapter_config)
        new_adapter_config.pop("cli_session_id", None)  # B 是全新会话，不继承 A 的 CLI 上下文
        new_pan_access = copy.deepcopy(dict(a.pan_access))
        new_character_id = a.character_id
        new_template = a.session_template
        new_game_id = a.game_id
        new_notification_settings = copy.deepcopy(a.notification_settings)
    else:
        new_adapter = adapter or "cbc"
        new_model = model
        new_permission_mode = permission_mode
        new_adapter_config = {}
        new_pan_access = {}
        new_character_id = None
        new_template = None
        new_game_id = None
        new_notification_settings = None

    # Allocate and persist B while holding the global store lock.
    # This closes the check/create window between concurrent handoffs. A is
    # excluded because it is about to be archived and must not force a suffix.
    with _STORE_LOCK:
        b = create(
            name=_available_name(orig_name, exclude_ids={a.id}),
            adapter=new_adapter,
            model=new_model,
            permission_mode=new_permission_mode,
            adapter_config=new_adapter_config,
            character_id=new_character_id,
            session_template=new_template,
            original_prompt=a.original_prompt,
            handoff_prompt=handoff_prompt.strip(),
            game_id=new_game_id,
            notification_settings=new_notification_settings,
            pan_access=new_pan_access,
            workdir=a.workdir,
            workspace_ids=effective_workspace_ids(a) if not a.managed_by else [],
        )

    # ── 2. 关系网接替 ──
    # 2a. A 的子会话 → 改由 B 管理
    for child_id in list(a.managed):
        child = get(child_id)
        if child is not None:
            child.managed_by = b.id
            child.workspace_ids = []
            save(child)
    b.managed = list(a.managed)

    # 2b. B 自动 manage A（A 归档为 B 的被管理会话；B 订阅 A 的报告）
    b.managed.append(a.id)
    b.report_subscriptions = set(a.report_subscriptions)
    b.report_subscriptions.add(a.id)

    # 2c. A 的原父 manager → B 接替 A 的位置（A 曾被他人管理时）
    parent_id = a.managed_by
    if parent_id:
        parent = get(parent_id)
        if parent is not None:
            if a.id in parent.managed:
                parent.managed[parent.managed.index(a.id)] = b.id
            if a.id in parent.report_subscriptions:
                parent.report_subscriptions.discard(a.id)
                parent.report_subscriptions.add(b.id)
            save(parent)
        b.managed_by = parent_id

    # 2d. 其它会话对 A 的 report 订阅 → 改指向 B（一般即原父 manager，兜底全量扫）
    for s in list_all(load_history=False):
        if s.id in (a.id, b.id):
            continue
        if a.id in s.report_subscriptions:
            s.report_subscriptions.discard(a.id)
            s.report_subscriptions.add(b.id)
            save(s)

    # 2e. QQ postbox 绑定 → B
    b.qq_subscriptions = set(a.qq_subscriptions)
    # 2f. 微信订阅 → B（与 QQ 平行：handoff 后由 B 继续接收该会话提醒）
    b.wechat_subscriptions = set(a.wechat_subscriptions)

    # ── 3. 解除 A 的原关系网（A.managed_by 保留 = B，见 2b）──
    a.managed = []
    a.managed_by = b.id
    a.workspace_ids = []
    a.report_subscriptions = set()
    a.qq_subscriptions = set()
    a.wechat_subscriptions = set()
    # Re-check the archive name after relationship work. Another handoff may
    # have archived a session while this one was transferring relationships.
    # Keep allocation and save atomic under the same lock as B creation.
    with _STORE_LOCK:
        a.name = _available_name(
            f"(archive) {orig_name}", exclude_ids={a.id, b.id})
        save(a)
    save(b)
    return a, b


_all_loaded: bool = False


@_store_serialized
def list_all(*, load_history: bool = True) -> list[Session]:
    global _all_loaded
    if not _all_loaded:
        if SESSION_DIR.exists():
            for f in sorted(SESSION_DIR.iterdir()):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        sid = data.get("id")
                        # 不覆盖已缓存的 Session（worker 可能在 _read_stdout
                        # 里 append 了 history 但还没 save，磁盘版本更旧）
                        if sid and sid not in _cache:
                            s = (
                                _from_data_with_history(sid, data)
                                if load_history
                                else _from_data_without_history(sid, data)
                            )
                            _cache[sid] = s
                    except (json.JSONDecodeError, OSError):
                        pass
        _all_loaded = True
    if load_history:
        # A previous summary=1 request may have populated shallow cache entries.
        # Hydrate those entries only when a caller explicitly asks for full
        # history; ordinary summary/list reads never touch companion JSONL.
        for sid, cached in list(_cache.items()):
            if not getattr(cached, "_history_loaded", True):
                _hydrate_cached_session(sid, cached)
    # after initial load, cache is always current (create/save/delete sync it)
    # 排序：显式 order 优先（升序）；未排序（order=None）按 created_at 排在末尾，
    # 因此新建 session 自然出现在列表底部，已有自定义顺序不被打乱。
    return sorted(_cache.values(),
                  key=lambda s: (s.order is None,
                                 s.order if s.order is not None else 0,
                                 s.created_at))


@_store_serialized
def apply_order(ordered_ids: list[str]) -> str | None:
    """Persist a user-defined display order for the session list.

    ``ordered_ids`` is the desired display order (as submitted by the
    dashboard after a drag & drop). Sessions not listed keep their current
    relative order (list_all() ordering) and are appended after the listed
    ones. Afterwards every session carries an explicit integer ``order``
    value, so the ranking is dense and stable across restarts.

    Consistency:
    - delete: the session file (and its order) disappears; remaining
      relative order is unaffected (gaps in order values are harmless);
    - create: new sessions start with order=None and sort to the end until
      the next reorder;
    - rename: order is untouched.

    Returns None on success, or an error message string when ordered_ids
    contains duplicates or unknown session ids (nothing is modified then).
    """
    if len(set(ordered_ids)) != len(ordered_ids):
        return "ordered session ids contain duplicates"
    all_sessions = list_all(load_history=False)
    by_id = {s.id: s for s in all_sessions}
    unknown = [sid for sid in ordered_ids if sid not in by_id]
    if unknown:
        return f"Unknown session id(s): {', '.join(unknown)}"
    listed = [by_id[sid] for sid in ordered_ids]
    listed_ids = set(ordered_ids)
    rest = [s for s in all_sessions if s.id not in listed_ids]
    for i, s in enumerate(listed + rest):
        if s.order != i:
            s.order = i
            save(s)
    return None


def _pin_state_path() -> Path:
    """Pin metadata lives beside, but separately from, Session order fields."""
    return SESSION_DIR.parent / "session-pins.json"


def _read_pin_state_locked() -> tuple[Path, int, tuple[str, ...]]:
    global _PIN_STATE_CACHE, _PIN_STATE_CACHE_STAMP
    path = _pin_state_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except FileNotFoundError:
        stamp = None
    if (_PIN_STATE_CACHE is not None and _PIN_STATE_CACHE[0] == path
            and _PIN_STATE_CACHE_STAMP == stamp):
        return _PIN_STATE_CACHE
    if stamp is None:
        state = (path, 0, ())
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("Invalid Session pin metadata")
        revision = raw.get("revision")
        session_ids = raw.get("sessionIds")
        if (isinstance(revision, bool) or not isinstance(revision, int)
                or revision < 0 or not isinstance(session_ids, list)
                or not all(isinstance(item, str) and item for item in session_ids)
                or len(set(session_ids)) != len(session_ids)):
            raise ValueError("Invalid Session pin metadata")
        state = (path, revision, tuple(session_ids))
    _PIN_STATE_CACHE = state
    _PIN_STATE_CACHE_STAMP = stamp
    return state


def _write_pin_state_locked(path: Path, revision: int, session_ids: list[str]) -> None:
    global _PIN_STATE_CACHE, _PIN_STATE_CACHE_STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    payload = {
        "version": 1,
        "revision": revision,
        "sessionIds": session_ids,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    _PIN_STATE_CACHE = (path, revision, tuple(session_ids))
    # Recheck the file on the next read. An external Pan process may replace
    # it after this write; a path-only cache would never observe that change.
    _PIN_STATE_CACHE_STAMP = None


def session_pin_state() -> dict:
    """Return a detached authoritative pin ordering snapshot."""
    with _PIN_LOCK:
        _, revision, session_ids = _read_pin_state_locked()
        return {"pinRevision": revision, "sessionIds": list(session_ids)}


def session_pin_order(session_id: str) -> int | None:
    """Return this Session's rank in the shared pinned order, if pinned."""
    with _PIN_LOCK:
        _, _, session_ids = _read_pin_state_locked()
        try:
            return session_ids.index(session_id)
        except ValueError:
            return None


def session_pin_projection(session_id: str) -> dict:
    """Return the list-safe pin fields for one Session."""
    with _PIN_LOCK:
        _, revision, session_ids = _read_pin_state_locked()
        try:
            order = session_ids.index(session_id)
        except ValueError:
            order = None
        return {
            "pinned": order is not None,
            "pinOrder": order,
            "pinRevision": revision,
        }


@_store_serialized
def set_session_pinned(session_id: str, pinned: bool) -> tuple[dict | None, str | None]:
    """Pin/unpin one existing Session without touching Session.order."""
    if session_id not in _cache and get(session_id) is None:
        return None, "Session not found"
    with _PIN_LOCK:
        path, revision, current = _read_pin_state_locked()
        ordered = list(current)
        present = session_id in ordered
        if pinned and not present:
            ordered.append(session_id)
        elif not pinned and present:
            ordered.remove(session_id)
        if ordered != list(current):
            _write_pin_state_locked(path, revision + 1, ordered)
            revision += 1
        return {"pinRevision": revision, "sessionIds": ordered}, None


@_store_serialized
def reorder_pinned_sessions(
    session_ids: list[str], expected_revision: int,
) -> tuple[dict | None, str | None, str | None]:
    """Reorder selected pinned IDs while leaving every unlisted slot intact.

    The caller supplies the visible pinned members of one current UI group.
    Replacing only those IDs' existing slots means filtered, collapsed, and
    other-group pins retain both their slots and their mutual relative order.
    """
    with _PIN_LOCK:
        path, revision, current = _read_pin_state_locked()
        if expected_revision != revision:
            return None, "pin_state_conflict", "Pinned Session state changed; refresh and retry"
        if len(set(session_ids)) != len(session_ids):
            return None, "duplicate_session_ids", "sessionIds contains duplicates"
        all_ids = {session.id for session in list_all(load_history=False)}
        unknown = [sid for sid in session_ids if sid not in all_ids]
        if unknown:
            return None, "session_not_found", f"Unknown Session id(s): {', '.join(unknown)}"
        unpinned = [sid for sid in session_ids if sid not in current]
        if unpinned:
            return None, "session_not_pinned", "Every sessionId must currently be pinned"
        requested = set(session_ids)
        positions = [index for index, sid in enumerate(current) if sid in requested]
        if len(positions) != len(session_ids):
            return None, "session_not_pinned", "Every sessionId must currently be pinned"
        ordered = list(current)
        for index, sid in zip(positions, session_ids):
            ordered[index] = sid
        if ordered != list(current):
            _write_pin_state_locked(path, revision + 1, ordered)
            revision += 1
        return {"pinRevision": revision, "sessionIds": ordered}, None, None


def _remove_session_pin_locked(session_id: str) -> None:
    """Drop a deleted Session from pin metadata while delete owns the store lock."""
    with _PIN_LOCK:
        path, revision, current = _read_pin_state_locked()
        if session_id not in current:
            return
        try:
            _write_pin_state_locked(
                path, revision + 1, [sid for sid in current if sid != session_id],
            )
        except (OSError, ValueError):
            # Session deletion must not strand the Session cache/files if a
            # secondary metadata write fails; list projections ignore orphan IDs.
            pass


# ── migration helpers ──

def _migrate_legacy_fields(s: Session):
    """Migrate old top-level adapter-specific fields into adapter_config."""
    changed = False
    # if old-style fields exist as attributes, move them to adapter_config
    legacy_map = {
        "cbc_session_id": "cli_session_id",
        "always_thinking_enabled": "always_thinking_enabled",
        "effort": "effort",
        "max_thinking_tokens": "max_thinking_tokens",
    }
    for old_key, new_key in legacy_map.items():
        value = getattr(s, old_key, None)
        if value and new_key not in s.adapter_config:
            s.adapter_config[new_key] = value
            changed = True
    if changed:
        _save_sync(s)


def _deep_sum_raw_usage(a: dict, b: dict) -> dict:
    """递归累加两个 rawUsage dict 中所有数值字段。"""
    result = dict(a)
    for k, v in b.items():
        if k not in result:
            result[k] = v
        elif isinstance(v, dict) and isinstance(result[k], dict):
            result[k] = _deep_sum_raw_usage(result[k], v)
        elif isinstance(v, (int, float)) and isinstance(result[k], (int, float)):
            result[k] += v
    return result


def accumulate_raw_usage(existing: dict | None, entries: list[dict]) -> dict:
    """将 raw_usage 条目按 model 累加，返回 {model: {model, request_count, rawUsage}}。

    existing: 已有的累加结果（dict keyed by model），None 表示无
    entries:  待合并的条目列表，每项 {"model": str, "rawUsage": dict, ...}
    """
    result: dict = dict(existing) if existing else {}
    for entry in entries:
        model = entry.get("model", "unknown")
        ru = entry.get("rawUsage")
        if not ru:
            continue
        if model in result:
            result[model]["rawUsage"] = _deep_sum_raw_usage(result[model]["rawUsage"], ru)
            result[model]["request_count"] += 1
        else:
            result[model] = {
                "model": model,
                "request_count": 1,
                "rawUsage": ru,
            }
    return result


def compute_total_usage(raw_usage: dict | None) -> dict | None:
    """从按模型累加的 raw_usage 汇总累计消耗。

    返回 {"prompt_tokens": int, "cache_hit_tokens": int, "cache_miss_tokens": int,
          "completion_tokens": int, "credit": float}
    或 None（raw_usage 为空时）。

    credit 同时兼容 "credit"（cbc/kimi）与 "cost"（claude/opencode）两种
    rawUsage 键名——各 adapter 对「金额」字段命名不统一，聚合处收敛到 credit。
    """
    if not raw_usage:
        return None
    total = {"prompt_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0,
             "completion_tokens": 0, "credit": 0.0}
    for entry in raw_usage.values():
        ru = entry.get("rawUsage", {})
        total["prompt_tokens"] += ru.get("prompt_tokens", 0)
        # Adapters historically used several names for the same cache
        # counters. Prefer the canonical prompt_cache_* spelling when it is
        # present; otherwise bridge provider-specific aliases. This prevents
        # Codex/Claude/OpenCode cache reads from remaining stranded in
        # raw_usage while totalUsage reports cache_hit_tokens=0, and avoids
        # double-counting when both spellings are present.
        total["cache_hit_tokens"] += next(
            (ru[key] for key in (
                "prompt_cache_hit_tokens", "cache_read_tokens",
                "cached_input_tokens",
            ) if key in ru and ru[key] is not None),
            0,
        )
        total["cache_miss_tokens"] += next(
            (ru[key] for key in (
                "prompt_cache_miss_tokens", "cache_write_tokens",
                "cache_write_input_tokens",
            ) if key in ru and ru[key] is not None),
            0,
        )
        total["completion_tokens"] += ru.get("completion_tokens", 0)
        total["credit"] += ru.get("credit", 0) + ru.get("cost", 0)
    return total


# Keep this projection separate from ``compute_total_usage``: the latter is
# the long-standing billing-compatible aggregate, while this view also reads
# re-imported provider snapshots such as Codex input_tokens/output_tokens.
_USAGE_VIEW_ALIASES = {
    "input": ("prompt_tokens", "input_tokens"),
    "output": ("completion_tokens", "output_tokens"),
    "cache_read": (
        "prompt_cache_hit_tokens", "cache_read_tokens", "cached_input_tokens",
    ),
    "cache_write": (
        "prompt_cache_miss_tokens", "cache_write_tokens",
        "cache_write_input_tokens",
    ),
    "credit": ("credit", "cost"),
}


def _usage_view_entries(raw_usage) -> list[dict]:
    """Return model entries from current and legacy raw usage shapes."""
    if isinstance(raw_usage, dict):
        return [entry for entry in raw_usage.values() if isinstance(entry, dict)]
    if isinstance(raw_usage, list):
        return [entry for entry in raw_usage if isinstance(entry, dict)]
    return []


def _usage_view_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _usage_view_sum(entries: list[dict], aliases: tuple[str, ...]):
    """Sum one field, selecting at most one alias per model entry.

    ``None`` means no entry carried a numeric value. An explicit numeric zero
    remains zero. Selecting one alias prevents compatibility names from being
    counted twice when a provider payload contains more than one spelling.
    """
    found = False
    total = 0
    for entry in entries:
        raw = entry.get("rawUsage")
        if not isinstance(raw, dict):
            raw = entry if any(key in entry for key in aliases) else None
        if not isinstance(raw, dict):
            continue
        value = next(
            (raw[key] for key in aliases
             if key in raw and raw[key] is not None),
            None,
        )
        value = _usage_view_number(value)
        if value is not None:
            total += value
            found = True
    return total if found else None


def session_usage_view(s: Session) -> dict:
    """Project persisted Session usage into a stable input/output/cache view.

    This read-only view never refreshes provider state or mutates the session.
    Non-empty ``raw_usage`` is primary; ``total_usage`` is a compatibility
    fallback for old sessions that only have the derived aggregate. Cache is
    reported separately and is not added to input/output again. Thus
    ``total.tokens`` is ``input + output`` only.
    """
    raw_entries = _usage_view_entries(s.raw_usage)
    if raw_entries:
        values = {
            name: _usage_view_sum(raw_entries, aliases)
            for name, aliases in _USAGE_VIEW_ALIASES.items()
        }
        source_kind = "Session.rawUsage"
        source_fields = {
            "input": "rawUsage.prompt_tokens|input_tokens",
            "output": "rawUsage.completion_tokens|output_tokens",
            "cache.read": (
                "rawUsage.prompt_cache_hit_tokens|cache_read_tokens|"
                "cached_input_tokens"
            ),
            "cache.write": (
                "rawUsage.prompt_cache_miss_tokens|cache_write_tokens|"
                "cache_write_input_tokens"
            ),
            "credit": "rawUsage.credit|cost",
        }
    elif isinstance(s.total_usage, dict):
        total_usage = s.total_usage
        values = {
            "input": _usage_view_number(total_usage.get("prompt_tokens"))
                if "prompt_tokens" in total_usage else None,
            "output": _usage_view_number(total_usage.get("completion_tokens"))
                if "completion_tokens" in total_usage else None,
            "cache_read": _usage_view_number(total_usage.get("cache_hit_tokens"))
                if "cache_hit_tokens" in total_usage else None,
            "cache_write": _usage_view_number(total_usage.get("cache_miss_tokens"))
                if "cache_miss_tokens" in total_usage else None,
            "credit": _usage_view_number(total_usage.get("credit"))
                if "credit" in total_usage else None,
        }
        source_kind = "Session.totalUsage"
        source_fields = {
            "input": "totalUsage.prompt_tokens",
            "output": "totalUsage.completion_tokens",
            "cache.read": "totalUsage.cache_hit_tokens",
            "cache.write": "totalUsage.cache_miss_tokens",
            "credit": "totalUsage.credit",
        }
    else:
        values = {name: None for name in _USAGE_VIEW_ALIASES}
        source_kind = None
        source_fields = {}

    input_tokens = values["input"]
    output_tokens = values["output"]
    cache_read = values["cache_read"]
    cache_write = values["cache_write"]
    cache_total = (
        cache_read + cache_write
        if _usage_view_number(cache_read) is not None
        and _usage_view_number(cache_write) is not None else None
    )
    total_tokens = (
        input_tokens + output_tokens
        if _usage_view_number(input_tokens) is not None
        and _usage_view_number(output_tokens) is not None else None
    )
    return {
        "ok": True,
        "sessionId": s.id,
        "adapter": s.adapter,
        "input": input_tokens,
        "output": output_tokens,
        "cache": {
            "read": cache_read,
            "write": cache_write,
            "total": cache_total,
        },
        "total": {
            "tokens": total_tokens,
            "credit": values["credit"],
        },
        "source": {
            "kind": source_kind,
            "fields": source_fields,
            "updatedAt": "Session.updatedAt",
            "updatedAtMeaning": (
                "Pan session persistence time; provider event time is not "
                "retained by the aggregate view"
            ),
        },
        "updatedAt": s.updated_at or None,
    }


def _migrate_session_usage(s: Session):
    """Migrate legacy list-format raw_usage to dict + compute total_usage."""
    if isinstance(s.raw_usage, list):
        s.raw_usage = accumulate_raw_usage(None, s.raw_usage)
    if s.total_usage is None and isinstance(s.raw_usage, dict):
        s.total_usage = compute_total_usage(s.raw_usage)


def clear_cache():
    _cache.clear()
