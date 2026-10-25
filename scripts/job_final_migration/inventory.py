"""Build a read-only migration inventory for the Job final model.

The scanner only looks at fixed, known locations under an explicitly supplied
data root. It reads bytes, computes hashes, and classifies records. It never
imports the running service, starts a process, writes to the root, or creates
migration targets.

Classification is fail-closed. A record is ``simple-script-migratable`` only
when every required field of a known v1 shape is present, every unknown field
is absent, the schedule/action shape is unambiguous, no active effect is
visible, and the source can be mapped without guessing. Everything else is
``incompatible-preserved``; unreadable or unrecognisable records are reported
as ``unknown`` within that preserved disposition.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

TOOL_SCHEMA = "pan.job-final-migration.inventory/v1"
MAPPING_VERSION = "1"
SCHEMA_WHITELIST = "v1-known-20261025"

SIMPLE = "simple-script-migratable"
PRESERVED = "incompatible-preserved"
DETAIL_SIMPLE = "simple"
DETAIL_COMPLEX = "complex"
DETAIL_UNKNOWN = "unknown"

MIG_BG = "MIG-BG"
MIG_MSG = "MIG-MSG"
MIG_SCHED = "MIG-SCHED"
MIG_ACTION_JOB = "MIG-ACTION-JOB"
MIG_TRIGGER = "MIG-TRIGGER"
MIG_TEMPLATE = "MIG-TEMPLATE"
MIG_MAIN_LIFECYCLE = "MIG-MAIN-LIFECYCLE"
MIG_UNKNOWN = "UNKNOWN"

REASON_CODES = frozenset({
    "unknown_schema",
    "unknown_field",
    "corrupt_record",
    "unverified_authority",
    "target_missing",
    "provider_unavailable",
    "platform_unsupported",
    "active_resource",
    "uncertain_effect",
    "pending_notice",
    "schedule_semantic_loss",
    "business_completion_coupling",
    "counter_coupling",
    "missing_reference",
    "unsupported_complex_instance",
})

ACTIVE_JOB_STATUS = frozenset({"running", "cleaning", "restarting", "uncertain"})
ACTIVE_RUN_STATUS = frozenset({"queued", "dispatched", "retry_wait", "running"})
UNCERTAIN_RUN_STATUS = frozenset({"uncertain", "unknown"})
TERMINAL_BG_STATUS = frozenset({"completed", "failed", "cancelled"})
AUTH_TYPES = frozenset({"user", "agent", "system"})
KNOWN_SOURCES = frozenset({"user", "agent", "system", "automation"})
MISFIRE_POLICIES = frozenset({"fire_now", "skip"})
CONDITION_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte"})
BINDING_FROM = frozenset({"literal", "input", "event", "system", "step"})

# ---------------------------------------------------------------------------
# Field whitelists. Missing required fields are not defaulted by this tool.
# ---------------------------------------------------------------------------

COMMON_JOB_AUDIT = {
    "lastStatus", "lastError", "lastDelivery", "lastFireAt", "lastRunAt",
    "nextFireAt", "nextRunAt", "runStartedAt", "notificationState",
    "terminalEventId", "terminalEventEmittedFor", "attemptSnapshot", "attempts",
    "snapshotCleanup", "runId", "lastExecution", "cancelRequestedAt", "retryOf",
}

BACKGROUND_PROCESS_FIELDS = {
    "jobId", "kind", "operation", "targetSessionId", "argv", "hideWindow",
    "creatorSessionId", "source", "sourceSessionId", "sourceStruct",
    "targetStruct", "name", "description", "commandSummary", "cwd", "label",
    "status", "createdAt", "updatedAt", "pid", "processCreatedAt", "logPath",
    "processFiletime", "processJobHandle", "runnerPid", "runnerProcessCreatedAt",
    "runnerFiletime", "runnerClaim", "runnerSpawnStartedAt", "exitCode", "error",
    "processTreeEnded", "cleanupOriginalStatus", "cleanupAttemptAt",
    "cleanupError", "cleanupMembers", "cleanupTreeEnded",
    "cleanupRunnerOutcome", "processOutcome", "shellCommand",
    "scheduledParentJobId", "dispatchKey", "scheduledFireAt", "scheduleEntryId",
} | COMMON_JOB_AUDIT

BACKGROUND_PROCESS_REQUIRED = {
    "jobId", "kind", "operation", "targetSessionId", "argv", "hideWindow",
    "creatorSessionId", "sourceStruct", "targetStruct", "name", "description",
    "commandSummary", "cwd", "label", "status", "createdAt", "updatedAt",
    "pid", "processCreatedAt", "logPath", "notificationState",
    "terminalEventId",
}

MESSAGE_FIELDS = {
    "jobId", "kind", "operation", "targetSessionId", "targetSessionIds", "text",
    "name", "description", "source", "sourceSessionId", "sourceStruct",
    "targetStruct", "creatorSessionId", "schedule", "status", "runCount",
    "createdAt", "updatedAt",
} | COMMON_JOB_AUDIT

MESSAGE_REQUIRED = {
    "jobId", "kind", "operation", "text", "name", "description", "source",
    "sourceSessionId", "sourceStruct", "targetStruct", "creatorSessionId",
    "schedule", "nextRunAt", "status", "runCount", "lastRunAt", "lastDelivery",
    "lastError", "createdAt", "updatedAt",
}

SCHEDULED_FIELDS = {
    "jobId", "kind", "taskId", "name", "description", "targetSessionId",
    "sourceStruct", "targetStruct", "text", "action", "source",
    "creatorSessionId", "enabled", "paused", "schedule", "misfirePolicy",
    "nextFireAt", "lastFireAt", "runCount", "maxRuns", "status",
    "runStartedAt", "undeliveredFires", "createdAt", "updatedAt",
    # Fields written by the owner switch or Action wrapper are known, but their
    # presence is rejected below: they are execution projections, not v1 input.
    "config", "configRevision", "referenceGeneration", "creationFingerprint",
    "creationEventPending", "templateId", "templateRevision", "authorization",
    "legacyCompat", "legacySchedule", "legacySchedulerCompat",
    "schedulerOwner", "legacySourceId", "legacyTriggerId", "schedulerMigration",
    "runPolicy", "runAuthorization", "runRecoveryEligible", "recoveryFailures",
    "activeExecutions", "execution", "executionFinalizations", "terminalOutbox",
    "pendingRuns", "eventReceipts", "nextAttemptAt", "recoveryConfirmation",
    "chainRecovery",
} | COMMON_JOB_AUDIT

SCHEDULED_REQUIRED = {
    "jobId", "kind", "taskId", "name", "description", "targetSessionId",
    "sourceStruct", "targetStruct", "text", "action", "source",
    "creatorSessionId", "enabled", "paused", "schedule", "misfirePolicy",
    "nextFireAt", "lastFireAt", "lastStatus", "lastError", "runCount",
    "maxRuns", "status", "runStartedAt", "undeliveredFires", "createdAt",
    "updatedAt",
}

LEGACY_TASK_FIELDS = {
    "id", "name", "description", "target_session_id", "text", "enabled",
    "paused", "schedule", "next_fire_at", "last_fire_at", "last_status",
    "last_error", "run_count", "max_runs", "misfire_policy", "created_at",
    "updated_at", "action",
}

LEGACY_TASK_REQUIRED = {
    "id", "name", "description", "target_session_id", "text", "enabled",
    "paused", "schedule", "next_fire_at", "last_fire_at", "last_status",
    "last_error", "run_count", "max_runs", "misfire_policy", "created_at",
    "updated_at",
}

ACTION_JOB_FIELDS = SCHEDULED_FIELDS | {
    "config", "configRevision", "referenceGeneration", "creationFingerprint",
    "creationEventPending", "templateId", "templateRevision", "authorization",
}

TRIGGER_FIELDS = {
    "id", "name", "description", "eventTypes", "eventFilter", "condition",
    "actionId", "arguments", "steps", "targetJobId", "ownerJobId",
    "ownerLinkId", "executionMode", "maxConcurrency", "retry", "enabled",
    "revision", "createdBy", "createdAt", "updatedAt", "authorization",
}

TEMPLATE_FIELDS = {"id", "name", "description", "config", "revision",
                   "referenceGeneration"}

CONFIG_FIELDS = {
    "name", "description", "actionId", "steps", "arguments", "schedule",
    "trigger", "recovery", "overlap", "notification", "completion", "enabled",
    "maxRuns", "misfirePolicy",
}
CONFIG_REQUIRED = set(CONFIG_FIELDS) - {"actionId", "steps"}

STEP_FIELDS = {"stepId", "actionId", "arguments"}
SCHEDULE_ENTRY_FIELDS = {
    "id", "kind", "timezone", "at", "interval_sec", "intervalSec", "anchor",
    "cron", "misfirePolicy", "enabled", "nextFireAt", "lastFireAt", "graceSec",
}
ACTION_FIELDS = {"api", "args", "actionId", "arguments"}
SHELL_ARG_FIELDS = {"command", "cwd"}
QQ_ARG_FIELDS = {"targetType", "targetId", "botUin"}

REAL_PROFILE_MARKERS = ("process.json", "data/process.json")

BG_FIELD_MAP = {
    "jobId": "provenance.originalJobId",
    "argv": "process.exec.arguments.argv",
    "cwd": "process.exec.arguments.cwd",
    "hideWindow": "process.exec.arguments.hideWindow",
    "name": "job.metadata.name",
    "description": "job.metadata.description",
    "createdAt": "provenance.createdAt",
    "updatedAt": "provenance.updatedAt",
    "exitCode": "historicalEvidence.exitCode",
    "logPath": "historicalEvidence.logPath",
    "terminalEventId": "historicalEvidence.terminalEventId",
}

def _config_field_map(config: dict, target: str) -> dict[str, str]:
    mapping = {
        "config.name": f"{target}.metadata.name",
        "config.description": f"{target}.metadata.description",
        "config.arguments": f"{target}.action.arguments",
        "config.schedule": f"{target}.scheduler.rule",
        "config.trigger": f"{target}.trigger",
        "config.recovery": f"{target}.policy.recovery",
        "config.overlap": f"{target}.policy.overlap",
        "config.notification": f"{target}.policy.notification",
        "config.completion": f"{target}.policy.completion",
        "config.enabled": f"{target}.enabled",
        "config.maxRuns": f"{target}.policy.maxRuns",
        "config.misfirePolicy": f"{target}.policy.misfirePolicy",
    }
    if isinstance(config.get("steps"), list):
        mapping["config.steps"] = f"{target}.action.steps"
        mapping["config.actionId"] = "provenance.firstActionId"
    else:
        mapping["config.actionId"] = f"{target}.action.actionId"
    return mapping


def _message_field_map(data: dict) -> dict[str, str]:
    mapping = {
        "jobId": "provenance.originalJobId",
        "text": "session.send.text",
        "schedule.type": "scheduler.rule.kind",
        "schedule.at": "scheduler.rule.at",
        "nextRunAt": "scheduler.nextFireAt",
        "source": "provenance.source",
        "creatorSessionId": "authorization.creatorSessionId",
        "createdAt": "provenance.createdAt",
        "updatedAt": "provenance.updatedAt",
    }
    if data.get("kind") == "session-broadcast":
        mapping["targetSessionIds"] = "session.send-many.targetSessionIds"
    else:
        mapping["targetSessionId"] = "session.send.targetSessionId"
    return mapping


def _scheduled_field_map(data: dict, *, legacy: bool) -> dict[str, str]:
    if legacy:
        return {
            "id": "provenance.originalTaskId",
            "schedule.kind": "scheduler.rule.kind",
            "schedule.at": "scheduler.rule.at",
            "schedule.intervalSec": "scheduler.rule.intervalSec",
            "schedule.cron": "scheduler.rule.cron",
            "schedule.timezone": "scheduler.rule.timezone",
            "next_fire_at": "scheduler.nextFireAt",
            "run_count": "scheduler.cursor.runCount",
            "misfire_policy": "scheduler.misfirePolicy",
            "paused": "scheduler.paused",
            "action.api": "action.kind",
            "text": "action.arguments.text",
            "target_session_id": "action.arguments.sessionId",
        }
    return {
        "jobId": "provenance.originalJobId",
        "taskId": "provenance.taskId",
        "schedule.kind": "scheduler.rule.kind",
        "schedule.at": "scheduler.rule.at",
        "schedule.intervalSec": "scheduler.rule.intervalSec",
        "schedule.cron": "scheduler.rule.cron",
        "schedule.timezone": "scheduler.rule.timezone",
        "nextFireAt": "scheduler.nextFireAt",
        "runCount": "scheduler.cursor.runCount",
        "misfirePolicy": "scheduler.misfirePolicy",
        "paused": "scheduler.paused",
        "action.api": "action.kind",
        "action.args": "action.arguments",
        "text": "action.arguments.text",
        "targetSessionId": "action.arguments.sessionId",
    }


def _trigger_field_map(data: dict) -> dict[str, str]:
    mapping = {
        "id": "provenance.originalTriggerId",
        "name": "trigger.metadata.name",
        "description": "trigger.metadata.description",
        "eventTypes": "trigger.eventTypes",
        "eventFilter": "trigger.eventFilter",
        "condition": "trigger.condition",
        "actionId": "trigger.actionId",
        "executionMode": "trigger.executionMode",
        "maxConcurrency": "trigger.maxConcurrency",
        "retry": "trigger.retry",
        "enabled": "trigger.enabled",
        "createdBy": "provenance.createdBy",
        "authorization": "authorization",
    }
    if "steps" in data:
        mapping["steps"] = "trigger.steps"
    if "arguments" in data:
        mapping["arguments"] = "trigger.arguments"
    return mapping


class RealProfileError(RuntimeError):
    """Raised when the root looks like a real Pan profile and was not allowed."""

    def __init__(self, markers: Iterable[str]):
        self.markers = tuple(markers)
        super().__init__(
            "refusing to scan a real Pan profile without --allow-real-profile: "
            + ", ".join(self.markers)
        )


class DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a key; json.loads would silently lose one."""


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass
class Loaded:
    path: Path
    relpath: str
    raw: bytes | None
    digest: str | None
    data: Any
    error_code: str | None = None
    error_detail: str | None = None
    fingerprint: str | None = None


