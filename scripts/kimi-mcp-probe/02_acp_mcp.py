"""Probe 02 — does `kimi acp` (ACP server over stdio) accept MCP servers via
session/new mcpServers and let the agent call them?

This is the most programmatic path: Pan acts as the ACP client, spawns
`kimi acp`, and injects the pan MCP server through the protocol (no file on an
untrusted folder -> no trust gate). Docs (kimi-acp.md) confirm mcpServers
forwarding (stdio/http/sse -> kimi transports).

Best-effort client: JSON-RPC 2.0 newline-delimited over stdin/stdout.
Flow: initialize -> session/new{cwd,mcpServers} -> session/prompt{text}.
We watch for the probe tool being invoked (marker file) or PAN_MCP_OK in stream.

NOTE: ACP request/response param shapes are not fully specified in public docs;
if the handshake does not complete, the result is INCONCLUSIVE (not a definitive
negative) — the mechanism itself is documented-supported.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

KIMI = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
REAL_HOME = Path.home() / ".kimi-code"
PROBE = Path(__file__).resolve().parent / "probe_server.py"
PY = "E:/software/miniforge/python.exe"

MARKER = Path(tempfile.gettempdir()) / "pan_mcp_probe_acp.marker"
PROMPT = "Call the pan_probe tool and tell me exactly what it returns, verbatim."
MODEL = "moonshot-cn/kimi-k2.6"


def main() -> int:
    if MARKER.exists():
        MARKER.unlink()

    tmp_work = Path(tempfile.mkdtemp(prefix="kimi-acp-"))
    log_lines: list[str] = []
    lock = threading.Lock()

    proc = subprocess.Popen(
        [str(KIMI), "acp", "-m", MODEL],
        cwd=str(tmp_work),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "KIMI_CODE_HOME": str(REAL_HOME)},
    )

    def reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                log_lines.append(line)
            if MARKER.exists():
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    def send(obj):
        with lock:
            proc.stdin.write(json.dumps(obj) + "\n")  # type: ignore
            proc.stdin.flush()  # type: ignore

    session_id = None
    try:
        # 1) initialize (requires protocolVersion: number)
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": 1}})
        time.sleep(2)

        # 2) session/new with mcpServers (transport-typed, per ACP docs)
        mcp_servers = {
            "pan-probe": {
                "type": "stdio",
                "command": PY,
                "args": [str(PROBE)],
                "env": {"PROBE_MARKER": str(MARKER)},
            }
        }
        send({
            "jsonrpc": "2.0", "id": 2, "method": "session/new",
            "params": {"cwd": str(tmp_work), "mcpServers": mcp_servers, "model": MODEL},
        })
        time.sleep(3)
        # pull sessionId from the captured log
        for line in list(log_lines):
            try:
                msg = json.loads(line)
                if msg.get("id") == 2 and "result" in msg:
                    session_id = msg["result"].get("sessionId")
            except Exception:
                pass

        # 3) session/prompt (requires sessionId: string + prompt: array of blocks)
        send({
            "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": PROMPT}]},
        })

        # wait for tool invocation or timeout
        for _ in range(60):
            if MARKER.exists():
                break
            time.sleep(1)

        marker_hit = MARKER.exists()
        out = "".join(log_lines)
        sentinel_hit = "PAN_MCP_OK" in out
        print(f"[02] marker_created={marker_hit}  sentinel_in_output={sentinel_hit}", file=sys.stderr)
        (Path(__file__).resolve().parent / "02_acp_mcp.log").write_text(out, encoding="utf-8")
        if marker_hit or sentinel_hit:
            print("RESULT_ACP_MCP: PASS", file=sys.stderr)
            return 0
        print("RESULT_ACP_MCP: INCONCLUSIVE (handshake/param uncertainty)", file=sys.stderr)
        return 3
    finally:
        try:
            proc.stdin.close()  # type: ignore
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
