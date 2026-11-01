"""Thin local completion-notice adapter; persistence remains in final outbox.

Only original local Session pre-enqueue checks and native WebSocket event
preparation can produce no-attempt evidence. Serialized/custom sender
dictionaries are never such evidence.
"""
import copy
import asyncio


NOTIFICATION_RUNTIME = {
    "owner": "notification-manager", "fields": ["targetRef", "payload"],
    "allowedStates": ["pending"], "effectiveStage": "before-enqueue",
    "concurrency": "outbox-reservation+job-config-cas", "audit": "final_operations+outbox-receipt",
    "unknownPolicy": "query-original", "acceptedPolicy": "frozen",
}


class _LocalNoticeReceipt(dict):
    """In-process producer provenance; no wire result can acquire this type."""


def binding(row):
    from packages.jobs.final_contracts import digest
    return {"outboxId": row["outboxId"], "runId": row["runId"],
            "transitionSeq": row["transitionSeq"], "recipientId": row["recipientId"],
            "targetRef": row.get("targetRef"), "targetRevision": row.get("targetRevision", 0),
            "payloadDigest": digest(row["summary"])}


def is_local_no_attempt(receipt, row):
    return (isinstance(receipt, _LocalNoticeReceipt)
            and receipt.get("binding") == binding(row)
            and receipt.get("outcome") == "pending"
            and receipt.get("phase") == "before-enqueue"
            and receipt.get("attempted") is False)


async def enqueue_session_notice(row):
    """Original queue implementation; no transport, replay engine or history load."""
    from packages.core import session, worker
    from packages.jobs.final_contracts import canonical_json
    bound = binding(row)
    base = {"binding": bound, "producer": "session.enqueue_notice"}
    target = row.get("targetRef")
    if worker._shutdown_started:
        return _LocalNoticeReceipt(**base, outcome="pending", attempted=False,
            phase="before-enqueue", error={"code": "pan_shutting_down",
                "message": "Pan main service is shutting down"})
    # Both facts are observed before invoking the effectful original enqueue.
    # An exception/negative result after entering that call remains unknown.
    if not target or await asyncio.to_thread(session.get, target, load_history=False) is None:
        return _LocalNoticeReceipt(**base, outcome="pending", attempted=False,
            phase="before-enqueue", error={"code": "session_not_found"})
    summary = row["summary"]
    result = await worker.enqueue_notice(target, canonical_json(summary), source="automation",
        event_id=row["outboxId"], notice_kind="job_terminal", job_id=row["jobId"],
        notice_status=summary["status"], creator_session_id=summary.get("creatorSessionId"),
        target_session_ids=[target])
    if result.get("ok"):
        return _LocalNoticeReceipt(**base, outcome="succeeded", attempted=True,
            phase="after-enqueue", queueReceipt=copy.deepcopy(result))
    return _LocalNoticeReceipt(**base, outcome="unknown", attempted=True,
        phase="after-enqueue", error=copy.deepcopy(result.get("error")))


def websocket_preparation_not_attempted(row, error):
    """Original WS producer proved failure before every outbound enqueue."""
    if row.get('channel') != 'ws':
        raise ValueError('WebSocket preparation evidence requires the original WS row')
    return _LocalNoticeReceipt(binding=binding(row), producer='websocket.broadcast.prepare',
        outcome='pending', attempted=False, phase='before-enqueue',
        error={'code': 'broadcast_preparation_failed', 'message': str(error)})