@dataclass
class ActionIndex:
    available: bool = False
    actions: dict[str, dict] = field(default_factory=dict)
    generations: dict[str, int] = field(default_factory=dict)
    templates: list[tuple[str, dict]] = field(default_factory=list)
    registry: Loaded | None = None


@dataclass
class Context:
    action_index: ActionIndex
    task_runs: dict[str, dict[str, str]] = field(default_factory=dict)
    task_runs_unknown: bool = False
    trigger_runs: dict[str, dict[str, str]] = field(default_factory=dict)
    trigger_runs_unknown: bool = False


@dataclass
class Finding:
    source_type: str
    source_kind: str | None
    original_id: str | None
    owner: dict
    target: dict | None
    auth_evidence: str
    cursor: dict
    active_handles: list[str] = field(default_factory=list)
    notice_receipts: list[str] = field(default_factory=list)
    reference_edges: list[str] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)
    fatal: bool = False
    field_map: dict | None = None
    expected_diff: list[str] = field(default_factory=list)
    references_verified: bool = False


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        variants = sorted({_canonical(_shape(item)) for item in value})
        return ["list", [json.loads(item) for item in variants]]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    return type(value).__name__


def _fingerprint(value: Any) -> str:
    return _sha256_text(_canonical(_shape(value)))


