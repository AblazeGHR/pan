"""Legacy Job shapes over the canonical single authority.

The designed Jobs page speaks the old Job shape (kind/status/target/schedule).
This module is a *projection boundary*, not a second runtime: it builds ordinary
canonical Job definitions from the four legacy create inputs, projects canonical
Jobs back to the old shape, and never writes the retired JSON writer.

Only explicit identities are merged: a canonical Job ID and a legacy Job ID are
different ids unless they are equal, so one logical Job is never listed twice.
"""
from __future__ import annotations

import copy
import uuid

from packages.jobs import final_contracts as fc

#: Legacy kind -> product Action suffix. The product Actions already exist and
#: are maintained by FinalService; no new executor is introduced.
ACTIONS = {
    "background-process": "product_process",
    "session-message": "product_session_message_v2",
    "session-broadcast": "product_session_broadcast_v2",
}

COMPLETIONS = {
    "background-process": "process-exit",
    "session-message": "durable-queue-accepted",
    "session-broadcast": "durable-queue-accepted",
}

DEFAULT_NAMES = {
    "background-process": "Background process",
    "session-message": "Session message",
    "session-broadcast": "Session broadcast",
    "scheduled-task": "Scheduled task",
}


def _literal(value):
    return {"from": "literal", "value": copy.deepcopy(value)}


def _request_key(request, data=None):
    key = request.headers.get("x-pan-request-key") if request is not None else None
    if not key and isinstance(data, dict) and isinstance(data.get("requestKey"), str):
        key = data["requestKey"]
    return key or "legacy-http-" + uuid.uuid4().hex


def _arguments(kind, data):
    if kind == "background-process":
        # The process Action owns argv/cwd only; the Session belongs to the
        # creator context, not to the Action input.
        return {
            "argv": _literal(list(data.get("argv") or [])),
            "cwd": _literal(data.get("cwd") or ""),
            "hideWindow": _literal(bool(data.get("hideWindow", True))),
        }
    if kind == "session-message":
        return {
            "sessionId": _literal((data.get("target") or {}).get("sessionId") or ""),
            "text": _literal(data.get("text") or ""),
        }
    return {
        "sessionIds": _literal(list((data.get("target") or {}).get("sessionIds") or [])),
        "text": _literal(data.get("text") or ""),
    }


def definition_for(service, actor, data):
    """Legacy create input -> canonical Job definition + echoed legacy fields."""
    if not isinstance(data, dict):
        raise fc.ContractError("invalid_schema", "Job body must be an object")
    kind = data.get("kind")
    if kind not in ACTIONS:
        # scheduled-task composition (Creator/Trigger) is mapped separately by
        # the scheduler adapter; this projection keeps the three runtime kinds.
        raise fc.ContractError(
            "unsupported_operation",
            "This legacy Job kind needs its accepted scheduler composition adapter",
        )
    action = service.action_ref(ACTIONS[kind], actor=actor, latest=True)
    name = (data.get("name") or DEFAULT_NAMES[kind]).strip() or DEFAULT_NAMES[kind]
    definition = {
        "name": name,
        "description": data.get("description") or "",
        "steps": [{
            "stepId": "effect",
            "actionRef": action,
            "arguments": _arguments(kind, data),
            "completion": {"contract": COMPLETIONS[kind]},
        }],
        "retention": {"policyRef": "default"},
    }
    legacy = {
        "kind": kind,
        "target": copy.deepcopy(data.get("target") or {}),
        "text": data.get("text"),
        "argv": copy.deepcopy(data.get("argv")),
        "cwd": data.get("cwd"),
        "hideWindow": data.get("hideWindow", True),
        "label": data.get("label"),
    }
    return definition, legacy


_STATUS = {
    "draft": "draft",
    "enabled": "scheduled",
    "paused": "paused",
    "retired": "retired",
}


