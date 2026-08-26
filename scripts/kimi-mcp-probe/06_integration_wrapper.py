"""Plan C 端到端验证（worker 级，最贴近 Pan worker 真实调用链）。

直接构造 Pan worker 实际使用的命令（KimiAdapter.build_spawn_args → 包含
--kimi-home 的 wrapper 命令），把一条消息喂给 wrapper 的 stdin，验证：
  kimi 在 KIMI_CODE_HOME 隔离 HOME 下加载 mcp.json → 模型真实调用 MCP 工具。

探针 server 复用 probe_server.py（pan_probe 工具，被调用时写 marker 文件）。
这是 Pan 编排走的非交互 -p 路径的等价复现。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
from packages.core.session import Session
from packages.core.adapters.kimi.adapter import KimiAdapter

PROBE = Path(__file__).resolve().parent / "probe_server.py"
PY = "E:/software/miniforge/python.exe"
MARKER = Path(tempfile.gettempdir()) / "pan_mcp_c_integration.marker"
PROMPT = "Call the pan_probe tool and tell me exactly what it returns, verbatim."
MODEL = "moonshot-cn/kimi-k2.6"


def main() -> int:
    if MARKER.exists():
        MARKER.unlink()
    workdir = Path(tempfile.mkdtemp(prefix="kimi-c-int-"))

    s = Session(id="ses_c_integration_001", name="c-integ", adapter="kimi",
                model=MODEL, workdir=str(workdir))
    s.adapter_config["mcp_servers"] = [{
        "name": "pan", "type": "stdio",
        "command": PY, "args": [str(PROBE)],
        "env": {"PROBE_MARKER": str(MARKER)},
    }]

    a = KimiAdapter()
    args = a.build_spawn_args(s)
    print("[integ] spawn args:", args, file=sys.stderr)

    home = Path(s.adapter_config["kimi_home_dir"])
    print("[integ] HOME:", home, "exists:", home.exists(), file=sys.stderr)
    print("[integ] HOME config.toml:", (home / "config.toml").exists(),
          "mcp.json:", (home / "mcp.json").exists(), file=sys.stderr)

    # 复现 worker：subprocess 启动 wrapper，env 设 PAN_KIMI_CWD 作为 kimi cwd
    env = {**os.environ, "PAN_KIMI_CWD": str(workdir)}
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env, text=True, bufsize=1,
    )
    try:
        # 喂一条消息（与 worker 经 wrapper stdin 发送的格式一致）
        proc.stdin.write(json.dumps({"text": PROMPT}) + "\n")
        proc.stdin.flush()

        tool_seen = False
        marker_hit = False
        deadline = time.time() + 120
        for line in proc.stdout:
            line = line.rstrip("\n")
            # 实时观察是否有 tool_use / pan_probe 调用
            if ("pan_probe" in line) or ("tool_use" in line) or ('"tool"' in line):
                tool_seen = True
            if MARKER.exists():
                marker_hit = True
                break
            if time.time() > deadline:
                break
        # 兜底再检查 marker
        marker_hit = marker_hit or MARKER.exists()
        print(f"[integ] tool_seen_in_stream={tool_seen} marker_created={marker_hit}",
              file=sys.stderr)
        print("RESULT_INTEGRATION:", "PASS" if marker_hit else "FAIL", file=sys.stderr)
        return 0 if marker_hit else 1
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