def is_within_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _is_abs_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith(("/", "\\\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    try:
        return Path(value).is_absolute()
    except (OSError, ValueError):
        return False


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _valid_timezone(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text in {"UTC", "GMT"}:
        return True
    return bool(re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)+", text))


def _valid_cron(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split()
    if len(parts) != 5:
        return False
    return all(re.fullmatch(r"[0-9A-Za-z*?/,#\-]+", part) for part in parts)


def _reason(finding: Finding, code: str, detail: str) -> None:
    assert code in REASON_CODES, code
    finding.reasons.append({"code": code, "detail": detail})


def _unknown_fields(finding: Finding, value: dict, allowed: set[str],
                    scope: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _reason(finding, "unknown_field",
                f"{scope} contains unknown field(s): {', '.join(unknown)}")


def _require_fields(finding: Finding, value: dict, required: set[str],
                    scope: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        finding.fatal = True
        _reason(finding, "unknown_schema",
                f"{scope} is missing required field(s): {', '.join(missing)}")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _derive_owner(data: dict, *, system_owned: bool = False,
                  registry_owned: bool = False) -> tuple[dict, str]:
    authorization = data.get("authorization")
    if isinstance(authorization, dict) and authorization.get("type") in AUTH_TYPES:
        owner_id = (authorization.get("userId") or authorization.get("agentId")
                    or authorization.get("id"))
        return {"kind": authorization["type"], "id": owner_id}, "authorization"
    for key in ("creatorSessionId", "sourceSessionId"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return {"kind": "session", "id": value}, key
    source = data.get("sourceStruct")
    if isinstance(source, dict):
        value = source.get("sessionId")
        if isinstance(value, str) and value:
            return {"kind": "session", "id": value}, "sourceStruct.sessionId"
    created_by = data.get("createdBy")
    if isinstance(created_by, dict) and created_by.get("type") in AUTH_TYPES:
        return ({"kind": created_by["type"], "id": created_by.get("id")},
                "createdBy")
    if system_owned:
        return {"kind": "system", "id": "system"}, "system_owned"
    if registry_owned:
        return {"kind": "registry", "id": "registry"}, "registry_scope"
    return {"kind": "unknown", "id": None}, "none"


def _target_from_job(data: dict) -> dict | None:
    target_struct = data.get("targetStruct")
    if isinstance(target_struct, dict):
        session_id = target_struct.get("sessionId")
        session_ids = target_struct.get("sessionIds")
        if isinstance(session_id, str) and session_id:
            return {"kind": "session", "sessionId": session_id}
        if isinstance(session_ids, list) and session_ids and all(
                isinstance(item, str) and item for item in session_ids):
            return {"kind": "sessions", "sessionIds": list(session_ids)}
    session_id = data.get("targetSessionId")
    if isinstance(session_id, str) and session_id:
        return {"kind": "session", "sessionId": session_id}
    session_ids = data.get("targetSessionIds")
    if isinstance(session_ids, list) and session_ids and all(
            isinstance(item, str) and item for item in session_ids):
        return {"kind": "sessions", "sessionIds": list(session_ids)}
    return None


def _job_cursor(data: dict) -> dict:
    return {
        "nextFireAt": data.get("nextFireAt") or data.get("nextRunAt"),
        "lastFireAt": data.get("lastFireAt") or data.get("lastRunAt"),
        "runCount": data.get("runCount") if isinstance(data.get("runCount"), int) else None,
        "maxRuns": data.get("maxRuns"),
    }


def _active_from_attempts(data: dict) -> list[str]:
    handles: list[str] = []
    attempts = data.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if isinstance(attempt, dict) and attempt.get("status") in {
                    "running", "uncertain", "unknown", "queued"}:
                handles.append("attempt:" + str(attempt.get("id") or "?"))
    return handles


def _runs_for_task(finding: Finding, task_id: str | None, ctx: Context) -> None:
    if ctx.task_runs_unknown:
        _reason(finding, "uncertain_effect",
                "scheduler/background run ledger is unreadable; active state cannot be excluded")
    if not task_id:
        return
    statuses = ctx.task_runs.get(task_id, {})
    for run_id, status in sorted(statuses.items()):
        if status in ACTIVE_RUN_STATUS:
            _reason(finding, "active_resource",
                    f"run {run_id} is still {status}")
        elif status in UNCERTAIN_RUN_STATUS:
            _reason(finding, "uncertain_effect",
                    f"run {run_id} has unresolved status {status}")


def _validate_action(finding: Finding, action: Any, *, text: Any,
                     target: Any) -> None:
    if action is None:
        action = {"api": "assign"}
    if not isinstance(action, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "action is not an object")
        return
    _unknown_fields(finding, action, ACTION_FIELDS, "action")
    api = action.get("api")
    if api in {"assign", "send_session"}:
        if set(action) != {"api"}:
            _reason(finding, "unknown_field",
                    f"action {api} must not carry arguments")
        if not isinstance(text, str) or not text.strip():
            _reason(finding, "corrupt_record", f"action {api} requires non-empty text")
        if not isinstance(target, str) or not target:
            _reason(finding, "target_missing", f"action {api} requires a target Session")
    elif api == "shell":
        args = action.get("args")
        if not isinstance(args, dict):
            finding.fatal = True
            _reason(finding, "corrupt_record", "shell action requires args")
            return
        _unknown_fields(finding, args, SHELL_ARG_FIELDS, "action.args")
        if set(args) != SHELL_ARG_FIELDS:
            _reason(finding, "corrupt_record",
                    "shell action requires exactly command and cwd")
        if not _is_abs_path(args.get("command")):
            _reason(finding, "platform_unsupported",
                    "shell action command is not an absolute path")
        if not _is_abs_path(args.get("cwd")):
            _reason(finding, "platform_unsupported",
                    "shell action cwd is not an absolute path")
    elif api == "send_qq":
        args = action.get("args")
        if not isinstance(args, dict):
            finding.fatal = True
            _reason(finding, "corrupt_record", "send_qq action requires args")
            return
        _unknown_fields(finding, args, QQ_ARG_FIELDS, "action.args")
        if args.get("targetType") not in {"user", "group"}:
            _reason(finding, "provider_unavailable",
                    "send_qq targetType must be user or group")
        target_id = args.get("targetId")
        if not isinstance(target_id, str) or not re.fullmatch(r"\d{5,20}", target_id):
            _reason(finding, "provider_unavailable",
                    "send_qq targetId must be a 5-20 digit identifier")
        if not isinstance(args.get("botUin"), str) or not args["botUin"].strip():
            _reason(finding, "provider_unavailable",
                    "send_qq requires a persisted bot identity")
        if not isinstance(text, str) or not text.strip():
            _reason(finding, "corrupt_record", "send_qq action requires non-empty text")
        if target not in (None, ""):
            _reason(finding, "unsupported_complex_instance",
                    "send_qq must not silently retarget a Session")
    elif api == "resume_legal_running":
        _reason(finding, "active_resource",
                "resume_legal_running depends on a live legal-running resource")
    elif api == "action_reference":
        _reason(finding, "unsupported_complex_instance",
                "action_reference is an Action wrapper, not a v1 scheduler action")
    elif api is None:
        _reason(finding, "unknown_schema", "scheduled task has no action")
    else:
        _reason(finding, "unsupported_complex_instance",
                f"unknown scheduler action: {api!r}")


def _validate_schedule_entry(finding: Finding, entry: Any, *,
                             require_timezone: bool,
                             require_next_fire: bool,
                             scope: str = "schedule entry") -> None:
    if not isinstance(entry, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", f"{scope} is not an object")
        return
    _unknown_fields(finding, entry, SCHEDULE_ENTRY_FIELDS, scope)
    kind = entry.get("kind")
    if kind not in {"once", "interval", "cron"}:
        finding.fatal = True
        _reason(finding, "unknown_schema", f"{scope}.kind is not once/interval/cron")
        return
    timezone = entry.get("timezone")
    if require_timezone and not _valid_timezone(timezone):
        _reason(finding, "schedule_semantic_loss",
                f"{scope} has no explicit valid IANA timezone")
    elif timezone is not None and not _valid_timezone(timezone):
        _reason(finding, "schedule_semantic_loss",
                f"{scope}.timezone is not a valid IANA name")
    if kind == "once":
        if _parse_time(entry.get("at")) is None:
            _reason(finding, "schedule_semantic_loss",
                    f"{scope}.at is missing or not ISO-8601")
    elif kind == "interval":
        raw = entry.get("interval_sec", entry.get("intervalSec"))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            _reason(finding, "schedule_semantic_loss",
                    f"{scope} interval must be a positive number")
        if _parse_time(entry.get("anchor")) is None:
            _reason(finding, "schedule_semantic_loss",
                    f"{scope} interval requires an explicit anchor")
        if "interval_sec" in entry and "intervalSec" in entry:
            if entry["interval_sec"] != entry["intervalSec"]:
                finding.fatal = True
                _reason(finding, "corrupt_record",
                        f"{scope} interval_sec and intervalSec disagree")
    else:
        if not _valid_cron(entry.get("cron")):
            _reason(finding, "schedule_semantic_loss",
                    f"{scope}.cron is not a complete five-field expression")
    if require_next_fire and _parse_time(entry.get("nextFireAt")) is None:
        _reason(finding, "schedule_semantic_loss",
                f"{scope}.nextFireAt is missing or invalid")
    elif entry.get("nextFireAt") is not None and _parse_time(entry["nextFireAt"]) is None:
        _reason(finding, "schedule_semantic_loss",
                f"{scope}.nextFireAt is invalid")
    if entry.get("lastFireAt") is not None and _parse_time(entry["lastFireAt"]) is None:
        _reason(finding, "corrupt_record", f"{scope}.lastFireAt is invalid")
    if entry.get("misfirePolicy") not in (None, *MISFIRE_POLICIES):
        _reason(finding, "schedule_semantic_loss",
                f"{scope}.misfirePolicy is invalid")
    if "enabled" in entry and type(entry["enabled"]) is not bool:
        finding.fatal = True
        _reason(finding, "corrupt_record", f"{scope}.enabled is not boolean")
    if "id" in entry and (not isinstance(entry["id"], str) or not entry["id"]):
        finding.fatal = True
        _reason(finding, "corrupt_record", f"{scope}.id is invalid")
    grace = entry.get("graceSec")
    if grace is not None:
        if isinstance(grace, bool) or not isinstance(grace, (int, float)) \
                or not 0 <= grace <= 86400:
            _reason(finding, "schedule_semantic_loss",
                    f"{scope}.graceSec is invalid")


def _validate_bindings(finding: Finding, value: Any, scope: str) -> None:
    if isinstance(value, dict):
        if "from" in value:
            origin = value.get("from")
            if origin not in BINDING_FROM:
                _reason(finding, "unsupported_complex_instance",
                        f"{scope} uses unknown binding source {origin!r}")
                return
            if origin == "literal":
                expected = {"from", "value"}
            elif origin == "step":
                expected = {"from", "stepId", "path"}
            else:
                expected = {"from", "path"}
            if set(value) != expected:
                _reason(finding, "unknown_field",
                        f"{scope} binding for {origin} must use {sorted(expected)}")
            return
        for key, item in sorted(value.items()):
            _validate_bindings(finding, item, f"{scope}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_bindings(finding, item, f"{scope}[{index}]")


def _validate_condition(finding: Finding, condition: Any) -> None:
    if condition is None:
        return
    if not isinstance(condition, dict):
        _reason(finding, "corrupt_record", "trigger.condition is not an object")
        return
    keys = set(condition)
    if keys in ({"and"}, {"or"}):
        values = condition[next(iter(keys))]
        if not isinstance(values, list) or not values:
            _reason(finding, "corrupt_record",
                    f"trigger.condition.{next(iter(keys))} must be a non-empty list")
            return
        for item in values:
            _validate_condition(finding, item)
        return
    if keys == {"not"}:
        _validate_condition(finding, condition["not"])
        return
    if keys != {"field", "op", "value"}:
        _reason(finding, "unsupported_complex_instance",
                "trigger.condition is not a known State condition")
        return
    if not isinstance(condition["field"], str) or not condition["field"]:
        _reason(finding, "corrupt_record", "trigger.condition.field is invalid")
    if condition["op"] not in CONDITION_OPS:
        _reason(finding, "unsupported_complex_instance",
                f"unknown State condition op: {condition['op']!r}")
    if not _is_scalar(condition["value"]):
        _reason(finding, "unsupported_complex_instance",
                "State condition value must be scalar")


def _check_action_refs(finding: Finding, action_ids: list[str],
                       index: ActionIndex) -> None:
    if not index.available:
        _reason(finding, "missing_reference",
                "actions/registry.json is missing or unreadable")
        return
    ok = True
    for action_id in action_ids:
        action = index.actions.get(action_id)
        if not isinstance(action, dict):
            _reason(finding, "missing_reference",
                    f"Action reference not found: {action_id}")
            ok = False
        elif action.get("enabled", True) is False:
            _reason(finding, "provider_unavailable",
                    f"Action is disabled: {action_id}")
            ok = False
    finding.references_verified = ok


def _reference_action_ids(config: dict) -> list[str]:
    if "steps" in config and isinstance(config.get("steps"), list):
        return [str(step.get("actionId")) for step in config["steps"]
                if isinstance(step, dict) and step.get("actionId")]
    action_id = config.get("actionId")
    return [action_id] if isinstance(action_id, str) else []


def _check_reference_generation(finding: Finding, index: ActionIndex,
                                config: dict, expected: Any) -> None:
    action_ids = _reference_action_ids(config)
    if isinstance(expected, dict):
        actual: Any = {aid: int(index.generations.get(aid, 0))
                       for aid in sorted(expected)}
    elif type(expected) is int and len(action_ids) == 1:
        actual = int(index.generations.get(action_ids[0], 0))
    else:
        _reason(finding, "missing_reference",
                "referenceGeneration does not match the Action reference shape")
        return
    if finding.references_verified and expected != actual:
        _reason(finding, "missing_reference",
                f"referenceGeneration is stale: persisted={expected!r} current={actual!r}")


def _validate_config(finding: Finding, config: Any, index: ActionIndex, *,
                     require_schedule: bool,
                     require_next_fire: bool) -> None:
    if not isinstance(config, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "config is not an object")
        return
    _unknown_fields(finding, config, CONFIG_FIELDS, "config")
    _require_fields(finding, config, CONFIG_REQUIRED, "config")
    if finding.fatal:
        return
    if not isinstance(config.get("name"), str) or not config["name"].strip():
        _reason(finding, "corrupt_record", "config.name is required")
    if not isinstance(config.get("description"), str):
        _reason(finding, "corrupt_record", "config.description must be a string")
    if not isinstance(config.get("arguments"), dict):
        _reason(finding, "corrupt_record", "config.arguments must be an object")
    else:
        _validate_bindings(finding, config["arguments"], "config.arguments")
    if type(config.get("enabled")) is not bool:
        _reason(finding, "corrupt_record", "config.enabled must be boolean")
    max_runs = config.get("maxRuns")
    if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
        _reason(finding, "corrupt_record", "config.maxRuns must be positive or null")
    if config.get("misfirePolicy") not in MISFIRE_POLICIES:
        _reason(finding, "schedule_semantic_loss",
                "config.misfirePolicy must be fire_now or skip")

    has_action = "actionId" in config
    has_steps = "steps" in config
    if not has_action and not has_steps:
        finding.fatal = True
        _reason(finding, "corrupt_record",
                "config must contain actionId or steps")
        return
    action_ids: list[str] = []
    if has_steps:
        steps = config.get("steps")
        if not isinstance(steps, list) or not steps:
            finding.fatal = True
            _reason(finding, "corrupt_record", "config.steps must be a non-empty list")
            return
        seen: set[str] = set()
        for position, step in enumerate(steps):
            if not isinstance(step, dict):
                finding.fatal = True
                _reason(finding, "corrupt_record", f"config.steps[{position}] is not an object")
                continue
            _unknown_fields(finding, step, STEP_FIELDS,
                            f"config.steps[{position}]")
            if set(step) != STEP_FIELDS:
                _reason(finding, "corrupt_record",
                        f"config.steps[{position}] needs stepId/actionId/arguments")
            step_id = step.get("stepId")
            if not isinstance(step_id, str) or not step_id or step_id in seen:
                _reason(finding, "corrupt_record",
                        f"config.steps[{position}].stepId is invalid or duplicated")
            else:
                seen.add(step_id)
            step_action = step.get("actionId")
            if not isinstance(step_action, str) or not step_action:
                _reason(finding, "corrupt_record",
                        f"config.steps[{position}].actionId is invalid")
            elif step_action not in action_ids:
                action_ids.append(step_action)
            if not isinstance(step.get("arguments"), dict):
                _reason(finding, "corrupt_record",
                        f"config.steps[{position}].arguments must be an object")
            else:
                _validate_bindings(finding, step["arguments"],
                                   f"config.steps[{position}].arguments")
        first_action = steps[0].get("actionId") if steps and isinstance(
            steps[0], dict) else None
        if has_action and config.get("actionId") != first_action:
            finding.fatal = True
            _reason(finding, "corrupt_record",
                    "config.actionId must match the first step")
    else:
        action_id = config.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            finding.fatal = True
            _reason(finding, "corrupt_record", "config.actionId is invalid")
            return
        action_ids = [action_id]
    _check_action_refs(finding, action_ids, index)

    trigger = config.get("trigger")
    if not isinstance(trigger, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "config.trigger is not an object")
        return
    trigger_keys = set(trigger)
    if trigger_keys - {"kind", "query", "eventTypes"}:
        _reason(finding, "unknown_field",
                "config.trigger contains unknown fields")
    trigger_kind = trigger.get("kind")
    if trigger_kind not in {"manual", "immediate", "once", "interval",
                            "cron", "event", "schedule"}:
        _reason(finding, "unknown_schema",
                f"unknown Job trigger kind: {trigger_kind!r}")
        return
    if trigger_kind == "event":
        _reason(finding, "unsupported_complex_instance",
                "event-triggered Action Job belongs to MIG-TRIGGER, not MIG-ACTION-JOB")
    if trigger_kind in {"manual", "immediate"} and trigger_keys - {"kind"}:
        _reason(finding, "unknown_field",
                f"config.trigger {trigger_kind} must not carry query/eventTypes")

    schedule = config.get("schedule")
    if not isinstance(schedule, list):
        finding.fatal = True
        _reason(finding, "corrupt_record", "config.schedule must be a list")
        return
    if trigger_kind in {"manual", "immediate"}:
        if schedule:
            _reason(finding, "schedule_semantic_loss",
                    f"config.trigger {trigger_kind} requires an empty schedule")
    elif len(schedule) != 1:
        _reason(finding, "schedule_semantic_loss",
                f"config.schedule must have exactly one entry; found {len(schedule)}")
    else:
        _validate_schedule_entry(
            finding, schedule[0], require_timezone=True,
            require_next_fire=require_next_fire, scope="config.schedule[0]")
        if trigger_kind in {"once", "interval", "cron"} \
                and schedule[0].get("kind") != trigger_kind:
            _reason(finding, "schedule_semantic_loss",
                    "config.trigger kind does not match config.schedule kind")

    recovery = config.get("recovery")
    if not isinstance(recovery, dict) or set(recovery) - {
            "enabled", "monitoring", "maxAttempts", "backoffSec",
            "stableResetSec"}:
        _reason(finding, "corrupt_record", "config.recovery policy is invalid")
    else:
        if type(recovery.get("enabled")) is not bool \
                or type(recovery.get("monitoring")) is not bool:
            _reason(finding, "corrupt_record",
                    "config.recovery enabled/monitoring must be boolean")
        attempts = recovery.get("maxAttempts")
        if type(attempts) is not int or not 1 <= attempts <= 10:
            _reason(finding, "corrupt_record",
                    "config.recovery.maxAttempts must be 1..10")
        backoff = recovery.get("backoffSec")
        if not isinstance(backoff, list) or len(backoff) != 3 or any(
                type(item) is not int or not 0 <= item <= 86400 for item in backoff):
            _reason(finding, "corrupt_record",
                    "config.recovery.backoffSec must be three non-negative integers")
        stable = recovery.get("stableResetSec")
        if type(stable) is not int or not 0 <= stable <= 86400:
            _reason(finding, "corrupt_record",
                    "config.recovery.stableResetSec must be 0..86400")
        if recovery.get("enabled") and not recovery.get("monitoring"):
            _reason(finding, "unsupported_complex_instance",
                    "recovery is enabled without monitoring")

    overlap = config.get("overlap")
    if not isinstance(overlap, dict) or set(overlap) - {"mode", "parallelLimit"}:
        _reason(finding, "corrupt_record", "config.overlap policy is invalid")
    else:
        if overlap.get("mode") not in {"forbid", "queue", "parallel"}:
            _reason(finding, "corrupt_record", "config.overlap.mode is invalid")
        limit = overlap.get("parallelLimit")
        if type(limit) is not int or not 1 <= limit <= 32:
            _reason(finding, "corrupt_record",
                    "config.overlap.parallelLimit must be 1..32")

    notification = config.get("notification")
    if not isinstance(notification, dict) or set(notification) - {
            "on", "target", "sessionIds"}:
        _reason(finding, "corrupt_record", "config.notification policy is invalid")
    else:
        events = notification.get("on")
        if not isinstance(events, list) or any(event not in {
                "completed", "failed", "cancelled", "unknown", "uncertain",
                "cleaning"} for event in events):
            _reason(finding, "corrupt_record",
                    "config.notification.on contains unknown states")
        if notification.get("target") != "job":
            _reason(finding, "unsupported_complex_instance",
                    "config.notification target must be job")
        recipients = notification.get("sessionIds", [])
        if not isinstance(recipients, list) or len(recipients) > 128 or any(
                not isinstance(item, str) or not item for item in recipients) \
                or len(set(recipients)) != len(recipients):
            _reason(finding, "corrupt_record",
                    "config.notification.sessionIds is invalid")

    completion = config.get("completion")
    if not isinstance(completion, dict) or set(completion) - {
            "kind", "signalType"}:
        _reason(finding, "corrupt_record", "config.completion policy is invalid")
    else:
        if completion.get("kind") not in {"ordinary", "monitoring"}:
            _reason(finding, "corrupt_record", "config.completion.kind is invalid")
        if "signalType" in completion and (
                not isinstance(completion["signalType"], str)
                or not 1 <= len(completion["signalType"]) <= 128):
            _reason(finding, "corrupt_record",
                    "config.completion.signalType is invalid")

    if require_schedule and not schedule and trigger_kind not in {"manual", "immediate"}:
        _reason(finding, "missing_reference", "scheduled Job has no schedule entry")


def _classify_background(loaded: Loaded, data: dict) -> Finding:
    finding = Finding(
        source_type=MIG_BG, source_kind="background-process",
        original_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
        owner={"kind": "unknown", "id": None}, target=_target_from_job(data),
        auth_evidence="none", cursor=_job_cursor(data),
    )
    _unknown_fields(finding, data, BACKGROUND_PROCESS_FIELDS, "background-process record")
    _require_fields(finding, data, BACKGROUND_PROCESS_REQUIRED, "background-process record")
    finding.owner, finding.auth_evidence = _derive_owner(data)
    if finding.auth_evidence == "none":
        _reason(finding, "unverified_authority",
                "no creator/source Session or authorization evidence")
    if data.get("scheduledParentJobId") or data.get("shellCommand"):
        _reason(finding, "unsupported_complex_instance",
                "scheduled shell child is owned by its parent scheduled task")
    if data.get("runId") or data.get("config") or data.get("legacyCompat"):
        _reason(finding, "unsupported_complex_instance",
                "record is an Action/legacy projection, not a classic background process")
    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or any(
            not isinstance(item, str) or not item for item in argv):
        _reason(finding, "corrupt_record", "argv must be a non-empty string array")
    if not _is_abs_path(data.get("cwd")):
        _reason(finding, "platform_unsupported", "cwd is not an absolute path")
    if not isinstance(data.get("hideWindow"), bool):
        _reason(finding, "corrupt_record", "hideWindow must be boolean")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        _reason(finding, "corrupt_record", "name is required")
    if not isinstance(data.get("description"), str):
        _reason(finding, "corrupt_record", "description must be a string")
    status = data.get("status")
    if status in ACTIVE_JOB_STATUS:
        _reason(finding, "active_resource", f"background process is {status}")
        finding.active_handles.append("status:" + str(status))
    elif status not in TERMINAL_BG_STATUS:
        _reason(finding, "uncertain_effect", f"unknown background status: {status!r}")
    else:
        if data.get("processTreeEnded") is not True \
                and data.get("cleanupTreeEnded") is not True:
            _reason(finding, "uncertain_effect",
                    "terminal record has no process-tree cleanup proof")
        if not isinstance(data.get("exitCode"), int):
            _reason(finding, "uncertain_effect",
                    "terminal record has no integer exit code")
    if data.get("attemptSnapshot"):
        _reason(finding, "uncertain_effect",
                "attemptSnapshot is present; an effect may still be in flight")
        finding.active_handles.append("attemptSnapshot")
    last_execution = data.get("lastExecution")
    if isinstance(last_execution, dict) and last_execution.get("status") in {
            "running", "unknown", "uncertain"}:
        _reason(finding, "uncertain_effect",
                f"lastExecution status is {last_execution.get('status')!r}")
    active_attempts = _active_from_attempts(data)
    if active_attempts:
        _reason(finding, "active_resource",
                "attempt ledger still contains active/uncertain attempts")
        finding.active_handles.extend(active_attempts)
    if data.get("notificationState") == "pending":
        _reason(finding, "pending_notice",
                "terminal notification has not been delivered")
        finding.notice_receipts.append("notificationState:pending")
    if data.get("pid") is not None and status in TERMINAL_BG_STATUS \
            and data.get("processTreeEnded") is not True:
        _reason(finding, "active_resource",
                "PID is retained without a verified process-tree end")
        finding.active_handles.append("pid:" + str(data.get("pid")))
    if status in TERMINAL_BG_STATUS:
        finding.expected_diff = [
            "old exit/log/resource receipts are historical evidence only",
            "no new Run is fabricated from the old runCount/status",
        ]
    if not finding.reasons:
        finding.field_map = dict(BG_FIELD_MAP)
    return finding


def _classify_message(loaded: Loaded, data: dict) -> Finding:
    kind = data.get("kind")
    finding = Finding(
        source_type=MIG_MSG, source_kind=kind,
        original_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
        owner={"kind": "unknown", "id": None}, target=_target_from_job(data),
        auth_evidence="none", cursor=_job_cursor(data),
    )
    _unknown_fields(finding, data, MESSAGE_FIELDS, f"{kind} record")
    _require_fields(finding, data, MESSAGE_REQUIRED, f"{kind} record")
    finding.owner, finding.auth_evidence = _derive_owner(data)
    if finding.auth_evidence == "none":
        _reason(finding, "unverified_authority",
                "no creator/source Session or authorization evidence")
    if data.get("source") not in KNOWN_SOURCES:
        _reason(finding, "unknown_schema",
                f"unknown message source policy: {data.get('source')!r}")
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "message schedule is not an object")
    else:
        _unknown_fields(finding, schedule, {"type", "at", "delaySeconds"},
                        "message schedule")
        if schedule.get("type") != "once":
            if schedule.get("type") == "interval":
                _reason(finding, "business_completion_coupling",
                        "interval delivery is coupled to business completion")
            elif schedule.get("type") == "weekly":
                _reason(finding, "schedule_semantic_loss",
                        "weekly schedule is not a v1 once-only message migration")
            else:
                _reason(finding, "unknown_schema",
                        f"unknown message schedule type: {schedule.get('type')!r}")
        else:
            has_at = schedule.get("at") is not None
            has_delay = schedule.get("delaySeconds") is not None
            if has_at == has_delay:
                _reason(finding, "schedule_semantic_loss",
                        "once schedule requires exactly one of at/delaySeconds")
            if has_at and _parse_time(schedule.get("at")) is None:
                _reason(finding, "schedule_semantic_loss",
                        "schedule.at is not ISO-8601")
            if has_delay and (
                    isinstance(schedule.get("delaySeconds"), bool)
                    or not isinstance(schedule.get("delaySeconds"), (int, float))
                    or schedule["delaySeconds"] <= 0):
                _reason(finding, "schedule_semantic_loss",
                        "schedule.delaySeconds must be positive")
    if _parse_time(data.get("nextRunAt")) is None:
        _reason(finding, "schedule_semantic_loss",
                "nextRunAt is missing or not an absolute ISO-8601 time")
    if data.get("runCount") != 0:
        _reason(finding, "counter_coupling",
                f"message has consumed runCount={data.get('runCount')!r}")
    if data.get("lastRunAt") is not None:
        _reason(finding, "uncertain_effect", "lastRunAt is set")
    if data.get("lastDelivery") is not None:
        _reason(finding, "uncertain_effect", "lastDelivery is set")
    if data.get("lastError") not in (None, ""):
        _reason(finding, "uncertain_effect",
                "lastError is set; a previous delivery may be unresolved")
    if data.get("attemptSnapshot"):
        _reason(finding, "uncertain_effect",
                "attemptSnapshot is present for a pending message")
    if data.get("status") not in {"pending", "scheduled"}:
        _reason(finding, "active_resource" if data.get("status") in ACTIVE_JOB_STATUS
                else "unsupported_complex_instance",
                f"message is not a pending one-shot: status={data.get('status')!r}")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        _reason(finding, "corrupt_record", "message text is required")
    if kind == "session-message":
        target = data.get("targetSessionId")
        if not isinstance(target, str) or not target:
            _reason(finding, "target_missing", "targetSessionId is required")
        if "targetSessionIds" in data and data.get("targetSessionIds") is not None:
            _reason(finding, "corrupt_record",
                    "session-message must not carry targetSessionIds")
    else:
        targets = data.get("targetSessionIds")
        if not isinstance(targets, list) or not targets or any(
                not isinstance(item, str) or not item for item in targets):
            _reason(finding, "target_missing",
                    "broadcast requires a non-empty targetSessionIds list")
        elif len(set(targets)) != len(targets):
            deduped = list(dict.fromkeys(targets))
            duplicates = sorted({target for target in targets
                                 if targets.count(target) > 1})
            finding.expected_diff.append(
                "duplicate broadcast targets are collapsed in stored order: "
                + ", ".join(duplicates))
            finding.target = {"kind": "sessions", "sessionIds": deduped}
        if "targetSessionId" in data and data.get("targetSessionId") is not None:
            _reason(finding, "corrupt_record",
                    "session-broadcast must not carry targetSessionId")
    target_struct = data.get("targetStruct")
    if not isinstance(target_struct, dict) or not (
            target_struct.get("sessionId") or target_struct.get("sessionIds")):
        _reason(finding, "target_missing", "targetStruct has no Session target")
    if not finding.reasons:
        finding.field_map = _message_field_map(data)
    return finding


def _legacy_schedule_view(data: dict) -> dict:
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        return {}
    view = dict(schedule)
    view["nextFireAt"] = data.get("next_fire_at")
    view["lastFireAt"] = data.get("last_fire_at")
    view["misfirePolicy"] = data.get("misfire_policy")
    view["enabled"] = bool(data.get("enabled"))
    return view


def _classify_scheduled(loaded: Loaded, data: dict, ctx: Context, *,
                        legacy: bool) -> Finding:
    source_kind = "legacy-scheduled-task" if legacy else "scheduled-task"
    finding = Finding(
        source_type=MIG_SCHED, source_kind=source_kind,
        original_id=(data.get("id") if legacy else data.get("taskId")) or
                    data.get("jobId"),
        owner={"kind": "system", "id": "system"}, target=_target_from_job(data),
        auth_evidence="system_owned", cursor=_job_cursor(data),
    )
    if legacy:
        _unknown_fields(finding, data, LEGACY_TASK_FIELDS, "legacy scheduled task")
        _require_fields(finding, data, LEGACY_TASK_REQUIRED, "legacy scheduled task")
        schedule_entry = _legacy_schedule_view(data)
        if not schedule_entry:
            finding.fatal = True
            _reason(finding, "corrupt_record", "legacy schedule is not an object")
        stored_action = data.get("action")
        if stored_action not in (None, {"api": "assign"}):
            _reason(finding, "schedule_semantic_loss",
                    "legacy migration only supported the default assign action")
        action = {"api": "assign"}
        task_id = data.get("id")
        run_count = data.get("run_count")
        if type(run_count) is not int or run_count < 0:
            _reason(finding, "corrupt_record", "legacy run_count must be a non-negative integer")
        elif run_count != 0:
            _reason(finding, "counter_coupling",
                    f"legacy task has consumed run_count={run_count!r}")
        target_session_id = data.get("target_session_id")
        if isinstance(target_session_id, str) and target_session_id:
            finding.target = {"kind": "session", "sessionId": target_session_id}
        finding.cursor = {
            "nextFireAt": data.get("next_fire_at"),
            "lastFireAt": data.get("last_fire_at"),
            "runCount": run_count,
            "maxRuns": data.get("max_runs"),
        }
        if data.get("last_status") not in (None, ""):
            _reason(finding, "uncertain_effect",
                    f"legacy last_status={data.get('last_status')!r}")
        if data.get("last_error") not in (None, ""):
            _reason(finding, "uncertain_effect", "legacy last_error is set")
        if data.get("enabled") is not True:
            _reason(finding, "unsupported_complex_instance",
                    "disabled legacy task has no unambiguous live state")
        if _parse_time(data.get("next_fire_at")) is None:
            _reason(finding, "schedule_semantic_loss",
                    "legacy next_fire_at is missing or invalid")
        for key in ("created_at", "updated_at"):
            if _parse_time(data.get(key)) is None:
                _reason(finding, "corrupt_record", f"legacy {key} is invalid")
        max_runs = data.get("max_runs")
        if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
            _reason(finding, "corrupt_record", "legacy max_runs is invalid")
        schedule_list = [schedule_entry] if schedule_entry else []
    else:
        _unknown_fields(finding, data, SCHEDULED_FIELDS, "scheduled-task record")
        _require_fields(finding, data, SCHEDULED_REQUIRED, "scheduled-task record")
        if data.get("source") != "automation":
            _reason(finding, "unsupported_complex_instance",
                    "scheduled task source is not automation")
        for key in ("config", "legacyCompat", "legacySchedule",
                    "legacySchedulerCompat", "schedulerOwner", "runId",
                    "attemptSnapshot", "activeExecutions", "pendingRuns",
                    "execution"):
            if data.get(key):
                _reason(finding, "unsupported_complex_instance",
                        f"scheduled task carries execution projection field {key!r}")
        schedule_list = data.get("schedule")
        if not isinstance(schedule_list, list):
            finding.fatal = True
            _reason(finding, "corrupt_record", "schedule is not a list")
            schedule_list = []
        action = data.get("action") or {"api": "assign"}
        task_id = data.get("taskId")
        if data.get("runCount") != 0:
            _reason(finding, "counter_coupling",
                    f"scheduled task has consumed runCount={data.get('runCount')!r}")
        if data.get("status") not in {"pending", "scheduled"}:
            _reason(finding, "active_resource" if data.get("status") in ACTIVE_JOB_STATUS
                    else "unsupported_complex_instance",
                    f"scheduled task is not unstarted: status={data.get('status')!r}")
        if data.get("runStartedAt") is not None:
            _reason(finding, "active_resource",
                    "runStartedAt is set on a pending/scheduled task")
            finding.active_handles.append("runStartedAt")
        active_attempts = _active_from_attempts(data)
        if active_attempts:
            _reason(finding, "active_resource",
                    "attempt ledger still contains active/uncertain attempts")
            finding.active_handles.extend(active_attempts)
        last_execution = data.get("lastExecution")
        if isinstance(last_execution, dict) and last_execution.get("status") in {
                "running", "unknown", "uncertain"}:
            _reason(finding, "uncertain_effect",
                    f"lastExecution status is {last_execution.get('status')!r}")
        if data.get("enabled") is not True:
            _reason(finding, "unsupported_complex_instance",
                    "disabled scheduled task has no unambiguous live state")
        if type(data.get("paused")) is not bool:
            _reason(finding, "corrupt_record", "paused must be boolean")
        if data.get("undeliveredFires"):
            _reason(finding, "pending_notice",
                    f"{len(data['undeliveredFires'])} undelivered fire(s) remain")
            finding.notice_receipts.append("undeliveredFires")
        if data.get("terminalOutbox"):
            _reason(finding, "pending_notice", "terminal outbox has not been drained")
            finding.notice_receipts.append("terminalOutbox")
        if data.get("notificationState") == "pending":
            _reason(finding, "pending_notice", "terminal notification is still pending")
            finding.notice_receipts.append("notificationState:pending")
        if data.get("lastDelivery") is not None:
            _reason(finding, "uncertain_effect",
                    "lastDelivery is set on an unstarted task")
        if data.get("lastStatus") in {"error", "undeliverable", "uncertain", "unknown"}:
            _reason(finding, "uncertain_effect",
                    f"lastStatus={data.get('lastStatus')!r}")
        if data.get("lastError") not in (None, ""):
            _reason(finding, "uncertain_effect", "lastError is set")
        if _parse_time(data.get("nextFireAt")) is None:
            _reason(finding, "schedule_semantic_loss",
                    "nextFireAt is missing or invalid")
        max_runs = data.get("maxRuns")
        if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
            _reason(finding, "corrupt_record", "maxRuns is invalid")
        if isinstance(max_runs, int) and data.get("runCount", 0) >= max_runs:
            _reason(finding, "counter_coupling", "maxRuns is already consumed")

    if len(schedule_list) != 1:
        _reason(finding, "schedule_semantic_loss",
                f"exactly one schedule entry is supported; found {len(schedule_list)}")
    elif schedule_list:
        _validate_schedule_entry(
            finding, schedule_list[0], require_timezone=True,
            require_next_fire=True, scope="schedule[0]")
        entry = schedule_list[0]
        if isinstance(entry, dict):
            if not legacy:
                missing = sorted({"id", "misfirePolicy", "enabled",
                                  "nextFireAt", "lastFireAt"} - set(entry))
                if missing:
                    finding.fatal = True
                    _reason(finding, "unknown_schema",
                            "schedule[0] is missing control field(s): "
                            + ", ".join(missing))
            if entry.get("misfirePolicy") not in (None, *MISFIRE_POLICIES):
                _reason(finding, "schedule_semantic_loss",
                        "schedule entry misfirePolicy is invalid")
            if entry.get("enabled") is False:
                _reason(finding, "schedule_semantic_loss",
                        "schedule entry is disabled")
    _validate_action(
        finding, action, text=data.get("text"),
        target=(data.get("target_session_id") if legacy
                else data.get("targetSessionId")))
    _runs_for_task(finding, task_id if isinstance(task_id, str) else None, ctx)
    if not finding.reasons:
        finding.field_map = _scheduled_field_map(data, legacy=legacy)
    return finding


def _classify_action_job(loaded: Loaded, data: dict, ctx: Context) -> Finding:
    finding = Finding(
        source_type=MIG_ACTION_JOB, source_kind="action-job",
        original_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
        owner={"kind": "unknown", "id": None}, target=_target_from_job(data),
        auth_evidence="none", cursor=_job_cursor(data),
    )
    _unknown_fields(finding, data, ACTION_JOB_FIELDS, "Action Job record")
    _require_fields(finding, data, {
        "jobId", "kind", "config", "action", "status", "sourceStruct",
        "targetStruct", "creatorSessionId", "referenceGeneration",
        "creationFingerprint", "createdAt", "updatedAt",
    }, "Action Job record")
    finding.owner, finding.auth_evidence = _derive_owner(data)
    if finding.auth_evidence == "none":
        _reason(finding, "unverified_authority",
                "no creator/source Session or authorization evidence")
    if data.get("authorization") is not None:
        authorization = data.get("authorization")
        if not isinstance(authorization, dict) \
                or authorization.get("type") not in AUTH_TYPES:
            _reason(finding, "unverified_authority",
                    "authorization object has no known user/agent/system basis")
    for key in ("legacyCompat", "legacySchedule", "legacySchedulerCompat",
                "schedulerOwner"):
        if data.get(key):
            _reason(finding, "unsupported_complex_instance",
                    f"Action Job carries legacy execution projection {key!r}")
    if data.get("status") in ACTIVE_JOB_STATUS:
        _reason(finding, "active_resource",
                f"Action Job is {data.get('status')!r}")
        finding.active_handles.append("status:" + str(data.get("status")))
    last_execution = data.get("lastExecution")
    if isinstance(last_execution, dict):
        status = last_execution.get("status")
        if status in {"running", "unknown", "uncertain"}:
            _reason(finding, "uncertain_effect",
                    f"lastExecution status is {status!r}")
    for key in ("attemptSnapshot", "activeExecutions", "pendingRuns",
                "execution", "runId"):
        if data.get(key):
            _reason(finding, "active_resource" if key != "attemptSnapshot"
                    else "uncertain_effect",
                    f"Action Job carries live handle {key!r}")
            finding.active_handles.append(key)
    active_attempts = _active_from_attempts(data)
    if active_attempts:
        _reason(finding, "active_resource",
                "attempt ledger still contains active/uncertain attempts")
        finding.active_handles.extend(active_attempts)
    if data.get("undeliveredFires"):
        _reason(finding, "pending_notice", "Action Job has undelivered fires")
        finding.notice_receipts.append("undeliveredFires")
    if data.get("notificationState") == "pending":
        _reason(finding, "pending_notice", "terminal notification is still pending")
        finding.notice_receipts.append("notificationState:pending")
    if data.get("terminalOutbox"):
        _reason(finding, "pending_notice", "terminal outbox has not been drained")
        finding.notice_receipts.append("terminalOutbox")

    config = data.get("config")
    if not isinstance(config, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "Action Job config is not an object")
    else:
        _validate_config(finding, config, ctx.action_index,
                         require_schedule=False, require_next_fire=False)
        trigger_kind = config.get("trigger", {}).get("kind") \
            if isinstance(config.get("trigger"), dict) else None
        schedule = config.get("schedule")
        if trigger_kind in {"once", "interval", "cron", "schedule"}:
            if isinstance(schedule, list) and schedule and data.get("runCount"):
                _reason(finding, "counter_coupling",
                        "scheduled Action Job already consumed a run")
            if data.get("status") not in {"pending", "scheduled"}:
                _reason(finding, "unsupported_complex_instance",
                        "scheduled Action Job is not pending/scheduled")
            if isinstance(schedule, list) and schedule:
                _validate_schedule_entry(
                    finding, schedule[0], require_timezone=True,
                    require_next_fire=True, scope="config.schedule[0]")
        elif trigger_kind == "event":
            _reason(finding, "unsupported_complex_instance",
                    "event-triggered Job must be migrated as MIG-TRIGGER")
        action_ref = data.get("action")
        if not isinstance(action_ref, dict):
            finding.fatal = True
            _reason(finding, "missing_reference", "Action Job has no Action reference")
        else:
            _unknown_fields(finding, action_ref,
                            {"api", "actionId", "arguments"}, "action")
            if action_ref.get("api") != "action_reference":
                _reason(finding, "unsupported_complex_instance",
                        "Action Job does not use action_reference")
            ref_action = action_ref.get("actionId")
            config_action = config.get("actionId") if isinstance(config, dict) else None
            if isinstance(config, dict) and "steps" in config:
                steps = config.get("steps") or []
                config_action = steps[0].get("actionId") if steps and isinstance(
                    steps[0], dict) else None
            if ref_action != config_action:
                _reason(finding, "missing_reference",
                        "action.actionId does not match config actionId")
            if not isinstance(action_ref.get("arguments"), dict):
                _reason(finding, "corrupt_record",
                        "action.arguments must be an object")
            _check_reference_generation(finding, ctx.action_index, config,
                                        data.get("referenceGeneration"))
    if not isinstance(data.get("creationFingerprint"), str) \
            or not data["creationFingerprint"]:
        _reason(finding, "missing_reference",
                "creationFingerprint is missing; Action references are not pinned")
    if finding.target is None:
        _reason(finding, "target_missing",
                "Action Job has no persisted Session target")
    _runs_for_task(finding, data.get("taskId") if isinstance(data.get("taskId"), str)
                   else None, ctx)
    if not finding.reasons:
        finding.field_map = {
            "jobId": "provenance.originalJobId",
            "authorization": "authorization",
            "createdAt": "provenance.createdAt",
            "updatedAt": "provenance.updatedAt",
            **_config_field_map(config, "job"),
        }
    return finding


def _classify_trigger(loaded: Loaded, data: dict, ctx: Context) -> Finding:
    finding = Finding(
        source_type=MIG_TRIGGER, source_kind="direct-trigger",
        original_id=data.get("id") if isinstance(data.get("id"), str) else None,
        owner={"kind": "unknown", "id": None}, target=None,
        auth_evidence="none", cursor={},
    )
    _unknown_fields(finding, data, TRIGGER_FIELDS, "Trigger record")
    _require_fields(finding, data, {
        "id", "name", "eventTypes", "enabled", "revision",
        "createdBy", "createdAt", "updatedAt", "authorization",
    }, "Trigger record")
    finding.owner, finding.auth_evidence = _derive_owner(data)
    if finding.auth_evidence == "none":
        _reason(finding, "unverified_authority",
                "no createdBy or authorization evidence")
    trigger_id = data.get("id")
    if not isinstance(trigger_id, str) or not trigger_id.startswith("trg_"):
        _reason(finding, "corrupt_record", "Trigger ID must start with trg_")
    for key in ("ownerJobId", "ownerLinkId"):
        if data.get(key):
            _reason(finding, "unsupported_complex_instance",
                    f"managed Trigger field {key!r} requires Job graph migration")
    if data.get("targetJobId"):
        _reason(finding, "unsupported_complex_instance",
                "Trigger targets an existing Job; target graph is not v1-simple")
        finding.reference_edges.append("job:" + str(data["targetJobId"]))
    let = data.get("eventTypes")
    if not isinstance(let, list) or not let or any(
            not isinstance(item, str) or not item for item in let):
        _reason(finding, "corrupt_record", "eventTypes must be non-empty strings")
    elif len(set(let)) != len(let):
        _reason(finding, "unsupported_complex_instance",
                "duplicate eventTypes cannot be preserved without guessing")
    event_filter = data.get("eventFilter", {})
    if not isinstance(event_filter, dict) or len(event_filter) > 16:
        _reason(finding, "corrupt_record", "eventFilter must be an object with <=16 keys")
    else:
        for key, value in event_filter.items():
            if not isinstance(key, str) or not key.startswith("payload.") \
                    or any(not part for part in key.split(".")) \
                    or not _is_scalar(value):
                _reason(finding, "corrupt_record",
                        "eventFilter requires payload.* scalar equality values")
                break
    _validate_condition(finding, data.get("condition"))
    has_action = "actionId" in data
    has_steps = "steps" in data
    if not has_action and not has_steps:
        finding.fatal = True
        _reason(finding, "corrupt_record",
                "Trigger must contain actionId or steps")
    action_ids: list[str] = []
    if has_steps:
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            finding.fatal = True
            _reason(finding, "corrupt_record", "Trigger steps must be non-empty")
        else:
            seen: set[str] = set()
            for position, step in enumerate(steps):
                if not isinstance(step, dict):
                    finding.fatal = True
                    _reason(finding, "corrupt_record",
                            f"Trigger steps[{position}] is not an object")
                    continue
                _unknown_fields(finding, step, STEP_FIELDS,
                                f"Trigger steps[{position}]")
                if set(step) != STEP_FIELDS:
                    _reason(finding, "corrupt_record",
                            f"Trigger steps[{position}] needs stepId/actionId/arguments")
                step_id = step.get("stepId")
                if not isinstance(step_id, str) or not step_id or step_id in seen:
                    _reason(finding, "corrupt_record",
                            f"Trigger steps[{position}].stepId is invalid or duplicated")
                else:
                    seen.add(step_id)
                step_action = step.get("actionId")
                if not isinstance(step_action, str) or not step_action:
                    _reason(finding, "corrupt_record",
                            f"Trigger steps[{position}].actionId is invalid")
                elif step_action not in action_ids:
                    action_ids.append(step_action)
                if not isinstance(step.get("arguments"), dict):
                    _reason(finding, "corrupt_record",
                            f"Trigger steps[{position}].arguments must be an object")
                else:
                    _validate_bindings(finding, step["arguments"],
                                       f"Trigger steps[{position}].arguments")
            first_action = steps[0].get("actionId") if steps and isinstance(
                steps[0], dict) else None
            if has_action and data.get("actionId") != first_action:
                finding.fatal = True
                _reason(finding, "corrupt_record",
                        "Trigger actionId must match the first step")
    elif has_action:
        action_id = data.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            finding.fatal = True
            _reason(finding, "corrupt_record", "Trigger actionId is invalid")
        else:
            action_ids = [action_id]
    if has_action and not has_steps:
        if "arguments" not in data:
            _reason(finding, "corrupt_record",
                    "actionId Trigger must persist an arguments object")
        elif not isinstance(data["arguments"], dict):
            _reason(finding, "corrupt_record", "Trigger arguments must be an object")
        else:
            _validate_bindings(finding, data["arguments"], "Trigger arguments")
    elif isinstance(data.get("arguments"), dict):
        _validate_bindings(finding, data["arguments"], "Trigger arguments")
    _check_action_refs(finding, action_ids, ctx.action_index)
    if type(data.get("revision")) is not int or data["revision"] < 1:
        _reason(finding, "corrupt_record", "Trigger revision must be a positive integer")
    if data.get("executionMode") not in (None, "serial", "parallel"):
        _reason(finding, "corrupt_record", "Trigger executionMode is invalid")
    concurrency = data.get("maxConcurrency")
    if concurrency is not None and (type(concurrency) is not int or concurrency < 1):
        _reason(finding, "corrupt_record", "Trigger maxConcurrency is invalid")
    retry = data.get("retry")
    if not isinstance(retry, dict) or set(retry) != {"enabled", "maxAttempts"}:
        _reason(finding, "corrupt_record",
                "Trigger retry must be {enabled, maxAttempts}")
    elif type(retry["enabled"]) is not bool or type(retry["maxAttempts"]) is not int \
            or not 1 <= retry["maxAttempts"] <= 10:
        _reason(finding, "corrupt_record", "Trigger retry values are invalid")
    if type(data.get("enabled")) is not bool:
        _reason(finding, "corrupt_record", "Trigger enabled must be boolean")
    runs = ctx.trigger_runs.get(trigger_id, {}) if isinstance(trigger_id, str) else {}
    if ctx.trigger_runs_unknown:
        _reason(finding, "uncertain_effect",
                "automation run ledger is unreadable; active state cannot be excluded")
    for run_id, status in sorted(runs.items()):
        if status in {"queued", "retry_wait", "running"}:
            _reason(finding, "active_resource", f"Trigger run {run_id} is {status}")
        elif status in UNCERTAIN_RUN_STATUS:
            _reason(finding, "uncertain_effect",
                    f"Trigger run {run_id} has unresolved status {status}")
    if not finding.reasons:
        finding.field_map = _trigger_field_map(data)
    return finding


def _classify_schedule_template(loaded: Loaded, data: dict) -> Finding:
    finding = Finding(
        source_type=MIG_TEMPLATE, source_kind="schedule-template",
        original_id=data.get("id") if isinstance(data.get("id"), str) else None,
        owner={"kind": "registry", "id": "registry"},
        target=None, auth_evidence="registry_scope", cursor={},
    )
    _unknown_fields(finding, data, {
        "id", "name", "kind", "misfirePolicy", "builtin", "at",
        "intervalSec", "cron", "timezone",
    }, "schedule-only Template")
    _require_fields(finding, data, {"id", "name", "kind"},
                    "schedule-only Template")
    if data.get("kind") not in {"once", "interval", "cron"}:
        finding.fatal = True
        _reason(finding, "unknown_schema",
                f"unknown schedule template kind: {data.get('kind')!r}")
    _reason(finding, "schedule_semantic_loss",
            "schedule-only Template has no complete Job configuration or Action references")
    return finding


def _classify_template(loaded: Loaded, data: dict, ctx: Context) -> Finding:
    finding = Finding(
        source_type=MIG_TEMPLATE, source_kind="job-template",
        original_id=data.get("id") if isinstance(data.get("id"), str) else None,
        owner={"kind": "registry", "id": "registry"},
        target=None, auth_evidence="registry_scope", cursor={},
    )
    _unknown_fields(finding, data, TEMPLATE_FIELDS, "Job Template record")
    _require_fields(finding, data, TEMPLATE_FIELDS, "Job Template record")
    if "enabled" in data:
        _reason(finding, "unsupported_complex_instance",
                "legacy Template enabled flag has no unambiguous meaning")
    if not isinstance(data.get("id"), str) or not data["id"].startswith("jtpl_"):
        _reason(finding, "corrupt_record", "Template ID must start with jtpl_")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        _reason(finding, "corrupt_record", "Template name is required")
    if not isinstance(data.get("description"), str):
        _reason(finding, "corrupt_record", "Template description must be a string")
    if type(data.get("revision")) is not int or data["revision"] < 1:
        _reason(finding, "corrupt_record", "Template revision must be positive")
    config = data.get("config")
    if not isinstance(config, dict):
        finding.fatal = True
        _reason(finding, "corrupt_record", "Template config is not an object")
    else:
        _validate_config(finding, config, ctx.action_index,
                         require_schedule=False, require_next_fire=False)
        trigger_kind = config.get("trigger", {}).get("kind") \
            if isinstance(config.get("trigger"), dict) else None
        if trigger_kind == "event":
            _reason(finding, "unsupported_complex_instance",
                    "event-triggered Template belongs to MIG-TRIGGER")
        schedule = config.get("schedule")
        if trigger_kind in {"once", "interval", "cron", "schedule"}:
            if not isinstance(schedule, list) or len(schedule) != 1:
                _reason(finding, "schedule_semantic_loss",
                        "Template schedule composition must have exactly one entry")
            else:
                _validate_schedule_entry(
                    finding, schedule[0], require_timezone=True,
                    require_next_fire=False, scope="config.schedule[0]")
    if isinstance(config, dict):
        _check_reference_generation(finding, ctx.action_index, config,
                                    data.get("referenceGeneration"))
    if not finding.reasons:
        finding.field_map = {
            "id": "provenance.originalTemplateId",
            "name": "template.metadata.name",
            "description": "template.metadata.description",
            "revision": "provenance.revision",
            "referenceGeneration": "provenance.referenceGeneration",
            **_config_field_map(config, "template"),
        }
    return finding


def _load_json_file(root: Path, path: Path, errors: list[dict]) -> Loaded:
    try:
        relpath = path.relative_to(root).as_posix()
    except ValueError:
        relpath = str(path)
    if not is_within_root(root, path):
        detail = "resolved path escapes the supplied root"
        errors.append({"code": "path_escape", "path": relpath, "detail": detail})
        return Loaded(path, relpath, None, None, None, "path_escape", detail)
    raw: bytes | None = None
    digest: str | None = None
    try:
        raw = path.read_bytes()
        digest = _sha256_bytes(raw)
    except OSError as exc:
        errors.append({"code": "corrupt_record", "path": relpath,
                       "detail": f"read failed: {exc}"})
        return Loaded(path, relpath, None, None, None,
                      "corrupt_record", f"read failed: {exc}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        errors.append({"code": "corrupt_record", "path": relpath,
                       "detail": f"not UTF-8: {exc}"})
        return Loaded(path, relpath, raw, digest, None,
                      "corrupt_record", f"not UTF-8: {exc}")
    try:
        data = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except DuplicateKeyError as exc:
        errors.append({"code": "corrupt_record", "path": relpath,
                       "detail": str(exc)})
        return Loaded(path, relpath, raw, digest, None, "corrupt_record", str(exc))
    except json.JSONDecodeError as exc:
        detail = f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        errors.append({"code": "corrupt_record", "path": relpath,
                       "detail": detail})
        return Loaded(path, relpath, raw, digest, None, "corrupt_record", detail)
    return Loaded(path, relpath, raw, digest, data,
                  fingerprint=_fingerprint(data))


def _load_actions_registry(root: Path, errors: list[dict]) -> ActionIndex:
    path = root / "actions" / "registry.json"
    if not path.is_file():
        return ActionIndex()
    loaded = _load_json_file(root, path, errors)
    if loaded.data is None:
        return ActionIndex()
    state = loaded.data
    if not isinstance(state, dict):
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "actions registry is not an object"})
        return ActionIndex()
    actions = state.get("actions")
    if not isinstance(actions, dict):
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "actions registry has no actions object"})
        return ActionIndex()
    generations = state.get("actionGenerations", {})
    if not isinstance(generations, dict):
        generations = {}
    parsed_generations = {
        key: value for key, value in generations.items()
        if isinstance(key, str) and type(value) is int
    }
    templates: list[tuple[str, dict]] = []
    raw_templates = state.get("jobTemplates", {})
    if isinstance(raw_templates, dict):
        iterable = sorted(raw_templates.items())
    elif isinstance(raw_templates, list):
        iterable = [(str(item.get("id")) if isinstance(item, dict) else str(index), item)
                    for index, item in enumerate(raw_templates)]
    else:
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "jobTemplates is not an object or list"})
        iterable = []
    for key, item in iterable:
        if not isinstance(item, dict):
            errors.append({"code": "corrupt_record", "path": loaded.relpath,
                           "detail": f"jobTemplates[{key!r}] is not an object"})
            continue
        templates.append((key, item))
    return ActionIndex(True, actions, parsed_generations, templates, loaded)


def _load_automation_registry(root: Path, errors: list[dict]) -> tuple[
        list[tuple[str, dict]], dict[str, dict[str, str]], bool, Loaded | None]:
    path = root / "automation" / "registry.json"
    if not path.is_file():
        return [], {}, False, None
    loaded = _load_json_file(root, path, errors)
    if loaded.data is None:
        return [], {}, True, loaded
    state = loaded.data
    if not isinstance(state, dict):
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "automation registry is not an object"})
        return [], {}, True, loaded
    raw_triggers = state.get("triggers", {})
    if isinstance(raw_triggers, dict):
        trigger_items = sorted(raw_triggers.items())
    elif isinstance(raw_triggers, list):
        trigger_items = [(str(item.get("id")) if isinstance(item, dict) else str(index), item)
                         for index, item in enumerate(raw_triggers)]
    else:
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "triggers is not an object or list"})
        trigger_items = []
    triggers: list[tuple[str, dict]] = []
    for key, item in trigger_items:
        if not isinstance(item, dict):
            errors.append({"code": "corrupt_record", "path": loaded.relpath,
                           "detail": f"triggers[{key!r}] is not an object"})
            continue
        triggers.append((key, item))

    raw_runs = state.get("runs", {})
    if isinstance(raw_runs, dict):
        run_items = list(raw_runs.values())
    elif isinstance(raw_runs, list):
        run_items = raw_runs
    else:
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "runs is not an object or list"})
        return triggers, {}, True, loaded
    runs: dict[str, dict[str, str]] = {}
    unknown = False
    for index, run in enumerate(run_items):
        if not isinstance(run, dict) or not isinstance(run.get("triggerId"), str) \
                or not isinstance(run.get("status"), str):
            unknown = True
            continue
        run_id = run.get("id") if isinstance(run.get("id"), str) else f"run-{index}"
        runs.setdefault(run["triggerId"], {})[run_id] = run["status"]
    return triggers, runs, unknown, loaded


