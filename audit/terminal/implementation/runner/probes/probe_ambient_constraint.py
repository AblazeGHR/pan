"""只读探针：宿主 ambient Job 与 durable detach 能力（真实环境确证）。

- 查询自身 ``IsProcessInJob(GetCurrentProcess(), NULL)``（runner 从不把自己 assign
  进自有 guard Job，因此为真 = 宿主 ambient Job）；
- 尝试以 ``CREATE_BREAKAWAY_FROM_JOB`` 派生子进程：失败（通常 ERROR_ACCESS_DENIED=5）
  即证明宿主 Job **不允许逃逸**，进程内无法清除该限制；
- 汇总 ``runner.detect_durability_capability()``。

不触碰任何既有父进程；自建子进程自行退出（本脚本不杀进程）。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = _here
while not (REPO_ROOT / "packages").is_dir():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

CREATE_BREAKAWAY_FROM_JOB = 0x01000000

CHILD_CODE = (
    "import ctypes,os,json;"
    "r=ctypes.c_int(0);"
    "ctypes.WinDLL('kernel32').IsProcessInJob(ctypes.c_void_p(-1),None,ctypes.byref(r));"
    "print(json.dumps({'pid':os.getpid(),'in_any_job':bool(r.value)}))"
)


def main() -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    result = ctypes.c_int(0)
    ok = bool(k32.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(result)))

    normal = subprocess.run(
        [sys.executable, "-c", CHILD_CODE], capture_output=True, text=True, timeout=60
    )
    breakaway: dict = {}
    try:
        child = subprocess.run(
            [sys.executable, "-c", CHILD_CODE],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=CREATE_BREAKAWAY_FROM_JOB,
        )
        breakaway = {
            "spawned": True,
            "returncode": child.returncode,
            "child": child.stdout.strip(),
        }
    except OSError as exc:
        breakaway = {
            "spawned": False,
            "winerror": getattr(exc, "winerror", None),
            "error": type(exc).__name__,
        }
    except Exception as exc:  # noqa: BLE001
        breakaway = {"spawned": False, "error": type(exc).__name__}

    from packages.core.terminal import runner as runner_module

    capability = runner_module.detect_durability_capability()
    payload = {
        "is_process_in_job_query_ok": ok,
        "in_any_job": bool(result.value),
        "normal_child": normal.stdout.strip(),
        "breakaway_child": breakaway,
        "durability_capability": capability.as_dict(),
        "conclusion": (
            "host ambient Job present and breakaway refused: durable detach cannot be "
            "proven in this environment; runner must refuse detach (fail-closed)"
            if result.value and not breakaway.get("spawned")
            else "no ambient constraint observed in this environment"
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
