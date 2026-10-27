"""Normative contracts for the unified Job model (design §10, schemaVersion 2).

This module is definitions only. It owns the wire vocabulary for the six
definition objects (Action, Event, Trigger, Event Creator, Job, Template) plus
the Run/Step/Attempt/Effect/Operation/Admission records, the state transition
tables, the closed error-code set and the idempotency/version rules.

It deliberately contains no store, no scheduler pass and no execution loop, and
it imports nothing from ``packages.core.background_jobs`` or the legacy Job
builders. Runtime packages (WP02+) consume these schemas and checks instead of
hand-writing their own loose validators.

The JSON Schema artifacts under ``packages/jobs/schemas/final/`` are generated
from this module:

    python -m packages.jobs.final_contracts --write
    python -m packages.jobs.final_contracts --check

Design references: ``docs/design/job-unification/JOB_FINAL_MODEL_20261025.md``
§10 (fields, operations, states, errors), §12 WP01 and §12.1 FC01-FC04.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import jsonschema
from referencing import Registry, Resource

from packages.core.system_fields import SystemFieldError, system_binding_name

# ── Contract identity and capacity limits (§10.1) ────────────────────────────

CONTRACT_VERSION = 2
SCHEMA_BASE_URI = "https://pan.local/pan/job-final/v2/"
SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas" / "final"

MAX_ID_LENGTH = 128
MAX_NAME_LENGTH = 200
MAX_REQUEST_KEY_LENGTH = 200
MAX_DEFINITION_BYTES = 1024 * 1024
MAX_JOB_STEPS = 64
MAX_COMPOSITION_OBJECTS = 256
MAX_EVENT_FILTER_FIELDS = 16
MAX_CONDITION_DEPTH = 8
MAX_CONDITION_NODES = 64
MAX_BACKOFF_STEPS = 10
MAX_OVERLAP_PARALLEL = 32
MAX_MAX_PENDING = 10000
MAX_SCHEDULER_RULES = 64

# ── Closed enums (§10.2, §10.3, §2.3) ───────────────────────────────────────

EFFECT_CLASSES = ("read", "write_local", "write_external", "high_risk")
COMPLETION_CONTRACTS = (
    "process-exit",
    "durable-queue-accepted",
    "provider-receipt",
    "monitoring",
    "supervisor-phase",
)
BINDING_SOURCES = ("literal", "input", "event", "step", "system")
CONDITION_OPS = ("eq", "ne", "gt", "gte", "lt", "lte")
OVERLAP_MODES = ("forbid", "queue", "parallel")
CREATOR_SELECTOR_MODES = ("ids", "registeredType")
CREATOR_PROVIDER_TYPES = ("time", "core", "plugin")
OWNER_TYPES = ("session", "agent", "user", "system", "plugin")
PRINCIPAL_TYPES = ("user", "agent", "system", "plugin")
MISFIRE_POLICIES = ("skip", "fire_now")
ADVANCE_MODES = ("grid", "afterPublication")
SCHEDULE_RULE_TYPES = ("once", "interval", "weekly", "cron")
COUNTER_DOMAINS = ("run-admission",)
TEMPLATE_JOB_KEY = "job"

DEFINITION_STATES = {
    "action": ("draft", "published", "retired"),
    "trigger": ("draft", "enabled", "paused", "disabled", "retired"),
    "event_creator": ("draft", "enabled", "paused", "disabled", "retired"),
    "job": ("draft", "enabled", "paused", "disabled", "retired"),
    "template": ("draft", "published", "retired"),
    "event": (),
}
RUN_STATES = (
    "queued",
    "running",
    "retry_wait",
    "paused_at_boundary",
    "cancelling",
    "settling",
    "uncertain",
    "completed",
    "failed",
    "cancelled",
)
STEP_STATES = ("pending", "running", "completed", "failed", "uncertain", "cancelled")
ATTEMPT_STATES = ("preparing", "running", "succeeded", "failed", "uncertain", "cancelled")
ADMISSION_STATES = ("admitted", "skipped", "rejected", "blocked")
ADMISSION_SKIP_REASONS = (
    "overlap",
    "paused",
    "condition",
    "capacity",
    "retired",
    "provider_unavailable",
)
EFFECT_OUTCOMES = ("succeeded", "rejected", "unknown", "partial")
OPERATION_STATES = ("accepted", "pending", "completed", "rejected", "unknown")

#: Every operation name the canonical service may accept (§10.4).
OPERATIONS = (
    "create",
    "update",
    "publish",
    "enable",
    "disable",
    "pause",
    "resume",
    "retire",
    "transfer-owner",
    "retry",
    "cancel",
    "clone-and-run",
    "instantiate-template",
)

#: Operations exposed through ``POST /{object}/{id}/operations`` per object.
#: Event has no lifecycle control API at all (§2.5).
OBJECT_OPERATIONS = {
    "action": ("publish", "enable", "disable", "retire", "transfer-owner"),
    "trigger": ("enable", "disable", "pause", "resume", "retire", "transfer-owner"),
    "event_creator": ("enable", "disable", "pause", "resume", "retire", "transfer-owner"),
    "job": ("enable", "disable", "pause", "resume", "retire", "transfer-owner"),
    "template": ("publish", "retire", "transfer-owner"),
    "event": (),
}
#: Operations exposed through ``POST /api/job-runs/{runId}/operations``.
RUN_OPERATIONS = ("retry", "cancel", "pause", "resume")
#: Operations exposed through ``POST /api/compositions``.
COMPOSITION_OPERATIONS = ("create", "update", "retire", "clone-and-run", "instantiate-template")

OBJECT_KINDS = ("action", "event", "event_creator", "job", "template", "trigger")
DEFINITION_KINDS = ("action", "trigger", "event_creator", "job", "template")
RUNTIME_KINDS = ("admission", "run", "step", "attempt", "effect", "operation")

#: Canonical HTTP paths owned by WP06 (§10.4).
OBJECT_PATHS = {
    "action": "/api/actions",
    "trigger": "/api/triggers",
    "event_creator": "/api/event-creators",
    "job": "/api/jobs",
    "template": "/api/job-templates",
}

#: The optimistic-lock version field per object (§10.1): Job uses
#: ``configRevision``; every other definition object uses ``revision``.
VERSION_FIELD = {
    "action": "revision",
    "trigger": "revision",
    "event_creator": "revision",
    "template": "revision",
    "job": "configRevision",
}

#: Server-assigned fields. Write bodies are ``{definition, expectedRevision,
#: requestKey}`` only, so none of these may appear inside a definition.
SERVER_FIELDS = frozenset(
    {
        "schemaVersion",
        "id",
        "profileId",
        "revision",
        "configRevision",
        "createdBy",
        "owner",
        "createdAt",
        "updatedAt",
        "state",
        "stateRevision",
        "ownerEpoch",
        "generation",
        "providerGeneration",
        "enabled",
        "authorization",
        "authorizationRevision",
        "runCount",
    }
)

#: Legacy names that must never be accepted by the new contract. They are also
#: rejected as unknown fields; the list only exists to produce an explicit
#: ``legacy_field`` reason instead of a generic schema error (FC04).
LEGACY_FIELDS = {
    "job": frozenset(
        {
            "kind",
            "schedule",
            "trigger",
            "config",
            "maxRuns",
            "runCount",
            "enabled",
            "paused",
            "legacyCompat",
            "legacySchedulerCompat",
        }
    ),
    "trigger": frozenset(
        {
            "kind",
            "actionId",
            "steps",
            "retry",
            "maxConcurrency",
            "schedule",
            "name_legacy",
        }
    ),
    "action": frozenset({"input_schema", "output_schema", "executionKind", "actionId", "effect"}),
    "event_creator": frozenset({"kind", "schedule", "cursor", "producedCount", "pendingPublication"}),
    "template": frozenset({"kind", "enabled", "paused", "schedule"}),
    "event": frozenset({"type", "schemaRevision", "id", "trustedSystem", "receivedAt", "factDigest"}),
}

# ── Error codes (§10.4) ─────────────────────────────────────────────────────

ERROR_HTTP_STATUS = {
    "invalid_schema": 400,
    "invalid_condition": 400,
    "unsupported_operation": 400,
    "unauthenticated": 401,
    "authorization_denied": 403,
    "not_found": 404,
    "revision_conflict": 409,
    "idempotency_conflict": 409,
    "reference_conflict": 409,
    "pending_publication": 409,
    "reconciliation_required": 409,
    "not_retryable": 409,
    "cancellation_window_closed": 409,
    "capability_unsupported": 422,
    "unsupported_schema_version": 422,
    "capacity_exceeded": 429,
    "provider_unavailable": 503,
    "storage_unavailable": 503,
}
ERROR_CODES = tuple(ERROR_HTTP_STATUS)


class ContractError(ValueError):
    """A structured, side-effect-free contract rejection."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_HTTP_STATUS:
            raise ValueError(f"Unknown contract error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.details = dict(details or {})

    @property
    def status(self) -> int:
        return ERROR_HTTP_STATUS[self.code]

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path:
            result["path"] = self.path
        if self.details:
            result["details"] = copy.deepcopy(self.details)
        return result


# ── Canonical JSON, digests, scalar helpers (§10.1) ─────────────────────────

_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
_DURATION_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*$")
_REQUEST_KEY_RE = re.compile(r"^\S(?:.*\S)?$", re.DOTALL)
_TIME_OF_DAY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")
_POLICY_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class _DuplicateKey(ValueError):
    pass


def _reject_constant(value: str) -> Any:
    raise ContractError("invalid_schema", f"Non-finite JSON number is not allowed: {value}")


def _no_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def loads(text: str) -> Any:
    """Parse JSON rejecting duplicate keys and non-finite numbers."""
    if not isinstance(text, str):
        raise ContractError("invalid_schema", "JSON input must be text")
    try:
        return json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except _DuplicateKey as exc:
        raise ContractError(
            "invalid_schema", f"Duplicate JSON key: {exc}", details={"reason": "duplicate_key"}
        ) from exc
    except json.JSONDecodeError as exc:
        raise ContractError(
            "invalid_schema", f"Invalid JSON: {exc.msg}", details={"reason": "invalid_json"}
        ) from exc


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            "invalid_schema", f"Value is not canonical JSON: {exc}", details={"reason": "invalid_json"}
        ) from exc


def digest(value: Any) -> str:
    """SHA256 hex over canonical JSON (the only digest form in this contract)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def definition_bytes(definition: Mapping[str, Any]) -> int:
    return len(canonical_json(definition).encode("utf-8"))


def check_timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.match(value):
        raise ContractError(
            "invalid_schema",
            "Timestamp must be RFC3339 with an explicit offset",
            path=path,
            details={"reason": "invalid_timestamp"},
        )
    normalized = value.replace("z", "+00:00").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(
            "invalid_schema",
            "Timestamp is not a valid calendar instant",
            path=path,
            details={"reason": "invalid_timestamp"},
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(
            "invalid_schema",
            "Timestamp must carry an offset",
            path=path,
            details={"reason": "invalid_timestamp"},
        )
    return value


def check_duration(value: Any, path: str) -> str:
    """Finite non-negative decimal seconds string. Booleans/numbers fail."""
    if not isinstance(value, str) or not _DURATION_RE.match(value):
        raise ContractError(
            "invalid_schema",
            "Duration must be a non-negative decimal seconds string",
            path=path,
            details={"reason": "invalid_duration"},
        )
    try:
        number = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - regex already restricts
        raise ContractError("invalid_schema", "Invalid duration", path=path) from exc
    if not number.is_finite():
        raise ContractError(
            "invalid_schema", "Duration must be finite", path=path, details={"reason": "invalid_duration"}
        )
    return value


def check_request_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_REQUEST_KEY_LENGTH
        or not _REQUEST_KEY_RE.match(value)
        or any(ord(char) < 32 for char in value)
    ):
        raise ContractError(
            "invalid_schema",
            f"requestKey must be 1-{MAX_REQUEST_KEY_LENGTH} non-control characters",
            path="requestKey",
            details={"reason": "invalid_request_key"},
        )
    return value


def check_schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != CONTRACT_VERSION:
        raise ContractError(
            "unsupported_schema_version",
            f"Only schemaVersion {CONTRACT_VERSION} is supported; no automatic upcast",
            path="schemaVersion",
            details={"reason": "unsupported_schema_version", "received": value},
        )
    return value


def next_revision(current: int, *, expected: int | None = None) -> int:
    """Content edits and immutable publishes increment the definition version."""
    _integer(current, "revision", minimum=1)
    if expected is not None:
        check_revision(current, expected)
    return current + 1


def _integer(value: Any, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(
            "invalid_schema",
            f"{path} must be an integer >= {minimum}",
            path=path,
            details={"reason": "invalid_integer"},
        )
    return value


# ── CAS and version rules (§10.1) ───────────────────────────────────────────


def check_revision(current: int, expected: int) -> int:
    """Optimistic lock: the caller's expectedRevision must equal current."""
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ContractError(
            "invalid_schema",
            "expectedRevision must be an integer >= 0 (0 only on create)",
            path="expectedRevision",
            details={"reason": "invalid_integer"},
        )
    if expected != current:
        raise ContractError(
            "revision_conflict",
            "Revision changed; reload before confirming",
            path="expectedRevision",
            details={"reason": "revision_conflict", "currentRevision": current},
        )
    return current


