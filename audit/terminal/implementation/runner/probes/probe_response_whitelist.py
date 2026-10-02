"""只读探针：IPC 响应 payload 冻结白名单会拒绝未知字段（为什么 runner 的
``_payload`` 必须把未知字段折进 ``detail``）。

背景：P1 开发中 detach 拒绝响应曾把 ``durability`` 放在顶层 → ``build_response``
抛 ``MalformedFrameError`` → 整条请求以 ``handler-error`` 结束。runner.py 现在
在构造 payload 时只输出白名单内字段，未知字段自动折入 ``detail``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = _here
while not (REPO_ROOT / "packages").is_dir():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal import ipc  # noqa: E402

REQUEST_ID = "r-0123456789abcdef"


def _attempt(payload: dict) -> dict:
    try:
        frame = ipc.build_response(REQUEST_ID, payload, ok=True)
        return {"accepted": True, "payload": frame.get("payload")}
    except ipc.ProtocolError as exc:
        return {"accepted": False, "error_type": type(exc).__name__, "error": str(exc)}


def main() -> int:
    payload = {
        "top_level_unknown_field_rejected": _attempt({"status": "detach-refused", "durability": {}}),
        "unknown_numeric_field_rejected": _attempt({"status": "ok", "duration": 1}),
        "valid_fields_accepted": _attempt(
            {
                "status": "ok",
                "detail": "{\"durability\":{\"capable\":false}}",
                "rows": 24,
                "cols": 80,
                "data_b64": "",
                "cursor": 0,
            }
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
