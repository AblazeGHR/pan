#!/usr/bin/env python
"""Narrow follow-up: why does POST /api/v1/acp answer 406 on a --serve process?

Tries accept/content-type variants and a `--serve --acp` process, printing only
status codes and short bodies. Own process, own free loopback port, isolated
CODEBUDDY_CONFIG_DIR, cleaned up at the end.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_cbc_feasibility import (  # noqa: E402
    CBC_ENTRY, NODE, iso_env, new_workdirs, port_open, safe_stop_tree,
)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port: int, path: str, method="GET", body=None, headers=None, timeout=8):
    hdr = {"X-CodeBuddy-Request": "1"}
    if headers:
        hdr.update(headers)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=data, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "body": resp.read(400).decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read(400).decode("utf-8", "replace")}
    except Exception as exc:
        return {"error": repr(exc)}


def probe(extra_args: list[str], label: str) -> dict:
    root, cfg, ws = new_workdirs(f"acpv-{label}")
    env = iso_env(cfg)
    port = free_port()
    out = (Path(root) / "srv.out").open("w", encoding="utf-8")
    err = (Path(root) / "srv.err").open("w", encoding="utf-8")
    proc = subprocess.Popen([NODE, str(CBC_ENTRY), "--serve", "--host", "127.0.0.1",
                             "--port", str(port), "--auth", "none", *extra_args],
                            env=env, cwd=str(ws), stdout=out, stderr=err)
    res: dict = {"label": label, "args": extra_args, "port": port, "pid": proc.pid,
                 "create_time": psutil.Process(proc.pid).create_time()}
    try:
        deadline = time.time() + 30
        while time.time() < deadline and call(port, "/api/v1/health").get("status") != 200:
            time.sleep(1)
        res["health"] = call(port, "/api/v1/health")
        conn = call(port, "/api/v1/acp/connect", "POST", {}, {"Content-Type": "application/json"})
        res["connect"] = {"status": conn.get("status"), "body": "<redacted: token omitted>"}
        try:
            parsed = json.loads(conn.get("body", "{}"))
            cid = parsed.get("connectionId")
            res["connect"]["connectionId_present"] = bool(cid)
            res["connect"]["sessionToken_present"] = bool(parsed.get("sessionToken"))
        except Exception:
            cid = None
        res["connection_id"] = bool(cid)
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": 1, "clientCapabilities": {}}}
        variants = {
            "both": {"Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json", "acp-connection-id": cid or ""},
            "sse_only": {"Accept": "text/event-stream", "Content-Type": "application/json",
                         "acp-connection-id": cid or ""},
            "json_only": {"Accept": "application/json", "Content-Type": "application/json",
                          "acp-connection-id": cid or ""},
        }
        res["variants"] = {}
        for k, h in variants.items():
            v = call(port, "/api/v1/acp", "POST", rpc, h, timeout=6)
            res["variants"][k] = {"status": v.get("status"),
                                  "body_head": (v.get("body") or v.get("error") or "")[:400]}
        g = call(port, "/api/v1/acp", "GET",
                 headers={"Accept": "application/json, text/event-stream",
                          "acp-connection-id": cid or ""}, timeout=4)
        res["get_sse"] = {"status": g.get("status"),
                          "body_head": (g.get("body") or g.get("error") or "")[:200]}
    finally:
        res["cleanup"] = safe_stop_tree(proc.pid, res["create_time"], label=label,
                                        scope_markers=[str(root)], popen=proc)
        res["port_free_after"] = not port_open(port)
        res["stderr_tail"] = Path(root, "srv.err").read_text(
            encoding="utf-8", errors="replace")[-800:]
        res["stdout_tail"] = Path(root, "srv.out").read_text(
            encoding="utf-8", errors="replace")[-800:]
    return res


def main() -> int:
    out = {"plain_serve": probe([], "plain"),
           "serve_with_acp": probe(["--acp"], "withacp")}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
