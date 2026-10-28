"""Opt-in instrumentation for the unchanged seven owned-host pytest nodes.

Copies the existing launcher into that test's own data root, isolating the
canonical receipt root and port. No product or shared fixture is patched.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3

import httpx
import psutil
import pytest

from packages.core import launcher


def pytest_addoption(parser):
    parser.addoption("--cpx1-genuine-main", action="store_true", default=False)


@pytest.fixture(autouse=True)
def instrument_owned_host(request, monkeypatch):
    if request.node.originalname != "test_owned_host_signal_vs_exit_api_handshake":
        yield
        return
    import test_real_queue_http_e2e as real
    genuine = request.config.getoption("--cpx1-genuine-main")

    original_start, original_stop, original_request = real._PanRuntime.start, real._PanRuntime.stop, real._PanRuntime.request
    source = real.SERVER_LAUNCHER.read_text(encoding="utf-8")
    anchor = "    server.DATA_DIR = data_root\n"
    assert source.count(anchor) == 1
    injected = '''    # CP-X1: this process and every lifecycle receipt belong to this fixture.
    server._PROJECT_DIR = data_root
    os.environ["PAN_PORT"] = str(args.port)
    class Cpx1Capture:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("path") != "/api/internal/main/shutdown":
                return await self.app(scope, receive, send)
            event = {"statusCode": None, "response": None}
            async def capture(message):
                if message["type"] == "http.response.start":
                    event["statusCode"] = message["status"]
                if message["type"] == "http.response.body":
                    try:
                        event["response"] = json.loads(message.get("body", b""))
                    except (ValueError, UnicodeError):
                        pass
                    host = getattr(server.app.state, "pan_uvicorn_server", None)
                    event["shouldExit"] = getattr(host, "should_exit", None)
                    (data_root / "cpx1-internal.json").write_text(json.dumps(event), encoding="utf-8")
                await send(message)
            await self.app(scope, receive, capture)
    server.app.add_middleware(Cpx1Capture)
    @server.app.get("/__test/cpx1-should-exit")
    async def cpx1_should_exit():
        host = getattr(server.app.state, "pan_uvicorn_server", None)
        return {"shouldExit": getattr(host, "should_exit", None)}
'''
    source = source.replace(anchor, injected + anchor)
    records = []

    def snapshot(instance, label):
        process = instance.process
        port = real.PORT
        owner = launcher.listener_owner(port)
        event = {"point": label, "port": port, "popenPid": process.pid if process else None,
                 "exitCode": process.poll() if process else None, "listenerOwner": owner}
        for key, pid in (("popenBirth", event["popenPid"]), ("listenerBirth", owner)):
            try:
                event[key] = psutil.Process(pid).create_time() if pid else None
            except psutil.Error:
                event[key] = None
        if owner and event["listenerBirth"]:
            checked = launcher.service_process_identity(owner, instance.data_root, event["listenerBirth"])
            event["serviceIdentity"] = {key: checked.get(key) for key in
                ("pid", "createdAt", "rootMarker", "entryMarker", "commandMarker", "ok", "error")}
        try:
            response = httpx.get(instance.base_url + "/__test/cpx1-should-exit", timeout=.5, trust_env=False)
            event["shouldExit"] = response.json().get("shouldExit")
        except (httpx.HTTPError, ValueError):
            event["shouldExit"] = "unavailable"
        internal = instance.data_root / "cpx1-internal.json"
        if internal.exists():
            event["internalShutdown"] = json.loads(internal.read_text(encoding="utf-8"))
        else:
            event["internalShutdown"] = "not_observed"
        path = instance.data_root / "data" / "lifecycle-executions" / "executions.sqlite3"
        if path.exists():
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
            event["receipts"] = [{"effectId": d.get("effectId"), "jobId": d.get("jobId"),
                "requestId": d.get("frozenPlan", {}).get("arguments", {}).get("requestId"),
                "phase": d.get("phase"), "result": d.get("result")} for d in docs]
        else:
            event["receipts"] = []
        records.append(event)

    def start(instance):
        instance.data_root.mkdir(parents=True, exist_ok=True)
        if genuine:
            owned_launcher = instance.data_root / "main.py"
            shutil.copyfile(real.ROOT / "main.py", owned_launcher)
            assert owned_launcher.read_bytes() == (real.ROOT / "main.py").read_bytes()
            shutil.copyfile(real.ROOT / "tests/support/lifecycle_main_sitecustomize.py",
                            instance.data_root / "sitecustomize.py")
            config_path = instance.data_root / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            config["qq"] = {"enabled": False}
            config["wechat"] = {"enabled": False}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            monkeypatch.setenv("PAN_PORT", str(real.PORT))
            existing = os.environ.get("PYTHONPATH", "")
            monkeypatch.setenv("PYTHONPATH", str(instance.data_root) + os.pathsep + existing)
        else:
            owned_launcher = instance.data_root / "isolated_http_server.py"
            owned_launcher.write_text(source, encoding="utf-8")
        monkeypatch.setattr(real, "SERVER_LAUNCHER", owned_launcher)
        if genuine:
            # Execute the actual copied entry from its owned checkout root;
            # launcher identity checks both entry and checkout containment.
            popen = real.subprocess.Popen
            def owned_cwd(*args, **kwargs):
                kwargs["cwd"] = str(instance.data_root)
                return popen(*args, **kwargs)
            with monkeypatch.context() as local:
                local.setattr(real.subprocess, "Popen", owned_cwd)
                result = original_start(instance)
        else:
            result = original_start(instance)
        snapshot(instance, "started")
        return result

    def observed_request(instance, method, path, **kwargs):
        response = original_request(instance, method, path, **kwargs)
        if path == "/api/system-actions/main-lifecycle":
            records.append({"point": "actionResponse", "response": response})
        return response

    def stop(instance):
        snapshot(instance, "before_cleanup")
        try:
            result = original_stop(instance)
        except Exception as exc:
            records.append({"point": "cleanup", "ok": False, "errorType": type(exc).__name__})
            raise
        records.append({"point": "cleanup", "ok": True, "listenerOwner": launcher.listener_owner(real.PORT)})
        return result

    monkeypatch.setattr(real._PanRuntime, "start", start)
    monkeypatch.setattr(real._PanRuntime, "stop", stop)
    monkeypatch.setattr(real._PanRuntime, "request", observed_request)
    yield
    target = Path("evidence/cp-x1-lifecycle-v2-20261029/owned-host-genuine" if genuine
                  else "evidence/cp-x1-lifecycle-v2-20261029/owned-host")
    target.mkdir(parents=True, exist_ok=True)
    parameter = request.node.callspec.params["method"]
    (target / (parameter + ".json")).write_text(json.dumps(records, indent=2), encoding="utf-8")
