"""No desktop console for the launcher, including Windows Python redirectors."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from packages.core.terminal.service import TerminalService


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation flags")
@pytest.mark.parametrize("breakaway_denied", [False, True])
def test_launcher_no_window_flags_survive_breakaway_fallback(tmp_path, monkeypatch, breakaway_denied):
    service = TerminalService(tmp_path / "terminals", log_stderr=False)
    calls = []

    def spawn(argv, **kwargs):
        calls.append(kwargs["creationflags"])
        if breakaway_denied and len(calls) == 1:
            error = OSError("owned injected policy refusal")
            error.winerror = 5
            raise error
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    service._spawn_production_launcher(
        "term_visibility", secret_file=tmp_path / "fixture.secret",
        rows=24, cols=80, cwd=None, shell_argv=None,
    )
    assert len(calls) == (2 if breakaway_denied else 1)
    for flags in calls:
        assert flags & subprocess.CREATE_NO_WINDOW
        assert not flags & subprocess.DETACHED_PROCESS
        assert not flags & subprocess.CREATE_NEW_CONSOLE
    assert calls[0] & subprocess.CREATE_BREAKAWAY_FROM_JOB
    if breakaway_denied:
        assert not calls[1] & subprocess.CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.skipif(os.name != "nt", reason="real Windows console visibility")
def test_real_launcher_interpreter_has_no_visible_console(tmp_path, monkeypatch):
    """Uses sys.executable intentionally: uv's redirector is part of the test.

    Only the owned probe process is spawned/waited; no user window is hidden.
    The Win32 witness checks the actual interpreter, not just Popen's shim PID.
    """
    destination = tmp_path / "console.json"
    code = """
import ctypes,json,os,sys
from pathlib import Path
k=ctypes.WinDLL('kernel32',use_last_error=True)
u=ctypes.WinDLL('user32',use_last_error=True)
k.GetConsoleWindow.restype=ctypes.c_void_p
u.IsWindowVisible.argtypes=[ctypes.c_void_p]
u.IsWindowVisible.restype=ctypes.c_int
h=k.GetConsoleWindow()
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'console':h or 0,
    'visible':bool(h and u.IsWindowVisible(h))}),encoding='utf-8')
"""
    service = TerminalService(tmp_path / "terminals", log_stderr=False)
    monkeypatch.setattr(service, "_launcher_argv", lambda *_a, **_k:
                        [sys.executable, "-c", code, str(destination)])
    process = service._spawn_production_launcher(
        "term_visibility", secret_file=tmp_path / "fixture.secret",
        rows=24, cols=80, cwd=None, shell_argv=None,
    )
    try:
        assert process.wait(timeout=15) == 0
        witness = json.loads(destination.read_text(encoding="utf-8"))
        assert witness["pid"] > 0
        assert witness["visible"] is False, witness
    finally:
        if process.poll() is None:
            # Popen retains the owned process handle; never kill by name/PID alone.
            process.terminate()
            process.wait(timeout=10)


def test_interrupt_witness_waits_for_contents_not_file_creation(monkeypatch):
    import importlib.util
    from pathlib import Path
    from unittest.mock import Mock
    path = Path(__file__).resolve().parents[1] / "audit/terminal/implementation/interrupt-ma/production_probe.py"
    spec = importlib.util.spec_from_file_location("visibility_interrupt_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    witness = Mock()
    witness.read_text.side_effect = ["", "12", "1234"]
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    assert probe.wait_file_text(witness, lambda text: text == "1234", 1) == "1234"
    assert witness.read_text.call_count == 3
    witness.read_text.side_effect = None
    witness.read_text.return_value = "WRONG"
    assert probe.wait_file_text(witness, lambda text: text == "1234", 0) is None
