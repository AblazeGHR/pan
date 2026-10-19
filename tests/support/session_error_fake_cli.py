"""Deterministic local provider for session-error-log E2E tests.

Supports CBC stream mode (stdin stream-json) and CBC one-shot mode (prompt as
the last argv).  No network, no real model.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _append(record: dict) -> None:
    path = os.environ.get("PAN_E2E_FAKE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _write_stdout_line(text: str) -> None:
    """Emit UTF-8 bytes regardless of the Windows pipe/console code page.

    Pan's Worker decodes provider stdout as UTF-8 (and the real Codex wrappers
    write UTF-8 bytes explicitly); a text-mode ``print`` would use cp936/gbk
    under a non-UTF8 Windows environment and turn Chinese into replacement
    characters.
    """
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _emit(event: dict) -> None:
    _write_stdout_line(json.dumps(event, ensure_ascii=False))


def _assistant(text: str) -> None:
    _emit({"type": "assistant",
           "message": {"content": [{"type": "text", "text": text}]}})


def _tool(name: str = "Command") -> None:
    _emit({"type": "assistant", "message": {"content": [{
        "type": "tool_use", "name": name,
        "input": {"command": "false", "result": "tool failed normally"},
    }]}})


def _raw_error(message: str) -> dict:
    return {
        "message": message,
        "type": "rate_limit",
        "code": "rate_limit_exceeded",
        "status": 429,
        "retryable": True,
        "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 429}},
        "additionalDetails": "retry-after=12",
    }


def _result_error(text: str, *, partial: str = "") -> None:
    _emit({
        "type": "result",
        "is_error": True,
        "turn_status": "failed",
        "result": partial,
        "error": _raw_error(text),
        "usage": {"input_tokens": 1, "output_tokens": 2},
    })


def _handle(text: str, mode: str) -> None:
    _append({"kind": "received", "text": text, "mode": mode,
             "pid": os.getpid(), "at": time.time()})
    if "err-crash" in text:
        _assistant("about to crash")
        os._exit(7)
    if "err-huge" in text:
        huge = "第一行：超大错误\n第二行：中文\n" + ("超长错误负载" * 8000) + "\n尾部"
        _assistant("partial answer before huge error")
        _result_error(huge, partial="partial answer before huge error")
        return
    if "err-oversize-error-first" in text:
        huge = "错" * 120000
        # error is the first key, so type/is_error are after the 256 KiB cap.
        _emit({"error": {"message": huge, "code": "rate_limit",
                         "retryable": True},
               "type": "result", "is_error": True, "turn_status": "failed",
               "result": "partial before error-first"})
        return
    if "err-oversize-is-error-first" in text:
        huge = "错" * 120000
        _emit({"is_error": True, "type": "result",
               "error": {"message": huge, "code": "rate_limit",
                         "retryable": True},
               "result": "partial before is_error-first"})
        return
    if "err-oversize" in text:
        oversize = "错" * 120000
        _assistant("partial answer before oversize error")
        _result_error(oversize, partial="partial answer before oversize error")
        return
    if "err-rawdiag" in text:
        _write_stdout_line("第一行 stderr 诊断：磁盘警告")
        _write_stdout_line("[second raw line] 重连日志")
        _assistant("partial answer before raw diagnostic error")
        _result_error("raw diagnostic scenario failed",
                      partial="partial answer before raw diagnostic error")
        return
    if "err-tool" in text:
        _emit({"type": "assistant", "message": {"content": [{
            "type": "thinking", "thinking": "checking the tool"}]}})
        _tool()
        _result_error("tool failed, task failed", partial="")
        return
    if "err-terminal" in text:
        _assistant("partial answer before failure")
        _result_error("上游 429：请稍后重试\n第二行：完整错误正文",
                      partial="partial answer before failure")
        return
    if "err-duplicate" in text:
        _assistant("duplicate terminal")
        _result_error("duplicate error", partial="duplicate terminal")
        _result_error("duplicate error", partial="duplicate terminal")
        return
    if "err-transient" in text:
        _emit({"type": "error", "error": {
            "message": "Reconnecting... 2/5\n瞬时重连",
            "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": None}},
        }})
        _assistant("recovered answer")
        _emit({"type": "result", "is_error": False, "result": "recovered answer"})
        return
    if "err-native" in text:
        _emit({"type": "pan.native_error", "error": "native-client-disconnected",
               "detail": {"transport": "websocket", "attempt": 2}})
        _assistant("native answer")
        _emit({"type": "result", "is_error": False, "result": "native answer"})
        return
    if "err-item" in text:
        _emit({"type": "codex.item_error",
               "item": {"type": "commandExecution", "id": "item-1",
                        "error": "tool failed normally"},
               "item_type": "commandexecution", "item_id": "item-1",
               "item_status": "failed",
               "error": {"message": "tool failed normally"}})
        _assistant("task still succeeded")
        _emit({"type": "result", "is_error": False, "result": "task still succeeded"})
        return
    if "err-recovered" in text:
        _emit({"type": "codex.transient_error_recovered",
               "error": {"message": "Reconnecting... 3/5",
                          "code": "response_stream_disconnected"},
               "error_text": "Reconnecting... 3/5",
               "turn_status": "completed"})
        _assistant("recovered after reconnect")
        _emit({"type": "result", "is_error": False,
               "result": "recovered after reconnect"})
        return
    if "err-cancelled" in text:
        _assistant("cancelled answer")
        _emit({"type": "result", "is_error": False, "cancelled": True,
               "result": ""})
        return
    if "oneshot-error" in text:
        # CBC exits 0 on a structured error and emits no result event.
        _emit({"type": "error", "error": {
            "message": "oneshot structured failure\n第二行",
            "type": "invalid_session",
            "code": "resume_not_found",
            "status": 404,
            "retryable": False,
        }})
        return
    _assistant(f"ack:{text}")
    _emit({"type": "result", "is_error": False, "result": text})


def main() -> None:
    mode = "stream" if "--input-format" in sys.argv else "oneshot"
    _append({"kind": "init", "mode": mode, "argv": sys.argv[1:],
             "pid": os.getpid(), "at": time.time()})
    _emit({"type": "system", "subtype": "init",
           "session_id": f"fake-{os.getpid()}", "model": "fake-model"})
    if mode == "oneshot":
        text = sys.argv[-1] if len(sys.argv) > 1 else ""
        _handle(text, mode)
        return
    # stdin is Pan's UTF-8 stream-json.  Use the binary stream so a cp936
    # default text encoding cannot corrupt or reject Chinese prompts.
    for raw in sys.stdin.buffer:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            line = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(line)
            text = payload["message"]["content"][0]["text"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        _handle(text, mode)


if __name__ == "__main__":
    main()