def job_view(service, job, *, legacy=None, pinned=False, run_count=0):
    """Project one canonical Job to the legacy shape; never invents success."""
    item = job.get("canonical", job)
    legacy = copy.deepcopy(legacy or {})
    kind = legacy.get("kind") or "background-process"
    state = item.get("state")
    return {
        "jobId": item["id"],
        "id": item["id"],
        "kind": kind,
        "name": item.get("name") or DEFAULT_NAMES.get(kind, "Job"),
        "description": item.get("description") or "",
        "status": _STATUS.get(state, state),
        "enabled": state == "enabled",
        "paused": state == "paused",
        "retired": state == "retired",
        "configRevision": item.get("configRevision"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "runCount": run_count,
        "pinned": bool(pinned),
        "target": legacy.get("target") or {},
        "text": legacy.get("text"),
        "argv": legacy.get("argv"),
        "cwd": legacy.get("cwd"),
        "label": legacy.get("label"),
        "schedule": legacy.get("schedule") or [],
        "unified": None,
        "integrity": {"status": "complete", "approvalRequired": False},
    }


def canonical_jobs(service, actor):
    """Explicitly enabled/draft canonical Jobs, without the retired ones.

    Read-only: this never mutates lifecycle and never projects a Run result.
    """
    page = service.list("job", actor=actor, cursor=None, limit=100)
    return [item for item in page.get("items", []) if item.get("state") != "retired"]


def get_canonical(service, job_id, actor):
    try:
        return service.get("job", job_id, actor=actor)
    except (KeyError, TypeError):
        return None


def save_binding(service, job_id, kind, legacy):
    """Explicit identity binding for projection only; never an executor."""
    from packages.actions.groups import Groups
    with service.transaction() as db:
        Groups.save(db, "legacy_job_binding", job_id, {"kind": kind, "fields": copy.deepcopy(legacy)})


def load_binding(service, job_id):
    try:
        with service.transaction(readonly=True) as db:
            return service._get(db, "legacy_job_binding", job_id)  # noqa: SLF001
    except KeyError:
        return None


def patch_definition(service, job, data, kind):
    """Apply the legacy editable fields onto the current Job definition.

    Schedule/Creator edits are not silently dropped: they raise so the caller
    keeps the original semantics instead of writing a different Job.
    """
    allowed = {"name", "description", "text", "target", "enabled", "paused",
               "targetSessionId", "target_session_ids"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise fc.ContractError("unsupported_operation",
                               "This legacy Job field needs its scheduler composition adapter")
    definition = {key: copy.deepcopy(job[key]) for key in
                  ("name", "description", "steps", "retention", "inputSchema",
                   "overlap", "recovery", "runLimit", "deliveryRuntime") if key in job}
    definition["steps"] = copy.deepcopy(job["steps"])
    if "name" in data:
        definition["name"] = str(data["name"])
    if "description" in data:
        definition["description"] = str(data["description"])
    text = data.get("text")
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    for step in definition["steps"]:
        arguments = step.get("arguments") or {}
        if text is not None and "text" in arguments:
            arguments["text"] = {"from": "literal", "value": str(text)}
        if kind == "session-message" and target.get("sessionId"):
            arguments["sessionId"] = {"from": "literal", "value": target["sessionId"]}
        if kind == "session-broadcast" and target.get("sessionIds"):
            arguments["sessionIds"] = {"from": "literal", "value": list(target["sessionIds"])}
        step["arguments"] = arguments
    return definition


def set_pin(service, job_id, pinned):
    """Presentation pin in the canonical store, never the retired writer."""
    from packages.actions.groups import Groups
    with service.transaction() as db:
        Groups.save(db, "job_pin", job_id, {"pinned": bool(pinned)})


def get_pin(service, job_id):
    try:
        with service.transaction(readonly=True) as db:
            return bool(service._get(db, "job_pin", job_id).get("pinned"))  # noqa: SLF001
    except KeyError:
        return False


def scheduler_payload(data):
    """Legacy scheduled input -> the approved scheduler task payload."""
    kind = data.get("kind")
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    target_id = (target.get("sessionId") or data.get("targetSessionId")
                 or data.get("target_session_id"))
    if kind == "session-message":
        from packages.jobs.legacy_compat import _schedule
        schedule = data.get("schedule")
        if not isinstance(schedule, dict):
            raise fc.ContractError("invalid_schema", "A message schedule is required")
        specs = _schedule(schedule)
        action = {"api": "send_session"}
    elif kind == "scheduled-task":
        specs = data.get("schedule")
        action = data.get("action") if isinstance(data.get("action"), dict) else {"api": "assign"}
    else:
        raise fc.ContractError("unsupported_operation",
                               "This legacy Job kind has no scheduler composition")
    if not isinstance(target_id, str) or not target_id:
        raise fc.ContractError("invalid_schema", "A Session target is required")
    text = data.get("text") if isinstance(data.get("text"), str) else ""
    if action.get("api") != "shell" and not text.strip():
        raise fc.ContractError("invalid_schema", "Message text is required")
    return {
        "name": data.get("name") or DEFAULT_NAMES[kind],
        "description": data.get("description") or "",
        "target_session_id": target_id,
        "text": text,
        "enabled": bool(data.get("enabled", True)),
        "paused": False,
        "schedule": specs,
        "max_runs": data.get("maxRuns", data.get("max_runs")),
        "misfire_policy": data.get("misfirePolicy", "fire_now"),
        "action": action,
    }
