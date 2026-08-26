"""Probe 01 — does USER-LEVEL ~/.kimi-code/mcp.json bypass folder-trust in -p mode?

Plan A verification: write a user-level mcp.json pointing at probe_server.py,
run `kimi -p` with a tool-calling prompt, and check whether the MCP tool
`pan_probe` was actually invoked (marker file + sentinel in stdout).

Safety:
- Backs up any existing ~/.kimi-code/mcp.json and restores it afterward.
- Uses a fresh temp workdir so no project-level mcp.json interferes.
- Short prompt; kimi run wrapped in timeout.

Outcome recorded to scripts/kimi-mcp-probe/01_user_level_mcp.log
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
USER_MCP = Path.home() / ".kimi-code" / "mcp.json"
PROBE = Path(__file__).resolve().parent / "probe_server.py"
PY = "E:/software/miniforge/python.exe"

MARKER = Path(tempfile.gettempdir()) / "pan_mcp_probe_user.marker"
PROMPT = "Call the pan_probe tool and tell me exactly what it returns, verbatim."
MODEL = "moonshot-cn/kimi-k2.6"


def main() -> int:
    MARKER.write_text("") if MARKER.exists() else None
    if MARKER.exists():
        MARKER.unlink()

    # backup existing user mcp.json
    backup = None
    if USER_MCP.exists():
        backup = USER_MCP.with_suffix(".mcp.json.bak")
        shutil.copy(USER_MCP, backup)

    tmp_work = Path(tempfile.mkdtemp(prefix="kimi-probe-user-"))
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
    USER_MCP.parent.mkdir(parents=True, exist_ok=True)
    USER_MCP.write_text(json.dumps(mcp_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[01] wrote user-level mcp.json -> {USER_MCP}", file=sys.stderr)

    try:
        cmd = [str(KIMI), "-p", PROMPT, "-m", MODEL, "--output-format", "stream-json"]
        print(f"[01] running: {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.run(
            cmd, cwd=str(tmp_work), capture_output=True, text=True, timeout=120,
            env={**os.environ, "KIMI_CODE_HOME": str(Path.home() / ".kimi-code")},
        )
        out = proc.stdout + "\n" + proc.stderr
        (Path(__file__).resolve().parent / "01_user_level_mcp.log").write_text(
            out, encoding="utf-8"
        )
        marker_hit = MARKER.exists()
        sentinel_hit = "PAN_MCP_OK" in out
        print(f"[01] exit={proc.returncode}", file=sys.stderr)
        print(f"[01] marker_created={marker_hit}  sentinel_in_output={sentinel_hit}", file=sys.stderr)
        print("RESULT_USER_LEVEL_MCP:", "PASS" if (marker_hit or sentinel_hit) else "FAIL",
              file=sys.stderr)
        return 0 if (marker_hit or sentinel_hit) else 1
    except subprocess.TimeoutExpired:
        print("[01] TIMEOUT", file=sys.stderr)
        return 2
    finally:
        # restore user mcp.json
        if backup and backup.exists():
            shutil.move(backup, USER_MCP)
            print(f"[01] restored user mcp.json from backup", file=sys.stderr)
        elif USER_MCP.exists():
            USER_MCP.unlink()
            print(f"[01] removed temp user mcp.json", file=sys.stderr)
        shutil.rmtree(tmp_work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
