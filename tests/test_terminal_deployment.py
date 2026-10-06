"""Check missing deployment dependencies before provider-side effects."""
import builtins
import importlib.util
import sys
import time
from pathlib import Path
import pytest
from packages.core.rewind import hybrid
from packages.core.terminal import observer
from packages.core.terminal.contracts import BackendUnavailableError


def test_missing_pyte_names_deployed_environment(monkeypatch):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "pyte":
            raise ImportError("test missing dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(BackendUnavailableError, match="运行 Pan"):
        observer.PyteScreenObserver(rows=24, cols=80)


@pytest.mark.parametrize("scope", [1, 2, 3])
def test_missing_dependency_prevents_fork(monkeypatch, tmp_path, scope):
    forks = []
    def refuse():
        raise BackendUnavailableError("dependency missing")
    monkeypatch.setattr(hybrid, "require_pyte", refuse)
    monkeypatch.setattr(hybrid, "fork_session", lambda *a, **k: forks.append(a))
    result = hybrid.run_hybrid_rewind("synthetic", tmp_path, "anchor", scope=scope)
    assert not result.success
    assert result.stage == "failed"
    assert result.fork is None
    assert forks == []


def test_deployment_check_rejects_missing_sidecar(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/check_terminal_deployment.py"
    spec = importlib.util.spec_from_file_location("deployment_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.check(tmp_path)
    assert report["checks"]["sidecar"] is False
    assert report["ready"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows rewind PTY")
def test_real_rewind_observer_in_deployed_python(tmp_path):
    from packages.core.rewind.pty_session import PtySession, retained_owner_ids
    owner = PtySession([sys.executable, "-X", "utf8", "-c",
                        "print('部署中文观察_READY', flush=True); input()"], tmp_path)
    try:
        outcome = owner.context.wait_for(lambda text: "部署中文观察_READY" in text, 10)
        assert outcome.matched is True
        assert outcome.timed_out is False
        assert "部署中文观察_READY" in outcome.last_text
    finally:
        report = owner.close()
        deadline = time.monotonic() + 10
        while report["ok"] is not True and time.monotonic() < deadline:
            time.sleep(.1)
            report = owner.close()
        assert report["ok"] is True
        assert owner.owner_id not in retained_owner_ids()
