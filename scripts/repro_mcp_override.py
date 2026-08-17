"""Reproduce _consumer_mcp's cli_session_id override behavior.

Simulates worker.py:607-675 (MCP one-shot path):
1. spawn cbc with --resume <cli_session_id>
2. parse output, capture system/init session_id
3. unconditionally set s.cli_session_id = captured id

Goal: confirm whether a session whose cli_session_id points to an
invalid/non-resumable id gets silently overwritten (or stays broken).
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))

def _resolve_cbc_argv():
    """Mirror CbcAdapter._resolve_cbc_argv: resolve npm shim to node entry."""
    path = shutil.which("cbc")
    if path and path.lower().endswith((".cmd", ".bat")):
        shim_dir = os.path.dirname(os.path.abspath(path))
        node_exe = os.path.join(shim_dir, "node.exe")
        if not os.path.exists(node_exe):
            node_exe = "node"
        import glob as _glob
        candidates = [
            os.path.join(shim_dir, "node_modules", p, "bin", name)
            for p in ("@tencent-ai/codebuddy-code", "@tencent-ai/codebuddy")
            for name in ("codebuddy", "codebuddy.js")
        ]
        if not any(os.path.exists(c) for c in candidates):
            hits = _glob.glob(os.path.join(shim_dir, "node_modules", "*", "*", "bin", "codebuddy*"))
            candidates += hits
        for c in candidates:
            if os.path.exists(c):
                return [node_exe, c]
        return [path]
    return [path or "cbc"]

CBC = _resolve_cbc_argv() + ["-p", "--output-format", "stream-json", "-y",
       "--model", "deepseek-v4-flash", "--permission-mode", "bypassPermissions"]


async def run_one(cwd, resume_id, label):
    args = list(CBC)
    if resume_id:
        args += ["--resume", resume_id]
    args.append("reply with the single word: ok")
    print(f"\n=== {label}: --resume {resume_id!r} ===")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    output = b""
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=120)
            if not chunk:
                break
            output += chunk
            if len(output) > 64 * 1024 * 1024:
                proc.kill()
                print("  [OUTPUT>64MB, killed]")
                break
    except asyncio.TimeoutError:
        proc.kill()
        print("  [TIMEOUT]")
    rc = await proc.wait()
    print(f"  raw output bytes={len(output)} first_line={output.decode(errors='replace').splitlines()[:1]}")
    if output:
        for line in output.decode(errors='replace').splitlines():
            if '"type":"system"' in line or '"type":"result"' in line or '"type":"error"' in line:
                print(f"  EVENT: {line[:200]}")

    # worker.py:607-633 parsing logic
    cli_session_id = None
    result_text = ""
    for line in output.decode(errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = event.get("type", "")
        if t == "result":
            result_text = event.get("result", "")
        elif t == "system" and event.get("subtype") == "init":
            cli_session_id = event.get("session_id")

    # worker.py:655-674
    if not result_text and rc not in (None, 0):
        status = "error"
        result = f"cbc exited with code {rc}"
    else:
        status = "done" if result_text else "error"
        result = result_text or "(no output)"

    print(f"  returncode={rc} captured_init_session_id={cli_session_id!r}")
    print(f"  status={status} result={result!r}")
    print(f"  => worker would set s.cli_session_id = {cli_session_id!r}")
    return cli_session_id, status, result


async def main():
    # Case 1: valid resume id (like a correctly-bound session) — cwd = Pan root
    await run_one(r"D:\project\Pan", "ee06ff1e-f7ff-43f7-9877-ecffbb966d82", "valid resume (cwd=Pan)")

    # Case 2: invalid resume id (58a2baf6 is not resumable) — cwd = Pan root
    await run_one(r"D:\project\Pan", "58a2baf6-33d2-4163-a8e7-2753c36ed383", "invalid resume (cwd=Pan)")

    # Case 3: no resume (fresh session) — cwd = Pan root
    await run_one(r"D:\project\Pan", None, "no resume (cwd=Pan)")


asyncio.run(main())
