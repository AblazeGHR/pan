"""Own queue projection and original-item consumption; provider routing is private."""
import asyncio
import copy

from . import session as sess
from . import worker


_attempts: dict[tuple[str, str], asyncio.Task] = {}
UNAVAILABLE = {"ok": False, "error": "item_unavailable"}


def _eligible(s, item):
    if s.readonly_session or not worker.queue_item_eligible(s, item):
        return False
    if worker._queue_item_kind(item) == "task":
        if not worker._is_valid_task_item(item) or worker._task_source(item) is None:
            return False
        try:
            worker.project_message_parts(item.get("parts"), item["text"])
        except (ValueError, TypeError):
            return False
    w = worker.find_alive_worker_by_session(s.id)
    if (w is not None and w.status == "running"
            and worker.resolve_execution_mode(w.adapter, s) == "stream"
            and getattr(w.adapter, "supports_native_steer", False)):
        if worker._queue_item_kind(item) != "task" and item.get("parts"):
            return False
        # The native control contract excludes every edit lease, including a
        # lease on another row. Match that actual gate in the read projection.
        return not any(worker.queue_item_edit_locked(s, identity)
                       for identity in list(s.queue_edit_locks))
    return True


async def _confirmed(s, *, already=False, receipt=None):
    # An in-memory sent marker alone is insufficient after a failed writer.
    outcome = sess.DurableOutcome()
    try:
        await worker._save_receipt(s, outcome=outcome)
    except asyncio.CancelledError:
        if not outcome.succeeded:
            raise
    except Exception:
        return {"ok": False, "status": "unconfirmed", "retryable": False}
    result = {"ok": True, "status": "consumed", "alreadyConsumed": already}
    # "consumed" here is removal of the original Pan queue item. Native
    # admission is separate from the model's later safe-point consumption.
    if receipt and isinstance(receipt.get("steer"), dict):
        result["steer"] = copy.deepcopy(receipt["steer"])
    return result


