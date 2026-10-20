"""Disposable Pan HTTP/WS + Chromium; Codex/local fixture or opt-in CBC/hy3."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main(cbc=False):
    for key in tuple(os.environ):
        if key.startswith("PAN_"):
            os.environ.pop(key)
    runtime = Path(tempfile.mkdtemp(prefix="pan-codex-sync-"))
    for name, folder in (("PAN_BACKGROUND_JOBS_DIR", "jobs"), ("PAN_SCHEDULER_DIR", "scheduler"),
                         ("PAN_TERMINALS_DIR", "terminals")):
        os.environ[name] = str(runtime / folder)
    home = runtime / "home"
    home.mkdir()
    os.environ["CODEX_HOME"] = str(home)
    if cbc:
        os.environ.update(CODEBUDDY_CONFIG_DIR=str(runtime / "cbc-profile"),
            CODEBUDDY_PROJECT_CONFIG_DIR=str(runtime / "cbc-project-config"),
            CODEBUDDY_DISABLE_SESSION_HISTORY_CLEANUP="1", XDG_CACHE_HOME=str(runtime / "cache"))
    from packages.core import config, session, workspace, worker
    config.CONFIG_FILE = runtime / "config.json"
    config.CONFIG_FILE.write_text('{"plugin_manifests":[],"scheduler":{"enabled":false}}')
    session.SESSION_DIR = runtime / "sessions"
    session._all_loaded = True
    session._cache.clear()
    workspace.WORKSPACE_DIR = runtime / "workspaces"
    workspace._loaded = True
    workspace._cache.clear()
    helper_spec = importlib.util.spec_from_file_location("remote_helper", ROOT / "audit/terminal/codex/probe_codex_remote_tui.py")
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    helper.seed_placeholder_credentials(str(home), [])
    from tests.test_codex_native_kernel_real import SyntheticResponses
    from packages.core.adapters.codex import CodexAdapter
    provider = SyntheticResponses()
    provider.release.set()
    CodexAdapter.mcp_args = lambda *args: ["-c", 'model_provider="probe_bh"', "-c",
        f'model_providers.probe_bh={{name="probe_bh",base_url="{provider.base_url}",wire_api="responses"}}']
    CodexAdapter.enrich_after_result = lambda *args: None
    if cbc:
        from packages.core.adapters.cbc.adapter import CbcAdapter
        assert "hy3" in CbcAdapter().supported_models
        CbcAdapter.mcp_args = lambda *args: ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--tools", "", "--settings", '{"trustAll":true,"autoUpdates":false}']
        CbcAdapter.enrich_after_result = lambda *args: None
    worker._DEFAULTS_INITIALIZED = True
    from packages.web import server
    import uvicorn
    from types import SimpleNamespace
    server.DATA_DIR = runtime / "data"
    server.WORKDIRS_DIR = runtime / "workdirs"
    server.ATTACHMENTS_DIR = runtime / "attachments"
    server._character_manager = SimpleNamespace(_manifest_config=SimpleNamespace(plugins=[], mcp_servers=[], characters=[]))
    worker._broadcast = server.broadcast
    cwd = runtime / "work"
    cwd.mkdir()
    s = session.create("Native Sync Proof", adapter="cbc" if cbc else "codex", workdir=str(cwd))
    s.model = "hy3" if cbc else "probe-model"
    s.permission_mode = "bypassPermissions" if cbc else "bypass"
    s.always_thinking_enabled = False
    s.system_prompt = "Reply only DONE to every user message. Do not use tools."
    s.adapter_config.update(native_control=True, output_mode="stream")
    session.save(s)
    w = await worker.create_worker(s.id)
    assert isinstance(w, worker.Worker), w
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in (8767, 8768)
    from packages.web import terminal_api
    terminal_runtime = await terminal_api.start_runtime(server.app, port=port)
    if terminal_runtime._reconcile_task:
        await terminal_runtime._reconcile_task
    http = uvicorn.Server(uvicorn.Config(server.app, lifespan="off", access_log=False, log_level="warning"))
    task = asyncio.create_task(http.serve(sockets=[listener]))
    browser = None
    try:
        async with asyncio.timeout(20):
            while not http.started or not s.cli_session_id:
                await asyncio.sleep(.02)
        thread, pid = s.cli_session_id, w.process.pid
        browser = await asyncio.create_subprocess_exec("node", str(ROOT / "packages/web/e2e/codex-native-sync-real.e2e.mjs"),
            f"http://127.0.0.1:{port}", s.id, str(runtime), "cbc" if cbc else "codex", cwd=ROOT / "packages/web",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(browser.communicate(), 150)
        print(stdout.decode("utf-8", errors="replace"), flush=True)
        if browser.returncode:
            print("NATIVE_SCREEN", helper.strip_ansi(bytes(w.native_output).decode(errors="replace"))[-5000:], flush=True)
        assert browser.returncode == 0, stderr.decode("utf-8", errors="replace")
        assert s.cli_session_id == thread and w.process.pid == pid and w.process.returncode is None
        await worker._flush_history_now(w)
        disk = json.loads(session._path(s.id).read_text(encoding="utf-8"))
        assert [row["content"] for row in disk["history"] if row["role"] == "user"] == ["BROWSER PAN ONE", "BROWSER NATIVE TWO", "BROWSER PAN THREE", "BROWSER NATIVE FOUR"]
        assert len([row for row in disk["history"] if row["role"] == "assistant"]) == 4
        print(json.dumps({"passed": True, "runtime": str(runtime), "port": port, "wrapperPid": pid,
            "thread": thread, "sameRuntime": True, "historyRevision": s.history_revision,
            "historyRows": len(disk["history"]), "provider": "actual CBC hy3" if cbc else "local synthetic Responses; actual Codex CLI"}), flush=True)
    finally:
        if browser is not None and browser.returncode is None:
            browser.terminate()
            await browser.wait()
        http.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()
        await worker.kill_worker(w.worker_id)
        await terminal_api.stop_runtime(server.app)
        provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cbc", action="store_true", help="Opt in to four real hy3 turns on a new temporary profile")
    asyncio.run(main(parser.parse_args().cbc))
