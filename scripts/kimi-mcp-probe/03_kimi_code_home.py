"""Probe 03 — does KIMI_CODE_HOME relocate the user dir so Pan can inject a
user-level mcp.json into an ISOLATED home (no touch of real ~/.kimi-code)?

Plan C verification: copy real config.toml into a temp KIMI_CODE_HOME, drop a
probe mcp.json there, run `kimi -p` with KIMI_CODE_HOME set, and check whether
the MCP tool was invoked.

Safety: only writes under the temp home + a temp workdir. Real ~/.kimi-code
is never modified.
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

MARKER = Path(tempfile.gettempdir()) / "pan_mcp_probe_home.marker"
PROMPT = "Call the pan_probe tool and tell me exactly what it returns, verbatim."
MODEL = "moonshot-cn/kimi-k2.6"


def main() -> int:
    if MARKER.exists():
        MARKER.unlink()

    tmp_home = Path(tempfile.mkdtemp(prefix="kimi-home-"))
    tmp_work = Path(tempfile.mkdtemp(prefix="kimi-work-"))

    # replicate minimal real config (provider/api_key) so kimi can auth
    (tmp_home / "config.toml").write_text(
        (REAL_HOME / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )

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
    (tmp_home / "mcp.json").write_text(
        json.dumps(mcp_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[03] temp home mcp.json -> {tmp_home / 'mcp.json'}", file=sys.stderr)

    try:
        cmd = [str(KIMI), "-p", PROMPT, "-m", MODEL, "--output-format", "stream-json"]
        print(f"[03] running with KIMI_CODE_HOME={tmp_home}", file=sys.stderr)
        proc = subprocess.run(
            cmd, cwd=str(tmp_work), capture_output=True, text=True, timeout=120,
            env={**os.environ, "KIMI_CODE_HOME": str(tmp_home)},
        )
        out = proc.stdout + "\n" + proc.stderr
        (Path(__file__).resolve().parent / "03_kimi_code_home.log").write_text(
            out, encoding="utf-8"
        )
        marker_hit = MARKER.exists()
        sentinel_hit = "PAN_MCP_OK" in out
        print(f"[03] exit={proc.returncode}", file=sys.stderr)
        print(f"[03] marker_created={marker_hit}  sentinel_in_output={sentinel_hit}", file=sys.stderr)
        print("RESULT_KIMI_CODE_HOME_MCP:", "PASS" if (marker_hit or sentinel_hit) else "FAIL",
              file=sys.stderr)
        return 0 if (marker_hit or sentinel_hit) else 1
    except subprocess.TimeoutExpired:
        print("[03] TIMEOUT", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)
        shutil.rmtree(tmp_work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