# ── State machines (§10.5, §2.5, §2.3) ─────────────────────────────────────

DEFINITION_TRANSITIONS = {
    "action": {
        ("draft", "published"),
        ("published", "retired"),
    },
    "template": {
        ("draft", "published"),
        ("draft", "retired"),
        ("published", "retired"),
    },
    "trigger": {
        ("draft", "enabled"),
        ("draft", "retired"),
        ("enabled", "paused"),
        ("paused", "enabled"),
        ("enabled", "disabled"),
        ("paused", "disabled"),
        ("disabled", "enabled"),
        ("enabled", "retired"),
        ("paused", "retired"),
        ("disabled", "retired"),
    },
    "event_creator": {
        ("draft", "enabled"),
        ("draft", "retired"),
        ("enabled", "paused"),
        ("paused", "enabled"),
        ("enabled", "disabled"),
        ("paused", "disabled"),
        ("disabled", "enabled"),
        ("enabled", "retired"),
        ("paused", "retired"),
        ("disabled", "retired"),
    },
    "job": {
        ("draft", "enabled"),
        ("draft", "retired"),
        ("enabled", "paused"),
        ("paused", "enabled"),
        ("enabled", "disabled"),
        ("paused", "disabled"),
        ("disabled", "enabled"),
        ("enabled", "retired"),
        ("paused", "retired"),
        ("disabled", "retired"),
    },
    "event": set(),
}

#: ``operation -> (allowed from states, resulting state or None, version delta,
#: stateRevision delta)``. ``None`` means the lifecycle state is unchanged
#: (Action enable/disable is a live gate on a published definition).
DEFINITION_OPERATION_RULES = {
    "action": {
        "publish": (("draft",), "published", 1, 0),
        "enable": (("published",), None, 0, 1),
        "disable": (("published",), None, 0, 1),
        "retire": (("published",), "retired", 0, 1),
        "transfer-owner": (("draft", "published", "retired"), None, 0, 1),
    },
    "template": {
        "publish": (("draft",), "published", 1, 0),
        "retire": (("draft", "published"), "retired", 0, 1),
        "transfer-owner": (("draft", "published", "retired"), None, 0, 1),
    },
    "trigger": {
        "enable": (("draft", "disabled"), "enabled", 0, 1),
        "disable": (("enabled", "paused"), "disabled", 0, 1),
        "pause": (("enabled",), "paused", 0, 1),
        "resume": (("paused",), "enabled", 0, 1),
        "retire": (("draft", "enabled", "paused", "disabled"), "retired", 0, 1),
        "transfer-owner": (("draft", "enabled", "paused", "disabled", "retired"), None, 0, 1),
    },
    "job": {},
    "event_creator": {},
    "event": {},
}
DEFINITION_OPERATION_RULES["job"] = copy.deepcopy(DEFINITION_OPERATION_RULES["trigger"])
DEFINITION_OPERATION_RULES["event_creator"] = copy.deepcopy(DEFINITION_OPERATION_RULES["trigger"])

RUN_TRANSITIONS = {
    ("queued", "running"),
    ("queued", "cancelling"),
    ("queued", "failed"),
    ("running", "retry_wait"),
    ("running", "paused_at_boundary"),
    ("running", "cancelling"),
    ("running", "settling"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "uncertain"),
    ("retry_wait", "queued"),
    ("retry_wait", "running"),
    ("retry_wait", "cancelling"),
    ("paused_at_boundary", "running"),
    ("paused_at_boundary", "cancelling"),
    ("cancelling", "cancelled"),
    ("cancelling", "uncertain"),
    ("cancelling", "settling"),
    ("settling", "completed"),
    ("settling", "failed"),
    ("settling", "cancelled"),
    ("settling", "uncertain"),
    ("failed", "retry_wait"),
    ("failed", "queued"),
    ("uncertain", "retry_wait"),
    ("uncertain", "completed"),
    ("uncertain", "failed"),
    ("uncertain", "cancelled"),
}
STEP_TRANSITIONS = {
    ("pending", "running"),
    ("pending", "cancelled"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "uncertain"),
    ("running", "cancelled"),
    ("failed", "running"),
    ("uncertain", "running"),
    ("uncertain", "completed"),
    ("uncertain", "failed"),
    ("uncertain", "cancelled"),
}
ATTEMPT_TRANSITIONS = {
    ("preparing", "running"),
    ("preparing", "failed"),
    ("preparing", "cancelled"),
    ("running", "succeeded"),
    ("running", "failed"),
    ("running", "uncertain"),
    ("running", "cancelled"),
}
EFFECT_TRANSITIONS = {
    ("unknown", "succeeded"),
    ("unknown", "rejected"),
    ("unknown", "partial"),
}
OPERATION_TRANSITIONS = {
    ("accepted", "pending"),
    ("accepted", "completed"),
    ("accepted", "rejected"),
    ("accepted", "unknown"),
    ("pending", "completed"),
    ("pending", "rejected"),
    ("pending", "unknown"),
    ("unknown", "completed"),
    ("unknown", "rejected"),
}
#: Admission is a decision record, not a long-lived state machine.
ADMISSION_TRANSITIONS: set[tuple[str, str]] = set()

#: Transitions that additionally require a specific operation to be recorded.
GUARDED_TRANSITIONS = {
    "run": {
        ("failed", "retry_wait"): "retry",
        ("failed", "queued"): "retry",
        ("uncertain", "retry_wait"): "reconcile",
        ("uncertain", "completed"): "reconcile",
        ("uncertain", "failed"): "reconcile",
        ("uncertain", "cancelled"): "reconcile",
    },
    "step": {
        ("failed", "running"): "retry",
        ("uncertain", "running"): "reconcile",
        ("uncertain", "completed"): "reconcile",
        ("uncertain", "failed"): "reconcile",
        ("uncertain", "cancelled"): "reconcile",
    },
    "attempt": {},
    "effect": {
        ("unknown", "succeeded"): "reconcile",
        ("unknown", "rejected"): "reconcile",
        ("unknown", "partial"): "reconcile",
    },
    "operation": {},
    "admission": {},
}

LAYER_STATES = {
    "run": RUN_STATES,
    "step": STEP_STATES,
    "attempt": ATTEMPT_STATES,
    "effect": EFFECT_OUTCOMES,
    "operation": OPERATION_STATES,
    "admission": ADMISSION_STATES,
}
LAYER_TRANSITIONS = {
    "run": RUN_TRANSITIONS,
    "step": STEP_TRANSITIONS,
    "attempt": ATTEMPT_TRANSITIONS,
    "effect": EFFECT_TRANSITIONS,
    "operation": OPERATION_TRANSITIONS,
    "admission": ADMISSION_TRANSITIONS,
}


def check_definition_operation(kind: str, state: str, operation: str) -> dict[str, Any]:
    """Return the planned definition transition or raise ``unsupported_operation``."""
    rules = DEFINITION_OPERATION_RULES.get(kind)
    if rules is None or operation not in rules:
        raise ContractError(
            "unsupported_operation",
            f"Object {kind} does not support operation {operation}",
            path="operation",
            details={"reason": "unsupported_operation", "objectKind": kind, "operation": operation},
        )
    allowed_from, target, version_delta, state_delta = rules[operation]
    if state not in allowed_from:
        raise ContractError(
            "unsupported_operation",
            f"Operation {operation} is not available from state {state}",
            path="operation",
            details={
                "reason": "invalid_state_transition",
                "objectKind": kind,
                "operation": operation,
                "fromState": state,
                "allowedFrom": list(allowed_from),
            },
        )
    if target is not None and (state, target) not in DEFINITION_TRANSITIONS[kind]:
        raise ContractError(
            "unsupported_operation",
            f"Transition {state} -> {target} is not declared for {kind}",
            path="operation",
            details={"reason": "invalid_state_transition", "fromState": state, "toState": target},
        )
    return {
        "state": state if target is None else target,
        "versionDelta": version_delta,
        "stateRevisionDelta": state_delta,
    }


def check_transition(layer: str, from_state: str, to_state: str, *, operation: str | None = None) -> str:
    """Validate one runtime-layer transition, returning the recorded trigger."""
    states = LAYER_STATES.get(layer)
    if states is None:
        raise ValueError(f"Unknown runtime layer: {layer}")
    if from_state not in states or to_state not in states:
        raise ContractError(
            "invalid_schema",
            f"Unknown {layer} state",
            details={"reason": "unknown_state", "layer": layer, "fromState": from_state, "toState": to_state},
        )
    if (from_state, to_state) not in LAYER_TRANSITIONS[layer]:
        raise ContractError(
            "unsupported_operation",
            f"{layer} transition {from_state} -> {to_state} is not allowed",
            details={
                "reason": "invalid_state_transition",
                "layer": layer,
                "fromState": from_state,
                "toState": to_state,
            },
        )
    required = GUARDED_TRANSITIONS.get(layer, {}).get((from_state, to_state))
    if required and operation != required:
        raise ContractError(
            "unsupported_operation",
            f"{layer} transition {from_state} -> {to_state} requires operation {required}",
            details={
                "reason": "operation_required",
                "layer": layer,
                "requiredOperation": required,
                "fromState": from_state,
                "toState": to_state,
            },
        )
    return required or "advance"


def check_operation(kind: str, operation: str) -> str:
    """Check the operation is exposed for the object; returns the surface name."""
    if kind in OBJECT_OPERATIONS:
        if operation not in OBJECT_OPERATIONS[kind]:
            raise ContractError(
                "unsupported_operation",
                f"Object {kind} does not support operation {operation}",
                path="operation",
                details={"reason": "unsupported_operation", "objectKind": kind, "operation": operation},
            )
        return "definition"
    if kind == "run" and operation in RUN_OPERATIONS:
        return "run"
    if kind == "composition" and operation in COMPOSITION_OPERATIONS:
        return "composition"
    raise ContractError(
        "unsupported_operation",
        f"Unknown operation surface or unsupported operation: {kind}/{operation}",
        path="operation",
        details={"reason": "unsupported_operation", "objectKind": kind, "operation": operation},
    )


# ── Idempotency (§10.1) ────────────────────────────────────────────────────


def _check_principal(value: Any, path: str) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"type", "id"}
        or value.get("type") not in PRINCIPAL_TYPES
        or not isinstance(value.get("id"), str)
        or not value["id"]
    ):
        raise ContractError(
            "invalid_schema",
            "Principal must be {type:user|agent|system|plugin, id}",
            path=path,
            details={"reason": "invalid_principal"},
        )
    return value