async def read(session_id: str) -> dict:
    s = worker._get_session_shallow(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        # No queue revision, position or total: these would reveal hidden rows.
        items = []
        for item in s.queue_pending:
            if _eligible(s, item):
                value = copy.deepcopy(item)
                for key in list(value):
                    if key.startswith("selfConsume") or key in {"position", "queueRevision"}:
                        value.pop(key)
                value["id"] = worker._queue_item_id(item)
                value["content"] = (worker.project_message_parts(item.get("parts"), item.get("text", ""))
                                    if worker._queue_item_kind(item) == "task"
                                    else worker._format_report_batch([item]))
                items.append(value)
        return {"ok": True, "items": items}


async def consume(session_id: str, item_id: str) -> dict:
    s = worker._get_session_shallow(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        item = next((it for it in s.queue_pending if isinstance(it, dict)
                     and worker._queue_item_id(it) == item_id), None)
        if s.queue_paused or (item is not None and not _eligible(s, item)):
            return dict(UNAVAILABLE)
    key = (session_id, item_id)
    task = _attempts.get(key)
    if task is None:
        task = asyncio.create_task(_consume(session_id, item_id))
        _attempts[key] = task

        def finished(done):
            if _attempts.get(key) is done:
                _attempts.pop(key, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
    # A restarting caller can disappear. The server owns the attempt, and a
    # duplicate uses the same original item/receipt instead of replaying it.
    try:
        return await asyncio.wait_for(asyncio.shield(task), 10)
    except asyncio.TimeoutError:
        return {"ok": False, "status": "pending", "retryable": False}


async def _consume(session_id: str, item_id: str) -> dict:
    s = worker._get_session_shallow(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        item = next((it for it in s.queue_pending if isinstance(it, dict)
                     and worker._queue_item_id(it) == item_id), None)
        # Hidden and nonexistent IDs are deliberately indistinguishable.
        if s.queue_paused or (item is not None and not _eligible(s, item)):
            return dict(UNAVAILABLE)
        if item is None:
            receipt = worker._ledger_record(s, item_id)
            if receipt and worker._delivery_state(receipt) == worker._DELIVERY_SENT:
                return await _confirmed(s, already=True, receipt=receipt)
            return dict(UNAVAILABLE)
        if s.readonly_session:
            return dict(UNAVAILABLE)
        if item.get("selfConsumeRequested"):
            return {"ok": False, "status": "pending", "retryable": False}
        if any(isinstance(it, dict) and it.get("selfConsumeRequested") and _eligible(s, it)
               for it in s.queue_pending):
            return {"ok": False, "status": "pending", "retryable": False}
        w = worker.find_alive_worker_by_session(session_id)
        worker_id, generation = (w.worker_id, w.generation) if w is not None else (None, None)
        route = worker.steer_delivery_route(w, s)
        native = route == "steer"
        revision = item.get("revision", 1)
        if not native:
            # Candidate apply/save/rollback belong to ONE ordered ticket. A
            # previously queued writer cannot persist a failed candidate.
            outcome = sess.DurableOutcome()

            def apply():
                old_queue = list(s.queue_pending)
                old_item = dict(item)
                old_revision = s.queue_revision
                try:
                    if not worker.queue_item_eligible(s, item):
                        raise ValueError("item unavailable")
                    item["selfConsumeRequested"] = True
                    s.queue_pending = [item] + [it for it in s.queue_pending if it is not item]
                    s.queue_revision += 1
                    sess._save_body(s)
                except BaseException:
                    s.queue_pending = old_queue
                    item.clear()
                    item.update(old_item)
                    s.queue_revision = old_revision
                    raise

            try:
                await sess._persist_async_outcome(s.id, apply, outcome)
            except asyncio.CancelledError:
                if not outcome.succeeded:
                    raise
                # A committed intent must continue even if its requester left.
            except Exception:
                return {"ok": False, "error": "persist_failed", "retryable": True}
    if native:
        outcome = {}
        try:
            error = await worker.steer_queue_item(session_id, item_id, revision, outcome=outcome)
        except Exception:
            error = "Control failed"
        # Never forward provider errors (which can mention hidden queue state).
        if error is not None:
            async with worker.queue_lock(session_id):
                if not _eligible(s, item):
                    return dict(UNAVAILABLE)
            if outcome.get("status") == "rejected":
                return {"ok": False, "status": "rejected", "retryable": True}
            return {"ok": False, "status": "unconfirmed", "retryable": False}
    else:
        # No queue lock across process exit, spawn lock acquisition or restart.
        if w is not None and w.pending_restart:
            # An accepted delayed intent owns retirement even for adapters
            # without Steer. Never replace it with an immediate restart.
            await worker._wake_worker(session_id)
            result = w
        elif (w is not None and w.status in {"idle", "queued"}
                and worker.resolve_execution_mode(w.adapter, s) == "stream"
                and getattr(w.adapter, "supports_native_steer", False)):
            # There is no active turn to interrupt. The existing consumer also
            # owns delayed-restart priority; a wakeup does not cancel it.
            await worker._wake_worker(session_id)
            result = w
        else:
            result = (await worker.restart_or_start_worker(
                session_id, expected_worker_id=worker_id, expected_generation=generation)
                      if worker_id is not None else await worker.create_worker(session_id))
        if isinstance(result, str):
            return {"ok": False, "status": "unconfirmed", "retryable": False}
    async with worker.queue_lock(session_id):
        receipt = worker._ledger_record(s, item_id)
        if receipt and worker._delivery_state(receipt) == worker._DELIVERY_SENT:
            return await _confirmed(s, receipt=receipt)
        if s.readonly_session or not worker.queue_item_eligible(s, item, queued=False):
            return dict(UNAVAILABLE)
    return {"ok": False, "status": "pending", "retryable": False}
