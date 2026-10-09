"""Opt-in Pan adapter; execution and outbox truth belong to the Job service.

The adapter must not spawn the service or fall back to local execution after a
connection error. Legacy Jobs remain owned by the old registry.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit


class JobServiceUnavailable(OSError):
    """Static, public error; upstream command/error text is never copied."""


def configured_client():
    from packages.core.config import load_config
    settings = load_config().get("independent_jobs", {})
    if not isinstance(settings, dict):
        raise JobServiceUnavailable("invalid independent Job service configuration")
    if settings.get("enabled", False) is False:
        return None
    if settings.get("enabled") is not True:
        raise JobServiceUnavailable("invalid independent Job service configuration")
    endpoint = settings.get("endpoint")
    try:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        valid = (parsed is not None and parsed.scheme == "http"
                 and parsed.hostname == "127.0.0.1" and parsed.port is not None
                 and not parsed.username and not parsed.password
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise JobServiceUnavailable("independent Job service requires an explicit loopback endpoint")
    from .independent_client import IndependentJobClient
    return IndependentJobClient(endpoint.rstrip("/"), timeout=2.0)


def request(method, *args, **kwargs):
    """Call only the configured service, with a static outage error."""
    client = configured_client()
    if client is None:
        return None
    try:
        return getattr(client, method)(*args, **kwargs)
    except Exception as exc:
        raise JobServiceUnavailable("independent Job service unavailable") from exc


def public_job(job):
    if not isinstance(job, dict) or not isinstance(job.get("jobId"), str):
        raise JobServiceUnavailable("invalid independent Job service response")
    result = dict(job)
    result["executionOwner"] = "independent-job-service"
    result.setdefault("kind", "background-process")
    return result


async def recover_events(enqueue_notice):
    """Ack only after durable Pan enqueue; replay uses the existing event ledger."""
    client = configured_client()
    if client is None:
        return 0
    try:
        response = await asyncio.to_thread(client.events)
        events = response.get("events") if isinstance(response, dict) else None
        if not isinstance(events, list):
            raise JobServiceUnavailable("invalid independent Job service outbox")
        delivered = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            job_id, target = event.get("jobId"), event.get("targetSessionId")
            event_id, status = event.get("eventId"), event.get("status")
            if (not isinstance(job_id, str) or not isinstance(target, str) or not target
                    or event_id != job_id + ":terminal"
                    or status not in {"completed", "failed", "cancelled"}):
                continue
            # Free-form service diagnostics never become Session messages.
            text = json.dumps({"jobId": job_id, "status": status,
                               "exitCode": event.get("exitCode"),
                               "logPath": event.get("logPath")}, ensure_ascii=False)
            result = await enqueue_notice(
                target, text, source="automation", event_id=event_id,
                notice_kind="background_job_terminal", job_id=job_id,
                notice_status=status, creator_session_id=event.get("creatorSessionId"),
                target_session_ids=[target])
            if isinstance(result, dict) and result.get("ok") is True:
                await asyncio.to_thread(client.ack, event_id)
                delivered += 1
        return delivered
    except JobServiceUnavailable:
        raise
    except Exception as exc:
        raise JobServiceUnavailable("independent Job service unavailable") from exc
