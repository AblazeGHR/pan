"""Canonical system field registry and bounded normalization.

System fields are runtime identity values that Pan itself owns or validates.
They are never inferred from message text and never promoted from event payload.
The Event envelope carries them in an optional ``system`` object; ``payload`` is
source-defined data and cannot override that object.

This module has no IO and no dependency on the Automation runtime so that Event
admission, Trigger matching, Action execution and RuntimeIssue mapping share one
definition.
"""
from __future__ import annotations

import copy
import json

MAX_VALUE_LENGTH = 256
MAX_SYSTEM_BYTES = 4096

_FIELD_ROWS = (
    dict(
        name="sessionId",
        type="string",
        description="Pan Session identity associated with the Event",
        source="session",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=True,
        targetKind="session",
        crossSession=False,
        requiresOwnerCheck=True,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("session_id",),
    ),
    dict(
        name="sourceSessionId",
        type="string",
        description="Session that originated a cross-Session delivery or report",
        source="session",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=True,
        targetKind="session",
        crossSession=True,
        requiresOwnerCheck=True,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("source_session_id",),
    ),
    dict(
        name="workspaceId",
        type="string",
        description="Workspace that owns the associated Session",
        source="workspace",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("workspace_id",),
    ),
    dict(
        name="workerId",
        type="string",
        description="Worker process identity that produced the Event",
        source="runtime",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("worker_id",),
    ),
    dict(
        name="taskId",
        type="string",
        description="External or assigned Agent task identity",
        source="task",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("task_id",),
    ),
    dict(
        name="executionId",
        type="string",
        description="Durable Job or Run execution identity",
        source="execution",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("execution_id",),
    ),
    dict(
        name="attemptId",
        type="string",
        description="Attempt identity within an execution",
        source="execution",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("attempt_id",),
    ),
    dict(
        name="queueItemId",
        type="string",
        description="Session Queue item identity",
        source="queue",
        optional=True,
        nullable=False,
        sensitive=False,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="host",
        availableIn=("event", "trigger", "action", "job", "gui"),
        aliases=("queue_item_id",),
    ),
    dict(
        name="threadId",
        type="string",
        description="Adapter-native thread identity, only when reported structurally",
        source="adapter",
        optional=True,
        nullable=False,
        sensitive=True,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="adapter",
        availableIn=("event", "trigger", "job", "gui"),
        aliases=("thread_id",),
    ),
    dict(
        name="turnId",
        type="string",
        description="Adapter-native turn identity, only when reported structurally",
        source="adapter",
        optional=True,
        nullable=False,
        sensitive=True,
        actionTarget=False,
        targetKind=None,
        crossSession=False,
        requiresOwnerCheck=False,
        trustedFrom="adapter",
        availableIn=("event", "trigger", "job", "gui"),
        aliases=("turn_id",),
    ),
)

SYSTEM_FIELDS = {row["name"]: row for row in _FIELD_ROWS}
SYSTEM_FIELD_NAMES = tuple(SYSTEM_FIELDS)
ALIASES = {alias: row["name"] for row in _FIELD_ROWS for alias in row["aliases"]}
ACTION_TARGET_FIELDS = tuple(name for name in SYSTEM_FIELD_NAMES if SYSTEM_FIELDS[name]["actionTarget"])


class SystemFieldError(ValueError):
    """An input violated the canonical system field contract."""


