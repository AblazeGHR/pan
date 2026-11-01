"""CP-X3: policy unit tests, never evidence of a target POSIX runtime."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import platform, session, worker
from packages.core.adapters import get_adapter


@pytest.mark.parametrize("adapter", ["codex", "cbc", "kimi"])
@pytest.mark.parametrize("pending", [False, True])
def test_native_start_rejected_before_recovery_or_spawn(monkeypatch, adapter, pending):
    s = session.Session(id="ses-native-denied", name="native", adapter=adapter)
    s.adapter_config = {"native_control_next" if pending else "native_control": True}
    monkeypatch.setattr(session, "get", lambda *a, **k: s)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: None)
    recover = AsyncMock(side_effect=AssertionError("recovery before admission"))
    monkeypatch.setattr(worker, "_prepare_offline_recovery", recover)
    before = dict(s.adapter_config)
    result = asyncio.run(worker._create_worker(s.id))
    assert isinstance(result, str) and "unsupported_platform" in result
    assert s.adapter_config == before
    recover.assert_not_awaited()


def test_direct_spawn_rejects_native_before_adapter_or_plugin_io(monkeypatch):
    s = session.Session(id="ses-spawn-denied", name="native", adapter="codex")
    s.adapter_config = {"native_control": True}
    monkeypatch.setattr(session, "get", lambda *a, **k: s)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    adapter = Mock(name="adapter", build_spawn_args=Mock(side_effect=AssertionError("argv built")))
    adapter.name = "codex"
    result = asyncio.run(worker._spawn_process(s.id, adapter))
    assert isinstance(result, str) and "unsupported_platform" in result
    adapter.build_spawn_args.assert_not_called()


def test_stream_spawn_preserves_literal_cwd_and_argv(monkeypatch, tmp_path):
    s = session.Session(id="ses-stream", name="stream", adapter="claude")
    s.workdir = str(tmp_path / "space 中文 & literal")
    monkeypatch.setattr(session, "get", lambda *a, **k: s)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    from packages.plugins import lifecycle
    monkeypatch.setattr(lifecycle, "capture_async", AsyncMock())
    proc = object()
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = get_adapter("claude")
    monkeypatch.setattr(adapter, "build_spawn_args", lambda *a: ["cli", "arg with spaces", "a&b"])
    monkeypatch.setattr(worker, "_log_mcp_launch", lambda **k: None)
    assert asyncio.run(worker._spawn_process(s.id, adapter)) is proc
    assert spawn.call_args.args == ("cli", "arg with spaces", "a&b")
    assert spawn.call_args.kwargs["cwd"] == s.workdir
    assert "shell" not in spawn.call_args.kwargs


@pytest.mark.parametrize("target", ["linux", "darwin"])
def test_worker_spawn_options_isolate_host_signals(target):
    from packages.core.platform.worker_runtime import spawn_options
    assert spawn_options(target) == {"start_new_session": True}


def test_worker_spawn_options_preserve_windows_policy(monkeypatch):
    from packages.core.platform import worker_runtime
    sentinel = {"creationflags": 123}
    monkeypatch.setattr(worker_runtime, "command_window_options", lambda: sentinel)
    assert worker_runtime.spawn_options("win32") == sentinel


@pytest.mark.parametrize("operation", ["_restart_worker_unlocked", "_respawn_worker_unlocked"])
def test_native_restart_rejects_before_stopping_existing_runtime(monkeypatch, operation):
    s = session.Session(id="ses-restart-denied", name="native", adapter="codex")
    s.adapter_config = {"native_control": True}
    w = worker.Worker(worker_id="w-denied", session_id=s.id, adapter=get_adapter("codex"))
    monkeypatch.setattr(worker, "workers", {w.worker_id: w})
    monkeypatch.setattr(session, "get", lambda *a, **k: s)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    stop = AsyncMock(side_effect=AssertionError("must not stop"))
    monkeypatch.setattr(worker, "_kill_process_tree", stop)
    result = asyncio.run(getattr(worker, operation)(w.worker_id))
    assert "unsupported_platform" in result
    assert w.status == "idle" and w.generation == 0
    stop.assert_not_awaited()


def test_unknown_capability_probe_is_not_reported_as_unsupported(monkeypatch):
    s = session.Session(id="ses-probe-failure", name="native", adapter="codex")
    s.adapter_config = {"native_control": True}
    monkeypatch.setattr(platform, "supports", Mock(side_effect=RuntimeError("probe failed")))
    with pytest.raises(RuntimeError, match="probe failed"):
        worker._native_platform_error(s)
