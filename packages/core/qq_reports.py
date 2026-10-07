"""Durable task completion deliveries through the existing QQ send route.

The terminal commit creates the outbox; claim-before-send allows one attempt.
Gateway failures are recorded without retries, as requested by the user.
No history lookup or card-specific request is involved.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from packages.core import session as sess

_log = logging.getLogger(__name__)
_sender = None
_tasks: dict[str, asyncio.Task] = {}


def parse_target(key: str) -> tuple[str, str, str] | None:
    match = re.fullmatch(r"(user|group):([1-9][0-9]*)(?:@([1-9][0-9]*))?", key)
    return match.groups(default="") if match else None


def prepare(s, terminal_key: str, body: str) -> None:
    if not body.strip():
        return
    retained = {r.get("terminalKey") for r in s.terminal_results}
    s.qq_report_outbox = {
        key: record for key, record in s.qq_report_outbox.items()
        if record.get("state") == "pending" or record.get("terminalKey") in retained
    }
    for target in sorted(s.qq_report_targets):
        if parse_target(target) is None:
            continue
        identity = hashlib.sha256(f"{s.id}\0{terminal_key}\0{target}".encode()).hexdigest()
        s.qq_report_outbox.setdefault(identity, {
            "target": target, "text": body, "state": "pending",
            "terminalKey": terminal_key,
        })


def cancel_pending(s, target: str | None = None) -> None:
    """Off/on must not revive a previous task's not-yet-claimed report."""
    for record in s.qq_report_outbox.values():
        if record.get("state") == "pending" and (target is None or record["target"] == target):
            record["state"] = "cancelled"


def start(sender, sessions) -> None:
    global _sender
    _sender = sender
    for s in sessions:
        kick(s.id)


async def stop() -> None:
    global _sender
    _sender = None
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


def kick(session_id: str) -> None:
    if _sender is None or session_id in _tasks:
        return
    s = sess.get(session_id, load_history=False)
    if not s or not any(r.get("state") == "pending" for r in s.qq_report_outbox.values()):
        return
    task = asyncio.create_task(_drain(session_id), name=f"qq-report:{session_id}")
    _tasks[session_id] = task
    def finished(done):
        _tasks.pop(session_id, None)
        # A new task can finish while an earlier contact send is in flight.
        # Drain its newly committed records without retrying attempted sends.
        if not done.cancelled() and done.result():
            kick(session_id)
    task.add_done_callback(finished)


async def _drain(session_id: str) -> None:
    from packages.core.worker import queue_lock
    try:
        s = sess.get(session_id, load_history=False)
        if not s:
            return
        for record in list(s.qq_report_outbox.values()):
            if record.get("state") != "pending":
                continue
            async with queue_lock(session_id):
                if sess.get(session_id, load_history=False) is not s:
                    return
                # Persist before any external effect, including a terminal
                # commit which previously failed and left memory dirty.
                if record["target"] not in s.qq_report_targets or s.readonly_session:
                    record["state"] = "cancelled"
                    await sess.save_async(s)
                    continue
                record["state"] = "attempted"
                try:
                    await sess.save_async(s)
                except BaseException:
                    record["state"] = "pending"
                    raise
                target = parse_target(record["target"])
            if target is None:
                continue
            scope, peer, bot = target
            try:
                response = await _sender(
                    target_type="private" if scope == "user" else "group",
                    target_id=peer, bot_uin=bot or None, text=record["text"],
                )
            except Exception:
                response = {"ok": False, "error": {"code": "connection_error"}}
            async with queue_lock(session_id):
                if sess.get(session_id, load_history=False) is not s:
                    return
                record["state"] = "sent" if response.get("ok") else "failed"
                record["response"] = response
                try:
                    await sess.save_async(s)
                except Exception:
                    # The attempt is already durable; a diagnostic save must
                    # not prevent the other contacts getting their attempt.
                    _log.exception("QQ attempt response save failed for Session %s", session_id)
        return True
    except Exception:
        _log.exception("QQ completion attempt failed for Session %s", session_id)
