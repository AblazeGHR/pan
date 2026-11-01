"""Owned full-lifespan test launcher; never starts a QQ gateway."""
import asyncio
import json
import os
import sys
from pathlib import Path
from tests.support import isolated_http_server

root = Path(sys.argv[sys.argv.index("--data-root") + 1]).resolve()
port = int(sys.argv[sys.argv.index("--port") + 1])
assert port not in {8767, 8768} and root.name == "data"
root.mkdir(parents=True, exist_ok=True)
from packages.qq import gateway_plugins
# The empty local manifest makes auto_start a no-op, with no real gateway reads.
gateway_plugins.ROOT = root.parent
gateway_plugins.MANIFEST = root / "qq_plugins" / "manifest.json"
gateway_plugins.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
gateway_plugins.MANIFEST.write_text('{"plugins":[]}', encoding="utf-8")
import packages.web.server as server
server._PROJECT_DIR = root.parent

@server.app.get("/__test/inbox-identity")
async def identity():
    from packages.core import session, config, background_jobs
    from packages.core.platform import data_paths
    from packages.core.terminal import registry
    from packages.core.inbox_service import get_inbox
    return {"pid": os.getpid(), "port": port, "cwd": str(Path.cwd()), "roots": {
        "data": str(server.DATA_DIR), "sessions": str(session.SESSION_DIR),
        "config": str(config.CONFIG_FILE),
        "jobs": str(Path(os.environ.get("PAN_BACKGROUND_JOBS_DIR") or background_jobs.DEFAULT_ROOT).expanduser()),
        "scheduler": str(data_paths.job_root()), "terminal": str(registry.DEFAULT_REGISTRY_ROOT),
        "gateway": str(gateway_plugins.MANIFEST), "inbox": str(get_inbox().path),
        "attachments": str(server.ATTACHMENTS_DIR), "workdirs": str(server.WORKDIRS_DIR),
        "errorLog": os.environ["PAN_SESSION_ERROR_LOG_PATH"],
        "mcpConfigs": str(__import__("packages.core.adapters.cbc.adapter", fromlist=["MCP_CONFIG_DIR"]).MCP_CONFIG_DIR)},
        "fakeCLI": os.environ["PAN_E2E_FAKE_CLI"], "nonce": os.environ["PAN_TEST_INBOX_NONCE"]}

@server.app.post("/__test/inbox-exit")
async def stop():
    # If graceful shutdown stalls, retain thread stacks before the harness's
    # unchanged 20-second wait expires. Only this owned test launcher uses it.
    import faulthandler
    faulthandler.dump_traceback_later(15)
    def task_stacks():
        import traceback
        host = server.app.state.pan_uvicorn_server
        print('SHUTDOWN_CONNECTIONS', repr(host.server_state.connections), file=sys.stderr, flush=True)
        for listener in host.servers:
            print('SHUTDOWN_LISTENER', 'active_count=', getattr(listener, '_active_count', None),
                  'clients=', len(getattr(listener, '_clients', ())), file=sys.stderr, flush=True)
            for transport in list(getattr(listener, '_clients', ())):
                print('SHUTDOWN_TRANSPORT', repr(transport),
                      'protocol=', repr(transport.get_protocol()),
                      'closing=', transport.is_closing(),
                      'state=', {key: repr(getattr(transport, key, None)) for key in
                          ('_read_fut', '_write_fut', '_pending_write', '_called_connection_lost')},
                      file=sys.stderr, flush=True)
        for task in asyncio.all_tasks():
            print('SHUTDOWN_TASK', repr(task), file=sys.stderr, flush=True)
            current = task.get_coro()
            while current is not None:
                frame = getattr(current, 'cr_frame', None) or getattr(current, 'gi_frame', None)
                if frame is not None:
                    traceback.print_stack(frame, limit=1, file=sys.stderr)
                current = getattr(current, 'cr_await', None) or getattr(current, 'gi_yieldfrom', None)
        sys.stderr.flush()
    asyncio.get_running_loop().call_later(15, task_stacks)
    asyncio.get_running_loop().call_later(.1, setattr, server.app.state.pan_uvicorn_server, "should_exit", True)
    return {"ok": True}

isolated_http_server.main()
