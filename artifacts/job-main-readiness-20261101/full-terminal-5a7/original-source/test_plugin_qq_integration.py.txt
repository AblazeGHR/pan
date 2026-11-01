"""Existing QQ MCP and Job contracts through projections, using an owned fake HTTP upstream."""
import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.core import session as sess
from packages.core.manifest_loader import REPO_ROOT, load_manifests
from packages.plugins.catalog import Catalog
from packages.plugins.ipc import Bridge
from packages.plugins.runtime import Runtime
from packages.web import server as srv


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body):
            parsed = urlsplit(self.path)
            calls.append((self.command, parsed.path, body))
            result = {"ok": True, "message_id": "fixture-message", "received": body, "path": parsed.path}
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.reply({k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()})

        def do_POST(self):
            self.reply(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("PAN_QQ_API_URL", endpoint)
    monkeypatch.setenv("PAN_API_URL", endpoint)
    yield calls
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.mark.parametrize("mode", ["standalone", "pan_native", "gateway"])
def test_all_seven_qq_tools_keep_legacy_wire_contract(mode, upstream, tmp_path):
    async def run():
        manifest = load_manifests(["manifest.json", "packages/mcp/manifest.json"])
        catalog = Catalog(manifest)
        binding = {"plugin_id": "qq", "enabled": True, "mode": mode}
        session = sess.Session(id="ses_qq_fixture", name="QQ fixture", adapter_config={"plugin_bindings": [binding]})
        runtime = Runtime(lambda: manifest, lambda sid: session if sid == session.id else None)
        bridge = Bridge(runtime, tmp_path)
        physical = catalog.compile([binding])[0]
        from packages.core.adapters.mcp import build_mcp_servers
        session.adapter_config["mcp_servers"] = [physical]
        entry = next(iter(build_mcp_servers(session).values()))
        try:
            async with stdio_client(StdioServerParameters(command=entry["command"], args=entry["args"], cwd=entry["cwd"],
                env={**os.environ, **entry.get("env", {}), "PAN_PLUGIN_IPC": bridge.address})) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert len(names) == (2 if mode == "gateway" else 7)
                    assert "session_create" not in names
                    async def call(export, arguments):
                        name = "qq_" + export if mode == "standalone" else "pan_qq_" + export
                        if mode == "gateway":
                            result = await client.call_tool("api_call", {"name": "qq." + export, "arguments": arguments})
                        else:
                            result = await client.call_tool(name, arguments)
                        assert result.isError is False, result
                        # Existing QQ MCP returns JSON text on SDK versions
                        # without inferred structured output. Preserve that wire
                        # shape rather than inventing a new output schema.
                        return result.structuredContent or json.loads(next(c.text for c in result.content if c.type == "text"))
                    common = {"target_type": "private", "target_id": "123456", "bot_uin": "999999"}
                    sent = await call("send_message", {**common, "text": "isolated fixture"})
                    assert sent["message_id"] == "fixture-message"
                    await call("send_file", {**common, "file_path": "D:/gateway/test-fixture.txt", "name": "fixture.txt"})
                    await call("read_conversation", {"target_id": "123456", "bot_uin": "999999"})
                    await call("list_contacts", {})
                    await call("read_inbox", {"target_id": "123456", "bot_uin": "999999", "consume": True})
                    await call("bind", {"target_type": "user", "target_id": "123456", "bot_uin": "999999"})
                    await call("unbind", {"target_type": "user", "target_id": "123456", "bot_uin": "999999"})
            assert [path for _, path, _ in upstream] == ["/api/qq/send", "/api/qq/send_file", "/api/qq/history",
                "/api/qq/recent_contacts", "/api/qq/inbox", "/api/qq/subscribe", "/api/qq/unsubscribe"]
            assert upstream[0][2] == {**common, "text": "isolated fixture"}
            assert upstream[1][2]["file_path"] == "D:/gateway/test-fixture.txt"
            assert upstream[2][2]["limit"] == "30"
            assert upstream[4][2]["consume"] == "1"
            assert upstream[5][2]["sessionId"] == session.id
            assert upstream[6][2]["sessionId"] == session.id
        finally:
            await bridge.close()
    asyncio.run(run())


def test_qq_job_and_outbox_continue_when_session_tools_are_disabled(upstream, monkeypatch):
    from packages.core import background_jobs, qq_reports
    async def run():
        session = sess.create(name="qq-background", adapter="codex", adapter_config={
            "plugin_bindings": [{"plugin_id": "qq", "enabled": False, "mode": "gateway"}], "mcp_servers": []})
        session.qq_report_targets = {"user:123456@999999"}
        session.qq_report_outbox = {"old-attempt": {"state": "attempted", "target": "user:123456@999999", "text": "never replay"},
                                   "queued-before-upgrade": {"state": "pending", "target": "user:123456@999999", "text": "pending report"}}
        sess.save(session)
        monkeypatch.setattr(srv, "_qq_plugin_client", None)
        monkeypatch.setitem(background_jobs._scheduled_task_hooks, "qq_send", srv._send_scheduled_qq)
        try:
            job = background_jobs._create({'jobId': 'job_legacy_qq', 'kind': 'scheduled-task',
                'status': 'scheduled', 'text': 'scheduled QQ', 'action': {'api': 'send_qq',
                'args': {'targetType': 'user', 'targetId': '123456', 'botUin': '999999'}}})
            result = await background_jobs._run_job_action(job, None, "old-job-fire")
            assert result["status"] == "completed"
            assert result["messageId"] == "fixture-message"
            settled = background_jobs.get(job['jobId'])
            assert settled['snapshotCleanup'] == 'cleaned' and 'attemptSnapshot' not in settled
            assert settled['attempts'][-1]['dispatchKey'] == 'old-job-fire'
            qq_reports.start(srv._send_scheduled_qq, [session])
            await asyncio.gather(*list(qq_reports._tasks.values()))
            assert session.qq_report_outbox["old-attempt"]["state"] == "attempted"
            assert session.qq_report_outbox["queued-before-upgrade"]["state"] == "sent"
            qq_reports.start(srv._send_scheduled_qq, [session])
            if qq_reports._tasks:
                await asyncio.gather(*list(qq_reports._tasks.values()))
            assert [body["text"] for _, _, body in upstream] == ["scheduled QQ", "pending report"]
        finally:
            await qq_reports.stop()
            if srv._qq_plugin_client:
                await srv._qq_plugin_client.aclose()
    asyncio.run(run())
