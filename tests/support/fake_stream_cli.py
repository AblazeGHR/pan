"""Small deterministic stream-json provider used only by the HTTP E2E test."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

_STDOUT_LOCK = threading.Lock()


def _append(record: dict) -> None:
    path = os.environ.get("PAN_E2E_FAKE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _emit(record: dict) -> None:
    with _STDOUT_LOCK:
        print(json.dumps(record, ensure_ascii=False), flush=True)


def _steer_receipts(session_id: str, messages: "queue.Queue[str]") -> None:
    """Read control requests while a gated turn still owns the main loop.

    This is a deterministic fixture for Pan's CBC control wire, not a real
    provider: it only proves the original queue item reaches Pan's Steer
    hand-off and receives an accepted receipt.
    """
    for line in sys.stdin:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "control_request":
            messages.put(line)
            continue
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        request_id = payload.get("request_id")
        _append({"kind": "steer", "pid": os.getpid(), "requestId": request_id,
                 "text": request.get("content_blocks", [{}])[0].get("text")
                 if request.get("content_blocks") else None,
                 "at": time.time()})
        _emit({"type": "control_response", "response": {
            "subtype": "success", "request_id": request_id,
            "response": {"steered": True, "session_id": session_id},
        }})
    messages.put(None)


def _gate_for(text: str) -> str | None:
    try:
        gates = json.loads(os.environ.get("PAN_E2E_GATE_MAP", "{}"))
    except json.JSONDecodeError:
        gates = {}
    scopes = {
        "hold-A": "real-fifo",
        "hold-recovery": "real-recovery",
        "child-report-1": "real-manager",
    }
    for token, gate in gates.items():
        if token in text and scopes.get(token, "") in os.path.basename(os.getcwd()):
            return str(gate)
    return None


def main() -> None:
    # The provider wire is UTF-8 regardless of Windows ANSI/pipe defaults.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    pid = os.getpid()
    _append({"kind": "init", "pid": pid, "cwd": os.getcwd(), "at": time.time(),
             "stdinEncoding": sys.stdin.encoding, "stdoutEncoding": sys.stdout.encoding})
    init_gate = os.environ.get("PAN_E2E_INIT_GATE")
    while init_gate and not Path(init_gate).exists():
        time.sleep(.01)
    if os.environ.get("PAN_E2E_INIT_FAIL") == "1":
        print("isolated fake invalid resume", flush=True)
        raise SystemExit(9)
    session_id = f"fake-{pid}"
    print(json.dumps({
        "type": "system", "subtype": "init", "session_id": session_id,
    }), flush=True)
    steer_mode = os.environ.get("PAN_E2E_STEER_MODE") == "1"
    if steer_mode:
        messages: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(
            target=_steer_receipts, args=(session_id, messages),
            name="fake-steer-reader", daemon=True,
        ).start()
        line_iter = iter(messages.get, None)
    else:
        line_iter = sys.stdin
    for line in line_iter:
        try:
            payload = json.loads(line)
            text = payload["message"]["content"][0]["text"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        _append({
            "kind": "received", "pid": pid, "cwd": os.getcwd(),
            "text": text, "at": time.time(),
        })
        if steer_mode:
            # Give Pan the native run identity before the turn is held so a
            # real control_request can bind expected_request_id.
            _emit({"type": "system", "subtype": "running",
                   "session_id": session_id,
                   "_requestId": f"run-{pid}"})
        gate = _gate_for(text)
        while gate and not Path(gate).exists():
            time.sleep(0.01)
        if os.environ.get('PAN_E2E_TAKEOVER_PROBE') == '1':
            (Path(os.environ['PAN_E2E_DATA_ROOT']) / f'fake-{pid}.resume.json').write_text(
                json.dumps([{'role': 'user', 'content': text}]), encoding='utf-8')
        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"ack:{text}"}]},
        }, ensure_ascii=False), flush=True)
        print(json.dumps({"type": "result", "result": text, "is_error": "fail-429" in text},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