def _load_schedule_templates(root: Path, errors: list[dict]) -> tuple[
        Loaded | None, list[tuple[str, dict]]]:
    path = root / "background_jobs" / "schedule_templates.json"
    if not path.is_file():
        return None, []
    loaded = _load_json_file(root, path, errors)
    if not isinstance(loaded.data, dict):
        return loaded, []
    raw = loaded.data.get("templates", [])
    if not isinstance(raw, list):
        errors.append({"code": "corrupt_record", "path": loaded.relpath,
                       "detail": "schedule_templates.templates is not a list"})
        return loaded, []
    items: list[tuple[str, dict]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append({"code": "corrupt_record", "path": loaded.relpath,
                           "detail": f"templates[{index}] is not an object"})
            continue
        items.append((str(item.get("id") or index), item))
    return loaded, items


def _load_run_ledgers(root: Path, errors: list[dict]) -> tuple[
        dict[str, dict[str, str]], bool]:
    result: dict[str, dict[str, str]] = {}
    unknown = False
    for relative in ("background_jobs/runs.jsonl", "scheduler/runs.jsonl"):
        path = root / relative
        if not path.is_file():
            continue
        if not is_within_root(root, path):
            errors.append({"code": "path_escape", "path": relative,
                           "detail": "resolved path escapes the supplied root"})
            unknown = True
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"code": "corrupt_record", "path": relative,
                           "detail": f"read failed: {exc}"})
            unknown = True
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line, object_pairs_hook=_pairs_without_duplicates)
            except (json.JSONDecodeError, DuplicateKeyError) as exc:
                errors.append({"code": "corrupt_record", "path": relative,
                               "detail": f"line {line_number}: {exc}"})
                unknown = True
                continue
            if not isinstance(record, dict):
                unknown = True
                continue
            task_id = record.get("task_id") or record.get("taskId")
            status = record.get("status")
            if not isinstance(task_id, str) or not isinstance(status, str):
                unknown = True
                continue
            run_id = record.get("run_id") or record.get("runId") or f"line-{line_number}"
            result.setdefault(task_id, {})[str(run_id)] = status
    return result, unknown


