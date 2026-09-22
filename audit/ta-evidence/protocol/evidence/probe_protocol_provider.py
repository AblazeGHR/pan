"""Read-only deterministic probes for the provider -> worker -> history contract.

Never spawns a real model or CLI.  It drives the *real* worker stdout consumer
(`packages.core.worker._read_stdout`) with a protocol-shaped fake process, using
the real Session persistence model, and drives the real adapter projection
functions directly.

Output is JSON to stdout so the audit report can cite exact bytes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
from packages.core import worker  # noqa: E402
from packages.core.adapters.cbc import CbcAdapter  # noqa: E402
from packages.core.adapters.codex import CodexAdapter  # noqa: E402
from packages.core.adapters.codex import sessions as codex_sessions  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

class _FakeProcess:
    """Mimic the slice of asyncio.subprocess.Process the reader touches."""

    def __init__(self, events: list[dict]):
        blob = b"".join(
            (json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8") for e in events
        )
        self._blob = blob
        self._pos = 0
        self.returncode = 0
        self.pid = 4242
        self.stdout = self

    async def read(self, _size: int = -1) -> bytes:
        if self._pos >= len(self._blob):
            return b""
        chunk = self._blob[self._pos:self._pos + 65536]
        self._pos += len(chunk)
        return chunk


def _reset():
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


async def _noop_save(_s):
    return None


def run_stream(events: list[dict], *, adapter, result_seq=None, task_id=None,
               preloaded_history=None) -> dict[str, Any]:
    """Drive the real worker stdout consumer once and capture the outcome."""
    _reset()
    _sess.save_async = _noop_save  # type: ignore[assignment]
    session = _sess.Session(id="ses-probe", name="probe", adapter=adapter.name)
    if preloaded_history:
        session.history = [dict(row) for row in preloaded_history]
        session._hist_persisted = len(session.history)
        session._history_loaded = True
    _sess._cache[session.id] = session

    broadcasts: list[dict] = []

    async def capture(data):
        broadcasts.append(data)

    worker.set_broadcaster(capture)

    current = worker.Worker(
        worker_id="worker-probe",
        session_id=session.id,
        adapter=adapter,
        status="running",
        process=_FakeProcess(events),
        pending_signal=asyncio.Queue(),
    )
    if result_seq is not None:
        current._current_seq = result_seq
    if task_id is not None:
        current._current_task_id = task_id
        current._current_task_idempotent = True
    worker.workers[current.worker_id] = current

    asyncio.run(worker._read_stdout(current))

    return {
        "history": [dict(row) for row in session.history],
        "last_result": dict(session.last_result) if session.last_result else None,
        "terminal_results": [
            {k: v for k, v in row.items()}
            for row in (getattr(session, "terminal_results", None) or [])
        ],
        "history_revision": session.history_revision,
        "hist_persisted": getattr(session, "_hist_persisted", None),
        "hist_dirty": getattr(current, "_hist_dirty", None),
        "result_cursor": getattr(session, "result_cursor", None),
        "status": current.status,
        "stream_events": [b for b in broadcasts if b.get("type") == "worker.stream"],
        "terminal_events": [b for b in broadcasts if b.get("type") == "worker.result"],
        "all_broadcast_types": [b.get("type") for b in broadcasts],
    }


def _ev_stream_roles(out: dict) -> list[tuple[str, str]]:
    roles = []
    for frame in out["stream_events"]:
        event = frame.get("event") or {}
        for block in _blocks_of(event):
            roles.append((block["role"], block["content"]))
    return roles


def _blocks_of(event: dict) -> list[dict]:
    """Frontend/backend-extract-shaped block view of one live event."""
    if event.get("type") == "assistant":
        content = (event.get("message") or {}).get("content") or []
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                out.append({"role": "assistant", "content": part.get("text", "")})
            elif part.get("type") in ("thinking", "think"):
                out.append({"role": "thinking",
                            "content": part.get("thinking") or part.get("think") or ""})
            elif part.get("type") == "tool_use":
                out.append({"role": "tool",
                            "content": f"{part.get('name')}({json.dumps(part.get('input') or {}, ensure_ascii=True, separators=(',', ':'))})"})
        return out
    return []


# ── P1: Codex live vs canonical tool projection ─────────────────────────────

def p1_codex_projection() -> dict:
    adapter = CodexAdapter()

    def live(item_is_type_assistant_event, name, args, item_id):
        return adapter.extract_assistant_blocks({
            "type": "assistant",
            "item_id": item_id,
            "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
        })

    cases = {}

    def single(blocks):
        return blocks[0] if blocks else None

    # command execution, live wrapper path (input {command, output})
    cases["command"] = {
        "live": single(live(None, "Command", {"command": "echo hi", "output": "hi"}, "c1")),
        "canonical": codex_sessions._item_to_block(
            {"id": "c1", "type": "commandExecution", "command": "echo hi",
             "aggregated_output": "hi"}),
    }
    # mcp tool call with structured result
    structured = {"content": [{"type": "text", "text": "ok"}], "structuredContent": None}
    cases["mcp"] = {
        "live": single(live(None, "pan_probe", {"x": 1, "result": structured}, "m1")),
        "canonical": codex_sessions._item_to_block(
            {"id": "m1", "type": "mcpToolCall", "tool": "pan_probe",
             "arguments": {"x": 1}, "result": structured}),
    }
    # file change
    fc = {"id": "f1", "type": "fileChange",
          "changes": [{"path": "a.txt", "kind": "update"}], "status": "completed"}
    cases["filechange"] = {
        "live": single(live(None, "FileChange", fc, "f1")),
        "canonical": codex_sessions._item_to_block(fc),
    }
    # unknown / future native item
    future = {"id": "u1", "type": "futureNativeItem", "summary": "kept"}
    cases["unknown_native"] = {
        "live": adapter.extract_assistant_blocks({"type": "item.completed", "item": future}),
        "canonical": codex_sessions._item_to_block(future),
    }
    # exec-fallback live path: raw item.completed for an MCP tool call.  The
    # app-server bridge maps this to a canonical tool_use, but the legacy exec
    # path hands the raw item straight to the adapter.
    raw_mcp = {"id": "x1", "type": "mcpToolCall", "tool": "pan_probe",
               "arguments": {"x": 1}, "result": "ok"}
    cases["exec_mcptoolcall_raw"] = {
        "live": single(adapter.extract_assistant_blocks(
            {"type": "item.completed", "item": raw_mcp})),
        "canonical": codex_sessions._item_to_block(raw_mcp),
    }
    raw_dynamic = {"id": "x2", "type": "dynamicToolCall", "name": "pan_probe",
                   "input": {"y": 2}}
    cases["exec_dynamictoolcall_raw"] = {
        "live": single(adapter.extract_assistant_blocks(
            {"type": "item.completed", "item": raw_dynamic})),
        "canonical": codex_sessions._item_to_block(raw_dynamic),
    }
    # float-bearing args (integral float) — canonical vs JS frontend pyJsonDumps
    cases["float_args"] = {
        "live": single(live(None, "Wait", {"seconds": 30.0}, "t1")),
        "canonical": codex_sessions._item_to_block(
            {"id": "t1", "type": "sleep", "seconds": 30.0}),
        "frontend_pyJsonDumps": "Wait(" + _js_stringify_python_escaped({"seconds": 30.0}) + ")",
    }
    # app-server unknown native item: emitted as `codex.item.completed`.
    cases["appserver_unknown_item"] = {
        "live_event_type": "codex.item.completed",
        "persisted_live": adapter.is_assistant_event(
            {"type": "codex.item.completed", "item": future}),
        "canonical": codex_sessions._item_to_block(future),
    }

    summary = {}
    for name, case in cases.items():
        if "live_event_type" in case:
            summary[name] = {
                "live_event_type": case["live_event_type"],
                "persisted_live": case["persisted_live"],
                "canonical": case["canonical"],
            }
            continue
        same = case["live"] == case["canonical"]
        summary[name] = {
            "live": case["live"],
            "canonical": case["canonical"],
            "identical": same,
            "frontend": case.get("frontend_pyJsonDumps"),
        }
    return summary


def _js_stringify_python_escaped(value) -> str:
    """Reproduce the frontend `pyJsonDumps` (JSON.stringify + \\uXXXX escape).

    Node's JSON.stringify renders integral floats without a trailing `.0`, which
    Python's `json.dumps` keeps.  This helper emulates the *frontend* result for
    the same logical value so the two projections can be compared byte-wise.
    """
    import re

    def js_number(v):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return json.dumps(v)

    def render(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        if isinstance(v, (int, float)):
            return js_number(v)
        if isinstance(v, str):
            encoded = json.dumps(v, ensure_ascii=False)
            return re.sub(r"[\u007f-\uffff]",
                          lambda m: "\\u%04x" % ord(m.group(0)), encoded)
        if isinstance(v, list):
            return "[" + ",".join(render(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ",".join(
                render(str(k)) + ":" + render(x) for k, x in v.items()) + "}"
        return "null"

    return render(value)


# ── P2: worker terminal / history behaviour ─────────────────────────────────

def p2_worker_terminal() -> dict:
    codex = CodexAdapter()
    results: dict[str, Any] = {}

    # (a) baseline: assistant then result, same text
    results["a_baseline"] = run_stream([
        {"type": "assistant", "final": True, "item_id": "a1",
         "message": {"content": [{"type": "text", "text": "A"}]}},
        {"type": "result", "is_error": False, "result": "A"},
    ], adapter=codex, result_seq=7, task_id="task-7")

    # (b) final answer followed by a tool item, then result = last agent text
    results["b_tool_last"] = run_stream([
        {"type": "assistant", "final": True, "item_id": "a1",
         "message": {"content": [{"type": "text", "text": "A"}]}},
        {"type": "assistant", "final": True, "item_id": "t1",
         "message": {"content": [{"type": "tool_use", "name": "Command",
                                  "input": {"command": "ls"}}]}},
        {"type": "result", "is_error": False, "result": "A"},
    ], adapter=codex, result_seq=8, task_id="task-8")

    # (c) result before the last assistant message (late final text)
    results["c_result_then_assistant"] = run_stream([
        {"type": "assistant", "final": True, "item_id": "a1",
         "message": {"content": [{"type": "text", "text": "partial"}]}},
        {"type": "result", "is_error": False, "result": "final answer"},
        {"type": "assistant", "final": True, "item_id": "a2",
         "message": {"content": [{"type": "text", "text": "final answer"}]}},
    ], adapter=codex, result_seq=9, task_id="task-9")

    # (d) aggregate result that is longer than the last assistant block
    results["d_aggregate_result"] = run_stream([
        {"type": "assistant", "final": True, "item_id": "a1",
         "message": {"content": [{"type": "text", "text": "first half "}]}},
        {"type": "assistant", "final": True, "item_id": "a2",
         "message": {"content": [{"type": "text", "text": "second half"}]}},
        {"type": "result", "is_error": False, "result": "first half second half"},
    ], adapter=codex, result_seq=10, task_id="task-10")

    # (e) no prior history then empty result
    results["e_empty_result"] = run_stream([
        {"type": "result", "is_error": False, "result": ""},
    ], adapter=codex, result_seq=11, task_id="task-11")

    return results


# ── P3: CBC adapter shapes ───────────────────────────────────────────────────

def p3_cbc_shapes() -> dict:
    cbc = CbcAdapter()
    out: dict[str, Any] = {}

    text_event = {"type": "assistant", "session_id": "cbc-1",
                  "message": {"content": [{"type": "text", "text": "hello"}]}}
    out["text_blocks"] = cbc.extract_assistant_blocks(text_event)
    out["text_is_assistant_event"] = cbc.is_assistant_event(text_event)

    thinking_event = {"type": "assistant", "session_id": "cbc-1",
                      "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}}
    out["thinking_blocks"] = cbc.extract_assistant_blocks(thinking_event)

    # variant: provider emits thinking text under `text` (seen for some CLIs)
    thinking_text_event = {"type": "assistant", "session_id": "cbc-1",
                           "message": {"content": [{"type": "thinking", "text": "hmm"}]}}
    try:
        out["thinking_text_blocks"] = cbc.extract_assistant_blocks(thinking_text_event)
        out["thinking_text_error"] = None
    except Exception as exc:  # noqa: BLE001
        out["thinking_text_blocks"] = None
        out["thinking_text_error"] = f"{type(exc).__name__}: {exc}"

    tool_event = {"type": "assistant", "session_id": "cbc-1",
                  "message": {"content": [{"type": "tool_use", "name": "Read",
                                           "input": {"file_path": "a.txt", "limit": 10.0}}]}}
    out["tool_blocks"] = cbc.extract_assistant_blocks(tool_event)

    # "final" attribute: nowhere in the cbc live path sets it
    out["final_attr_present_in_adapter_source"] = "final" in Path(
        CbcAdapter.extract_assistant_blocks.__code__.co_filename).read_text(encoding="utf-8")
    return out


# ── P4: history cursor / revision behavior ───────────────────────────────────

def p4_history_cursor() -> dict:
    codex = CodexAdapter()
    # preload canonical history with 2 rows so the terminal append is visible
    out = run_stream([
        {"type": "assistant", "final": True, "item_id": "a1",
         "message": {"content": [{"type": "text", "text": "tail agent"}]}},
        {"type": "assistant", "final": True, "item_id": "t1",
         "message": {"content": [{"type": "tool_use", "name": "Command",
                                  "input": {"command": "ls"}}]}},
        {"type": "result", "is_error": False, "result": "tail agent"},
    ], adapter=codex, result_seq=12, task_id="task-12",
        preloaded_history=[{"role": "user", "content": "q"}])
    return {
        "history": out["history"],
        "hist_persisted": out["hist_persisted"],
        "history_revision": out["history_revision"],
        "hist_dirty": out["hist_dirty"],
        "last_result": out["last_result"],
    }


# ── P5: CBC through the real worker consumer ─────────────────────────────────

def p5_cbc_worker() -> dict:
    cbc = CbcAdapter()
    out: dict[str, Any] = {}

    # CBC: thinking + text in one assistant message, then a tool message, then
    # the terminal result which repeats the final assistant text.
    out["thinking_then_text_then_result"] = run_stream([
        {"type": "assistant", "session_id": "cbc-1", "message": {"content": [
            {"type": "thinking", "thinking": "reasoning..."},
            {"type": "text", "text": "final answer"},
        ]}},
        {"type": "result", "is_error": False, "result": "final answer"},
    ], adapter=cbc, result_seq=21, task_id="cbc-task-21")

    # CBC: a tool-only assistant message then result with the real answer text
    out["tool_only_then_result"] = run_stream([
        {"type": "assistant", "session_id": "cbc-1", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}},
        ]}},
        {"type": "result", "is_error": False, "result": "done reading"},
    ], adapter=cbc, result_seq=22, task_id="cbc-task-22")

    # CBC: two assistant messages with the same text (identical tool retry)
    out["duplicate_tool_text"] = run_stream([
        {"type": "assistant", "session_id": "cbc-1", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}},
        ]}},
        {"type": "assistant", "session_id": "cbc-1", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}},
        ]}},
        {"type": "result", "is_error": False, "result": ""},
    ], adapter=cbc, result_seq=23, task_id="cbc-task-23")

    for key, value in out.items():
        value["stream_final_flags"] = [
            (frame.get("event") or {}).get("final")
            for frame in value["stream_events"]
        ]
        value["stream_item_ids"] = [
            (frame.get("event") or {}).get("item_id")
            for frame in value["stream_events"]
        ]
    return out


# ── P6: CBC live projection vs CBC canonical history projection ──────────────

def p6_cbc_live_vs_history() -> dict:
    from packages.core.adapters.cbc import sessions as cbc_sessions

    cbc = CbcAdapter()
    out: dict[str, Any] = {}

    # tool call: same logical content, live assistant block vs JSONL function_call
    live_tool = cbc.extract_assistant_blocks({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Read",
                                 "input": {"path": "a.txt", "limit": 10}}]},
    })
    hist_tool = cbc_sessions._event_to_block({
        "type": "function_call", "name": "Read",
        "arguments": {"path": "a.txt", "limit": 10},
    })
    out["tool"] = {
        "live": live_tool,
        "history": hist_tool,
        "identical": live_tool == [hist_tool] if hist_tool else False,
    }

    # non-ASCII / nested tool args
    live_tool_cn = cbc.extract_assistant_blocks({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Write",
                                 "input": {"content": "中文😀"}}]},
    })
    hist_tool_cn = cbc_sessions._event_to_block({
        "type": "function_call", "name": "Write",
        "arguments": {"content": "中文😀"},
    })
    out["tool_non_ascii"] = {
        "live": live_tool_cn,
        "history": hist_tool_cn,
        "identical": live_tool_cn == [hist_tool_cn] if hist_tool_cn else False,
    }

    # two text blocks + surrounding whitespace
    live_text = cbc.extract_assistant_blocks({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": " line1\n"},
            {"type": "text", "text": "line2 "},
        ]},
    })
    hist_text = cbc_sessions._event_to_block({
        "type": "message", "role": "assistant",
        "content": [
            {"type": "text", "text": " line1\n"},
            {"type": "text", "text": "line2 "},
        ],
    })
    out["multi_text_block"] = {
        "live": live_text,
        "history": hist_text,
        "identical": live_text == [hist_text] if hist_text else False,
    }

    # reasoning / thinking
    live_think = cbc.extract_assistant_blocks({
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": "hmm"}]},
    })
    hist_think = cbc_sessions._event_to_block({
        "type": "reasoning", "content": [{"type": "text", "text": "hmm"}],
    })
    out["reasoning"] = {
        "live": live_think,
        "history": hist_think,
        "identical": live_think == [hist_think] if hist_think else False,
    }

    # long tool args: history truncates at 500 chars, live does not
    big = {"blob": "x" * 600}
    live_big = cbc.extract_assistant_blocks({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Write", "input": big}]},
    })
    hist_big = cbc_sessions._event_to_block({
        "type": "function_call", "name": "Write", "arguments": big,
    })
    out["long_args"] = {
        "live_len": len(live_big[0]["content"]) if live_big else None,
        "history_len": len(hist_big["content"]) if hist_big else None,
    }

    return out


# ── P7: CBC `result` vs last assistant block alignment ───────────────────────

def p7_cbc_result_alignment() -> dict:
    cbc = CbcAdapter()
    out: dict[str, Any] = {}

    def _a(blocks):
        return {"type": "assistant", "session_id": "cbc-1", "message": {"content": blocks}}

    def run(name, events, result):
        res = run_stream(events, adapter=cbc, result_seq=31, task_id="cbc-31")
        assistants = [r["content"] for r in res["history"] if r.get("role") == "assistant"]
        out[name] = {
            "history": res["history"],
            "assistant_rows": assistants,
            "result": result,
            "result_equals_last_assistant_row": bool(assistants) and assistants[-1] == result,
            "result_equals_join_of_all_assistant_rows": "".join(assistants) == result,
            "result_equals_last_live_text_block":
                (res["stream_events"][-1]["event"].get("message", {})
                 .get("content", [{}])[-1].get("text") if res["stream_events"] else None) == result,
        }

    # one assistant message holding TWO text blocks; cbc `result` joins them
    run("multi_text_block_message", [
        _a([{"type": "text", "text": "part1 "}, {"type": "text", "text": "part2"}]),
        {"type": "result", "is_error": False, "result": "part1 part2"},
    ], "part1 part2")

    # two separate assistant messages; result repeats only the last
    run("two_assistant_messages", [
        _a([{"type": "text", "text": "first"}]),
        _a([{"type": "text", "text": "second"}]),
        {"type": "result", "is_error": False, "result": "second"},
    ], "second")

    # last assistant message is tool-only
    run("tool_only_last_message", [
        _a([{"type": "text", "text": "let me look"}]),
        _a([{"type": "tool_use", "name": "Read", "input": {"file_path": "a.txt"}}]),
        {"type": "result", "is_error": False, "result": "let me look"},
    ], "let me look")

    # result repeats an EARLIER message, not the last
    run("result_repeats_earlier_text", [
        _a([{"type": "text", "text": "A"}]),
        _a([{"type": "text", "text": "B"}]),
        {"type": "result", "is_error": False, "result": "A"},
    ], "A")

    # explicit thinking block then text block in one message
    run("thinking_then_text", [
        _a([{"type": "thinking", "thinking": "reason"},
            {"type": "text", "text": "answer"}]),
        {"type": "result", "is_error": False, "result": "answer"},
    ], "answer")

    return out


# ── P8: real-shape identity trace (where cbc identity is dropped) ────────────

def p8_identity_trace() -> dict:
    import re

    from packages.core.adapters.cbc import sessions as cbc_sessions
    from packages.web import server as srv

    cbc = CbcAdapter()
    out: dict[str, Any] = {}

    # Real JSONL envelope shape (verified against 103 real transcripts:
    # identity is top-level `id` / `callId`; nested `message` holds only `usage`).
    real_message_event = {
        "type": "message", "role": "assistant",
        "id": "e6a1c0f2-1111-4222-8333-444455556666",
        "parentId": "9f8e7d6c-0000-1111-2222-333344445555",
        "sessionId": "cbc-session", "cwd": "D:/x", "status": "completed",
        "message": {"usage": {"input_tokens": 1}},
        "content": [{"type": "output_text", "text": "hello",
                     "providerData": {"model": "hy3"}}],
    }
    real_function_call = {
        "type": "function_call", "name": "Read",
        "id": "5c4b3a29-aaaa-bbbb-cccc-ddddeeeeffff",
        "callId": "toolu_01AbCdEfGhIjKlMnOpQrStUv",
        "parentId": "e6a1c0f2-1111-4222-8333-444455556666",
        "arguments": {"file_path": "a.txt"},
        "message": {"usage": {}},
    }
    real_reasoning = {
        "type": "reasoning", "id": "aa11bb22-cccc-dddd-eeee-ffff00001111",
        "parentId": "e6a1c0f2-1111-4222-8333-444455556666",
        "content": [{"type": "text", "text": "because"}],
        "rawContent": [{"type": "text", "text": "because"}],
    }

    hist_msg = cbc_sessions._event_to_block(real_message_event)
    hist_fc = cbc_sessions._event_to_block(real_function_call)
    hist_reason = cbc_sessions._event_to_block(real_reasoning)
    out["canonical_message_block"] = hist_msg
    out["canonical_function_call_block"] = hist_fc
    out["canonical_reasoning_block"] = hist_reason
    out["canonical_kept_any_id"] = any(
        isinstance(b, dict) and any(k in b for k in ("nativeItemId", "messageId", "callId", "id"))
        for b in (hist_msg, hist_fc, hist_reason) if b
    )

    # Live Anthropic-shaped event (shape verified from the archived real probe
    # script docs/archive/cbc-mcp-experiments/multiround_probe.py).  Whether the
    # real CLI puts `message.id`/`tool_use.id` on this frame is NOT yet verified;
    # we feed the field present to show it would still be dropped.
    live_event_with_ids = {
        "type": "assistant", "session_id": "cbc-1",
        "message": {
            "id": "msg_01XyZ",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "toolu_01AbCdEf", "name": "Read",
                 "input": {"file_path": "a.txt"}},
            ],
        },
    }
    live_blocks = cbc.extract_assistant_blocks(live_event_with_ids)
    out["live_blocks"] = live_blocks
    out["live_kept_any_id"] = any(
        isinstance(b, dict) and any(k in b for k in ("nativeItemId", "messageId", "blockId"))
        for b in live_blocks
    )

    # worker broadcast keeps the raw event, so `message.id` does reach the socket
    # only inside the opaque provider payload (see P5 worker probes).

    # wire identity: a cbc-style history row has no messageId -> index-derived
    rows = srv._api_history(
        "cbc-session",
        [{"role": "user", "content": "q"},
         {"role": "assistant", "content": "analysis"},
         {"role": "tool", "content": 'Read({"file_path":"a.txt"})'},
         {"role": "assistant", "content": "final"}],
        start=0,
        history_epoch="epoch-1",
        include_identity=True,
    )
    out["wire_message_ids"] = [
        {"role": r["role"], "messageId": r.get("messageId")} for r in rows
    ]
    out["wire_ids_are_index_derived"] = all(
        str(r.get("messageId", "")).startswith("legacy:cbc-session:epoch-1:")
        for r in rows
    )

    # grep proof: the cbc adapter/session modules never read any native id
    src_dir = Path(__file__).resolve().parent.parent / "packages" / "core" / "adapters" / "cbc"
    reads_id = {}
    for path in sorted(src_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        reads_id[path.name] = {
            "get('id')": len(re.findall(r"\.get\(\s*['\"]id['\"]", text)),
            "callId": len(re.findall(r"callId", text)),
            "parentId": len(re.findall(r"parentId", text)),
            "nativeItemId": len(re.findall(r"nativeItemId", text)),
        }
    out["cbc_modules_reading_ids"] = reads_id
    return out


def main() -> int:
    report = {
        "P1_codex_projection": p1_codex_projection(),
        "P2_worker_terminal": p2_worker_terminal(),
        "P3_cbc_shapes": p3_cbc_shapes(),
        "P4_history_cursor": p4_history_cursor(),
        "P5_cbc_worker": p5_cbc_worker(),
        "P6_cbc_live_vs_history": p6_cbc_live_vs_history(),
        "P7_cbc_result_alignment": p7_cbc_result_alignment(),
        "P8_identity_trace": p8_identity_trace(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
