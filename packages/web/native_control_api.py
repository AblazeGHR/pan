"""Session-native terminal gateway. Never exposes the app-server endpoint/token."""
from __future__ import annotations

import asyncio
import base64
import time

from fastapi import APIRouter, Request
from packages.core import session, worker
from packages.web.terminal_api import _enter, read_json_object, fail, ok, BodyError

router = APIRouter(prefix="/api/sessions/{session_id}/native-control")


def _target(request, session_id):
    _, rejected = _enter(request)
    if rejected is not None:
        return None, rejected
    s = session.get(session_id, load_history=False)
    if s is None:
        return None, fail(404, "session-not-found")
    if s.readonly_session or s.adapter != "codex" or s.adapter_config.get("native_control") is not True:
        return None, fail(409, "native-control-unavailable")
    return s, None


def _status(w):
    return {"workerId": w.worker_id if w else None,
            "mode": w.native_control_mode if w else "pan",
            "generation": w.native_control_generation if w else 0,
            "error": w.native_control_error if w else None}


@router.get("")
async def status(request: Request, session_id: str):
    _, rejected = _target(request, session_id)
    if rejected is not None:
        return rejected
    return ok(_status(worker.find_alive_worker_by_session(session_id)))


@router.post("/mode")
async def mode(request: Request, session_id: str):
    _, rejected = _target(request, session_id)
    if rejected is not None:
        return rejected
    try:
        body = await read_json_object(request, allow_empty=False)
    except (ValueError, TypeError, BodyError):
        return fail(422, "invalid-body")
    if set(body) != {"mode"} or not isinstance(body["mode"], str) or body["mode"] not in {"pan", "native"}:
        return fail(422, "invalid-mode")
    w = worker.find_alive_worker_by_session(session_id)
    if w is None:
        if body["mode"] == "pan":
            return ok(_status(None))
        # Fence durable queue admission before spawn schedules its consumer.
        worker.native_starting_sessions.add(session_id)
        try:
            w = await worker.create_worker(session_id)
            if not isinstance(w, str):
                w.native_control_mode = "switching"
        finally:
            worker.native_starting_sessions.discard(session_id)
        if isinstance(w, str):
            return fail(503, "native-worker-unavailable")
    deadline = time.monotonic() + 10
    while not w.native_kernel_ready and time.monotonic() < deadline:
        if w.process is None or w.process.returncode is not None:
            return fail(503, "native-worker-unavailable")
        await asyncio.sleep(.05)
    if not w.native_kernel_ready:
        return fail(503, "native-worker-starting")
    receipt = await worker.switch_native_control(w.worker_id, body["mode"])
    if receipt.get("ok") is not True:
        return fail(409, "native-switch-unconfirmed")
    return ok(_status(w))


@router.get("/output")
async def output(request: Request, session_id: str):
    _, rejected = _target(request, session_id)
    if rejected is not None:
        return rejected
    w = worker.find_alive_worker_by_session(session_id)
    if w is None:
        return fail(409, "native-worker-unavailable")
    cursor_text = request.query_params.get("cursor", "0")
    if not cursor_text.isascii() or not cursor_text.isdecimal() or len(cursor_text) > 20:
        return fail(422, "invalid-cursor")
    cursor = int(cursor_text)
    if cursor > (1 << 64) - 1:
        return fail(422, "invalid-cursor")
    if cursor > w.native_output_total:
        return fail(409, "native-cursor-ahead")
    first = w.native_output_total - len(w.native_output)
    start = max(cursor, first)
    chunk = bytes(w.native_output[start - first:start - first + 65536])
    return ok({**_status(w), "cursor": str(start + len(chunk)), "gap": cursor < first,
               "data": base64.b64encode(chunk).decode("ascii")})


@router.post("/write")
async def write(request: Request, session_id: str):
    _, rejected = _target(request, session_id)
    if rejected is not None:
        return rejected
    try:
        body = await read_json_object(request, allow_empty=False)
    except (ValueError, TypeError, BodyError):
        return fail(422, "invalid-body")
    w = worker.find_alive_worker_by_session(session_id)
    if w is None or body.get("workerId") != w.worker_id:
        return fail(409, "native-worker-changed")
    if type(body.get("generation")) is not int:
        return fail(422, "invalid-generation")
    if set(body) == {"workerId", "generation", "text"}:
        text = body["text"]
        try:
            valid_text = isinstance(text, str) and len(text.encode("utf-8")) <= 32768
        except UnicodeEncodeError:
            valid_text = False
        if not valid_text:
            return fail(422, "invalid-input")
        control = {"type": "native_input", "text": text, "generation": body["generation"]}
    elif set(body) == {"workerId", "generation", "rows", "cols"}:
        if (type(body["rows"]) is not int or type(body["cols"]) is not int
                or not 1 <= body["rows"] <= 500 or not 1 <= body["cols"] <= 1000):
            return fail(422, "invalid-size")
        control = {"type": "native_resize", "rows": body["rows"], "cols": body["cols"], "generation": body["generation"]}
    else:
        return fail(422, "unknown-field")
    receipt = await worker.write_native_control(w.worker_id, control)
    return ok({"accepted": True}) if receipt.get("ok") is True else fail(409, "native-write-unconfirmed")
