"""Scripted control client (the "browser connection" stand-in).

Connects to a runner endpoint as role=controller and executes a JSON op list.
The process exiting is the disconnect model: nothing is sent, the socket is
simply closed by process death, exactly like a browser tab losing its socket.

Ops:
  {"op":"input","data":"echo X\\r\\n"}
  {"op":"wait","marker":"X","timeout":5,"optional":false}
  {"op":"state","save":"before"}
  {"op":"sleep","seconds":1}
  {"op":"detach"}
  {"op":"stop"}
  {"op":"snapshot","tail":2000,"save":"tail"}
  {"op":"disconnect"}          -> clean exit, no further ops
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_lib as lib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--ops", required=True, help="JSON array or @file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--client-id", default=None)
    args = parser.parse_args()

    if args.ops.startswith("@"):
        ops = json.loads(Path(args.ops[1:]).read_text(encoding="utf-8"))
    else:
        ops = json.loads(args.ops)
    client_id = args.client_id or f"controller-{os.getpid()}"
    result = {
        "ok": True, "clientId": client_id, "pid": os.getpid(),
        "startedAt": time.time(), "results": [], "saved": {}, "error": None,
    }

    def finish(code: int) -> int:
        result["endedAt"] = time.time()
        lib.write_json(args.out, result)
        print(json.dumps({"ok": result["ok"], "clientId": client_id,
                          "ops": len(result["results"]), "error": result["error"]}))
        return code

    endpoint = lib.read_endpoint(args.data_root, timeout=10.0)
    conn, endpoint = lib.connect_runtime(
        args.data_root, endpoint["token"], "controller", client_id, timeout=10.0)
    result["saved"]["endpoint"] = {k: v for k, v in endpoint.items() if k != "token"}
    cursor = 0
    try:
        for index, op in enumerate(ops):
            name = op.get("op")
            entry = {"index": index, "op": name, "at": time.time()}
            if name == "input":
                response = conn.request({"cmd": "input", "data": op.get("data") or ""})
                entry["response"] = response
            elif name == "wait":
                response = conn.request({
                    "cmd": "wait", "marker": op.get("marker") or "",
                    "from": cursor, "timeout": op.get("timeout", 10.0),
                }, timeout=float(op.get("timeout", 10.0)) + 5.0)
                if response is None:
                    entry["found"] = False
                    response = {}
                entry["found"] = bool(response.get("found"))
                entry["to"] = response.get("to")
                if response.get("to") is not None:
                    cursor = int(response["to"])
                if not entry["found"] and not op.get("optional"):
                    result["ok"] = False
                    result["error"] = f"marker not found: {op.get('marker')!r}"
                    result["results"].append(entry)
                    return finish(1)
            elif name == "state":
                response = conn.request({"cmd": "state"})
                entry["state"] = response.get("state") if response else None
                if op.get("save"):
                    result["saved"][op["save"]] = entry["state"]
            elif name == "sleep":
                time.sleep(float(op.get("seconds", 0.5)))
            elif name == "detach":
                response = conn.request({"cmd": "detach"})
                entry["response"] = response
            elif name == "stop":
                response = conn.request({"cmd": "stop"})
                entry["response"] = response
            elif name == "snapshot":
                response = conn.request({"cmd": "snapshot", "tail": op.get("tail", 2000)})
                entry["data"] = response.get("data") if response else None
                if op.get("save"):
                    result["saved"][op["save"]] = entry["data"]
            elif name == "disconnect":
                entry["note"] = "client exits; socket closed by process death"
                result["results"].append(entry)
                return finish(0)
            else:
                result["ok"] = False
                result["error"] = f"unknown op: {name}"
                result["results"].append(entry)
                return finish(1)
            result["results"].append(entry)
        return finish(0)
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        return finish(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