def idempotency_scope(principal: Mapping[str, Any], profile_id: str, operation: str) -> str:
    """Caller-scoped key: same key under another principal/profile is unrelated."""
    _check_principal(principal, "principal")
    if not isinstance(profile_id, str) or not profile_id:
        raise ContractError("invalid_schema", "profileId is required", path="profileId")
    if operation not in OPERATIONS:
        raise ContractError(
            "unsupported_operation",
            f"Unknown operation: {operation}",
            details={"reason": "unsupported_operation", "operation": operation},
        )
    return digest(
        {"principal": {"type": principal["type"], "id": principal["id"]}, "profileId": profile_id, "operation": operation}
    )


def request_digest(operation: str, payload: Any) -> str:
    return digest({"operation": operation, "payload": payload})


def idempotency_outcome(
    record: Mapping[str, Any] | None, *, scope: str, request_digest: str
) -> str:
    """``new`` | ``replay`` | ``conflict`` for one (scope, requestKey) record.

    Records are supplied by the caller's store: ``{scope, requestKey,
    requestDigest, operationId, state}``. Same key and content replays the
    original operation; same key with different content conflicts.
    """
    if record is None:
        return "new"
    if record.get("scope") != scope:
        return "new"
    if record.get("requestDigest") == request_digest:
        return "replay"
    raise ContractError(
        "idempotency_conflict",
        "requestKey was already used with a different request",
        path="requestKey",
        details={
            "reason": "idempotency_conflict",
            "operationId": record.get("operationId"),
            "requestKey": record.get("requestKey"),
        },
    )


def idempotency_purgeable(record: Mapping[str, Any]) -> bool:
    """Unsettled operations must never be TTL-purged and re-executed."""
    return record.get("state") in {"completed", "rejected"}


# ── Reference resolver interface (consumed by WP02+) ───────────────────────


class ReferenceResolver(Protocol):
    """Resolves a stored object for reference checks.

    Implementations return ``None`` for unknown/unauthorized objects and
    otherwise a mapping with ``id``, ``revision``, ``digest``, ``state`` and
    optional ``enabled`` / ``definition`` keys. Authorization filtering happens
    before resolution; hidden objects must look ``not_found``.
    """

    def __call__(self, kind: str, object_id: str) -> Mapping[str, Any] | None: ...


# ── JSON Schema generation ─────────────────────────────────────────────────

_ID = {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH, "pattern": r"^\S(?:.*\S)?$"}
_NAME = {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH}
_DESCRIPTION = {"type": "string"}
_DIGEST = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
_TIMESTAMP = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$",
}
_DURATION = {"type": "string", "pattern": r"^(?:0|[1-9]\d*)(?:\.\d+)?$"}
_PATH = {"type": "string", "minLength": 1, "maxLength": 512, "pattern": _PATH_RE.pattern}
_REQUEST_KEY = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_REQUEST_KEY_LENGTH,
    "pattern": _REQUEST_KEY_RE.pattern,
}
_CONTRACT_ID = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z][A-Za-z0-9._:-]*$"}
_JSON_SCHEMA = {"type": "object"}
_SCALAR = {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}
_POLICY_TOKEN = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "pattern": _POLICY_TOKEN_RE.pattern,
}
_TIME_OF_DAY = {"type": "string", "pattern": _TIME_OF_DAY_RE.pattern}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"common.schema.json#/$defs/{name}"}


def _obj(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
    }
    if required:
        schema["required"] = list(required)
    return schema


