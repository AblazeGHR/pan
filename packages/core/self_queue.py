"""Own queue projection and original-item consumption; provider routing is private."""
import asyncio
import copy
import json

from . import session as sess
from . import worker


_attempts: dict[tuple[str, str], asyncio.Task] = {}
UNAVAILABLE = {"ok": False, "error": "item_unavailable"}


def _own_session(session_id):
    # Cold shallow reads are detached projections. Mutation and receipt queries
    # must share one canonical object with queue/Worker coordinators, without
    # hydrating the entire history. There is no await between lookup and pin.
    s = worker._get_session_shallow(session_id)
    return sess._cache.setdefault(session_id, s) if s is not None else None


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
    s = _own_session(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        # No queue revision, position or total: these would reveal hidden rows.
        items = []
        for item in s.queue_pending:
            if worker._is_pauseable_agent_report(item):
                # Never allocate a legacy report ID or expose its body/parts.
                identity = item.get("queueItemId") or item.get("id")
                if (isinstance(identity, str) and identity
                        and _eligible(s, dict(item, queueItemId=identity))):
                    items.append({"id": identity, "queueItemId": identity,
                                  "type": "report", "source": "report"})
                continue
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


async def report_status(session_id: str) -> dict:
    """Own persisted subscription configuration, without queue/body counts."""
    s = _own_session(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        return {"ok": True, "sessionId": s.id,
                "reportSubscriptions": sorted(s.report_subscriptions),
                "agentReportsPaused": bool(getattr(s, "agent_reports_paused", False))}


_report_attempts: dict[str, asyncio.Task] = {}


def _report_receipt(s, item_id):
    """Metadata only. A provided original ID queries a frozen unit, never retries."""
    if (s.readonly_session or s.queue_paused or worker._native_handoff_blocked(s)
            or any(worker.queue_item_edit_locked(s, identity) for identity in list(s.queue_edit_locks))):
        return dict(UNAVAILABLE)
    anchor = next((row for row in s.queue_pending
                   if (row.get("queueItemId") or row.get("id")) == item_id), None)
    anchor = anchor or worker._ledger_record(s, item_id)
    if not anchor or not anchor.get("reportConsumeIds"):
        return dict(UNAVAILABLE)
    ids = anchor["reportConsumeIds"]
    if (not isinstance(ids, list) or not ids
            or not all(isinstance(identity, str) and identity for identity in ids)
            or len(ids) != len(set(ids))):
        return dict(UNAVAILABLE)
    rows = []
    for identity in ids:
        row = next((row for row in s.queue_pending
                    if (row.get("queueItemId") or row.get("id")) == identity), None)
        if row is not None:
            candidate = dict(row)
            # Our transaction's uncertainty is queryable, but user/policy gates
            # still hide its identity. These fields are NEVER cleared in storage.
            candidate.pop("queueSteerPending", None)
            candidate.pop("selfConsumeUncertain", None)
            if not worker.queue_item_eligible(s, candidate, queued=False):
                return dict(UNAVAILABLE)
        else:
            row = worker._ledger_record(s, identity)
        if row is None or row.get("reportConsumeIds") != ids:
            return {"ok": False, "status": "unknown", "retryable": False}
        rows.append(row)
    status = "queued"
    if all(worker._delivery_state(row) == worker._DELIVERY_SENT for row in rows):
        status = "accepted"
    elif any(row.get("queueSteerPending") or row.get("selfConsumeUncertain") for row in rows):
        status = "unknown"
    elif any(row.get("lastSteerError") for row in rows):
        status = "rejected"
    elif any(worker._delivery_state(row) != worker._DELIVERY_QUEUED for row in rows):
        status = "pending"
    return {"ok": status == "accepted", "status": status, "itemId": ids[0],
            "queueItemIds": list(ids), "retryable": False}


async def report_consume(session_id: str, item_id: str | None = None) -> dict:
    """Own a shielded attempt; explicit IDs only inspect the original receipt."""
    s = _own_session(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    if item_id is not None:
        async with worker.queue_lock(session_id):
            receipt = _report_receipt(s, item_id)
            if receipt.get("status") == "accepted":
                # Receipt inspection is read-only, including disk persistence.
                # Memory's sent marker alone cannot prove a failed writer.
                def durable():
                    try:
                        data = json.loads(sess._path(session_id).read_text(encoding="utf-8"))
                        ledger = data.get("queue_delivery_ledger", {})
                        return all(worker._delivery_state(ledger.get(identity, {})) == worker._DELIVERY_SENT
                                   and ledger[identity].get("reportConsumeIds") == receipt["queueItemIds"]
                                   for identity in receipt["queueItemIds"])
                    except (OSError, ValueError, TypeError, KeyError):
                        return False
                if not await asyncio.to_thread(durable):
                    receipt.update(ok=False, status="unconfirmed")
            return receipt
    # The existing native runtime validator/Worker lifecycle reads full own
    # history. Hydrate in place BEFORE capturing original row references; late
    # hydration would replace queue objects under a reserved transaction.
    s = sess.get(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    task = _report_attempts.get(session_id)
    if task is None:
        task = asyncio.create_task(_report_consume(session_id))
        _report_attempts[session_id] = task
        def finished(done):
            if _report_attempts.get(session_id) is done:
                _report_attempts.pop(session_id, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    try:
        return await asyncio.wait_for(asyncio.shield(task), 10)
    except asyncio.TimeoutError:
        async with worker.queue_lock(session_id):
            row = next((row for row in s.queue_pending if row.get("reportConsumeIds")), None)
            return _report_receipt(s, row["reportConsumeIds"][0]) if row else {
                "ok": False, "status": "pending", "retryable": False}


async def _report_consume(session_id: str) -> dict:
    s = _own_session(session_id)
    if s is None:
        return {"ok": False, "error": "caller_unavailable"}
    async with worker.queue_lock(session_id):
        # An unfinished frozen unit always wins. Never regroup an uncertain write.
        prior = next((row for row in s.queue_pending if row.get("reportConsumeIds")), None)
        if prior is not None:
            return _report_receipt(s, prior["reportConsumeIds"][0])
        if (s.readonly_session or s.queue_paused or worker._native_handoff_blocked(s)
                or worker._offline.is_illegal(s)
                or any(worker.queue_item_edit_locked(s, identity) for identity in list(s.queue_edit_locks))):
            return dict(UNAVAILABLE)
        selected = []
        for row in s.queue_pending:
            identity = row.get("queueItemId") or row.get("id")
            if (worker._is_pauseable_agent_report(row) and isinstance(identity, str) and identity
                    and not row.get("selfConsumeRequested")
                    and _eligible(s, dict(row, queueItemId=identity))):
                selected.append(row)
        ids = [row.get("queueItemId") or row["id"] for row in selected]
        if not ids or len(ids) != len(set(ids)):
            return dict(UNAVAILABLE)
        outcome = sess.DurableOutcome()
        def apply():
            old_queue = list(s.queue_pending)
            originals = [(row, dict(row)) for row in selected]
            old_revision = s.queue_revision
            try:
                if (s.readonly_session or worker._offline.is_illegal(s)
                        or any(not _eligible(s, row) for row in selected)):
                    raise ValueError("report unavailable")
                for row in selected:
                    row["reportConsumeIds"] = list(ids)
                    row["selfConsumeRequested"] = True
                chosen = {id(row) for row in selected}
                s.queue_pending = selected + [row for row in s.queue_pending if id(row) not in chosen]
                s.queue_revision += 1
                sess._save_body(s)
            except BaseException:
                s.queue_pending = old_queue
                for row, original in originals:
                    row.clear(); row.update(original)
                s.queue_revision = old_revision
                raise
        try:
            await sess._persist_async_outcome(s.id, apply, outcome)
        except asyncio.CancelledError:
            if not outcome.succeeded:
                raise
        except Exception:
            return {"ok": False, "error": "persist_failed", "retryable": True}
    # Reuse self-consume's actual Force/Steer routing, never enqueue new bodies.
    result = await _consume(session_id, ids[0], requested_batch=True)
    async with worker.queue_lock(session_id):
        receipt = _report_receipt(s, ids[0])
        if receipt.get("status") == "accepted" and not result.get("ok"):
            receipt.update(ok=False, status="unconfirmed")
        return receipt


async def consume(session_id: str, item_id: str) -> dict:
    s = sess.get(session_id)
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


async def _consume(session_id: str, item_id: str, *, requested_batch=False) -> dict:
    s = _own_session(session_id)
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
        if item.get("selfConsumeRequested") and not requested_batch:
            return {"ok": False, "status": "pending", "retryable": False}
        if not requested_batch and any(isinstance(it, dict) and it.get("selfConsumeRequested") and _eligible(s, it)
               for it in s.queue_pending):
            return {"ok": False, "status": "pending", "retryable": False}
        w = worker.find_alive_worker_by_session(session_id)
        worker_id, generation = (w.worker_id, w.generation) if w is not None else (None, None)
        route = worker.steer_delivery_route(w, s)
        native = route == "steer"
        revision = item.get("revision", 1)
        if not native and not requested_batch:
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
                and (requested_batch or (
                    worker.resolve_execution_mode(w.adapter, s) == "stream"
                    and getattr(w.adapter, "supports_native_steer", False)))):
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
