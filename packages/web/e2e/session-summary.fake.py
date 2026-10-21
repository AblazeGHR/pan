"""Gated deterministic stdout/stdin fixture, with no provider access."""
import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ["PAN_E2E_SUMMARY_GATES"])


def emit(event):
    print(json.dumps(event), flush=True)


emit({"type": "system", "subtype": "init", "session_id": f"fake-{os.getpid()}"})
for line in sys.stdin:
    payload = json.loads(line)
    text = payload.get("message", {}).get("content", [{}])[0].get("text", "")
    token = next((name for name in ["probe-one", "probe-interrupt", "probe-force"] if name in text), None)
    if not token:
        continue
    (root / f"{token}.received").write_text(str(os.getpid()), encoding="ascii")
    emit({"type": "assistant", "delta": True, "stream_text": f"{token} streaming",
          "content": f"{token} streaming", "item_id": token})
    while not (root / f"{token}.complete").exists():
        time.sleep(.01)
    emit({"type": "assistant", "item_id": token,
          "message": {"content": [{"type": "text", "text": f"{token} answer"}]}})
    emit({"type": "result", "result": f"{token} answer", "is_error": False})