def _arr(
    items: Mapping[str, Any],
    *,
    min_items: int | None = None,
    max_items: int | None = None,
    unique: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": dict(items)}
    if min_items is not None:
        schema["minItems"] = min_items
    if max_items is not None:
        schema["maxItems"] = max_items
    if unique:
        schema["uniqueItems"] = True
    return schema


def _string_array(*, min_items: int = 0, max_items: int | None = None) -> dict[str, Any]:
    return _arr(
        {"type": "string", "minLength": 1, "maxLength": 200},
        min_items=min_items,
        max_items=max_items,
        unique=True,
    )


def _common_defs() -> dict[str, Any]:
    binding = {
        "oneOf": [
            _obj({"from": {"const": "literal"}, "value": {}}, ("from", "value")),
            _obj({"from": {"const": "input"}, "path": _PATH}, ("from", "path")),
            _obj({"from": {"const": "event"}, "path": _PATH}, ("from", "path")),
            _obj({"from": {"const": "step"}, "stepId": _ID, "path": _PATH}, ("from", "stepId", "path")),
            _obj({"from": {"const": "system"}, "path": _PATH}, ("from", "path")),
        ]
    }
    condition = {
        "oneOf": [
            _obj(
                {"field": {"type": "string", "minLength": 1, "maxLength": 512}, "op": {"enum": list(CONDITION_OPS)}, "value": _SCALAR},
                ("field", "op", "value"),
            ),
            _obj({"and": _arr({"$ref": "#/$defs/Condition"}, min_items=1)}, ("and",)),
            _obj({"or": _arr({"$ref": "#/$defs/Condition"}, min_items=1)}, ("or",)),
            _obj({"not": {"$ref": "#/$defs/Condition"}}, ("not",)),
        ]
    }
    contract = _obj(
        {"id": _CONTRACT_ID, "schema": _JSON_SCHEMA, "terminalProofSchema": _JSON_SCHEMA},
        ("id", "schema", "terminalProofSchema"),
    )
    return {
        "Id": dict(_ID),
        "Name": dict(_NAME),
        "Description": dict(_DESCRIPTION),
        "Digest": dict(_DIGEST),
        "Timestamp": dict(_TIMESTAMP),
        "Duration": dict(_DURATION),
        "Path": dict(_PATH),
        "RequestKey": dict(_REQUEST_KEY),
        "PolicyToken": dict(_POLICY_TOKEN),
        "JsonSchema": dict(_JSON_SCHEMA),
        "Scalar": dict(_SCALAR),
        "Ref": _obj(
            {"id": _ID, "revision": {"type": "integer", "minimum": 1}, "digest": _DIGEST},
            ("id", "revision", "digest"),
        ),
        "Principal": _obj({"type": {"enum": list(PRINCIPAL_TYPES)}, "id": _ID}, ("type", "id")),
        "Owner": _obj({"type": {"enum": list(OWNER_TYPES)}, "id": _ID}, ("type", "id")),
        "Binding": binding,
        "Condition": condition,
        "Contract": contract,
    }


def _action_definition() -> dict[str, Any]:
    return {
        "title": "Action definition",
        "description": "Product/provider Action. Content publishing forms an immutable revision.",
        **_obj(
            {
                "name": _NAME,
                "description": _DESCRIPTION,
                "inputSchema": _JSON_SCHEMA,
                "outputSchema": _JSON_SCHEMA,
                "providerRef": _obj(
                    {"id": _ID, "releaseDigest": _DIGEST, "generation": {"type": "integer", "minimum": 1}},
                    ("id", "releaseDigest", "generation"),
                ),
                "execution": _obj(
                    {
                        "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                        "config": {"type": "object"},
                    },
                    ("operation", "config"),
                ),
                "effectClass": {"enum": list(EFFECT_CLASSES)},
                "requiredCapabilities": _string_array(),
                "completionContracts": _arr(_ref("Contract"), min_items=1),
                "defaultCompletion": _CONTRACT_ID,
                "cancel": _obj({"supported": {"type": "boolean"}, "proofSchema": _JSON_SCHEMA}, ("supported",)),
                "reconcile": _obj(
                    {"supported": {"type": "boolean"}, "querySchema": _JSON_SCHEMA}, ("supported",)
                ),
                "retry": _obj(
                    {
                        "idempotent": {"type": "boolean"},
                        "sameKeySupported": {"type": "boolean"},
                        "requiresProof": {"type": "boolean"},
                    },
                    ("idempotent", "sameKeySupported", "requiresProof"),
                ),
                "system": {"type": "boolean"},
            },
            (
                "name",
                "inputSchema",
                "outputSchema",
                "providerRef",
                "execution",
                "effectClass",
                "requiredCapabilities",
                "completionContracts",
                "defaultCompletion",
                "cancel",
                "reconcile",
                "retry",
                "system",
            ),
        ),
    }


def _trigger_definition() -> dict[str, Any]:
    selector = {
        "oneOf": [
            _obj({"mode": {"const": "ids"}, "ids": _arr(_ID, min_items=1, unique=True)}, ("mode", "ids")),
            _obj(
                {"mode": {"const": "registeredType"}, "providerType": {"type": "string", "minLength": 1, "maxLength": 200}},
                ("mode", "providerType"),
            ),
        ]
    }
    return {
        "title": "Trigger definition",
        "description": "Pure matching and admission. It never executes an Action.",
        **_obj(
            {
                "name": _NAME,
                "description": _DESCRIPTION,
                "eventTypes": _string_array(min_items=1),
                "creatorSelector": selector,
                "eventFilter": {
                    "type": "object",
                    "maxProperties": MAX_EVENT_FILTER_FIELDS,
                    "additionalProperties": _SCALAR,
                },
                "condition": {"oneOf": [{"type": "null"}, _ref("Condition")]},
                "arguments": {"type": "object", "additionalProperties": _ref("Binding")},
                "targetJobId": _ID,
                "managedByComposition": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            ("name", "eventTypes", "creatorSelector", "targetJobId"),
        ),
    }


def _job_step_defs() -> dict[str, Any]:
    return {
        "Step": _obj(
            {
                "stepId": _ID,
                "actionRef": _ref("Ref"),
                "arguments": {"type": "object", "additionalProperties": _ref("Binding")},
                "completion": _obj(
                    {"contract": _CONTRACT_ID, "parameters": {"type": "object"}}, ("contract",)
                ),
            },
            ("stepId", "actionRef", "arguments", "completion"),
        ),
        "TriggerLink": _obj({"triggerId": _ID, "creatorId": _ID}, ("triggerId",)),
        "Overlap": _obj(
            {
                "mode": {"enum": list(OVERLAP_MODES)},
                "maxActive": {"type": "integer", "minimum": 1, "maximum": MAX_OVERLAP_PARALLEL},
                "maxPending": {"type": "integer", "minimum": 0, "maximum": MAX_MAX_PENDING},
            },
            ("mode", "maxActive", "maxPending"),
        ),
        "Recovery": _obj(
            {
                "automatic": {"type": "boolean", "default": False},
                "maxAttempts": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "backoffSeconds": _arr(_DURATION, min_items=1, max_items=MAX_BACKOFF_STEPS, unique=False),
                "stableResetSeconds": _DURATION,
            }
        ),
        "Recipient": _obj(
            {"recipientId": _ID, "channelId": _ID, "targetRef": _ID},
            ("recipientId", "channelId", "targetRef"),
        ),
        "Notification": _obj(
            {
                "on": _arr({"enum": list(RUN_STATES)}, unique=True),
                "recipients": _arr({"$ref": "#/$defs/Recipient"}),
            }
        ),
        "Retention": _obj({"policyRef": _ID, "pinned": {"type": "boolean", "default": False}}, ("policyRef",)),
        "RunLimit": _obj(
            {"maxRuns": {"type": "integer", "minimum": 1}, "counterDomain": {"enum": list(COUNTER_DOMAINS)}},
            ("maxRuns", "counterDomain"),
        ),
    }


def _job_definition() -> dict[str, Any]:
    return {
        "title": "Job definition",
        "description": "Durable execution definition. A Run freezes the whole plan.",
        "$defs": _job_step_defs(),
        **_obj(
            {
                "name": _NAME,
                "description": _DESCRIPTION,
                "steps": _arr({"$ref": "#/$defs/Step"}, min_items=1, max_items=MAX_JOB_STEPS),
                "inputSchema": _ref("JsonSchema"),
                "arguments": {"type": "object"},
                "triggerLinks": _arr({"$ref": "#/$defs/TriggerLink"}),
                "overlap": {"$ref": "#/$defs/Overlap"},
                "recovery": {"$ref": "#/$defs/Recovery"},
                "notification": {"$ref": "#/$defs/Notification"},
                "retention": {"$ref": "#/$defs/Retention"},
                "runLimit": {"$ref": "#/$defs/RunLimit"},
                "templateProvenance": _ref("Ref"),
            },
            ("name", "steps", "retention"),
        ),
    }


def _event_creator_definition() -> dict[str, Any]:
    return {
        "title": "Event Creator definition",
        "description": "Source instance (Scheduler/system/plugin). Cursor and health are server runtime.",
        **_obj(
            {
                "name": _NAME,
                "description": _DESCRIPTION,
                "providerRef": _obj(
                    {
                        "type": {"enum": list(CREATOR_PROVIDER_TYPES)},
                        "id": _ID,
                        "releaseDigest": _DIGEST,
                        "generation": {"type": "integer", "minimum": 1},
                    },
                    ("type", "id", "releaseDigest", "generation"),
                ),
                "configSchemaRef": _ref("Ref"),
                "config": {"type": "object"},
                "eventTypes": _string_array(min_items=1),
                "requiredCapabilities": _string_array(),
            },
            ("name", "providerRef", "configSchemaRef", "config", "eventTypes", "requiredCapabilities"),
        ),
    }


def _template_definition() -> dict[str, Any]:
    creator_spec = {
        "oneOf": [
            _obj(
                {
                    "mode": {"const": "create"},
                    "key": _ID,
                    "definition": {"$ref": "event_creator_definition.schema.json"},
                },
                ("mode", "key", "definition"),
            ),
            _obj({"mode": {"const": "reference"}, "key": _ID, "ref": _ref("Ref")}, ("mode", "key", "ref")),
        ]
    }
    trigger_spec = _obj(
        {
            "key": _ID,
            "definition": {"$ref": "trigger_definition.schema.json"},
            "targetJobKey": _ID,
            "creatorKeys": _arr(_ID, unique=True),
        },
        ("key", "definition", "targetJobKey", "creatorKeys"),
    )
    return {
        "title": "Template definition",
        "description": "Passive complete configuration. Instantiating creates a new graph idempotently.",
        "$defs": {
            "CreatorSpec": creator_spec,
            "TriggerSpec": trigger_spec,
        },
        **_obj(
            {
                "name": _NAME,
                "description": _DESCRIPTION,
                "jobDefinition": {"$ref": "job_definition.schema.json"},
                "composition": _obj(
                    {
                        "creators": _arr({"$ref": "#/$defs/CreatorSpec"}, max_items=MAX_COMPOSITION_OBJECTS),
                        "triggers": _arr({"$ref": "#/$defs/TriggerSpec"}, max_items=MAX_COMPOSITION_OBJECTS),
                    }
                ),
                "provenance": _obj(
                    {
                        "sourceObjectId": _ID,
                        "sourceRevision": {"type": "integer", "minimum": 1},
                        "sourceDigest": _DIGEST,
                    }
                ),
            },
            ("name", "jobDefinition", "composition"),
        ),
    }


def _event_publish() -> dict[str, Any]:
    return {
        "title": "Event publish envelope",
        "description": "Immutable drive fact. Provider identity is bound by the trusted publish service.",
        **_obj(
            {
                "schemaVersion": {"const": CONTRACT_VERSION},
                "eventId": _ID,
                "eventType": {"type": "string", "minLength": 1, "maxLength": 200},
                "eventSchemaRevision": {"type": "integer", "minimum": 1},
                "creatorId": _ID,
                "creatorRevision": {"type": "integer", "minimum": 1},
                "occurrenceKey": {"type": "string", "minLength": 1, "maxLength": 200},
                "occurredAt": _TIMESTAMP,
                "emittedAt": _TIMESTAMP,
                "payload": {},
            },
            (
                "schemaVersion",
                "eventId",
                "eventType",
                "eventSchemaRevision",
                "creatorId",
                "creatorRevision",
                "occurrenceKey",
                "occurredAt",
                "payload",
            ),
        ),
    }


def _event_record() -> dict[str, Any]:
    publish = _event_publish()
    properties = dict(publish["properties"])
    properties.update(
        {
            "emittedAt": _TIMESTAMP,
            "receivedAt": _TIMESTAMP,
            "factDigest": _DIGEST,
            "profileId": _ID,
            "trustedSystem": {"type": "object"},
        }
    )
    required = list(publish["required"]) + [
        "emittedAt",
        "receivedAt",
        "factDigest",
        "profileId",
    ]
    return {
        "title": "Event store record",
        "description": "Immutable fact plus server-assigned receipt fields.",
        **_obj(properties, required),
    }


def _scheduler_config() -> dict[str, Any]:
    rule_id = {"type": "string", "minLength": 1, "maxLength": 128}
    rule = {
        "oneOf": [
            _obj(
                {"ruleId": rule_id, "type": {"const": "once"}, "at": _TIMESTAMP},
                ("ruleId", "type", "at"),
            ),
            _obj(
                {
                    "ruleId": rule_id,
                    "type": {"const": "interval"},
                    "seconds": _DURATION,
                    "anchor": _TIMESTAMP,
                    "advanceMode": {"enum": list(ADVANCE_MODES)},
                },
                ("ruleId", "type", "seconds", "anchor", "advanceMode"),
            ),
            _obj(
                {
                    "ruleId": rule_id,
                    "type": {"const": "weekly"},
                    "weekday": {"type": "integer", "minimum": 0, "maximum": 6},
                    "time": _TIME_OF_DAY,
                    "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
                    "dstPolicy": _POLICY_TOKEN,
                },
                ("ruleId", "type", "weekday", "time", "timezone", "dstPolicy"),
            ),
            _obj(
                {
                    "ruleId": rule_id,
                    "type": {"const": "cron"},
                    "expression": {"type": "string", "minLength": 1, "maxLength": 128},
                    "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
                    "dstPolicy": _POLICY_TOKEN,
                },
                ("ruleId", "type", "expression", "timezone", "dstPolicy"),
            ),
        ]
    }
    return {
        "title": "Scheduler provider config",
        "description": "Time provider config consumed by Event Creator instances (design §10.3).",
        **_obj(
            {
                "rules": _arr(rule, min_items=1, max_items=MAX_SCHEDULER_RULES),
                "misfirePolicy": {"enum": list(MISFIRE_POLICIES)},
                "graceSeconds": _DURATION,
                "pausePolicy": _POLICY_TOKEN,
                "driveLimit": _obj(
                    {
                        "maxEvents": {"type": "integer", "minimum": 1},
                        "initialProducedCount": {"type": "integer", "minimum": 0},
                    },
                    ("maxEvents", "initialProducedCount"),
                ),
            },
            ("rules", "misfirePolicy", "graceSeconds", "pausePolicy"),
        ),
    }


def _envelope(kind: str) -> tuple[dict[str, Any], list[str]]:
    properties: dict[str, Any] = {
        "schemaVersion": {"const": CONTRACT_VERSION},
        "id": _ref("Id"),
        "profileId": _ref("Id"),
        "createdBy": _ref("Principal"),
        "owner": _ref("Owner"),
        "createdAt": _ref("Timestamp"),
        "updatedAt": _ref("Timestamp"),
        "state": {"enum": list(DEFINITION_STATES[kind])},
        "stateRevision": {"type": "integer", "minimum": 0},
        VERSION_FIELD[kind]: {"type": "integer", "minimum": 1},
    }
    required = [
        "schemaVersion",
        "id",
        "profileId",
        "createdBy",
        "owner",
        "createdAt",
        "updatedAt",
        "state",
        "stateRevision",
        VERSION_FIELD[kind],
    ]
    if kind == "action":
        properties["enabled"] = {"type": "boolean"}
        required.append("enabled")
    if kind == "event_creator":
        properties["ownerEpoch"] = {"type": "integer", "minimum": 1}
        properties["providerGeneration"] = {"type": "integer", "minimum": 1}
        required.extend(["ownerEpoch", "providerGeneration"])
    return properties, required


def _object_document(kind: str) -> dict[str, Any]:
    definition = _definition_document(kind)
    envelope_properties, envelope_required = _envelope(kind)
    properties = dict(envelope_properties)
    properties.update(definition["properties"])
    required = list(dict.fromkeys(envelope_required + list(definition["required"])))
    document: dict[str, Any] = {
        "title": f"{kind} object",
        "description": "Server envelope plus definition. Writes send only the definition.",
        **_obj(properties, required),
    }
    if "$defs" in definition:
        # Nested definitions reference ``#/$defs/...``; keep them resolvable in
        # the merged object document.
        document["$defs"] = copy.deepcopy(definition["$defs"])
    return document


def _definition_document(kind: str) -> dict[str, Any]:
    builders = {
        "action": _action_definition,
        "trigger": _trigger_definition,
        "job": _job_definition,
        "event_creator": _event_creator_definition,
        "template": _template_definition,
    }
    return builders[kind]()


def _admission_record() -> dict[str, Any]:
    cause = {
        "oneOf": [
            _obj({"type": {"const": "manual"}}, ("type",)),
            _obj({"type": {"const": "event"}, "eventId": _ID, "triggerId": _ID}, ("type", "eventId", "triggerId")),
        ]
    }
    return {
        "title": "Admission decision",
        "description": "Persisted before queueing; each skip keeps an explicit reason.",
        **_obj(
            {
                "admissionId": _ID,
                "profileId": _ID,
                "jobId": _ID,
                "configRevision": {"type": "integer", "minimum": 1},
                "cause": cause,
                "authority": _ref("Principal"),
                "authorizationRevision": {"type": "integer", "minimum": 1},
                "state": {"enum": list(ADMISSION_STATES)},
                "reason": {"enum": list(ADMISSION_SKIP_REASONS)},
                "createdAt": _ref("Timestamp"),
                "decidedAt": _ref("Timestamp"),
            },
            (
                "admissionId",
                "profileId",
                "jobId",
                "configRevision",
                "cause",
                "authority",
                "state",
                "createdAt",
            ),
        ),
    }


def _run_record() -> dict[str, Any]:
    cause = {
        "oneOf": [
            _obj({"type": {"const": "manual"}}, ("type",)),
            _obj({"type": {"const": "event"}, "eventId": _ID, "triggerId": _ID}, ("type", "eventId", "triggerId")),
        ]
    }
    return {
        "title": "Run record",
        "description": "One logical business execution with a frozen plan digest.",
        **_obj(
            {
                "runId": _ID,
                "profileId": _ID,
                "jobId": _ID,
                "configRevision": {"type": "integer", "minimum": 1},
                "planDigest": _DIGEST,
                "admissionId": _ID,
                "cause": cause,
                "rerunOf": _ID,
                "state": {"enum": list(RUN_STATES)},
                "stateRevision": {"type": "integer", "minimum": 0},
                "transitionSeq": {"type": "integer", "minimum": 0},
                "createdAt": _ref("Timestamp"),
                "startedAt": _ref("Timestamp"),
                "finishedAt": _ref("Timestamp"),
                "error": _obj({"code": {"enum": list(ERROR_CODES)}, "message": {"type": "string"}}, ("code", "message")),
            },
            (
                "runId",
                "profileId",
                "jobId",
                "configRevision",
                "planDigest",
                "admissionId",
                "cause",
                "state",
                "stateRevision",
                "transitionSeq",
                "createdAt",
            ),
        ),
    }


def _step_record() -> dict[str, Any]:
    return {
        "title": "Step execution record",
        "description": "Identity is (runId, stepId); a step never re-runs after completion.",
        **_obj(
            {
                "runId": _ID,
                "stepId": _ID,
                "state": {"enum": list(STEP_STATES)},
                "stateRevision": {"type": "integer", "minimum": 0},
                "attemptCount": {"type": "integer", "minimum": 0},
                "startedAt": _ref("Timestamp"),
                "finishedAt": _ref("Timestamp"),
                "error": _obj({"code": {"enum": list(ERROR_CODES)}, "message": {"type": "string"}}, ("code", "message")),
            },
            ("runId", "stepId", "state", "stateRevision"),
        ),
    }


def _attempt_record() -> dict[str, Any]:
    return {
        "title": "Attempt record",
        "description": "One actual execution of the current Step under a lease/epoch.",
        **_obj(
            {
                "attemptId": _ID,
                "runId": _ID,
                "stepId": _ID,
                "state": {"enum": list(ATTEMPT_STATES)},
                "ownerEpoch": {"type": "integer", "minimum": 1},
                "generation": {"type": "integer", "minimum": 1},
                "leaseId": _ID,
                "startedAt": _ref("Timestamp"),
                "endedAt": _ref("Timestamp"),
                "backoffSeconds": _DURATION,
                "error": _obj({"code": {"enum": list(ERROR_CODES)}, "message": {"type": "string"}}, ("code", "message")),
            },
            ("attemptId", "runId", "stepId", "state", "ownerEpoch", "generation"),
        ),
    }


def _effect_record() -> dict[str, Any]:
    return {
        "title": "Effect receipt",
        "description": "Effect identity survives queries and retries for the same Run.",
        **_obj(
            {
                "effectKey": _obj(
                    {
                        "runId": _ID,
                        "stepId": _ID,
                        "targetId": _ID,
                        "operationOrdinal": {"type": "integer", "minimum": 0},
                    },
                    ("runId", "stepId", "targetId", "operationOrdinal"),
                ),
                "attemptId": _ID,
                "outcome": {"enum": list(EFFECT_OUTCOMES)},
                "receipt": {"type": "object"},
                "targetSelection": {"type": "object"},
                "cleanupProof": {"type": "object"},
                "createdAt": _ref("Timestamp"),
                "updatedAt": _ref("Timestamp"),
            },
            ("effectKey", "attemptId", "outcome", "receipt", "createdAt"),
        ),
    }


def _operation_record() -> dict[str, Any]:
    return {
        "title": "Operation receipt",
        "description": "Every write is an operation with a caller-scoped requestKey.",
        **_obj(
            {
                "operationId": _ID,
                "requestKey": _ref("RequestKey"),
                "principal": _ref("Principal"),
                "profileId": _ID,
                "operation": {"enum": list(OPERATIONS)},
                "state": {"enum": list(OPERATION_STATES)},
                "requestDigest": _DIGEST,
                "expectedRevision": {"type": "integer", "minimum": 0},
                "expectedStateRevision": {"type": "integer", "minimum": 0},
                "objectRefs": _arr(_ref("Ref")),
                "runId": _ID,
                "admissionId": _ID,
                "result": {"type": "object"},
                "error": _obj(
                    {
                        "code": {"enum": list(ERROR_CODES)},
                        "message": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    ("code", "message"),
                ),
                "createdAt": _ref("Timestamp"),
                "updatedAt": _ref("Timestamp"),
            },
            (
                "operationId",
                "requestKey",
                "principal",
                "profileId",
                "operation",
                "state",
                "requestDigest",
                "expectedRevision",
                "objectRefs",
                "createdAt",
            ),
        ),
    }


@lru_cache(maxsize=1)
def _artifacts() -> dict[str, dict[str, Any]]:
    """Canonical artifact map: file name -> JSON document (single source)."""
    documents: dict[str, dict[str, Any]] = {}

    common = {
        "title": "Shared contract definitions",
        "$defs": _common_defs(),
    }
    documents["common.schema.json"] = common

    for kind in DEFINITION_KINDS:
        documents[f"{kind}_definition.schema.json"] = _definition_document(kind)
        documents[f"{kind}.schema.json"] = _object_document(kind)

    documents["event_publish.schema.json"] = _event_publish()
    documents["event_record.schema.json"] = _event_record()
    documents["scheduler_config.schema.json"] = _scheduler_config()
    documents["admission.schema.json"] = _admission_record()
    documents["run.schema.json"] = _run_record()
    documents["step.schema.json"] = _step_record()
    documents["attempt.schema.json"] = _attempt_record()
    documents["effect.schema.json"] = _effect_record()
    documents["operation.schema.json"] = _operation_record()

    for name, document in documents.items():
        if name == "contract_manifest.json":
            continue
        document["$schema"] = SCHEMA_DRAFT
        document.setdefault("$id", SCHEMA_BASE_URI + name)
        document["$comment"] = (
            "Generated from packages/jobs/final_contracts.py; do not edit by hand. "
            "Run python -m packages.jobs.final_contracts --write."
        )

    documents["contract_manifest.json"] = _manifest()
    return documents


def schema_artifacts() -> dict[str, dict[str, Any]]:
    """Return a copy of every generated artifact (schemas plus manifest)."""
    return copy.deepcopy(_artifacts())


def _sorted_transitions(pairs: Sequence[tuple[str, str]]) -> list[list[str]]:
    return sorted([list(pair) for pair in pairs])


def _manifest() -> dict[str, Any]:
    return {
        "contractVersion": CONTRACT_VERSION,
        "generatedBy": "packages/jobs/final_contracts.py",
        "objects": {
            kind: {
                "path": OBJECT_PATHS.get(kind),
                "versionField": VERSION_FIELD.get(kind),
                "states": list(DEFINITION_STATES[kind]),
                "operations": list(OBJECT_OPERATIONS[kind]),
                "transitions": _sorted_transitions(DEFINITION_TRANSITIONS[kind]),
                "definitionSchema": f"{kind}_definition.schema.json" if kind in DEFINITION_KINDS else None,
                "objectSchema": f"{kind}.schema.json" if kind in DEFINITION_KINDS else "event_record.schema.json",
            }
            for kind in OBJECT_KINDS
        },
        "runtimeLayers": {
            layer: {
                "states": list(LAYER_STATES[layer]),
                "transitions": _sorted_transitions(LAYER_TRANSITIONS[layer]),
                "guardedTransitions": [
                    {"from": frm, "to": to, "operation": op}
                    for (frm, to), op in sorted(GUARDED_TRANSITIONS.get(layer, {}).items())
                ],
            }
            for layer in RUNTIME_KINDS
        },
        "enums": {
            "effectClasses": list(EFFECT_CLASSES),
            "completionContracts": list(COMPLETION_CONTRACTS),
            "bindingSources": list(BINDING_SOURCES),
            "conditionOps": list(CONDITION_OPS),
            "overlapModes": list(OVERLAP_MODES),
            "creatorSelectorModes": list(CREATOR_SELECTOR_MODES),
            "creatorProviderTypes": list(CREATOR_PROVIDER_TYPES),
            "misfirePolicies": list(MISFIRE_POLICIES),
            "advanceModes": list(ADVANCE_MODES),
            "scheduleRuleTypes": list(SCHEDULE_RULE_TYPES),
            "counterDomains": list(COUNTER_DOMAINS),
            "operations": list(OPERATIONS),
            "runOperations": list(RUN_OPERATIONS),
            "compositionOperations": list(COMPOSITION_OPERATIONS),
            "admissionSkipReasons": list(ADMISSION_SKIP_REASONS),
            "effectOutcomes": list(EFFECT_OUTCOMES),
        },
        "errorCodes": {
            code: {"http": status, "detailsRequired": code in {"revision_conflict", "idempotency_conflict", "reference_conflict"}}
            for code, status in ERROR_HTTP_STATUS.items()
        },
        "limits": {
            "maxIdLength": MAX_ID_LENGTH,
            "maxNameLength": MAX_NAME_LENGTH,
            "maxRequestKeyLength": MAX_REQUEST_KEY_LENGTH,
            "maxDefinitionBytes": MAX_DEFINITION_BYTES,
            "maxJobSteps": MAX_JOB_STEPS,
            "maxCompositionObjects": MAX_COMPOSITION_OBJECTS,
            "maxEventFilterFields": MAX_EVENT_FILTER_FIELDS,
            "maxConditionDepth": MAX_CONDITION_DEPTH,
            "maxConditionNodes": MAX_CONDITION_NODES,
            "maxBackoffSteps": MAX_BACKOFF_STEPS,
            "maxOverlapParallel": MAX_OVERLAP_PARALLEL,
            "maxMaxPending": MAX_MAX_PENDING,
            "maxSchedulerRules": MAX_SCHEDULER_RULES,
        },
        "legacyFieldsRejected": {kind: sorted(fields) for kind, fields in LEGACY_FIELDS.items()},
        "serverFields": sorted(SERVER_FIELDS),
        "idempotency": {
            "scope": ["principal", "profileId", "operation"],
            "sameKeySameContent": "replay original operation",
            "sameKeyDifferentContent": "idempotency_conflict",
            "purgeOnlyWhenSettled": True,
            "unsettledRetention": "must not be shorter than the recoverable window",
        },
        "templates": {"jobLocalKey": TEMPLATE_JOB_KEY},
        "authorizationProposal": _authorization_proposal(),
    }


def _authorization_proposal() -> dict[str, Any]:
    """WP01 deliverable: interface proposal only; it is not an existing grant."""
    return {
        "status": "proposal",
        "note": (
            "Reuses the existing profile-wide Agent matrix; Event Creator needs an explicit object "
            "entry and schema upcaster. Existing grants keep their values and missing new keys deny."
        ),
        "keys": {
            "action": ["read", "create", "updateConfig", "lifecycle", "delete"],
            "trigger": ["read", "create", "updateConfig", "lifecycle", "delete"],
            "eventCreator": ["read", "create", "updateConfig", "lifecycle", "delete"],
            "job": ["read", "create", "updateConfig", "lifecycle", "execute", "delete"],
            "template": ["read", "create", "updateConfig", "use", "delete"],
            "systemAction": ["manageSystemActions"],
        },
        "newObjectKey": "eventCreator",
    }


def write_schema_artifacts(directory: str | Path | None = None) -> list[Path]:
    """Write every artifact to disk; returns the written paths."""
    target = Path(directory) if directory is not None else SCHEMA_DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in sorted(_artifacts().items()):
        path = target / name
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        written.append(path)
    return written


def check_schema_artifacts(directory: str | Path | None = None) -> list[str]:
    """Return drift descriptions between the generator and the committed files."""
    target = Path(directory) if directory is not None else SCHEMA_DIRECTORY
    problems: list[str] = []
    for name, document in sorted(_artifacts().items()):
        path = target / name
        expected = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        if not path.is_file():
            problems.append(f"missing: {name}")
            continue
        if path.read_text(encoding="utf-8") != expected:
            problems.append(f"drift: {name}")
    return problems


# ── Validators ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for name, document in _artifacts().items():
        if "$id" in document and name.endswith(".schema.json"):
            resources.append((document["$id"], Resource.from_contents(document)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=1)
def _validators() -> dict[str, jsonschema.Draft202012Validator]:
    validators: dict[str, jsonschema.Draft202012Validator] = {}
    for name, document in _artifacts().items():
        if not name.endswith(".schema.json"):
            continue
        validators[name[: -len(".schema.json")]] = jsonschema.Draft202012Validator(
            document, registry=_registry(), format_checker=None
        )
    return validators


def _validation_error(name: str, error: jsonschema.ValidationError) -> ContractError:
    path = ".".join(str(part) for part in error.absolute_path)
    reason = "schema_violation"
    details: dict[str, Any] = {"reason": reason}
    if error.validator == "required":
        match = re.search(r"'(.+)' is a required property", error.message)
        field = match.group(1) if match else None
        reason = "missing_field"
        details = {"reason": reason, "field": field}
        path = f"{path}.{field}" if path and field else (field or path or "definition")
    elif error.validator == "additionalProperties":
        match = re.search(r"'(.+)' was unexpected", error.message)
        field = match.group(1) if match else None
        reason = "unknown_field"
        details = {"reason": reason, "field": field}
        path = f"{path}.{field}" if path and field else (field or path)
    elif error.validator == "enum":
        reason = "invalid_enum"
        details = {"reason": reason, "allowed": list(error.validator_value)}
    elif error.validator == "const":
        reason = "invalid_const"
        details = {"reason": reason, "expected": error.validator_value}
    elif error.validator == "type":
        reason = "invalid_type"
        details = {"reason": reason, "expected": error.validator_value}
    elif error.validator in {"minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "maxProperties", "uniqueItems"}:
        reason = "limit_exceeded" if error.validator in {"maxItems", "maxLength", "maximum", "maxProperties"} else "invalid_value"
        details = {"reason": reason, "limit": error.validator, "expected": error.validator_value}
    elif error.validator in {"oneOf", "anyOf"}:
        reason = "no_variant_matched"
        details = {"reason": reason}
    elif error.validator == "pattern":
        reason = "invalid_format"
        details = {"reason": reason}
    return ContractError("invalid_schema", f"{name}: {error.message}", path=path, details=details)


def _validate_document(name: str, instance: Any) -> None:
    _validate_name_semantics(instance)
    validator = _validators().get(name)
    if validator is None:
        raise ValueError(f"Unknown contract document: {name}")
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        raise _validation_error(name, errors[0])


def _validate_name_semantics(value: Any, *, path: str = "") -> None:
    """Reject whitespace-only canonical names without rewriting valid input."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "name" and isinstance(child, str) and not child.strip():
                raise ContractError(
                    "invalid_schema",
                    "name must contain at least one non-whitespace character",
                    path=child_path,
                    details={"reason": "blank_name"},
                )
            _validate_name_semantics(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_name_semantics(child, path=f"{path}.{index}" if path else str(index))


def _validate_condition(condition: Any, *, path: str, depth: int = 0, counter: list[int] | None = None) -> None:
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if counter[0] > MAX_CONDITION_NODES:
        raise ContractError(
            "invalid_condition",
            f"Condition exceeds {MAX_CONDITION_NODES} nodes",
            path=path,
            details={"reason": "condition_budget_exceeded", "maxNodes": MAX_CONDITION_NODES},
        )
    if depth > MAX_CONDITION_DEPTH:
        raise ContractError(
            "invalid_condition",
            f"Condition exceeds depth {MAX_CONDITION_DEPTH}",
            path=path,
            details={"reason": "condition_depth_exceeded", "maxDepth": MAX_CONDITION_DEPTH},
        )
    if not isinstance(condition, dict):
        raise ContractError("invalid_condition", "Condition must be an object", path=path)
    keys = set(condition)
    if keys == {"field", "op", "value"}:
        if not isinstance(condition["field"], str) or not condition["field"]:
            raise ContractError("invalid_condition", "Condition field must be a nonempty string", path=path)
        if condition["op"] not in CONDITION_OPS:
            raise ContractError(
                "invalid_condition",
                f"Condition op must be one of {', '.join(CONDITION_OPS)}",
                path=f"{path}.op",
                details={"reason": "invalid_enum", "allowed": list(CONDITION_OPS)},
            )
        if not isinstance(condition["value"], (str, int, float, bool, type(None))) or isinstance(
            condition["value"], complex
        ):
            raise ContractError(
                "invalid_condition", "Condition value must be a scalar", path=f"{path}.value"
            )
        return
    if keys == {"and"} or keys == {"or"}:
        children = condition[next(iter(keys))]
        if not isinstance(children, list) or not children:
            raise ContractError("invalid_condition", "and/or must be a nonempty list", path=path)
        for index, child in enumerate(children):
            _validate_condition(child, path=f"{path}.{index}", depth=depth + 1, counter=counter)
        return
    if keys == {"not"}:
        _validate_condition(condition["not"], path=f"{path}.not", depth=depth + 1, counter=counter)
        return
    raise ContractError(
        "invalid_condition",
        "Condition must be a leaf {field,op,value} or one of and/or/not",
        path=path,
        details={"reason": "invalid_condition_shape"},
    )


def _check_json_schema(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ContractError("invalid_schema", "Expected a JSON Schema object", path=path)
    try:
        jsonschema.Draft202012Validator.check_schema(value)
    except jsonschema.SchemaError as exc:
        raise ContractError(
            "invalid_schema",
            f"Invalid JSON Schema: {exc.message}",
            path=path,
            details={"reason": "invalid_json_schema"},
        ) from exc


def _validate_instance(schema: Mapping[str, Any], instance: Any, *, path: str) -> None:
    validator = jsonschema.Draft202012Validator(schema, registry=_registry())
    error = next(iter(sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))), None)
    if error is not None:
        nested = ".".join(str(part) for part in error.absolute_path)
        raise ContractError(
            "invalid_schema",
            f"Value does not satisfy its JSON Schema: {error.message}",
            path=f"{path}.{nested}" if nested else path,
            details={"reason": "schema_violation"},
        )


def _check_binding(
    binding: Any,
    *,
    path: str,
    earlier_steps: set[str],
    input_schema: Mapping[str, Any] | None,
    reject_sources: frozenset[str] = frozenset(),
) -> None:
    if not isinstance(binding, dict):
        raise ContractError("invalid_schema", "Binding must be an object", path=path)
    form = binding.get("from")
    if form in reject_sources:
        raise ContractError(
            "invalid_schema",
            f"Binding source {form} is not available in this context",
            path=path,
            details={"reason": "binding_source_unavailable", "source": form},
        )
    if form == "input":
        if input_schema is not None:
            declared = input_schema.get("properties")
            closed = input_schema.get("additionalProperties") is False
            first = str(binding.get("path", "")).split(".")[0]
            if closed and (not isinstance(declared, Mapping) or first not in declared):
                raise ContractError(
                    "invalid_schema",
                    "Input binding path is not declared by inputSchema",
                    path=f"{path}.path",
                    details={"reason": "undeclared_input_path", "field": first},
                )
    elif form == "step":
        step_id = binding.get("stepId")
        if step_id not in earlier_steps:
            raise ContractError(
                "invalid_schema",
                "Step binding must reference an earlier step",
                path=f"{path}.stepId",
                details={"reason": "unknown_or_forward_step_ref", "stepId": step_id},
            )
    elif form == "system":
        try:
            system_binding_name(binding.get("path"))
        except SystemFieldError as exc:
            raise ContractError(
                "invalid_schema",
                str(exc),
                path=f"{path}.path",
                details={"reason": "unknown_system_field"},
            ) from exc


def _check_step_bindings(
    job: Mapping[str, Any], *, path: str, reject_sources: frozenset[str] = frozenset()
) -> None:
    input_schema = job["inputSchema"]
    # Job-level ``arguments`` are default *values*, not bindings (§10.2): the
    # definition must already be complete and satisfy inputSchema.
    _validate_instance(input_schema, job["arguments"], path=f"{path}.arguments")
    seen: set[str] = set()
    for index, step in enumerate(job["steps"]):
        step_path = f"{path}.steps.{index}"
        for key, binding in step["arguments"].items():
            _check_binding(
                binding,
                path=f"{step_path}.arguments.{key}",
                earlier_steps=seen,
                input_schema=input_schema,
                reject_sources=reject_sources,
            )
        seen.add(step["stepId"])


def _check_job_definition(job: Mapping[str, Any], *, path: str = "definition", reject_sources: frozenset[str] = frozenset()) -> None:
    step_ids = [step["stepId"] for step in job["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise ContractError(
            "invalid_schema", "stepId must be unique within a Job", path=f"{path}.steps",
            details={"reason": "duplicate_step_id"},
        )
    _check_step_bindings(job, path=path, reject_sources=reject_sources)

    overlap = job["overlap"]
    mode = overlap["mode"]
    if mode == "forbid" and (overlap["maxActive"] != 1 or overlap["maxPending"] != 0):
        raise ContractError(
            "invalid_schema",
            "overlap.mode=forbid requires maxActive=1 and maxPending=0",
            path=f"{path}.overlap",
            details={"reason": "invalid_overlap"},
        )
    if mode == "queue" and (overlap["maxActive"] != 1 or overlap["maxPending"] < 1):
        raise ContractError(
            "invalid_schema",
            "overlap.mode=queue requires maxActive=1 and maxPending>=1",
            path=f"{path}.overlap",
            details={"reason": "invalid_overlap"},
        )
    if mode == "parallel" and not 1 <= overlap["maxActive"] <= MAX_OVERLAP_PARALLEL:
        raise ContractError(
            "invalid_schema", "overlap.mode=parallel requires maxActive in 1..32", path=f"{path}.overlap"
        )

    recovery = job["recovery"]
    for index, value in enumerate(recovery["backoffSeconds"]):
        check_duration(value, f"{path}.recovery.backoffSeconds.{index}")
    check_duration(recovery["stableResetSeconds"], f"{path}.recovery.stableResetSeconds")

    seen_links: set[str] = set()
    for index, link in enumerate(job["triggerLinks"]):
        trigger_id = link["triggerId"]
        if trigger_id in seen_links:
            raise ContractError(
                "invalid_schema",
                "triggerLinks must not repeat a triggerId",
                path=f"{path}.triggerLinks.{index}",
                details={"reason": "duplicate_trigger_link"},
            )
        seen_links.add(trigger_id)

    seen_recipients: set[tuple[str, str, str]] = set()
    for index, recipient in enumerate(job["notification"]["recipients"]):
        key = (recipient["recipientId"], recipient["channelId"], recipient["targetRef"])
        if key in seen_recipients:
            raise ContractError(
                "invalid_schema",
                "notification recipients must not repeat",
                path=f"{path}.notification.recipients.{index}",
                details={"reason": "duplicate_recipient"},
            )
        seen_recipients.add(key)


def _check_action_definition(action: Mapping[str, Any]) -> None:
    _check_json_schema(action["inputSchema"], "inputSchema")
    _check_json_schema(action["outputSchema"], "outputSchema")
    for index, contract in enumerate(action["completionContracts"]):
        _check_json_schema(contract["schema"], f"completionContracts.{index}.schema")
        _check_json_schema(contract["terminalProofSchema"], f"completionContracts.{index}.terminalProofSchema")
    ids = [contract["id"] for contract in action["completionContracts"]]
    if len(ids) != len(set(ids)):
        raise ContractError(
            "invalid_schema",
            "completionContracts ids must be unique",
            path="completionContracts",
            details={"reason": "duplicate_contract_id"},
        )
    if action["defaultCompletion"] not in ids:
        raise ContractError(
            "invalid_schema",
            "defaultCompletion must be one of the declared completionContracts",
            path="defaultCompletion",
            details={"reason": "unknown_completion_contract", "declared": ids},
        )
    optional_schemas = (
        ("cancel", "proofSchema"),
        ("reconcile", "querySchema"),
    )
    for section, field in optional_schemas:
        value = action[section]
        if value["supported"] is False and field in value:
            raise ContractError(
                "invalid_schema",
                f"{section}.{field} is only meaningful when {section}.supported is true",
                path=f"{section}.{field}",
                details={"reason": "contradictory_capability"},
            )
        if field in value:
            _check_json_schema(value[field], f"{section}.{field}")


def _check_trigger_definition(trigger: Mapping[str, Any], *, path: str = "") -> None:
    prefix = f"{path}." if path else ""
    if trigger["condition"] is not None:
        _validate_condition(trigger["condition"], path=f"{prefix}condition")
    for key, binding in trigger["arguments"].items():
        _check_binding(
            binding,
            path=f"{prefix}arguments.{key}",
            earlier_steps=set(),
            input_schema=None,
            reject_sources=frozenset({"step", "input"}),
        )


def _check_event_creator_definition(creator: Mapping[str, Any]) -> None:
    if creator["providerRef"]["type"] not in CREATOR_PROVIDER_TYPES:
        raise ContractError(
            "invalid_schema",
            "Unknown provider type",
            path="providerRef.type",
            details={"reason": "invalid_enum"},
        )
    # Provider config is validated by the fixed provider schema; the contract
    # layer only guarantees the ref exists and the value is an object.
    if not isinstance(creator["config"], dict):
        raise ContractError("invalid_schema", "config must be an object", path="config")


def _check_template_definition(template: Mapping[str, Any]) -> None:
    job = template["jobDefinition"]
    if job["triggerLinks"]:
        raise ContractError(
            "reference_conflict",
            "Template composition supplies trigger links; jobDefinition.triggerLinks must be empty",
            path="jobDefinition.triggerLinks",
            details={"reason": "template_links_from_composition"},
        )
    creators = template["composition"]["creators"]
    triggers = template["composition"]["triggers"]
    if len(creators) + len(triggers) > MAX_COMPOSITION_OBJECTS:
        raise ContractError(
            "capacity_exceeded",
            f"composition accepts at most {MAX_COMPOSITION_OBJECTS} objects",
            path="composition",
            details={"reason": "capacity_exceeded", "maxObjects": MAX_COMPOSITION_OBJECTS},
        )
    keys = [spec["key"] for spec in creators] + [spec["key"] for spec in triggers]
    if len(keys) != len(set(keys)):
        raise ContractError(
            "invalid_schema",
            "Composition local keys must be unique",
            path="composition",
            details={"reason": "duplicate_local_key"},
        )
    creator_keys = {spec["key"] for spec in creators}
    for spec in creators:
        if spec["mode"] == "create":
            _check_event_creator_definition(spec["definition"])
    for index, spec in enumerate(triggers):
        if spec["targetJobKey"] != TEMPLATE_JOB_KEY:
            raise ContractError(
                "reference_conflict",
                f"targetJobKey must reference the template Job key '{TEMPLATE_JOB_KEY}'",
                path=f"composition.triggers.{index}.targetJobKey",
                details={"reason": "dangling_local_key", "key": spec["targetJobKey"]},
            )
        for key_index, key in enumerate(spec["creatorKeys"]):
            if key not in creator_keys:
                raise ContractError(
                    "reference_conflict",
                    "creatorKeys must reference creators declared in this composition",
                    path=f"composition.triggers.{index}.creatorKeys.{key_index}",
                    details={"reason": "dangling_local_key", "key": key},
                )
        _check_trigger_definition(spec["definition"], path=f"composition.triggers.{index}.definition")


def normalize_definition(kind: str, definition: Any) -> dict[str, Any]:
    """Deep-copy a definition and apply only the defaults declared in §10.2."""
    if kind not in DEFINITION_KINDS:
        raise ContractError(
            "unsupported_operation",
            f"{kind} has no editable definition",
            details={"reason": "unsupported_operation", "objectKind": kind},
        )
    if not isinstance(definition, dict):
        raise ContractError("invalid_schema", "definition must be an object", path="definition")
    value = copy.deepcopy(definition)
    _validate_name_semantics(value, path="definition")
    value.setdefault("description", "")
    if kind == "trigger":
        value.setdefault("eventFilter", {})
        value.setdefault("condition", None)
        value.setdefault("arguments", {})
    elif kind == "job":
        value = _normalize_job(value)
    elif kind == "template":
        job_definition = value.get("jobDefinition")
        if isinstance(job_definition, dict):
            value["jobDefinition"] = _normalize_job(copy.deepcopy(job_definition))
        composition = value.get("composition")
        if isinstance(composition, dict):
            composition.setdefault("creators", [])
            composition.setdefault("triggers", [])
            for spec in composition.get("creators", ()):
                if isinstance(spec, dict) and spec.get("mode") == "create" and isinstance(spec.get("definition"), dict):
                    spec["definition"] = normalize_definition("event_creator", spec["definition"])
            for spec in composition.get("triggers", ()):
                if isinstance(spec, dict) and isinstance(spec.get("definition"), dict):
                    spec["definition"] = normalize_definition("trigger", spec["definition"])
    return value


def _normalize_job(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("inputSchema", {"type": "object"})
    value.setdefault("arguments", {})
    value.setdefault("triggerLinks", [])
    value.setdefault("overlap", {"mode": "forbid", "maxActive": 1, "maxPending": 0})
    value.setdefault(
        "recovery",
        {
            "automatic": False,
            "maxAttempts": 3,
            "backoffSeconds": ["5", "15", "60"],
            "stableResetSeconds": "300",
        },
    )
    value.setdefault("notification", {"on": [], "recipients": []})
    recovery = value.get("recovery")
    if isinstance(recovery, dict):
        recovery.setdefault("automatic", False)
        recovery.setdefault("maxAttempts", 3)
        recovery.setdefault("backoffSeconds", ["5", "15", "60"])
        recovery.setdefault("stableResetSeconds", "300")
    notification = value.get("notification")
    if isinstance(notification, dict):
        notification.setdefault("on", [])
        notification.setdefault("recipients", [])
    retention = value.get("retention")
    if isinstance(retention, dict):
        retention.setdefault("pinned", False)
    return value


def _reject_legacy_fields(kind: str, definition: Mapping[str, Any]) -> None:
    legacy = LEGACY_FIELDS.get(kind, frozenset())
    for field in definition:
        if field in legacy:
            raise ContractError(
                "invalid_schema",
                f"Legacy field is not accepted: {field}",
                path=field,
                details={"reason": "legacy_field", "field": field, "objectKind": kind},
            )


def _reject_server_fields(definition: Mapping[str, Any], *, path: str = "definition") -> None:
    for field in definition:
        if field in SERVER_FIELDS:
            raise ContractError(
                "invalid_schema",
                f"{field} is server-assigned and not writable",
                path=f"{path}.{field}",
                details={"reason": "read_only_field", "field": field},
            )


def _iter_step_arguments(definition: Mapping[str, Any]):
    for step in definition.get("steps", ()):
        if isinstance(step, Mapping):
            arguments = step.get("arguments")
            if isinstance(arguments, Mapping):
                yield from arguments.items()


def _iter_bindings(kind: str, definition: Mapping[str, Any]):
    if kind == "trigger":
        arguments = definition.get("arguments")
        if isinstance(arguments, Mapping):
            yield from arguments.items()
    elif kind == "job":
        yield from _iter_step_arguments(definition)
    elif kind == "template":
        job = definition.get("jobDefinition")
        if isinstance(job, Mapping):
            yield from _iter_step_arguments(job)
        composition = definition.get("composition")
        if isinstance(composition, Mapping):
            for spec in composition.get("triggers", ()):
                if isinstance(spec, Mapping) and isinstance(spec.get("definition"), Mapping):
                    arguments = spec["definition"].get("arguments")
                    if isinstance(arguments, Mapping):
                        yield from arguments.items()


def _precheck_bindings(kind: str, definition: Mapping[str, Any]) -> None:
    """Reject an unknown ``from`` discriminator before the union schema error."""
    for name, binding in _iter_bindings(kind, definition):
        if isinstance(binding, Mapping) and "from" in binding and binding["from"] not in BINDING_SOURCES:
            raise ContractError(
                "invalid_schema",
                f"Binding source must be one of {', '.join(BINDING_SOURCES)}",
                path=f"arguments.{name}.from",
                details={"reason": "invalid_enum", "allowed": list(BINDING_SOURCES)},
            )


def _precheck_condition(kind: str, definition: Mapping[str, Any]) -> None:
    """Condition shape/op/budget errors are reported as ``invalid_condition``."""
    if kind == "trigger" and definition.get("condition") is not None:
        _validate_condition(definition["condition"], path="condition")


def validate_definition(
    kind: str,
    definition: Any,
    *,
    capabilities: Sequence[str] | None = None,
    config_validator: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Validate and normalize a client-supplied definition (write model)."""
    if kind not in DEFINITION_KINDS:
        raise ContractError(
            "unsupported_operation",
            f"{kind} has no editable definition",
            details={"reason": "unsupported_operation", "objectKind": kind},
        )
    if not isinstance(definition, dict):
        raise ContractError("invalid_schema", "definition must be an object", path="definition")
    _reject_legacy_fields(kind, definition)
    _reject_server_fields(definition)
    _precheck_bindings(kind, definition)
    _precheck_condition(kind, definition)
    value = normalize_definition(kind, definition)
    _validate_document(f"{kind}_definition", value)

    size = definition_bytes(value)
    if size > MAX_DEFINITION_BYTES:
        raise ContractError(
            "capacity_exceeded",
            f"Definition exceeds {MAX_DEFINITION_BYTES} bytes",
            details={"reason": "capacity_exceeded", "bytes": size, "maxBytes": MAX_DEFINITION_BYTES},
        )
    for field in ("createdAt", "updatedAt"):
        if field in value:
            check_timestamp(value[field], field)

    if kind == "action":
        _check_action_definition(value)
    elif kind == "trigger":
        _check_trigger_definition(value)
    elif kind == "job":
        _check_job_definition(value)
    elif kind == "event_creator":
        _check_event_creator_definition(value)
        if config_validator is not None:
            config_validator(value["config"])
    elif kind == "template":
        _check_job_definition(value["jobDefinition"], path="jobDefinition")
        _check_template_definition(value)

    if capabilities is not None:
        missing = sorted(set(value.get("requiredCapabilities", ())) - set(capabilities))
        if missing:
            raise ContractError(
                "capability_unsupported",
                "Required capabilities are not available",
                path="requiredCapabilities",
                details={"reason": "capability_unsupported", "missing": missing},
            )
    return value


def validate_write(
    kind: str,
    body: Any,
    *,
    create: bool = False,
    capabilities: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate ``{definition, expectedRevision, requestKey}``.

    ``create=True`` requires ``expectedRevision == 0``; updates require >= 1.
    Returns the normalized definition plus the request digest for idempotency.
    """
    if not isinstance(body, dict):
        raise ContractError("invalid_schema", "Write body must be an object", path="body")
    unknown = set(body) - {"definition", "expectedRevision", "requestKey"}
    if unknown:
        raise ContractError(
            "invalid_schema",
            "Write body accepts only definition/expectedRevision/requestKey",
            path=sorted(unknown)[0],
            details={"reason": "unknown_field", "field": sorted(unknown)[0]},
        )
    for field in ("definition", "expectedRevision", "requestKey"):
        if field not in body:
            raise ContractError(
                "invalid_schema", f"Missing {field}", path=field, details={"reason": "missing_field", "field": field}
            )
    expected = body["expectedRevision"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ContractError(
            "invalid_schema",
            "expectedRevision must be an integer >= 0",
            path="expectedRevision",
            details={"reason": "invalid_integer"},
        )
    if create and expected != 0:
        raise ContractError(
            "invalid_schema",
            "Create requires expectedRevision=0",
            path="expectedRevision",
            details={"reason": "invalid_integer"},
        )
    if not create and expected == 0:
        raise ContractError(
            "invalid_schema",
            "Update requires expectedRevision >= 1",
            path="expectedRevision",
            details={"reason": "invalid_integer"},
        )
    request_key = check_request_key(body["requestKey"])
    definition = validate_definition(kind, body["definition"], capabilities=capabilities)
    return {
        "definition": definition,
        "expectedRevision": expected,
        "requestKey": request_key,
        "requestDigest": request_digest("update" if not create else "create", {
            "objectKind": kind,
            "definition": definition,
            "expectedRevision": expected,
        }),
    }


def validate_object(kind: str, value: Any) -> dict[str, Any]:
    """Validate a server-side object (envelope + definition) or Event record."""
    if kind == "event":
        _validate_document("event_record", value)
        check_timestamp(value["occurredAt"], "occurredAt")
        check_timestamp(value["emittedAt"], "emittedAt")
        check_timestamp(value["receivedAt"], "receivedAt")
        expected = event_fact_digest(value)
        if value["factDigest"] != expected:
            raise ContractError(
                "invalid_schema",
                "factDigest does not match the immutable fact",
                path="factDigest",
                details={"reason": "digest_mismatch", "expected": expected},
            )
        return copy.deepcopy(value)
    if kind in RUNTIME_KINDS:
        return validate_record(kind, value)
    if kind not in DEFINITION_KINDS:
        raise ContractError(
            "unsupported_operation",
            f"Unknown object kind: {kind}",
            details={"reason": "unsupported_operation", "objectKind": kind},
        )
    if not isinstance(value, dict):
        raise ContractError("invalid_schema", "Object must be an object")
    check_schema_version(value.get("schemaVersion"))
    _validate_document(kind, value)
    check_timestamp(value["createdAt"], "createdAt")
    check_timestamp(value["updatedAt"], "updatedAt")
    return copy.deepcopy(value)


# ── Event facts (§10.2) ───────────────────────────────────────────────────

#: Immutable fields that define the fact identity; ``emittedAt`` is fixed on
#: first publication and is not part of the digest input.
EVENT_FACT_FIELDS = (
    "eventId",
    "eventType",
    "eventSchemaRevision",
    "creatorId",
    "creatorRevision",
    "occurrenceKey",
    "occurredAt",
    "payload",
)


def event_fact_digest(envelope: Mapping[str, Any]) -> str:
    return digest({field: envelope.get(field) for field in EVENT_FACT_FIELDS})


def event_receipt_outcome(existing: Mapping[str, Any] | None, envelope: Mapping[str, Any]) -> str:
    """``new`` | ``replay`` | raise ``idempotency_conflict``.

    ``existing`` is the stored receipt looked up by this ``eventId``. The
    publish service stores ``factDigest`` with the immutable receipt: the same
    eventId and digest replays the original receipt, a different digest is a
    conflict and never advances the source cursor.
    """
    if existing is None:
        return "new"
    incoming_digest = event_fact_digest(envelope)
    if existing.get("eventId") == envelope.get("eventId") and existing.get("factDigest") == incoming_digest:
        return "replay"
    raise ContractError(
        "idempotency_conflict",
        "eventId was already persisted with a different fact",
        path="eventId",
        details={
            "reason": "event_fact_conflict",
            "eventId": envelope.get("eventId"),
            "storedDigest": existing.get("factDigest"),
            "incomingDigest": incoming_digest,
        },
    )


def validate_record(kind: str, value: Any) -> dict[str, Any]:
    """Validate one runtime-layer record (run/step/attempt/effect/operation/admission)."""
    if kind not in RUNTIME_KINDS:
        raise ContractError(
            "unsupported_operation",
            f"Unknown runtime record kind: {kind}",
            details={"reason": "unsupported_operation", "recordKind": kind},
        )
    if not isinstance(value, dict):
        raise ContractError("invalid_schema", f"{kind} record must be an object")
    _validate_document(kind, value)
    return copy.deepcopy(value)


def validate_event_publish(
    envelope: Any,
    *,
    resolve_creator: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Validate a provider publish envelope. Trusted identity is never client-supplied."""
    if not isinstance(envelope, dict):
        raise ContractError("invalid_schema", "Event envelope must be an object")
    for field in envelope:
        if field in LEGACY_FIELDS["event"]:
            raise ContractError(
                "invalid_schema",
                f"Legacy Event wire name is not accepted: {field}",
                path=field,
                details={"reason": "legacy_field", "field": field},
            )
    check_schema_version(envelope.get("schemaVersion"))
    _validate_document("event_publish", envelope)
    check_timestamp(envelope["occurredAt"], "occurredAt")
    if "emittedAt" in envelope:
        check_timestamp(envelope["emittedAt"], "emittedAt")
    if resolve_creator is not None:
        creator = resolve_creator(envelope["creatorId"])
        if creator is None:
            raise ContractError(
                "reference_conflict",
                "Event creator does not exist",
                path="creatorId",
                details={"reason": "not_found", "creatorId": envelope["creatorId"]},
            )
    return copy.deepcopy(envelope)


# ── Reference integrity (§10.2, FC03) ─────────────────────────────────────


def _resolve(resolver: ReferenceResolver, kind: str, object_id: str, *, path: str) -> Mapping[str, Any]:
    record = resolver(kind, object_id)
    if record is None:
        raise ContractError(
            "reference_conflict",
            f"Referenced {kind} does not exist",
            path=path,
            details={"reason": "not_found", "objectKind": kind, "id": object_id},
        )
    return record


def _check_action_ref(
    ref: Mapping[str, Any],
    *,
    path: str,
    completion: Mapping[str, Any] | None,
    completion_path: str,
    resolver: ReferenceResolver,
    require_enabled: bool,
) -> Mapping[str, Any]:
    record = _resolve(resolver, "action", ref["id"], path=f"{path}.id")
    if record.get("state") != "published":
        raise ContractError(
            "reference_conflict",
            "Only published Actions may be referenced",
            path=path,
            details={"reason": "unpublished", "id": ref["id"], "state": record.get("state")},
        )
    if record.get("revision") != ref["revision"]:
        raise ContractError(
            "reference_conflict",
            "Action revision does not match the reference",
            path=f"{path}.revision",
            details={
                "reason": "revision_mismatch",
                "id": ref["id"],
                "expected": ref["revision"],
                "currentRevision": record.get("revision"),
            },
        )
    if record.get("digest") and record["digest"] != ref["digest"]:
        raise ContractError(
            "reference_conflict",
            "Action digest does not match the reference",
            path=f"{path}.digest",
            details={"reason": "digest_mismatch", "id": ref["id"]},
        )
    if require_enabled and record.get("enabled") is False:
        raise ContractError(
            "reference_conflict",
            "Action is disabled; no new effects may be admitted",
            path=path,
            details={"reason": "disabled", "id": ref["id"]},
        )
    definition = record.get("definition") or {}
    contracts = {
        contract["id"]: contract
        for contract in definition.get("completionContracts", ())
        if isinstance(contract, Mapping) and "id" in contract
    }
    if completion is not None and contracts:
        contract_id = completion["contract"]
        if contract_id not in contracts:
            raise ContractError(
                "reference_conflict",
                "Step completion contract is not declared by the Action",
                path=f"{completion_path}.contract",
                details={"reason": "completion_contract_unsupported", "contract": contract_id},
            )
        parameters = completion.get("parameters")
        if parameters is not None:
            _validate_instance(
                contracts[contract_id].get("schema", {}),
                parameters,
                path=f"{completion_path}.parameters",
            )
    return record


def _check_job_references(
    job: Mapping[str, Any],
    *,
    resolver: ReferenceResolver,
    path: str,
    require_enabled: bool,
    job_id: str | None = None,
) -> list[Mapping[str, Any]]:
    checked: list[Mapping[str, Any]] = []
    owner_job_id = job_id or job.get("id")
    for index, step in enumerate(job["steps"]):
        checked.append(
            _check_action_ref(
                step["actionRef"],
                path=f"{path}.steps.{index}.actionRef",
                completion=step["completion"],
                completion_path=f"{path}.steps.{index}.completion",
                resolver=resolver,
                require_enabled=require_enabled,
            )
        )
    for index, link in enumerate(job["triggerLinks"]):
        record = _resolve(resolver, "trigger", link["triggerId"], path=f"{path}.triggerLinks.{index}.triggerId")
        if record.get("state") == "retired":
            raise ContractError(
                "reference_conflict",
                "Retired Trigger cannot be linked",
                path=f"{path}.triggerLinks.{index}.triggerId",
                details={"reason": "retired", "id": link["triggerId"]},
            )
        target = (record.get("definition") or {}).get("targetJobId") or record.get("targetJobId")
        if target is not None and owner_job_id is not None and target != owner_job_id:
            raise ContractError(
                "reference_conflict",
                "Trigger targets a different Job",
                path=f"{path}.triggerLinks.{index}.triggerId",
                details={"reason": "target_mismatch", "id": link["triggerId"], "targetJobId": target},
            )
        checked.append(record)
    return checked


def validate_references(
    kind: str,
    definition: Mapping[str, Any],
    resolver: ReferenceResolver,
    *,
    object_id: str | None = None,
    require_enabled: bool = False,
) -> list[Mapping[str, Any]]:
    """Check cross-object references against a resolver.

    ``object_id`` supplies the persisted identity when the definition itself has
    none yet (Job trigger links must target the Job being written). Returns the
    resolved records, in check order, for WP02 to persist as frozen provenance.
    Raises ``reference_conflict`` for missing, unpublished, retired, mismatched
    or (when ``require_enabled``) disabled references.
    """
    checked: list[Mapping[str, Any]] = []
    if kind not in DEFINITION_KINDS:
        raise ContractError(
            "unsupported_operation",
            f"{kind} has no reference graph to validate",
            details={"reason": "unsupported_operation", "objectKind": kind},
        )
    definition = normalize_definition(kind, definition)
    if kind == "job":
        checked.extend(
            _check_job_references(
                definition,
                resolver=resolver,
                path="definition",
                require_enabled=require_enabled,
                job_id=object_id,
            )
        )
    elif kind == "trigger":
        job = _resolve(resolver, "job", definition["targetJobId"], path="targetJobId")
        if job.get("state") in {"draft", "retired"}:
            raise ContractError(
                "reference_conflict",
                "Trigger target Job must be enabled, paused or disabled",
                path="targetJobId",
                details={"reason": "target_unavailable", "id": definition["targetJobId"], "state": job.get("state")},
            )
        checked.append(job)
    elif kind == "template":
        checked.extend(
            _check_job_references(
                definition["jobDefinition"], resolver=resolver, path="jobDefinition", require_enabled=require_enabled
            )
        )
        for index, spec in enumerate(definition["composition"]["creators"]):
            if spec["mode"] != "reference":
                continue
            ref = spec["ref"]
            record = _resolve(resolver, "event_creator", ref["id"], path=f"composition.creators.{index}.ref.id")
            if record.get("state") == "retired":
                raise ContractError(
                    "reference_conflict",
                    "Retired Event Creator cannot be referenced",
                    path=f"composition.creators.{index}.ref",
                    details={"reason": "retired", "id": ref["id"]},
                )
            if record.get("revision") != ref["revision"]:
                raise ContractError(
                    "reference_conflict",
                    "Event Creator revision does not match the reference",
                    path=f"composition.creators.{index}.ref.revision",
                    details={"reason": "revision_mismatch", "id": ref["id"]},
                )
            checked.append(record)
    elif kind in {"action", "event_creator"}:
        return checked
    else:
        raise ContractError(
            "unsupported_operation",
            f"{kind} has no reference graph to validate",
            details={"reason": "unsupported_operation", "objectKind": kind},
        )
    return checked


def validate_scheduler_config(config: Any) -> dict[str, Any]:
    """Validate a time-provider config (design §10.3) without storing it."""
    _validate_document("scheduler_config", config)
    rule_ids = [rule["ruleId"] for rule in config["rules"]]
    if len(rule_ids) != len(set(rule_ids)):
        raise ContractError(
            "invalid_schema", "ruleId must be unique", path="rules", details={"reason": "duplicate_rule_id"}
        )
    for index, rule in enumerate(config["rules"]):
        if rule["type"] == "interval":
            if Decimal(rule["seconds"]) <= 0:
                raise ContractError(
                    "invalid_schema",
                    "interval seconds must be greater than zero",
                    path=f"rules.{index}.seconds",
                    details={"reason": "invalid_duration"},
                )
            check_timestamp(rule["anchor"], f"rules.{index}.anchor")
        elif rule["type"] == "once":
            check_timestamp(rule["at"], f"rules.{index}.at")
    check_duration(config["graceSeconds"], "graceSeconds")
    return copy.deepcopy(config)


def validate_operation_result(result: Mapping[str, Any], *, known_operations: set[str] | None = None) -> Mapping[str, Any]:
    """Check the §10.4 success-write response envelope stays complete."""
    if not isinstance(result, Mapping):
        raise ContractError("invalid_schema", "Operation result must be an object")
    required = {"operationId", "requestKey", "status", "objectRefs", "links"}
    missing = sorted(required - set(result))
    if missing:
        raise ContractError(
            "invalid_schema",
            "Operation result is missing required fields",
            details={"reason": "missing_field", "fields": missing},
        )
    if result["status"] not in OPERATION_STATES:
        raise ContractError(
            "invalid_schema",
            "Unknown operation status",
            path="status",
            details={"reason": "invalid_enum", "allowed": list(OPERATION_STATES)},
        )
    check_request_key(result["requestKey"])
    if known_operations is not None and result.get("operation") not in known_operations:
        raise ContractError(
            "unsupported_operation",
            "Unknown operation",
            path="operation",
            details={"reason": "unsupported_operation"},
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", nargs="?", const="", metavar="DIR", help="write generated artifacts")
    parser.add_argument("--check", nargs="?", const="", metavar="DIR", help="fail if artifacts drift")
    args = parser.parse_args(argv)
    if args.write is not None:
        written = write_schema_artifacts(args.write or None)
        print(f"wrote {len(written)} artifacts")
        return 0
    if args.check is not None:
        problems = check_schema_artifacts(args.check or None)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