def _iter_job_paths(root: Path, errors: list[dict]) -> Iterable[Path]:
    directory = root / "background_jobs" / "jobs"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("job_*.json")):
        if not path.is_file():
            continue
        if not is_within_root(root, path):
            errors.append({"code": "path_escape",
                           "path": path.relative_to(root).as_posix(),
                           "detail": "resolved path escapes the supplied root"})
            continue
        yield path


def _iter_legacy_task_paths(root: Path, errors: list[dict]) -> Iterable[Path]:
    directory = root / "scheduler" / "tasks"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        if not is_within_root(root, path):
            errors.append({"code": "path_escape",
                           "path": path.relative_to(root).as_posix(),
                           "detail": "resolved path escapes the supplied root"})
            continue
        yield path


def _make_instance(finding: Finding, loaded: Loaded, record_digest: str,
                   schema_fingerprint: str | None = None) -> dict:
    detail = (DETAIL_UNKNOWN if finding.fatal
              else DETAIL_SIMPLE if not finding.reasons else DETAIL_COMPLEX)
    simple = detail == DETAIL_SIMPLE
    reason_codes = list(dict.fromkeys(item["code"] for item in finding.reasons))
    rejection = [f"{item['code']}: {item['detail']}" for item in finding.reasons]
    migration_id = "mig1_" + _sha256_text(
        f"{finding.source_type}\0{finding.original_id or ''}\0"
        f"{loaded.relpath}\0{MAPPING_VERSION}"
    ).split(":", 1)[1][:24]
    return {
        "migrationId": migration_id,
        "id": finding.original_id,
        "type": finding.source_type,
        "sourceKind": finding.source_kind,
        "owner": finding.owner,
        "target": finding.target,
        "authEvidence": finding.auth_evidence,
        "sourceFile": loaded.relpath,
        "originalPath": loaded.relpath,
        "originalDigest": loaded.digest,
        "sha256": loaded.digest.split(":", 1)[1] if loaded.digest else None,
        "recordDigest": record_digest,
        "schemaFingerprint": schema_fingerprint or loaded.fingerprint,
        "classification": SIMPLE if simple else PRESERVED,
        "classificationDetail": detail,
        "status": "classified" if simple else "preserved_and_reported",
        "mappingVersion": MAPPING_VERSION if simple else None,
        "reasonCodes": reason_codes,
        "reasons": finding.reasons,
        "rejectionReasons": rejection,
        "suggestedAction": "plan-only" if simple else "preserve-and-manual-recreate",
        "targetIds": [],
        "fieldMap": finding.field_map if simple else None,
        "cursor": finding.cursor,
        "activeEffectHandles": finding.active_handles,
        "noticeReceipts": finding.notice_receipts,
        "referenceEdges": finding.reference_edges,
        "expectedSemanticDiff": finding.expected_diff if simple else [
            "none; original record is preserved byte-for-byte"
        ],
        "proofs": {
            "sourceHashVerified": loaded.digest is not None,
            "schemaWhitelist": SCHEMA_WHITELIST,
            "authorityVerified": False,
            "referencesVerified": finding.references_verified,
            "readOnly": True,
        },
    }


