"""Real TCP HTTP -> shared Inbox -> durable Queue -> fake CLI, plus MCP stdio."""
import asyncio
import json
import os
from pathlib import Path
import socket
import secrets
import psutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import pytest
from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parents[1]


def test_real_http_mcp_fake_cli(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    data = tmp_path / "profile" / "data"
    data.mkdir(parents=True)
    (data / "workdirs/fake").mkdir(parents=True)
    (data / "config.json").write_text(json.dumps({
        "port": port, "scheduler": {"enabled": False}, "memory": {"enabled": False},
        "plugin_manifests": [str(ROOT / "manifest.json"), str(ROOT / "packages/mcp/manifest.json")],
        "logging": {"file": str(data / "logs/pan.log")},
        "qq": {"mode": "selective"}}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
    env.update(PYTHONIOENCODING="utf-8", PAN_E2E_FAKE_CLI=str(ROOT / "tests/support/fake_stream_cli.py"),
               PAN_E2E_FAKE_LOG=str(data / "fake-cli.jsonl"), PAN_TEST_DISABLE_QUEUE_WATCHDOG="1", PAN_TEST_INBOX_NONCE=secrets.token_hex(16))
    base = f"http://127.0.0.1:{port}"
    def request(method, path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    def eventually(fn, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = fn()
                if result:
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise AssertionError("condition did not become true")
    log = (tmp_path / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "tests.support.agent_inbox_http_server", "--port", str(port),
                                "--data-root", str(data)], cwd=ROOT, env=env, stdout=log, stderr=log,
                                **command_window_options())
    try:
        ident = eventually(lambda: request("GET", "/__test/inbox-identity"))
        assert ident["port"] == port and ident["nonce"] == env["PAN_TEST_INBOX_NONCE"]
        actual = psutil.Process(ident["pid"])
        assert process.pid == actual.pid or process.pid in {p.pid for p in actual.parents()}
        assert "tests.support.agent_inbox_http_server" in " ".join(actual.cmdline())
        assert any(c.status == "LISTEN" and c.laddr.port == port for c in actual.net_connections(kind="tcp"))
        assert Path(ident["cwd"]).resolve() == ROOT
        for path in ident["roots"].values():
            assert Path(path).resolve().is_relative_to(data)
        assert Path(ident["fakeCLI"]) == ROOT / "tests/support/fake_stream_cli.py"
        (tmp_path / "identity.json").write_text(json.dumps(ident, indent=2), encoding="utf-8")
        created = request("POST", "/api/sessions", {"name": "inbox-fake", "adapter": "cbc", "workdir": str(data / "workdirs/fake"), "mcpEnabled": False})
        assert "id" in created, created
        sid = created["id"]
        request("PATCH", f"/api/sessions/{sid}/queue/paused", {"paused": True})
        source = {"instanceId": "test-only", "botId": "synthetic-bot", "scope": "group", "peerId": "7"}
        def route(mid, text, target=None):
            return request("POST", "/api/plugins/qq/route", {"source": source, "target": target or {"kind": "agent_inbox"},
                    "sourceMessageId": mid, "content": {"text": text, "parts": [{"type": "text", "text": text}], "attachments": [{"name": "demo.txt", "path": "synthetic"}]}})
        with pytest.raises(urllib.error.HTTPError) as unsupported:
            route("unsupported", "must-not-deliver", {"kind": "user_inbox"})
        assert unsupported.value.code == 409
        old = route("old", "old-silent")
        lid = old["message"]["lockerId"]
        async def mcp_flow():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            menv = {**env, "PAN_API_URL": base, "PAN_AGENT_SESSION_ID": sid}
            params = StdioServerParameters(command=sys.executable, args=["-m", "packages.mcp.server"], cwd=str(ROOT), env=menv)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert "agent_inbox" in [t.name for t in tools.tools]
                    result = await client.call_tool("agent_inbox", {"operation": "subscribe", "locker_id": lid, "mode": "full"})
                    assert not result.isError
                    payload = json.loads(result.content[0].text)
                    assert payload["subscription"]["mode"] == "full"
                    read_result = await client.call_tool("agent_inbox", {"operation": "read", "locker_id": lid})
                    assert json.loads(read_result.content[0].text)["messages"][0]["content"]["text"] == "old-silent"
        asyncio.run(mcp_flow())
        new = route("new", "new-forwarded")
        rows = request("POST", "/api/agent-inbox/read", {"lockerId": lid})["messages"]
        assert [r["content"]["text"] for r in rows] == ["old-silent"]
        disk = json.loads((data / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
        queued = next(r for r in disk["queue_pending"] if r.get("messageId") == new["message"]["id"])
        qid = queued["id"]
        assert queued["queueItemId"] == qid and queued["attachments"][0]["name"] == "demo.txt"
        assert queued["parts"][0]["text"] == "new-forwarded"
        fake_log = data / "fake-cli.jsonl"
        paused_records = [json.loads(line) for line in fake_log.read_text(encoding="utf-8").splitlines()] if fake_log.exists() else []
        assert not any(r.get("kind") == "received" for r in paused_records)  # Pause blocks handoff; idle spawn is allowed.
        route("new", "new-forwarded")
        disk2 = json.loads((data / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
        assert len([r for r in disk2["queue_pending"] if r["id"] == qid]) == 1
        request("PATCH", f"/api/sessions/{sid}/queue/paused", {"paused": False})
        def received():
            path = data / "fake-cli.jsonl"
            return path.exists() and any("new-forwarded" in r.get("text", "") for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()))
        eventually(received)
        request("POST", "/api/agent-inbox/backlog", {"lockerId": lid})
        assert request("POST", "/api/agent-inbox/read", {"lockerId": lid})["messages"] == []
        (tmp_path / "evidence.json").write_text(json.dumps({"sessionId": sid, "lockerId": lid, "queueItemId": qid,
              "pauseAcceptance": True, "fakeCLIReceived": True, "mcpStdio": True}, indent=2), encoding="utf-8")
    finally:
        if process.poll() is None:
            try:
                request("POST", "/__test/inbox-exit", {})
                process.wait(timeout=20)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
        log.close()
    assert process.returncode == 0, (tmp_path / "server.log").read_text(encoding="utf-8")
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    if "actual" in locals():
        assert not actual.is_running()
    (tmp_path / "cleanup.json").write_text(json.dumps({"port": port, "listenerClosed": True, "serverExited": True, "exitCode": process.returncode}), encoding="utf-8")