def _value(name: str, value) -> str:
    if not isinstance(value, str):
        raise SystemFieldError(f"System field {name} must be a string")
    if not 1 <= len(value) <= MAX_VALUE_LENGTH:
        raise SystemFieldError(f"System field {name} must be 1..{MAX_VALUE_LENGTH} characters")
    if value != value.strip():
        raise SystemFieldError(f"System field {name} must not contain surrounding whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SystemFieldError(f"System field {name} must not contain control characters")
    return value


def canonical_system(values, *, allow_unknown: bool = False) -> dict:
    """Return only canonical camelCase keys.

    ``None`` values mean the field is absent and are omitted. Unknown keys are
    rejected by default. Host metadata may pass ``allow_unknown=True`` so that
    unrelated correlation metadata can be supplied without becoming a system
    field. Aliases are accepted on input only; equal duplicates are idempotent
    and conflicting duplicates fail closed.
    """
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise SystemFieldError("System fields must be an object")
    result = {}
    for raw_name, value in values.items():
        if not isinstance(raw_name, str):
            raise SystemFieldError("System field names must be strings")
        name = raw_name if raw_name in SYSTEM_FIELDS else ALIASES.get(raw_name)
        if name is None:
            if allow_unknown:
                continue
            raise SystemFieldError(f"Unknown system field: {raw_name}")
        if value is None:
            continue
        text = _value(name, value)
        if name in result and result[name] != text:
            raise SystemFieldError(f"Conflicting values for system field {name}")
        result[name] = text
    canonical = {name: result[name] for name in SYSTEM_FIELD_NAMES if name in result}
    size = len(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_SYSTEM_BYTES:
        raise SystemFieldError(f"System fields exceed {MAX_SYSTEM_BYTES} bytes")
    return canonical


def canonical_event(event: dict) -> dict:
    """Copy an Event envelope with only its canonical ``system`` object.

    This function deliberately never inspects ``payload``. A payload key with
    the same spelling as a system field remains payload data and cannot create,
    replace or override trusted system identity.
    """
    if not isinstance(event, dict):
        raise SystemFieldError("Event must be an object")
    system = canonical_system(event.get("system")) if event.get("system") is not None else {}
    result = copy.deepcopy(event)
    if system:
        result["system"] = system
    else:
        result.pop("system", None)
    return result


def system_binding_name(path: str) -> str:
    """Resolve a Trigger binding path to a canonical system field name."""
    if not isinstance(path, str) or not path:
        raise SystemFieldError("System binding path must be a nonempty string")
    name = path[7:] if path.startswith("system.") else path
    if name not in SYSTEM_FIELDS:
        raise SystemFieldError(f"Unknown system field binding: {path}")
    return name


def system_target_name(path: str) -> str:
    """Resolve an Action Session selector path that must be an Action target."""
    if path == "system":
        raise SystemFieldError("Unknown system Action target: system")
    if not isinstance(path, str) or not path.startswith("system."):
        raise SystemFieldError("System Action target path must start with system.")
    name = path[7:]
    row = SYSTEM_FIELDS.get(name)
    if row is None:
        raise SystemFieldError(f"Unknown system Action target: {path}")
    if not row["actionTarget"]:
        raise SystemFieldError(f"System field is not an Action target: {name}")
    return name


def system_value(event: dict, name: str) -> str:
    """Return one canonical system value from an admitted Event, or fail."""
    system = event.get("system") if isinstance(event, dict) else None
    if not isinstance(system, dict) or name not in system:
        raise SystemFieldError(f"System field unavailable: {name}")
    return system[name]


def validate_argument_bindings(mapping) -> None:
    """Validate literal/event/system bindings before a Trigger is stored."""
    if not isinstance(mapping, dict):
        raise SystemFieldError("arguments must be an object")
    for key, binding in mapping.items():
        if not isinstance(key, str) or not key:
            raise SystemFieldError("Argument names must be nonempty strings")
        if not isinstance(binding, dict):
            raise SystemFieldError(f"Invalid argument binding for {key}")
        form = binding.get("from")
        if form == "literal":
            if set(binding) != {"from", "value"}:
                raise SystemFieldError(f"Invalid literal binding for {key}")
            continue
        if form not in {"event", "system"}:
            raise SystemFieldError(f"Invalid argument source for {key}")
        if set(binding) != {"from", "path"} or not isinstance(binding.get("path"), str) or not binding["path"]:
            raise SystemFieldError(f"Invalid {form} binding for {key}")
        if form == "system":
            system_binding_name(binding["path"])


def public_schema() -> dict:
    """Read-only discovery contract shared by HTTP, MCP and Agents."""
    return {
        "version": 1,
        "canonicalOnly": True,
        "fields": [
            {
                **copy.deepcopy(SYSTEM_FIELDS[name]),
                "trusted": True,
                "availableIn": list(SYSTEM_FIELDS[name]["availableIn"]),
                "aliases": list(SYSTEM_FIELDS[name]["aliases"]),
            }
            for name in SYSTEM_FIELD_NAMES
        ],
        "aliases": dict(ALIASES),
        "actionTargets": list(ACTION_TARGET_FIELDS),
        "trust": {
            "system": "host/adapter validated; canonical camelCase only",
            "payload": "source-defined and untrusted; never promoted to system",
            "inference": "none; identifiers are never guessed from message text",
        },
        "eventEnvelope": {
            "system": "optional object containing only canonical fields",
            "payload": "source-defined value validated by the Event schema",
        },
        "limits": {
            "fields": len(SYSTEM_FIELD_NAMES),
            "maxValueLength": MAX_VALUE_LENGTH,
            "maxSystemBytes": MAX_SYSTEM_BYTES,
        },
    }
