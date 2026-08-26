"""Probe 04 — project-level mcp.json contrast (control for Plan A).

Expected: in -p mode, project-level .kimi-code/mcp.json is NOT registered because
the folder is untrusted and -p cannot answer the trust prompt. So the probe tool
should NOT be invoked. This isolates the variable: same server file, only the
location (project vs user) differs.

Safety: writes only into a temp workdir; uses KIMI_CODE_HOME pointing at the REAL
home so auth works, but never modifies real mcp.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIMI = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
REAL_HOME = Path.home() / ".kimi-code"
PROBE = Path(__file__).resolve().parent / "probe_server.py"
PY = "E:/software/miniforge/python.exe"

MARKER = Path(tempfile.gettempdir()) / "pan_mcp_probe_project.marker"
PROMPT = "Call the pan_probe tool and tell me exactly what it returns, verbatim."
MODEL = "moonshot-cn/kimi-k2.6"


def main() -> int:
    if MARKER.exists():
        MARKER.unlink()

    tmp_work = Path(tempfile.mkdtemp(prefix="kimi-proj-"))
    mcp_cfg = {
        "mcpServers": {
            "pan-probe": {
                "command": PY,
                "args": [str(PROBE)],
                "env": {"PROBE_MARKER": str(MARKER)},
                "type": "stdio",
            }
        }
    }
    (tmp_work / ".kimi-code" / "mcp.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_work / ".kimi-code" / "mcp.json").write_text(
        json.dumps(mcp_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[04] wrote project-level mcp.json -> {tmp_work / '.kimi-code' / 'mcp.json'}",
          file=sys.stderr)

    try:
        cmd = [str(KIMI), "-p", PROMPT, "-m", MODEL, "--output-format", "stream-json"]
        print(f"[04] running from {tmp_work} (untrusted folder)", file=sys.stderr)
        proc = subprocess.run(
            cmd, cwd=str(tmp_work), capture_output=True, text=True, timeout=120,
            env={**os.environ, "KIMI_CODE_HOME": str(REAL_HOME)},
        )
        out = proc.stdout + "\n" + proc.stderr
        (Path(__file__).resolve().parent / "04_project_level.log").write_text(
            out, encoding="utf-8"
        )
        marker_hit = MARKER.exists()
        print(f"[04] exit={proc.returncode} marker_created={marker_hit}", file=sys.stderr)
        print("RESULT_PROJECT_LEVEL_MCP:", "INVOKED(unexpected)" if marker_hit else "NOT_INVOKED(expected)",
              file=sys.stderr)
        return 0
    except subprocess.TimeoutExpired:
        print("[04] TIMEOUT", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp_work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