def _real_profile_markers(root: Path) -> list[str]:
    return [relative for relative in REAL_PROFILE_MARKERS
            if (root / relative).is_file()]


def build_inventory(root: str | Path, *, allow_real_profile: bool = False) -> dict:
    """Scan a data root and return an inventory/plan document.

    The function is pure with respect to the root: it only reads known files.
    It raises ``RealProfileError`` when the root looks like a real Pan profile
    and ``allow_real_profile`` is false.
    """
    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise NotADirectoryError(f"root is not a directory: {root_path}")
    markers = _real_profile_markers(root_path)
    if markers and not allow_real_profile:
        raise RealProfileError(markers)

    errors: list[dict] = []
    action_index = _load_actions_registry(root_path, errors)
    triggers_raw, trigger_runs, trigger_runs_unknown, automation_loaded = \
        _load_automation_registry(root_path, errors)
    task_runs, task_runs_unknown = _load_run_ledgers(root_path, errors)

    job_files: list[tuple[Loaded, str]] = []
    for path in _iter_job_paths(root_path, errors):
        loaded = _load_json_file(root_path, path, errors)
        job_files.append((loaded, "job"))

    ctx = Context(action_index=action_index, task_runs=task_runs,
                  task_runs_unknown=task_runs_unknown,
                  trigger_runs=trigger_runs,
                  trigger_runs_unknown=trigger_runs_unknown)

    findings: list[tuple[Finding, Loaded, str]] = []

    for loaded, origin in job_files:
        if loaded.data is None:
            finding = Finding(
                source_type=MIG_UNKNOWN, source_kind=None, original_id=None,
                owner={"kind": "unknown", "id": None}, target=None,
                auth_evidence="none", cursor={}, fatal=True,
            )
            _reason(finding, loaded.error_code or "corrupt_record",
                    loaded.error_detail or "record cannot be read")
            findings.append((finding, loaded, _canonical({"error": "unreadable"})))
            continue
        data = loaded.data
        if not isinstance(data, dict):
            finding = Finding(
                source_type=MIG_UNKNOWN, source_kind=None, original_id=None,
                owner={"kind": "unknown", "id": None}, target=None,
                auth_evidence="none", cursor={}, fatal=True,
            )
            _reason(finding, "corrupt_record", "record is not a JSON object")
            findings.append((finding, loaded, _canonical(data)))
            continue
        kind = data.get("kind")
        if kind == "background-process":
            finding = _classify_background(loaded, data)
        elif kind in {"session-message", "session-broadcast"}:
            finding = _classify_message(loaded, data)
        elif kind == "scheduled-task":
            is_action = isinstance(data.get("config"), dict) or (
                isinstance(data.get("action"), dict)
                and data["action"].get("api") == "action_reference")
            finding = (_classify_action_job(loaded, data, ctx) if is_action
                       else _classify_scheduled(loaded, data, ctx, legacy=False))
        elif kind == "main-lifecycle":
            finding = Finding(
                source_type=MIG_MAIN_LIFECYCLE, source_kind=kind,
                original_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
                owner={"kind": "unknown", "id": None}, target=_target_from_job(data),
                auth_evidence="none", cursor=_job_cursor(data),
            )
            _reason(finding, "unsupported_complex_instance",
                    "main-lifecycle instances are outside the v1 whitelist and are preserved")
        else:
            finding = Finding(
                source_type=MIG_UNKNOWN, source_kind=kind if isinstance(kind, str) else None,
                original_id=data.get("jobId") if isinstance(data.get("jobId"), str) else None,
                owner={"kind": "unknown", "id": None}, target=None,
                auth_evidence="none", cursor={}, fatal=True,
            )
            _reason(finding, "unknown_schema",
                    f"unknown or missing job kind: {kind!r}")
        findings.append((finding, loaded, _canonical(data)))

    for path in _iter_legacy_task_paths(root_path, errors):
        loaded = _load_json_file(root_path, path, errors)
        if loaded.data is None:
            finding = Finding(
                source_type=MIG_UNKNOWN, source_kind="legacy-scheduled-task",
                original_id=None, owner={"kind": "unknown", "id": None},
                target=None, auth_evidence="none", cursor={}, fatal=True,
            )
            _reason(finding, loaded.error_code or "corrupt_record",
                    loaded.error_detail or "record cannot be read")
            findings.append((finding, loaded, _canonical({"error": "unreadable"})))
        elif not isinstance(loaded.data, dict):
            finding = Finding(
                source_type=MIG_UNKNOWN, source_kind="legacy-scheduled-task",
                original_id=None, owner={"kind": "unknown", "id": None},
                target=None, auth_evidence="none", cursor={}, fatal=True,
            )
            _reason(finding, "corrupt_record", "legacy task is not a JSON object")
            findings.append((finding, loaded, _canonical(loaded.data)))
        else:
            finding = _classify_scheduled(loaded, loaded.data, ctx, legacy=True)
            findings.append((finding, loaded, _canonical(loaded.data)))

    if automation_loaded is not None and automation_loaded.data is not None:
        for key, trigger in triggers_raw:
            record_digest = _sha256_text(_canonical(trigger))
            object_loaded = Loaded(
                automation_loaded.path, automation_loaded.relpath,
                automation_loaded.raw, automation_loaded.digest, trigger,
                fingerprint=_fingerprint(trigger))
            finding = _classify_trigger(object_loaded, trigger, ctx)
            if finding.original_id is None:
                finding.original_id = key if isinstance(key, str) and key else None
            findings.append((finding, object_loaded, record_digest))

    if action_index.registry is not None:
        for key, template in action_index.templates:
            record_digest = _sha256_text(_canonical(template))
            object_loaded = Loaded(
                action_index.registry.path, action_index.registry.relpath,
                action_index.registry.raw, action_index.registry.digest,
                template, fingerprint=_fingerprint(template))
            finding = _classify_template(object_loaded, template, ctx)
            if finding.original_id is None:
                finding.original_id = key if isinstance(key, str) and key else None
            findings.append((finding, object_loaded, record_digest))

    schedule_loaded, schedule_templates = _load_schedule_templates(root_path, errors)
    if schedule_loaded is not None:
        for key, template in schedule_templates:
            record_digest = _sha256_text(_canonical(template))
            object_loaded = Loaded(
                schedule_loaded.path, schedule_loaded.relpath,
                schedule_loaded.raw, schedule_loaded.digest, template,
                fingerprint=_fingerprint(template))
            finding = _classify_schedule_template(object_loaded, template)
            if finding.original_id is None:
                finding.original_id = key if isinstance(key, str) and key else None
            findings.append((finding, object_loaded, record_digest))

    instances = [_make_instance(finding, loaded, digest)
                 for finding, loaded, digest in findings]
    instances.sort(key=lambda item: (
        item["type"], item["sourceFile"], item["id"] or "", item["migrationId"]))

    simple = [item for item in instances if item["classification"] == SIMPLE]
    complex_items = [item for item in instances
                     if item["classificationDetail"] == DETAIL_COMPLEX]
    unknown_items = [item for item in instances
                     if item["classificationDetail"] == DETAIL_UNKNOWN]
    by_source: dict[str, dict[str, int]] = {}
    for item in instances:
        bucket = by_source.setdefault(item["type"], {
            "total": 0, "simple": 0, "complex": 0, "unknown": 0})
        bucket["total"] += 1
        bucket[item["classificationDetail"]] += 1

    errors.sort(key=lambda item: (item.get("path") or "", item.get("code") or "",
                                  item.get("detail") or ""))
    plan_items = []
    for item in simple:
        plan_items.append({
            "migrationId": item["migrationId"],
            "originalId": item["id"],
            "type": item["type"],
            "sourceFile": item["sourceFile"],
            "originalDigest": item["originalDigest"],
            "mappingVersion": MAPPING_VERSION,
            "targetIdAllocation": "deferred-until-apply",
            "targetIds": [],
            "fieldMap": item["fieldMap"],
            "prerequisites": [
                "re-verify source digest immediately before apply",
                "prove the old owner is fenced and no active effect remains",
                "allocate target IDs from the persistent mapping table",
            ],
        })
    preserved = [{
        "migrationId": item["migrationId"],
        "originalId": item["id"],
        "type": item["type"],
        "sourceFile": item["sourceFile"],
        "originalDigest": item["originalDigest"],
        "reasonCodes": item["reasonCodes"],
        "rejectionReasons": item["rejectionReasons"],
        "suggestedAction": item["suggestedAction"],
    } for item in instances if item["classification"] == PRESERVED]

    return {
        "tool": "job-final-migration",
        "toolSchema": TOOL_SCHEMA,
        "mappingVersion": MAPPING_VERSION,
        "schemaWhitelist": SCHEMA_WHITELIST,
        "readOnly": True,
        "migrationExecuted": False,
        "targetWrites": 0,
        "root": {
            "path": str(root_path),
            "realProfile": bool(markers),
            "realProfileMarkers": markers,
            "allowRealProfile": bool(allow_real_profile),
        },
        "summary": {
            "total": len(instances),
            "simple": len(simple),
            "complex": len(complex_items),
            "unknown": len(unknown_items),
            "scanErrors": len(errors),
            "bySourceType": by_source,
            "invariant": {
                "sourceTotal": len(instances),
                "simplePlusPreserved": len(simple) + len(preserved),
                "classificationPending": 0,
            },
        },
        "instances": instances,
        "plan": {
            "executable": False,
            "executionAttempted": False,
            "mappingVersion": MAPPING_VERSION,
            "targetIdAllocation": "deferred-until-apply",
            "items": plan_items,
            "preserved": preserved,
            "invariant": {
                "sourceTotal": len(instances),
                "planned": len(plan_items),
                "preserved": len(preserved),
                "classificationPending": 0,
            },
        },
        "scanErrors": errors,
    }
