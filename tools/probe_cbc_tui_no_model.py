"""Owned real CBC TUI startup with private profile and a blocking submit hook.

No real credentials or model authorization. Submission is attempted only after
SessionStart; an unconditional fixture hook denies it before provider execution.
"""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.core.adapters.cbc.adapter import CbcAdapter


def main():
    with tempfile.TemporaryDirectory(prefix="pan-cbc-tui-no-model-") as directory:
        home = Path(directory)
        guard = home / "deny_submit.py"
        receipt = home / "submit-hook-receipt.json"
        guard.write_text('import json,sys,pathlib\nevent=json.load(sys.stdin)\npathlib.Path(' + repr(str(receipt)) + ').write_text(json.dumps({"event":event.get("hook_event_name")}))\nprint("Fixture submit guard: no provider authorized",file=sys.stderr)\nsys.exit(2)\n', encoding="utf-8")
        settings = {"trustAll": True, "autoUpdates": False, "alwaysThinkingEnabled": False,
                    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": subprocess.list2cmdline([sys.executable.replace('\\', '/'), guard.as_posix()]), "timeout": 12}]}]}}
        sid = str(uuid.uuid4())
        cli = CbcAdapter().resolved_cli_argv() + ["--session-id", sid, "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence", "--settings", json.dumps(settings)]
        env = {k: os.environ[k] for k in ("SystemRoot", "PATH", "TEMP", "TMP") if k in os.environ}
        env.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(home / "appdata"), LOCALAPPDATA=str(home / "local"), CODEBUDDY_CONFIG_DIR=str(home / "profile"), DISABLE_TELEMETRY="1", DISABLE_GALILEO="1")
        command = [sys.executable, str(ROOT / "packages/core/adapters/cbc/native_bridge.py"), "--cwd", str(home), "--session-id", sid, "--cli-argv", json.dumps(cli)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, cwd=str(home), creationflags=subprocess.CREATE_NO_WINDOW)
        frames = queue.Queue()
        def read():
            for line in process.stdout:
                try:
                    frames.put(json.loads(line))
                except ValueError:
                    pass
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        terminal = bytearray()
        types = []
        submitted = False
        children = []
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    event = frames.get(timeout=.1)
                except queue.Empty:
                    continue
                types.append(event.get("type"))
                if event.get("type") == "pan.native_output":
                    terminal.extend(base64.b64decode(event["data"]))
                if event.get("type") == "system" and event.get("subtype") == "init" and not submitted:
                    submitted = True
                    process.stdin.write((json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "  deterministic fixture prompt  "}]}}) + "\n").encode())
                    process.stdin.flush()
                if b"Fixture submit guard: no provider authorized" in terminal:
                    break
        finally:
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
            process.stdin.close()
            process.wait(timeout=10)
            thread.join(2)
            _, alive = psutil.wait_procs(children, timeout=5)
            assert not alive, "owned CBC child did not retire with bridge Job"
        text = terminal.decode("utf-8", errors="replace").lower()
        report = {"bridge_pid": process.pid, "retired": process.poll() is not None,
                  "submission_attempted": submitted, "submit_hook_witness": json.loads(receipt.read_text()) if receipt.exists() else None,
                  "owned_children_retired": True, "event_types": sorted(set(types)), "terminal_bytes": len(terminal),
                  "markers": [s for s in ["welcome", "authentication", "login", "log in", "sign in", "workspace", "fixture submit guard: no provider authorized", "cbc control channel unavailable"] if s in text]}
        destination = ROOT / "artifacts/cbc-hook-channel-20261014/real-tui-no-model.json"
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
