"""Legacy MCP -> system Action Job compatibility adapter.

The adapter owns only parameter/result translation.  It never creates a
legacy background/message record: the returned record is an ordinary Action
Job in the unified registry, with the legacy ``kind`` and fields retained so
old readers can address the same Job ID.
"""
from __future__ import annotations

import copy
import time

from packages.core import background_jobs
from packages.jobs.action_templates import digest


LEGACY_ACTIONS = {
    background_jobs.BACKGROUND_PROCESS_KIND: "system_background_process",
    background_jobs.SESSION_MESSAGE_KIND: "system_session_message",
    background_jobs.SESSION_BROADCAST_KIND: "system_session_broadcast",
}


def _schedule(schedule: dict) -> list[dict]:
    """Translate the proven message schedule forms without inventing a time."""
    normalized, next_run = background_jobs._normalize_message_schedule(schedule, time.time())
    kind = normalized["type"]
    if kind == "once":
        at = normalized.get("at") or background_jobs._iso_utc(next_run)
        return [{"kind": "once", "at": at}]
    if kind == "interval":
        return [{"kind": "interval", "intervalSec": normalized["intervalSeconds"],
                 "anchor": background_jobs._iso_utc(time.time())}]
    # The existing cron evaluator uses Sunday=0, while the legacy weekly
    # contract uses Python weekday Monday=0.  Keep timezone as a wall-clock
    # qualifier, exactly as the old scheduler did.
    return [{"kind": "cron",
             "cron": f"{normalized['time'].split(':')[1]} {normalized['time'].split(':')[0]} * * {(normalized['weekday'] + 1) % 7}",
             **({"timezone": normalized["timezone"]} if normalized.get("timezone") else {})}]


def config_for(kind: str, fields: dict) -> tuple[dict, dict]:
    """Return unified config and legacy storage fields for one MCP shape."""
    if kind not in LEGACY_ACTIONS:
        raise ValueError("legacy MCP kind is not losslessly migratable")
    if kind == background_jobs.BACKGROUND_PROCESS_KIND:
        target = fields["targetSessionId"]
        args = {"sessionId": target, "argv": copy.deepcopy(fields["argv"]),
                "cwd": fields["cwd"], "hideWindow": fields.get("hideWindow", True)}
        return ({"name": fields["name"], "description": fields.get("description", ""),
                 "actionId": LEGACY_ACTIONS[kind], "arguments": args, "schedule": [],
                 "trigger": {"kind": "immediate"}, "enabled": True,
                 "recovery": {"enabled": False, "monitoring": False, "maxAttempts": 3,
                               "backoffSec": [5, 15, 60], "stableResetSec": 300}},
                {"kind": kind, "targetSessionId": target, "argv": copy.deepcopy(fields["argv"]),
                 "cwd": fields["cwd"], "hideWindow": fields.get("hideWindow", True),
                 "label": fields.get("label"), "creatorSessionId": fields.get("creatorSessionId"),
                 "sourceSessionId": fields.get("creatorSessionId")})
    targets = (list(fields["targetSessionIds"]) if kind == background_jobs.SESSION_BROADCAST_KIND
               else [fields["targetSessionId"]])
    args = {"sessionIds": targets, "text": fields["text"]} if kind == background_jobs.SESSION_BROADCAST_KIND \
        else {"sessionId": targets[0], "text": fields["text"]}
    specs = _schedule(fields["schedule"])
    return ({"name": fields.get("name") or background_jobs.default_job_name(fields.get("registryRoot")),
             "description": fields.get("description", ""), "actionId": LEGACY_ACTIONS[kind],
             "arguments": args, "schedule": specs, "trigger": {"kind": "schedule"},
             "enabled": True, "recovery": {"enabled": False, "monitoring": False,
                                                "maxAttempts": 3, "backoffSec": [5, 15, 60],
                                                "stableResetSec": 300}},
            {"kind": kind, "targetSessionId": targets[0] if kind == background_jobs.SESSION_MESSAGE_KIND else None,
             "targetSessionIds": targets if kind == background_jobs.SESSION_BROADCAST_KIND else None,
             "text": fields["text"], "legacySchedule": copy.deepcopy(fields["schedule"]),
             "nextRunAt": background_jobs._iso_utc(background_jobs._normalize_message_schedule(
                 fields["schedule"], time.time())[1]),
             "source": "agent", "sourceSessionId": fields.get("creatorSessionId"),
             "creatorSessionId": fields.get("creatorSessionId"),
             **({"name": fields["name"]} if fields.get("name") is not None else {}),
             "description": fields.get("description", "")})


def request_key(kind: str, fields: dict) -> str | None:
    """Use an explicit transport retry key when one is supplied.

    Legacy tool signatures have no idempotency parameter, so ordinary repeated
    calls retain their historical new-Job behavior.  A transport may attach a
    private key without exposing Action concepts to the Agent.
    """
    value = fields.get("_legacyRequestKey")
    if not isinstance(value, str) or not value:
        return None
    return "legacy:" + digest({"kind": kind, "key": value})[:48]


def legacy_view(job: dict) -> dict:
    """Project an Action Job to the old MCP shape without changing its record."""
    value = copy.deepcopy(job)
    if "legacySchedule" in value:
        value["schedule"] = value.pop("legacySchedule")
        from packages.jobs.cron import parse_datetime
        point = parse_datetime(job.get('nextFireAt'))
        value['nextRunAt'] = background_jobs._iso_utc(point.timestamp()) if point else None
        if job.get('status') == 'scheduled' and not job.get('runCount'):
            value['status'] = 'pending'
    value.pop("config", None)
    value.pop("action", None)
    value.pop("unified", None)
    value.pop("integrity", None)
    value.pop("creationFingerprint", None)
    value.pop("referenceGeneration", None)
    return value


def is_legacy_action_job(job: dict | None) -> bool:
    return bool(isinstance(job, dict) and job.get("legacyCompat") in LEGACY_ACTIONS
                and isinstance(job.get("action"), dict)
                and job["action"].get("api") == "action_reference")
