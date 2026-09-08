"""Small deterministic stream-json provider used only by the HTTP E2E test."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _append(record: dict) -> None:
    path = os.environ.get("PAN_E2E_FAKE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


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
    pid = os.getpid()
    _append({"kind": "init", "pid": pid, "cwd": os.getcwd(), "at": time.time()})
    print(json.dumps({
        "type": "system", "subtype": "init", "session_id": f"fake-{pid}",
    }), flush=True)
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            text = payload["message"]["content"][0]["text"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        _append({
            "kind": "received", "pid": pid, "cwd": os.getcwd(),
            "text": text, "at": time.time(),
        })
        gate = _gate_for(text)
        while gate and not Path(gate).exists():
            time.sleep(0.01)
        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"ack:{text}"}]},
        }, ensure_ascii=False), flush=True)
        print(json.dumps({"type": "result", "result": text, "is_error": False},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
