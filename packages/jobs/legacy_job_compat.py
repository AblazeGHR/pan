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
        "source": {"type": "user", "sessionId": legacy.get("creatorSessionId")},
        "target": ({"type": "sessions", "sessionIds": (legacy.get("target") or {}).get("sessionIds")}
                   if kind == "session-broadcast"
                   else {"type": "session", "sessionId": (legacy.get("target") or {}).get("sessionId")}),
        "action": {
            "api": {"background-process": "shell", "session-message": "send_session",
                    "session-broadcast": "send_session", "scheduled-task": "assign"}.get(kind, "assign"),
            "args": ({"argv": legacy.get("argv"), "cwd": legacy.get("cwd")}
                     if kind == "background-process"
                     else {"text": legacy.get("text")}),
        },
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


def broadcast_specs(data):
    """Legacy broadcast schedule -> canonical schedule specs (no new timing)."""
    from packages.jobs.legacy_compat import _schedule
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        raise fc.ContractError("invalid_schema", "A broadcast schedule is required")
    return _schedule(schedule)


def compose_broadcast(service, actor, data, request_key):
    """Atomically compose Job + time Creator + Trigger for a multi-target send.

    Reuses the existing product runtime (`product_session_broadcast_runtime_v1`
    with its fanout/deliveryRuntime declaration) and the existing graph ports.
    One transaction, one operation receipt: a replayed requestKey returns the
    original graph instead of building a second one.
    """
    from packages.jobs.final_runtime.ledger import aggregate, principal
    from packages.jobs.final_service import definition, object_ref
    from packages.scheduler.providers import legacy_schedule_plan
    from scripts.job_final_migration.runtime_fields import runtime_definition
    from packages.actions.groups import Groups

    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    ids = list(target.get("sessionIds") or [])
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise fc.ContractError("invalid_schema", "Message text is required")
    if not ids or any(not isinstance(item, str) or not item for item in ids):
        raise fc.ContractError("invalid_schema", "Broadcast needs explicit Session identities")
    specs = broadcast_specs(data)
    if len(specs) != 1:
        raise fc.ContractError("invalid_schema", "Broadcast supports one schedule entry")
    spec = specs[0]
    name = (data.get("name") or DEFAULT_NAMES["session-broadcast"]).strip() or "Session broadcast"
    with service.transaction() as db:
        def mutate(operation_id):
            action = service._get(db, "action", "product_session_broadcast_runtime_v1")
            if not action.get("enabled") or definition("action", action).get("state") == "draft":
                raise fc.ContractError("reference_conflict", "Broadcast runtime product is unavailable")
            ref = object_ref("action", action)
            result = runtime_definition("session.send-many", {"sessionIds": ids, "text": text},
                                        {"object": action, "ref": ref})
            bindings, extra = result if isinstance(result, tuple) else (result, {})
            job_value = fc.validate_definition("job", {
                "name": name,
                "description": data.get("description") or "",
                "steps": [{"stepId": "execute", "actionRef": ref, "arguments": bindings,
                           "completion": {"contract": definition("action", action).get("defaultCompletion")}}],
                "overlap": {"mode": "forbid", "maxActive": 1, "maxPending": 0},
                "recovery": {"automatic": False, "maxAttempts": 1,
                             "backoffSeconds": ["1"], "stableResetSeconds": "0"},
                "retention": {"policyRef": "default"},
                **extra,
            })
            jid = "job_" + fc.digest(["broadcast", request_key])[:24]
            envelope = service._envelope("job", job_value, actor, object_id=jid,
                                         state="enabled" if data.get("enabled", True) else "disabled")
            envelope["configRevision"] = 1
            fc.validate_object("job", envelope)
            job = {"jobId": jid, "canonical": envelope, "config": definition("job", envelope),
                   "configRevision": 1}
            aggregate(job, service.root)["profileId"] = service.profile_id
            service._save(db, "job", jid, job)

            clock_source = {
                "id": jid, "name": name, "owner": "scheduler-json", "revision": 1,
                "enabled": bool(data.get("enabled", True)),
                "schedule": {key: value for key, value in spec.items()
                             if key in {"kind", "at", "intervalSec", "interval_sec", "anchor", "cron", "timezone"}},
                "misfirePolicy": data.get("misfirePolicy", "fire_now"), "graceSec": 0,
                "nextFireAt": spec.get("at"), "pending": None, "publishCursor": None,
            }
            creator_value, occurrence = legacy_schedule_plan(clock_source, timezone_name=None, rule_id="e1")
            creator_value = fc.validate_definition("event_creator", creator_value)
            creator_value["eventTypes"] = ["scheduler.time.v2"]
            cid = "creator_" + fc.digest(["broadcast-creator", request_key])[:24]
            created = service.creators.create(
                creator_value, request_key=operation_id + ":creator", creator_id=cid,
                state="enabled" if data.get("enabled", True) else "disabled",
                created_by=principal(actor), owner=principal(actor))
            created["cursor"]["rules"]["e1"].update(
                nextFireAt=occurrence["at"], nextOccurrence=occurrence)
            service.creators._write(db, created)

            trigger_id = "trg_" + fc.digest(["broadcast-trigger", request_key])[:24]
            trigger = service._envelope("trigger", {
                "name": name + " tick",
                "creatorSelector": {"mode": "ids", "ids": [cid]},
                "eventTypes": ["scheduler.time.v2"],
                "targetJobId": jid,
            }, actor, object_id=trigger_id, state="enabled")
            service._save(db, "trigger", trigger_id, trigger)
            service.creators.bind_link(cid, trigger_id, jid)
            for kind, oid in (("job", jid), ("trigger", trigger_id), ("event_creator", cid)):
                db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)",
                           (kind, oid, fc.canonical_json(actor)))
            Groups.save(db, "legacy_job_binding", jid, {
                "kind": "session-broadcast",
                "fields": {"kind": "session-broadcast", "target": {"sessionIds": ids},
                           "text": text, "schedule": specs},
            })
            return {"jobId": jid, "creatorId": cid, "triggerId": trigger_id, "nextFireAt": occurrence["at"]}, \
                [object_ref("job", envelope)], False
        # The idempotency digest must cover the whole request: the same key with
        # a different text/target/schedule is a conflict, never a silent replay.
        return service._operation(db, actor, "create", request_key,
                                  {"broadcastCompose": copy.deepcopy(data)}, "job", mutate)
