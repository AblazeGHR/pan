"""Synthetic stream CLI with concurrent controls and file-gated turn results."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from fake_stream_cli import _append, _gate_for

OUTPUT = threading.Lock()


def emit(value):
    with OUTPUT:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def turn(text):
    gate = _gate_for(text)
    while gate and not Path(gate).exists():
        time.sleep(0.005)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "ack:"+text}]}})
    emit({"type": "result", "result": text, "is_error": False})


def receipt(control):
    if control["text"] == "missing-receipt":
        return
    if control["text"] == "late-receipt":
        gate = Path(os.environ["PAN_E2E_DATA_ROOT"]) / "release-receipt"
        while not gate.exists():
            time.sleep(0.005)
    event = {"type": "pan.steer_receipt", "requestId": control["requestId"],
             "ok": True, "outcome": "accepted"}
    _append({"kind": "receipt", "text": control["text"], "at": time.time()})
    emit(event)


def main():
    _append({"kind": "init", "pid": os.getpid(), "cwd": os.getcwd(), "at": time.time()})
    emit({"type": "system", "subtype": "init", "session_id": f"fake-{os.getpid()}"})
    for line in sys.stdin:
        control = json.loads(line)
        if control.get("type") == "steer":
            _append({"kind": "control", "text": control["text"], "at": time.time()})
            threading.Thread(target=receipt, args=(control,), daemon=True).start()
        else:
            text = control["message"]["content"][0]["text"]
            _append({"kind": "received", "text": text, "pid": os.getpid(),
                     "cwd": os.getcwd(), "at": time.time()})
            threading.Thread(target=turn, args=(text,), daemon=True).start()


if __name__ == "__main__":
    main()
